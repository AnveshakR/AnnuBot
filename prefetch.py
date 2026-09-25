"""In-memory read-ahead for a signed googlevideo URL.

googlevideo kills a single long-lived GET that is drained at ~1x playback pace
after ~30s -- that is the ~31s cadence the bot used to hit, and it is a
property of the access pattern, not of the ffmpeg flags or the voice consumer.
So we never use that pattern: the audio is pulled in bounded ranged chunks, as
fast as the CDN will serve, into a RAM buffer that ffmpeg reads over a pipe.

A chunk that fails is retried at the SAME byte offset, so a reset (or a stale
signed URL) never leaves a hole in the byte stream: ffmpeg keeps draining the
buffer while the retry happens and never sees the error at all.
"""
import collections
import io
import logging
import threading
import time

import requests

import discord

logger = logging.getLogger(__name__)


class PrefetchStream(io.RawIOBase):
    """A forward-only byte stream backed by ranged HTTP downloads into RAM."""

    CHUNK = 1 << 20        # 1 MiB per ranged request
    HIGH_WATER = 32 << 20  # pause read-ahead past this much buffered audio

    def __init__(self, url, *, headers=None, refresh=None, timeout=10, retries=6, label=None):
        self._url = url
        self._label = label or 'stream'  # for logs (the signed URL is too long/sensitive)
        self._headers = dict(headers or {})
        self._refresh = refresh          # callable -> fresh signed URL, or None
        self._timeout = timeout
        self._retries = retries
        self._chunks = collections.deque()
        self._head = 0                   # read offset into _chunks[0]
        self._size = 0                   # total bytes buffered (not yet read)
        self._eof = False
        self._stopped = False
        self._closed = False
        self._cv = threading.Condition()
        self._thread = threading.Thread(target=self._pump, name=f'prefetch-{self._label}', daemon=True)
        self._max_fetch = 0.0   # longest single ranged request (diagnostic)
        self._fetches = 0        # number of ranged requests made
        self._thread.start()

    def _pump(self):
        offset = 0
        attempt = 0
        reason = 'unknown'
        t_start = time.time()
        session = requests.Session()
        logger.debug("prefetch %s: started", self._label)
        try:
            while True:
                with self._cv:
                    while not self._stopped and self._size >= self.HIGH_WATER:
                        self._cv.wait(0.25)
                    if self._stopped:
                        reason = 'stopped (closed by player)'
                        return
                headers = dict(self._headers)
                headers['Range'] = f'bytes={offset}-{offset + self.CHUNK - 1}'
                try:
                    _t0 = time.time()
                    r = session.get(self._url, headers=headers, timeout=self._timeout)
                    _dt = time.time() - _t0
                    if _dt > self._max_fetch:
                        self._max_fetch = _dt
                    self._fetches += 1
                    if r.status_code == 416:
                        reason = 'complete (416 past EOF)'
                        break                # asked past EOF: download complete
                    r.raise_for_status()
                    body = r.content
                    if _dt > 5.0:
                        logger.debug("prefetch %s: slow chunk at offset %d took %.1fs", self._label, offset, _dt)
                except Exception as e:
                    attempt += 1
                    logger.warning("prefetch %s: chunk at offset %d failed (attempt %d/%d): %s: %.200s",
                                   self._label, offset, attempt, self._retries, type(e).__name__, e)
                    if attempt > self._retries:
                        reason = f'gave up after {attempt - 1} retries'
                        logger.error("prefetch %s: giving up at offset %d; ffmpeg will see EOF",
                                     self._label, offset)
                        break                # give up; ffmpeg will see EOF and resume
                    # every other failure: assume the signed URL went stale
                    if self._refresh and attempt % 2 == 0:
                        try:
                            fresh = self._refresh()
                            if fresh:
                                self._url = fresh
                                logger.info("prefetch %s: refreshed signed URL", self._label)
                            else:
                                logger.warning("prefetch %s: URL refresh returned nothing", self._label)
                        except Exception as re:
                            logger.warning("prefetch %s: URL refresh failed: %s: %.200s",
                                           self._label, type(re).__name__, re)
                    time.sleep(min(0.25 * 2 ** attempt, 5.0))
                    continue                 # same offset -> no gap
                attempt = 0
                if body:
                    with self._cv:
                        self._chunks.append(body)
                        self._size += len(body)
                        self._cv.notify_all()
                    offset += len(body)
                if len(body) < self.CHUNK:
                    reason = 'complete (short last chunk)'
                    break                    # short read == last chunk
        except BaseException:
            reason = 'crashed'
            logger.exception("prefetch %s: pump thread crashed at offset %d", self._label, offset)
            raise
        finally:
            logger.debug("prefetch %s: pump finished: %s; %d bytes in %d requests over %.1fs, slowest %.1fs",
                         self._label, reason, offset, self._fetches, time.time() - t_start, self._max_fetch)
            with self._cv:
                self._eof = True
                self._cv.notify_all()

    def readable(self):
        return True

    def seekable(self):
        return False

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.CHUNK
        size = min(size, self.CHUNK)
        out = bytearray()
        with self._cv:
            while not out and not self._chunks and not self._eof and not self._stopped:
                self._cv.wait()
            while size > 0 and self._chunks:
                head = self._chunks[0]
                take = min(size, len(head) - self._head)
                out += head[self._head:self._head + take]
                self._head += take
                self._size -= take
                size -= take
                if self._head >= len(head):
                    self._chunks.popleft()
                    self._head = 0
            self._cv.notify_all()
        return bytes(out)                # b'' only at EOF -> ffmpeg sees clean EOF

    def close(self):
        if self._closed:
            return
        self._closed = True
        logger.debug("prefetch %s: closed (%d bytes still buffered)", self._label, self._size)
        with self._cv:
            self._stopped = True
            self._chunks.clear()
            self._head = 0
            self._size = 0
            self._cv.notify_all()


class PrefetchedFFmpegPCMAudio(discord.FFmpegPCMAudio):
    """FFmpegPCMAudio fed from a PrefetchStream over ffmpeg's stdin."""

    def __init__(self, stream: PrefetchStream, **kwargs):
        self._stream = stream
        super().__init__(stream, pipe=True, **kwargs)

    def cleanup(self):
        # FFmpegAudio.cleanup() kills ffmpeg but never closes the source object.
        # Without this, the prefetch thread and its buffer leak for the life of
        # the process and discord.py's pipe-writer thread stays blocked in read().
        proc = getattr(self, '_process', None)
        logger.debug("cleanup for %s: ffmpeg pid %s (returncode %s)", self._stream._label,
                     getattr(proc, 'pid', None), proc.poll() if proc else None)
        try:
            self._stream.close()
        except Exception:
            logger.exception("closing prefetch stream %s failed", self._stream._label)
        super().cleanup()
