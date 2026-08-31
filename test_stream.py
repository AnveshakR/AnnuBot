"""DEFINITIVE test: replicate annubot.audiostream() EXACTLY and prove it works.

Uses the real discord.FFmpegPCMAudio (not raw ffmpeg) and reads frames via
.read(), which is precisely what the bot does when it plays a song. Passes if
audio decodes from the live stream and NO temp file is written.
"""
import asyncio
import glob
import logging
import sys

import discord
import yt_dlp

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('stream-test')

# --- copied verbatim from annubot.py ---
yt_dlp_opts = {
    'quiet': True,
    'no_warnings': True,
    'socket_timeout': 30,
}
ffmpeg_opts = {
    'before_options': '-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}


async def audiostream(url, *, loop=None, stream=True):
    loop = loop or asyncio.get_event_loop()
    ydl_opts = dict(yt_dlp_opts)
    ydl_opts['format'] = 'bestaudio'
    try:
        data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=False))
    except Exception as e:
        logger.error(f"yt-dlp extract failed: {e}")
        return None
    if 'entries' in data:
        data = data['entries'][0]
    stream_url = data.get('url') if stream else None
    if not stream_url:
        logger.error("No stream URL found in yt-dlp result")
        return None
    return (discord.FFmpegPCMAudio(stream_url, **ffmpeg_opts), data)
# --- end verbatim copy ---


def main() -> int:
    url = 'https://www.youtube.com/watch?v=gBmxCcHtY2Y'  # Seishen (bot's /fangs song)
    tmp_before = set(glob.glob('/tmp/annubot_*'))
    logger.info("yt-dlp %s | discord.py %s", yt_dlp.version.__version__, discord.__version__)

    async def run():
        return await audiostream(url)

    source = asyncio.run(run())
    if source is None:
        logger.error("FAIL: audiostream returned None")
        return 1
    audio, data = source
    logger.info("got source; title=%r id=%r", data.get('title'), data.get('id'))

    total, chunks = 0, 0
    try:
        for _ in range(50):  # ~1s of 48k/16bit stereo PCM
            ret = audio.read()
            if not ret:
                logger.error("FAIL: read() empty after %d chunks (stream died)", chunks)
                return 1
            total += len(ret)
            chunks += 1
    except Exception as e:
        logger.error("FAIL: read() raised after %d chunks: %s", chunks, e)
        return 1

    tmp_after = set(glob.glob('/tmp/annubot_*'))
    new_files = tmp_after - tmp_before
    logger.info("decoded %d chunks (%d bytes) via discord.FFmpegPCMAudio.read()", chunks, total)
    logger.info("new /tmp/annubot_* files: %s", new_files or 'none (good - truly streaming)')

    if new_files or chunks == 0:
        logger.error("FAIL")
        return 1
    logger.info("PASS: annubot.audiostream() streams + decodes with no temp file")
    return 0


if __name__ == '__main__':
    sys.exit(main())
