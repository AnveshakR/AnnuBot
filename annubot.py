import yt_dlp
from utils import *
from config import CONFIG
from dotenv import load_dotenv
import os
import random
import discord
import asyncio
from discord.ext import commands
import queue
import logging
import time
import json
import tempfile
from prefetch import PrefetchStream, PrefetchedFFmpegPCMAudio

import sys
import threading

# --- logging ----------------------------------------------------------------
# Verbose by default: our own modules and discord's voice/player internals log
# at DEBUG, with thread + function:line on every record, so a silent stall
# (like the Sept 24 21:51 one) leaves a trail.
#
#   ANNUBOT_LOG_LEVEL=INFO    quieter bot logs (default DEBUG)
#   ANNUBOT_DISCORD_DEBUG=1   also put discord.gateway/http/client at DEBUG.
#                             Off by default: that logs every gateway payload,
#                             including message contents, and is very noisy.
#
# urllib3 stays at INFO on purpose: at DEBUG it logs full request URLs, which
# carry the YouTube API key.
#
# force=True: utils.py calls basicConfig(INFO) at import time (it's imported
# above), which would otherwise make this call a silent no-op.
_LOG_LEVEL = getattr(logging, os.environ.get('ANNUBOT_LOG_LEVEL', 'DEBUG').upper(), logging.DEBUG)
_LOG_FORMAT = ('%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s '
               '[%(threadName)s] %(funcName)s:%(lineno)d: %(message)s')
_LOG_DATEFMT = '%Y-%m-%d %H:%M:%S'
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, datefmt=_LOG_DATEFMT, force=True)
for _name in ('__main__', 'annubot', 'utils', 'prefetch', 'config', 'yt_dlp',
              'discord.voice_state', 'discord.voice_client', 'discord.player'):
    logging.getLogger(_name).setLevel(_LOG_LEVEL)
if os.environ.get('ANNUBOT_DISCORD_DEBUG') == '1':
    logging.getLogger('discord').setLevel(logging.DEBUG)
logging.getLogger('urllib3').setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# Also write to a per-run log file under ~/annubot-deploy/logs/.
# Falls back to stderr-only if the dir can't be created.
try:
    _log_dir = os.path.join(os.path.expanduser('~'), 'annubot-deploy', 'logs')
    os.makedirs(_log_dir, exist_ok=True)
    _log_path = os.path.join(_log_dir, f'logs-{int(time.time())}.log')
    _fh = logging.FileHandler(_log_path)
    _fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
    logging.getLogger().addHandler(_fh)
    logger.info(f"Logging to {_log_path} (bot level {logging.getLevelName(_LOG_LEVEL)})")
except Exception as e:
    logger.warning(f"File log unavailable ({e}); using stderr only")


# Nothing should die without a traceback in the log: uncaught exceptions in the
# main thread, in worker threads (prefetch pump, discord's audio player), and
# in asyncio callbacks/tasks nobody awaited.
def _log_uncaught(exc_type, exc, tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    logger.critical("uncaught exception", exc_info=(exc_type, exc, tb))


def _log_thread_exception(args):
    logger.critical("uncaught exception in thread %s",
                    args.thread.name if args.thread else '?',
                    exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def _log_loop_exception(loop, context):
    exc = context.get('exception')
    logger.error("asyncio: %s | context=%r", context.get('message', 'unhandled exception'),
                 {k: v for k, v in context.items() if k not in ('message', 'exception')},
                 exc_info=(type(exc), exc, exc.__traceback__) if exc else None)


sys.excepthook = _log_uncaught
threading.excepthook = _log_thread_exception

# Fix: load libopus directly since the symlink may be missing.
# Try the common distro locations (Debian/Ubuntu multiarch vs Arch/flat /usr/lib).
import discord.opus as opus
if not opus.is_loaded():
    _opus_candidates = [
        '/usr/lib/x86_64-linux-gnu/libopus.so.0',  # Debian/Ubuntu multiarch
        '/usr/lib/libopus.so.0',                   # Arch / flat multiarch
        '/usr/lib64/libopus.so.0',                 # RHEL-family
        '/usr/local/lib/libopus.so.0',
    ]
    _loaded = False
    for _p in _opus_candidates:
        try:
            opus.load_opus(_p)
            logger.info(f"Loaded libopus from {_p}")
            _loaded = True
            break
        except Exception:
            continue
    if not _loaded and not opus.is_loaded():
        logger.warning("Failed to load libopus from known paths — voice may not work")

#setup
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
ytbase = "https://www.youtube.com/watch?v="

filepath = 'sher.txt'
with open(filepath, encoding='utf8') as fp:
    sher = [line.strip() for line in fp if line.strip()]

yt_dlp_opts = {
    'quiet': True,
    'socket_timeout': 30,
    # Route all yt-dlp output (debug/info/warnings/errors) into our log instead
    # of stdout or nowhere; 'verbose' adds its [debug] lines.
    'logger': logging.getLogger('yt_dlp'),
    'verbose': _LOG_LEVEL <= logging.DEBUG,
}

# NOTE: ffmpeg no longer reads the network directly. The signed googlevideo URL
# is pulled into RAM by PrefetchStream (ranged 1MiB chunks, retry-at-same-offset)
# and fed to ffmpeg over stdin. googlevideo kills a single long-lived GET that is
# drained at ~1x playback pace after ~30s (the ~31s cadence); ranged chunks never
# hold such a socket, so the reset can't happen. A failed chunk retries at the
# SAME byte offset, so a reset never leaves a hole in the byte stream.
#
# The old network-facing flags are gone: -rw_timeout was a hair trigger (a 5s
# read gap under CDN throttling killed the process), -max_delay is a demuxer
# option that does nothing here, and -nostdin must go because we now pipe to
# stdin on purpose.
async def audiostream(url, *, loop=None, stream=True, start=0.0):
    loop = loop or asyncio.get_event_loop()
    ydl_opts = dict(yt_dlp_opts)
    ydl_opts['format'] = 'bestaudio'
    logger.debug("yt-dlp extract start: %s (start=%.1fs)", url, start)
    t0 = time.monotonic()
    try:
        data = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=False)),
            timeout=EXTRACT_TIMEOUT)
    except asyncio.TimeoutError:
        logger.error(f"yt-dlp extract timed out after {EXTRACT_TIMEOUT:.0f}s for {url}; skipping song")
        return None
    except Exception as e:
        logger.exception(f"yt-dlp extract failed for {url}: {e}")
        return None
    if data is None:
        logger.error("yt-dlp returned no data for %s", url)
        return None
    if 'entries' in data:
        entries = [e for e in (data.get('entries') or []) if e]
        if not entries:
            logger.error("yt-dlp returned an empty entry list for %s", url)
            return None
        data = entries[0]
    stream_url = data.get('url') if stream else None
    logger.debug("yt-dlp extract done in %.1fs: id=%s title=%r duration=%s format=%s acodec=%s abr=%s filesize=%s",
                 time.monotonic() - t0, data.get('id'), data.get('title'), data.get('duration'),
                 data.get('format_id'), data.get('acodec'), data.get('abr'),
                 data.get('filesize') or data.get('filesize_approx'))
    if not stream_url:
        logger.error("No stream URL found in yt-dlp result for %s", url)
        return None

    def _refresh():
        # The signed URL expires; re-resolve a fresh one. A retry happens at the
        # SAME byte offset, so a stale-URL failure never leaves a gap.
        d = yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=False)
        if 'entries' in d:
            d = d['entries'][0]
        return d.get('url')

    src = PrefetchStream(stream_url, headers=data.get('http_headers'), refresh=_refresh,
                         label=data.get('id') or url)
    before = f'-ss {start:.3f}' if (start and start > 0) else None
    return (PrefetchedFFmpegPCMAudio(src, before_options=before, options='-vn'), data)


class SongPosition:
    """Tracks a song's playback position in seconds, pause-aware.

    discord.py 2.7.1 removed get_position(), so we track it ourselves with a
    monotonic clock. Pausing freezes the clock; resuming resumes it, so the
    reported position is the true song position (not wall time).
    """

    def __init__(self):
        self._start = time.perf_counter()
        self._paused_total = 0.0
        self._pause_at = None

    def pause(self):
        if self._pause_at is None:
            self._pause_at = time.perf_counter()

    def resume(self):
        if self._pause_at is not None:
            self._paused_total += time.perf_counter() - self._pause_at
            self._pause_at = None

    def seconds(self) -> float:
        now = time.perf_counter()
        if self._pause_at is not None:
            now = self._pause_at  # paused: position frozen at the pause point
        return max(0.0, now - self._start - self._paused_total)

    def seek_to(self, seconds: float):
        """Move the position pointer to `seconds`.

        Used when a resume starts a fresh stream at -ss <seconds>: the new
        clock must read `seconds` at t=0 and continue from there.
        """
        self._start = time.perf_counter() - seconds
        self._paused_total = 0.0
        self._pause_at = None


# per-guild playback state, keyed by guild id (one song playing per guild).
class SongState:
    """Per-song playback state: position tracker + stream-recovery retry count.

    `generation` is bumped every time a new (re)start begins for a guild. The
    after-callbacks capture the generation at start time and ignore themselves
    if it has moved on — that prevents a stale callback from a replaced player
    (e.g. a manual `skip` mid-recovery) from double-advancing the queue.
    """
    def __init__(self):
        self.pos = SongPosition()
        self.retries = 0
        self.generation = 0
        # Positions already resumed from after a tail drop (progress guard:
        # if the CDN keeps cutting the same tail, stop looping and advance).
        self.tail_resume_positions = []


_states = {}


def _bump_generation(guild_id) -> SongState:
    """Get (creating if needed) the guild's SongState and bump its generation.

    Call this at the START of any user-initiated playback change (play, skip).
    The bump invalidates any in-flight recovery or stale after-callback from
    the previous player, so concurrent paths can't double-advance the queue.
    """
    st = _states.setdefault(guild_id, SongState())
    st.generation += 1
    return st


# A stream that dies within the first MIN_RESUME_POS seconds is just a bad
# start (cold CDN, expired URL) — restart from 0 rather than "resuming".
# After MAX_STREAM_RETRIES failed recoveries we give up and advance the queue.
MAX_STREAM_RETRIES = 2
MIN_RESUME_POS = 3.0
# A "clean" finish that lands more than TAIL_TOLERANCE seconds short of the
# song's known duration is not a real end — it's the CDN dropping the socket
# near the end, which ffmpeg reads as EOF (so on_stream_error never fires).
# We treat it as a resumable break instead of advancing the queue.
TAIL_TOLERANCE = 5.0

# Hard ceiling on a single yt-dlp extract_info() call. yt_dlp_opts'
# socket_timeout covers socket reads, but NOT DNS resolution or some TLS
# handshake paths — a hung call there blocks the executor thread forever,
# holds play_lock, and freezes the whole queue (the Sept 11 19:35 freeze).
# asyncio.wait_for cancels the coroutine on expiry; the orphaned executor
# thread is abandoned (harmless — it holds no lock, only a stuck socket).
EXTRACT_TIMEOUT = 60.0

# Max seconds play_next_song will wait for the current player to report
# "not playing / not paused" before forcing an advance. Catches a player that
# desyncs (stuck in a recovery, or a stale after-callback) so it can't hold
# play_lock forever. Generous: a normal song end is near-instant; a stuck one
# is indefinite. 120s is well beyond any legitimate "waiting for voice".
PLAY_WAIT_TIMEOUT = 120.0

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='annu ', intents=intents, help_command=None)

playerembed = discord.Embed(
    title="Now Playing",
    color=discord.Colour(0x7289DA)
)

class GuildQueue:

    # keeps track of object instances per guild
    instances = {}

    # Persistent queue file, OUTSIDE the reset deploy checkout (same stable
    # state dir as config.json, derived from ANNUBOT_CONFIG_PATH's directory).
    # Survives `git reset --hard` and bot restarts, so a crash/restart no
    # longer wipes the queue. Format: {"<guild_id>": [[query, is_video_id], ...]}
    _queue_path = os.environ.get(
        'ANNUBOT_QUEUE_PATH',
        os.path.join(
            os.path.dirname(os.path.abspath(os.environ.get('ANNUBOT_CONFIG_PATH', 'config.json'))),
            'queue.json'))

    def __init__(self, guild_id):
        self.guild_queue = queue.Queue(-1)
        self.guild_id = guild_id
        self.play_lock = asyncio.Lock()
        GuildQueue.instances[guild_id] = self
        self._load()

    # ---- persistence -------------------------------------------------------
    def _load(self):
        """Restore this guild's queue from the persistent file (if any)."""
        try:
            with open(self._queue_path, encoding='utf-8') as f:
                data = json.load(f)
        except FileNotFoundError:
            logger.debug("no saved queue at %s", self._queue_path)
            return
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("saved queue %s unreadable (%s); starting empty", self._queue_path, e)
            return  # corrupt -> start empty
        if not isinstance(data, dict):
            return
        for entry in data.get(str(self.guild_id), []):
            try:
                query, is_video_id = entry[0], bool(entry[1])
            except (IndexError, TypeError):
                continue
            self.guild_queue.put((query, is_video_id))
        if not self.guild_queue.empty():
            logger.info(f"Restored {self.guild_queue.qsize()} songs for guild {self.guild_id} from {self._queue_path}")

    def _save(self):
        """Atomically persist ALL guilds' queues (in-memory + this file)."""
        data = {}
        for gid, q in GuildQueue.instances.items():
            if not q.is_queue_empty():
                data[str(gid)] = [[item[0], bool(item[1])] for item in q.display_queue()]
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self._queue_path)), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self._queue_path)),
                                       prefix='.queue-', suffix='.tmp')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._queue_path)
        except OSError as e:
            logger.warning(f"queue save failed: {e}")
            return
        logger.debug("queue saved: %s", {gid: len(v) for gid, v in data.items()})


    # check if the guild id has an associated queue object
    @classmethod
    def exists(cls, guild_id):
        return guild_id in cls.instances

    # returns True if queue is empty
    def is_queue_empty(self) -> bool:
        return self.guild_queue.empty()

    # adds item to bottom of queue
    def put_in_queue(self, song):
        self.guild_queue.put(song)
        logger.debug("guild %s: queued %r (now %d)", self.guild_id, song, self.guild_queue.qsize())
        self._save()

    # pulls item from top of queue
    def get_latest_from_queue(self):
        if not self.is_queue_empty():
            item = self.guild_queue.get()
            logger.debug("guild %s: dequeued %r (%d left)", self.guild_id, item, self.guild_queue.qsize())
            self._save()
            return item
        else:
            return None

    # returns queue
    def display_queue(self):
        if not self.is_queue_empty():
            return list(self.guild_queue.queue)
        else:
            return None

    # randomize queue
    def shuffle(self):
        if not self.is_queue_empty():
            # randomly shuffle queue into a separate list
            shuffled_list = random.sample(list(self.guild_queue.queue), self.guild_queue.qsize())
            # reset current queue
            self.clearqueue()
            # put items from list into queue
            for item in shuffled_list:
                self.guild_queue.put(item)
            self._save()
            return True
        else:
            return None

    # resets queue
    def clearqueue(self):
        if not self.is_queue_empty():
            self.guild_queue = queue.Queue(-1)
            self._save()
            return True
        else:
            return None


@bot.event
async def on_ready():
    # Bot presence
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="annu help"))
    await bot.tree.sync()
    asyncio.get_running_loop().set_exception_handler(_log_loop_exception)
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")

@bot.event
async def on_disconnect():
    logger.warning("gateway disconnected")

@bot.event
async def on_resumed():
    logger.debug("gateway session resumed")

@bot.event
async def on_command(ctx):
    vc = ctx.voice_client
    logger.info("command %r from user %s in guild %s: args=%r kwargs=%r (vc connected=%s playing=%s paused=%s)",
                ctx.command.qualified_name if ctx.command else None,
                ctx.author.id if ctx.author else None,
                ctx.guild.id if ctx.guild else None,
                ctx.args[2:] if len(ctx.args) > 2 else [], ctx.kwargs,
                vc.is_connected() if vc else None,
                vc.is_playing() if vc else None,
                vc.is_paused() if vc else None)

@bot.event
async def on_command_completion(ctx):
    logger.debug("command %r completed", ctx.command.qualified_name if ctx.command else None)

@bot.event
async def on_command_error(ctx, error):
    logger.debug("command %r raised %s: %s", ctx.command.qualified_name if ctx.command else None,
                 type(error).__name__, error)
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"Try again in {error.retry_after:.1f}s.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing required argument. Use `annu help` for usage.")
    elif isinstance(error, NotInWorkingChannel):
        # working channel is set and this command came from somewhere else
        wc = CONFIG.working_channel(ctx.guild.id)
        ch = ctx.guild.get_channel(wc) if ctx.guild else None
        where = ch.mention if ch is not None else f"channel {wc}"
        await ctx.send(f"I only work in {where} right now.")
    elif isinstance(error, NotAdmin):
        await ctx.send("You need admin to do that.")
    elif isinstance(error, discord.errors.InteractionResponded):
        pass  # already responded
    else:
        logger.error(f"Command error in {ctx.command}: {error}", exc_info=error)
        # respond to the interaction so Discord doesn't show "did not respond"
        if getattr(ctx, 'interaction', None) is not None and not ctx.interaction.response.is_done():
            try:
                await ctx.send("Something went wrong. Try again.")
            except Exception:
                pass


# --- permission + working-channel gating -----------------------------------
# Two independent gates, both per-guild and opt-in:
#
#  * in_working_channel (global check, every command): when a guild has a
#    working_channel configured, the bot only responds to commands in that
#    channel and only sends its chatter there (option 3). No channel set ->
#    behaves exactly like before (any channel).
#  * is_admin (per-command check on admin commands): owner, or a member with
#    the Administrator permission, or a member holding one of the guild's
#    configured admin_roles.
#
# The config command itself is exempt from the working-channel gate (it's how
# you set the channel), but it IS admin-gated.

class NotInWorkingChannel(commands.CheckFailure):
    pass


class NotAdmin(commands.CheckFailure):
    pass


def is_admin(ctx: commands.Context) -> bool:
    guild = ctx.guild
    author = ctx.author
    if guild is None or author is None:
        raise NotAdmin
    if author.id == guild.owner_id:
        return True
    if author.guild_permissions.administrator:
        return True
    author_roles = {r.id for r in author.roles}
    if any(rid in author_roles for rid in CONFIG.admin_roles(guild.id)):
        return True
    raise NotAdmin


def in_working_channel(ctx: commands.Context) -> bool:
    # DMs and no per-guild channel configured -> anywhere is fine (legacy behaviour)
    if ctx.guild is None:
        return True
    channel_id = CONFIG.working_channel(ctx.guild.id)
    if channel_id is None:
        return True
    # the config command is exempt (it's how you set/change the channel) so an
    # admin is never locked out of fixing a bad config from another channel.
    if ctx.command is not None and ctx.command.qualified_name.split()[0] == 'config':
        return True
    if ctx.channel is not None and ctx.channel.id == channel_id:
        return True
    raise NotInWorkingChannel


bot.check(in_working_channel)


@bot.group(name='config', description="Admin: configure the bot for this server (prefix-only)")
@commands.check(is_admin)
async def config(ctx: commands.Context):
    """Shows the bot's configuration for this server."""
    wc = CONFIG.working_channel(ctx.guild.id)
    wc_name = None
    if wc is not None:
        ch = ctx.guild.get_channel(wc)
        wc_name = f"<#{wc}>" if ch is not None else f"(deleted channel {wc})"
    roles = CONFIG.admin_roles(ctx.guild.id)
    role_names = ", ".join(f"<@&{r}>" for r in roles) if roles else "(none — owner/admin only)"
    await ctx.send(
        f"**{ctx.guild.name}**\n"
        f"Working channel: {wc_name or '(any channel)'}\n"
        f"Admin roles: {role_names}"
    )


@config.command(name='setchannel', description="Set the channel the bot works in (option 3)")
@commands.check(is_admin)
async def config_setchannel(ctx: commands.Context):
    """Sets the working channel to the one you're in."""
    CONFIG.set_working_channel(ctx.guild.id, ctx.channel.id)
    await ctx.send(f"Working channel set to {ctx.channel.mention}. I'll only respond there now.")


@config.command(name='clearchannel', description="Clear the working channel (any channel again)")
@commands.check(is_admin)
async def config_clearchannel(ctx: commands.Context):
    CONFIG.clear_working_channel(ctx.guild.id)
    await ctx.send("Working channel cleared. I'll respond in any channel again.")


@config.command(name='setadminrole', description="Add a role to the admin list")
@commands.check(is_admin)
async def config_setadminrole(ctx: commands.Context, role: discord.Role):
    roles = CONFIG.admin_roles(ctx.guild.id)
    if role.id not in roles:
        roles.append(role.id)
    CONFIG.set_admin_roles(ctx.guild.id, roles)
    await ctx.send(f"Admin roles: {', '.join(f'<@&{r}>' for r in roles)}")


@config.command(name='clearadminrole', description="Remove a role from the admin list")
@commands.check(is_admin)
async def config_clearadminrole(ctx: commands.Context, role: discord.Role):
    roles = [r for r in CONFIG.admin_roles(ctx.guild.id) if r != role.id]
    CONFIG.set_admin_roles(ctx.guild.id, roles)
    await ctx.send(f"Admin roles: {', '.join(f'<@&{r}>' for r in roles) if roles else '(none — owner/admin only)'}")


# Auto-leave empty voice channels.
# If everyone leaves the bot's VC (or the bot is left alone), it stays for
# EMPTY_VC_LEAVE_DELAY seconds and then disconnects. A voice_state_update with
# a member (re)joining the bot's VC cancels the pending leave.
EMPTY_VC_LEAVE_DELAY = 30  # seconds
_leave_tasks = {}  # guild_id -> pending auto-leave Task
# guild_ids whose playback was paused BY AUTO-LEAVE (not by the user). On
# re-join we resume only these — a user's manual pause is left alone.
_autopaused = set()


def _empty_people(channel):
    # everyone in the channel except bots (the bot itself + any other bots)
    return [m for m in channel.members if not m.bot]


async def _do_leave(guild, channel):
    await asyncio.sleep(EMPTY_VC_LEAVE_DELAY)
    vc = discord.utils.get(bot.voice_clients, guild=guild)
    # only act if we're still in the same channel and it's still empty
    if vc is None or vc.channel is not channel or _empty_people(channel) != []:
        return
    # If music is active, do NOT disconnect. disconnect() -> stop() kills the
    # player, which fires on_finished() -> play_next_song(), eating the NEXT
    # song from the queue (two songs lost, stream broken). Instead: pause (if
    # playing) and stay in the VC; playback resumes when someone re-joins.
    if vc.is_playing():
        vc.pause()
        st = _states.get(guild.id)
        if st is not None:
            st.pos.pause()  # freeze position so resume recovers the right second
        _autopaused.add(guild.id)
        logger.info(f"Empty VC in {guild} but music playing; paused, staying in {channel}")
        return
    if vc.is_paused():
        # user manually paused; stay, don't touch the pause (no auto-resume)
        logger.info(f"Empty VC in {guild} but music paused; staying in {channel}")
        return
    # nothing playing: safe to leave
    logger.info(f"Empty VC in {guild} for {EMPTY_VC_LEAVE_DELAY}s, leaving")
    try:
        if guild.system_channel is not None:
            await guild.system_channel.send(f"Leaving — it's been empty for {EMPTY_VC_LEAVE_DELAY}s.")
    except Exception:
        pass  # no perms / channel gone; leave anyway
    await vc.disconnect()


@bot.event
async def on_voice_state_update(member, before, after):
    logger.debug("voice state update in guild %s: member %s%s channel %s -> %s",
                 getattr(member.guild, 'id', None), getattr(member, 'id', None),
                 " (bot itself)" if member == bot.user else "",
                 getattr(before.channel, 'id', None), getattr(after.channel, 'id', None))
    vc = discord.utils.get(bot.voice_clients, guild=member.guild)
    if vc is None or vc.channel is None:
        return
    bot_channel = vc.channel

    # the bot itself moved (join/leave) -> reset tracking for its new channel.
    # Checked FIRST: the bot's own join/leave must not be treated as a human
    # re-join (which would just cancel a leave) or a human departure.
    if member == bot.user:
        task = _leave_tasks.pop(member.guild.id, None)
        if task is not None and not task.done():
            task.cancel()
        if after.channel is not None and _empty_people(after.channel) == []:
            logger.info(f"Bot in empty VC, leaving in {EMPTY_VC_LEAVE_DELAY}s")
            _leave_tasks[member.guild.id] = asyncio.create_task(_do_leave(member.guild, after.channel))
        return

    # a non-bot member (re)joined the bot's channel -> cancel any pending leave
    if after.channel is bot_channel and before.channel is not bot_channel:
        task = _leave_tasks.pop(member.guild.id, None)
        if task is not None and not task.done():
            task.cancel()
            logger.info(f"Auto-leave cancelled: someone joined {bot_channel}")
        # If auto-leave paused the music (everyone left while it played),
        # resume it now that someone is back. Only auto-paused guilds — a
        # user's manual pause is left for them to resume.
        if member.guild.id in _autopaused:
            _autopaused.discard(member.guild.id)
            if bot_channel.is_paused():
                logger.info(f"Resuming auto-paused music: someone joined {bot_channel}")
                bot_channel.resume()
                st = _states.get(member.guild.id)
                if st is not None:
                    st.pos.resume()
        return

    # a non-bot member left the bot's channel -> maybe start the countdown
    if before.channel is bot_channel and after.channel is not bot_channel:
        if _empty_people(bot_channel) == []:
            existing = _leave_tasks.get(member.guild.id)
            if existing is None or existing.done():
                logger.info(f"VC emptied, leaving in {EMPTY_VC_LEAVE_DELAY}s")
                _leave_tasks[member.guild.id] = asyncio.create_task(_do_leave(member.guild, bot_channel))

@bot.hybrid_command(name='join', description="Joins your voice channel", aliases=['connect'], pass_context=True)
async def join(ctx: commands.Context, bot_voice=None, loading_msg=None, called=False):

    if loading_msg is None:
        loading_msg = await ctx.send("Loading...")

    # getting bot's voice channel object
    bot_voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    # if user not in VC
    if ctx.author.voice is None:
        await loading_msg.edit(content="You are not connected to a voice channel.")
        return False, "You are not connected to a voice channel."

    # if bot not in VC but author in VC
    elif bot_voice is None and ctx.author.voice:
        await loading_msg.edit(content=f"Joining {ctx.author.voice.channel}!")
        await ctx.author.voice.channel.connect()
        return True, "Success"

    # if author and bot in same VC but wasn't called by another function
    elif ctx.author.voice.channel == bot_voice.channel and not called:
        await loading_msg.edit(content="Already in your voice channel!")
        return True, "Success"

    elif ctx.author.voice.channel == bot_voice.channel and called:
        return True, "Success"

    # if bot and author in different VCs
    elif ctx.author.voice.channel != bot_voice.channel and ctx.author.voice:
        await loading_msg.edit(content="Bot already in another voice channel!")
        return False, "Bot already in another voice channel!"

@bot.hybrid_command(name='disconnect', description="Leaves your voice channel", aliases=['nikal', 'leave'])
async def dc(ctx: commands.Context):

    # getting bot's voice channel object
    bot_voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    # if bot not in any VC
    if bot_voice is None:
        await ctx.send("Bot not in any voice channel!")

    # if author not in any VC
    elif ctx.author.voice is None:
        await ctx.send("You cannot make the bot leave.")

    # if author and bot are in same VC
    elif ctx.author.voice.channel == bot_voice.channel:
        await ctx.send("Leaving!")
        await bot_voice.disconnect()

    # if author and bot are in different VCs
    else:
        await ctx.send("You cannot make the bot leave.")

@bot.hybrid_command(name='irshad', description="Delivers a true-blue Anu Malik shayari", aliases=['sher'], pass_context=True)
async def shayari(ctx: commands.Context):

    # random shayri
    await ctx.send(f'Annu says: {random.choice(sher)}')

# play song based on youtube or spotify links, or a general query
@bot.hybrid_command(name='play', description="Plays your song by name/YT/Spotify URL or resumes playing from queue if no query given", aliases=['baja'], pass_context=True)
async def play(ctx: commands.Context, *, query=None):

    loading_msg = await ctx.send("Loading...")
    bot_voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    connect_flag, message = await join(ctx, bot_voice=bot_voice, loading_msg=loading_msg, called=True)
    # connects bot to vc if not there
    if connect_flag:
        # if connection succeeds then searches if the guild already has an active queue
        if not GuildQueue.exists(ctx.guild.id):
            # if not then creates a queue and registers it. __init__ may RESTORE
            # a previously-saved queue (persistence), so "no query" can now mean
            # "resume the saved queue" rather than "you forgot the song".
            Queue_Object = GuildQueue(ctx.guild.id)
            if query is None or query.strip() == "":
                if Queue_Object.is_queue_empty():
                    return await ctx.send("No query given!")
                return await play_next_song(ctx)  # restored queue: resume it
        else:
            # if yes then initialise the variable to it
            Queue_Object = GuildQueue.instances[ctx.guild.id]
            # if there is a queue and play is given without any query then continue playing from queue
            if query is None or query.strip() == "":
                return await play_next_song(ctx)

        items, is_video_id = request(query)
        for item in items:
            Queue_Object.put_in_queue((item, is_video_id))
        await loading_msg.edit(content="Added to queue, now playing!")
        if not ctx.voice_client.is_playing() or not ctx.voice_client.is_paused():
            return await play_next_song(ctx)

    else:
        # if connection fails then prints reason
        return await loading_msg.edit(content=message)
    return

async def play_audio(ctx: commands.Context, query, is_video_id):
    # plays audio and sends the embed into chat.
    # Returns True once a song is actually playing, False if the song could
    # not be resolved/extracted (caller advances the queue). MUST NOT call
    # play_next_song itself: callers may already hold play_lock, and
    # asyncio.Lock is not re-entrant — the old recursive call deadlocked the
    # whole queue on the first "not found" song (freeze: nothing could
    # pause/resume/skip).
    logger.info("play_audio: guild %s, query=%r is_video_id=%s", ctx.guild.id, query, is_video_id)
    # ytpull does blocking HTTP (up to 2x10s); keep it off the event loop so
    # voice heartbeats and commands keep running meanwhile.
    url, time = await asyncio.to_thread(ytpull, query, is_video_id)
    logger.debug("play_audio: ytpull -> url=%s duration=%s", url, time)
    if url is None:
        await ctx.send(f"{ytvideolistnames([query])[0] if is_video_id else query} not found, skipping to next song")
        return False

    source = await audiostream(url, loop=bot.loop, stream=True)
    if source is None:
        await ctx.send(f"{ytvideolistnames([query])[0] if is_video_id else query} not found, skipping to next song")
        return False
    data = source[1]
    # empty/invisible titles: use "_" as a visible placeholder. Whitespace-only
    # labels (space/nbsp) are NOT rendered as links by Discord (it trims them and
    # shows raw markdown), and invisible chars are zero-width (nothing to click).
    # A single "_" renders literally (italic needs a _pair_) and stays clickable.
    title = clean_title(data.get('title'), fallback="_")
    ytid = data.get('id') or query

    # per-song state: position clock + recovery retry count + generation guard.
    # Bumping the generation here (and in skip/play) invalidates any in-flight
    # recovery or stale after-callback from the previous player, so a user
    # action can't double-advance the queue.
    state = _bump_generation(ctx.guild.id)
    gen = state.generation
    state.pos.seek_to(0.0)   # fresh song starts at 0
    state.retries = 0
    state.tail_resume_positions = []

    async def on_finished():
        # after(None) means the stream hit EOF. That is EITHER a genuine end,
        # the user skipping (skip calls vc.stop()), OR — the case this guards —
        # the CDN dropping the socket near the end, which ffmpeg reads as EOF
        # rather than an error (so on_stream_error never fires and the last
        # 10-15s silently vanish). If we're still significantly short of the
        # song's known duration, treat it as a break and resume from the break
        # point instead of advancing the queue. NOT generation-guarded: the
        # guard lives on the *starting* side (skip/play bump it before stopping
        # the old player, so the old player's callback no-ops, not this one).
        st = _states.get(ctx.guild.id)
        logger.debug("on_finished for %s (gen %d, current gen %s, pos %.1fs of %ss)",
                     ytid, gen, st.generation if st else None,
                     st.pos.seconds() if st else -1.0, data.get('duration'))
        if st is not None and st.generation == gen:
            dur = data.get('duration')
            pos = st.pos.seconds()
            if dur and pos < dur - TAIL_TOLERANCE:
                # Progress guard: if we've already resumed from (near) this
                # point, the CDN keeps cutting the same tail — stop looping and
                # advance rather than resuming forever.
                if any(abs(pos - p) < 3.0 for p in st.tail_resume_positions):
                    logger.error("tail recovery loop for %s at %.1fs; advancing", ytid, pos)
                    await play_next_song(ctx)
                    return
                st.tail_resume_positions.append(pos)
                logger.warning("song %s ended early at %.1fs of %.1fs (CDN dropped the tail); resuming",
                               ytid, pos, dur)
                await on_stream_error(
                    f"ended at {pos:.1f}s of {dur:.1f}s (CDN dropped the tail)",
                    count_retry=False)
                return
        # Genuine end (or superseded): advance the queue.
        await play_next_song(ctx)

    async def on_stream_error(error, *, count_retry=True):
        # The stream broke mid-song (or the CDN dropped the tail, which
        # on_finished routes here). Re-resolve a FRESH signed URL and resume
        # from the break point. A fresh URL is byte-seekable (verified: 206 to
        # Range requests), so -ss <pos> lands directly with no download-from-0
        # penalty and no temp file.
        st = _states.get(ctx.guild.id)
        logger.debug("on_stream_error for %s (gen %d, current gen %s, count_retry=%s): %r",
                     ytid, gen, st.generation if st else None, count_retry, error)
        if st is None or st.generation != gen:
            logger.debug("on_stream_error for %s: superseded, ignoring", ytid)
            return  # a user action (skip/play) superseded this song
        vc = ctx.voice_client
        if vc is None:
            logger.warning("on_stream_error for %s: no voice client, not resuming", ytid)
            return
        if not vc.is_connected():
            # Voice dropped, not the stream. Wait for voice auto-reconnect
            # (up to 30s) before resuming; abort if a user action landed.
            logger.warning("stream error for %s while voice not connected: %s", ytid, error)
            for _ in range(30):
                await asyncio.sleep(1)
                if _states.get(ctx.guild.id) is None or _states[ctx.guild.id].generation != gen:
                    return  # superseded
                if vc.is_connected():
                    break
            else:
                logger.error("voice did not reconnect within 30s for %s; skipping", ytid)
                try:
                    await ctx.send("Voice connection lost — skipping this song.")
                except Exception:
                    pass
                await play_next_song(ctx)
                return
        if count_retry:
            st.retries += 1
            if st.retries > MAX_STREAM_RETRIES:
                logger.error("stream failed %d times for %s; skipping song: %s",
                             st.retries, ytid, error)
                try:
                    await ctx.send("Stream kept dropping — skipping this song.")
                except Exception:
                    pass
                await play_next_song(ctx)
                return
        # Resume from the true song position. If we waited for voice to
        # reconnect, the listener heard dead air, so the wall-clock elapsed
        # since the error started is the correct resume point.
        pos = st.pos.seconds()
        start = pos if pos >= MIN_RESUME_POS else 0.0
        logger.warning("stream error for %s (%s); attempt %d/%d, resuming at %.1fs",
                       ytid, error, st.retries, MAX_STREAM_RETRIES, start)
        # No chat message here: the resume is transparent to the listener
        # (fresh URL + -ss seek lands at the break point). The log line above
        # is enough; a per-resume "recovering..." message was just noise.
        # The old ffmpeg process is already dead (that's what raised the error);
        # stop() clears the dead player so play() below can start a fresh one.
        try:
            vc.stop()
        except Exception:
            pass
        fresh = await audiostream(url, loop=bot.loop, stream=True, start=start)
        if fresh is None:
            logger.error("re-extract failed for %s; skipping song", ytid)
            await play_next_song(ctx)
            return
        # The extract above awaited; a skip/play may have replaced this song in
        # the meantime. If so, drop the recovery — the new song owns the player.
        if _states.get(ctx.guild.id) is None or _states[ctx.guild.id].generation != gen:
            logger.info("recovery superseded for %s; dropping resume", ytid)
            try:
                fresh[0].cleanup()
            except Exception:
                pass
            return
        st.pos.seek_to(start)
        try:
            vc.play(fresh[0], after=_make_after(ctx, gen, on_finished, on_stream_error))
        except discord.ClientException as e:
            # something else grabbed the player (e.g. a concurrent play) — don't
            # fight it; the other path owns playback from here.
            logger.warning("could not restart stream for %s: %s", ytid, e)
            try:
                fresh[0].cleanup()
            except Exception:
                pass

    def _make_after(ctx, gen, on_finished, on_stream_error):
        def after(error):
            # Called from the audio-player thread; hop onto the event loop.
            st = _states.get(ctx.guild.id)
            logger.debug("after-callback for %s (gen %d, pos %.1fs): error=%r",
                         ytid, gen, st.pos.seconds() if st else -1.0, error)
            coro = on_finished() if error is None else on_stream_error(error)
            try:
                fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
            except Exception:
                logger.exception("failed to schedule after-callback")
                coro.close()
                return
            # Nobody awaits this future, so an exception raised while advancing
            # the queue used to vanish without a trace and playback just
            # stopped (Sept 24 21:51). Always log it.
            fut.add_done_callback(_log_after_result)
        return after

    def _log_after_result(fut):
        if fut.cancelled():
            logger.warning("after-callback for %s was cancelled", ytid)
            return
        exc = fut.exception()
        if exc is not None:
            logger.error("after-callback for %s raised; queue may have stalled", ytid,
                         exc_info=(type(exc), exc, exc.__traceback__))

    logger.info("starting playback of %s %r [%s] (gen %d)", ytid, title, time, gen)
    ctx.voice_client.play(source[0], after=_make_after(ctx, gen, on_finished, on_stream_error))
    # The song is playing from here on: a failure to post the embed must not
    # propagate, or play_next_song would treat the song as failed and try to
    # start another one on top of it.
    try:
        playerembed.set_image(url=data.get('thumbnail'))
        playerembed.description = f"[{title}]({ytbase}{ytid}) [{time}]"
        await ctx.send(content=None, embed=playerembed)
    except Exception:
        logger.exception("failed to send now-playing embed for %s (playback continues)", ytid)
    return True

async def play_next_song(ctx: commands.Context):
    # plays next song if available in that guild's queue
    Queue_Object = GuildQueue.instances[ctx.guild.id]
    logger.debug("play_next_song: guild %s, %d queued, lock %s",
                 ctx.guild.id, Queue_Object.guild_queue.qsize(),
                 "held (waiting)" if Queue_Object.play_lock.locked() else "free")
    lock_t0 = asyncio.get_running_loop().time()

    async with Queue_Object.play_lock:
        logger.debug("play_next_song: acquired play_lock after %.1fs",
                     asyncio.get_running_loop().time() - lock_t0)
        # wait for current song to finish, with a real safety timeout and a
        # disconnect check. The timeout was previously a comment with no code:
        # if the player desyncs (reports playing/paused forever) or a stale
        # after-callback stalls, this loop held play_lock indefinitely and the
        # whole queue froze. Now it gives up after PLAY_WAIT_TIMEOUT seconds.
        waited = 0.0
        while True:
            try:
                if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
                    break
                await asyncio.sleep(1)
            except (discord.ClientException, AttributeError):
                # voice client disconnected or became invalid
                break
            waited += 1.0
            if waited >= PLAY_WAIT_TIMEOUT:
                logger.error("play_next_song: player still active after %.0fs; forcing advance", waited)
                break
        if waited:
            logger.debug("play_next_song: waited %.0fs for the previous player to stop", waited)

        # Bounded loop: keep advancing while songs fail to resolve (the old
        # recursive play_audio -> play_next_song call deadlocked here on a
        # "not found" song because play_lock is not re-entrant). A song that
        # actually starts playing returns True and ends the loop; an empty
        # queue ends it too.
        while True:
            vc = ctx.voice_client
            if vc is None or not vc.is_connected():
                # Don't pop (and lose) songs we have nowhere to play.
                logger.warning("play_next_song: not connected to voice in guild %s; %d songs stay queued",
                               ctx.guild.id, Queue_Object.guild_queue.qsize())
                return
            if Queue_Object.is_queue_empty():
                # if end of queue is reached
                logger.info("play_next_song: end of queue in guild %s", ctx.guild.id)
                await ctx.send("End of queue reached!")
                return
            # gets latest song from queue and plays
            query, is_video_id = Queue_Object.get_latest_from_queue()
            try:
                started = await play_audio(ctx, query, is_video_id)
            except Exception as e:
                # Any unexpected failure on one song (bad API response, deleted
                # video, Discord hiccup) skips that song instead of silently
                # ending playback for the whole queue.
                logger.exception("play_next_song: failed to play %r; skipping to next song", query)
                try:
                    await ctx.send(f"Couldn't play `{query}` ({type(e).__name__}), skipping to next song")
                except Exception:
                    logger.exception("play_next_song: couldn't send the skip notice")
                vc = ctx.voice_client
                if vc is not None and (vc.is_playing() or vc.is_paused()):
                    # something is playing despite the error; don't stack another song on it
                    logger.warning("play_next_song: player active after failure; not advancing further")
                    return
                started = False
            if started:
                return
            logger.info("play_next_song: %r did not start; trying the next song (%d left)",
                        query, Queue_Object.guild_queue.qsize())

# pauses music
@bot.hybrid_command(name='pause', description="Pauses playback", aliases=['ruk'], pass_context=True)
async def pause(ctx: commands.Context):
    if ctx.voice_client:
        if ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            # freeze the position clock so a later resume recovers from the
            # paused second, not from wall time.
            st = _states.get(ctx.guild.id)
            if st is not None:
                st.pos.pause()
            await ctx.send("Paused!")
        else:
            await ctx.send("Music already paused. Do you mean to resume?")
    else:
        await ctx.send("Nothing is playing.")

# resumes music
@bot.hybrid_command(name='resume', description="Resumes playback", aliases=['chal'], pass_context=True)
async def resume(ctx: commands.Context):
    if ctx.voice_client:
        if ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            st = _states.get(ctx.guild.id)
            if st is not None:
                st.pos.resume()
            await ctx.send("Resumed!")
        else:
            await ctx.send("Music already playing. Do you mean to pause?")
    else:
        await ctx.send("Nothing is playing. If you want to restart existing queue type just annu play")

# skips current song
@bot.hybrid_command(name='skip', description="Skips to next song", aliases=['next', 'agla'], pass_context=True)
async def skip(ctx: commands.Context, *, query=""):

    bot_voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    # guard: bot not in a VC, or user not in the bot's VC
    if bot_voice is None or ctx.author.voice is None or ctx.author.voice.channel != bot_voice.channel:
        return await ctx.send("Join the bot's VC")

    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        # Bump generation FIRST so a stale after-callback from the player we're
        # about to stop can't double-advance the queue, then stop it.
        _bump_generation(ctx.guild.id)
        # stops current song - the after callback will trigger play_next_song
        ctx.voice_client.stop()
    else:
        # Player is None. Either nothing is playing, OR a stream recovery is in
        # flight (player was stopped while a fresh URL is being resolved). In
        # the latter case the in-flight recovery would otherwise resume the old
        # song after its extract completes — bump the generation so it drops
        # out, then advance directly.
        st = _states.get(ctx.guild.id)
        if st is not None and st.generation > 0:
            _bump_generation(ctx.guild.id)
            await ctx.send("Skipped!")
            return await play_next_song(ctx)
        return await ctx.send("No song playing.")

    # if query is a number then try skipping to that song
    if query.isdigit():
        query = int(query)

        if not GuildQueue.exists(ctx.guild.id):
            # if no more songs left in queue
            return await ctx.send("Reached end of queue.")

        Queue_Object = GuildQueue.instances[ctx.guild.id]
        # if given index is larger then length of queue then its invalid
        if query > len(Queue_Object.display_queue()):
            return await ctx.send("Invalid queue index.")

        # remove all songs before that index
        for _ in range(query - 1):
            temp = Queue_Object.get_latest_from_queue()

        # next song will be required song - after callback handles this
        return await ctx.send(f"Skipping to song {query}")

    # else play the next song - after callback handles this
    return await ctx.send("Skipped!")

# displays queue
@bot.hybrid_command(name='queue', description="Displays song queue", pass_context=True)
async def display_queue(ctx: commands.Context):
    # checks if the guild already has an active queue
    if not GuildQueue.exists(ctx.guild.id):
        return await ctx.send("No songs in queue.")
    else:
        # if yes then initialise the variable to it
        Queue_Object = GuildQueue.instances[ctx.guild.id]
        queuelist = Queue_Object.display_queue()
        if queuelist is None:
            return await ctx.send("No songs in queue.")

    # gets the values which are YT links
    true_tuples = [t for t in queuelist if t[1]]

    # Extract video_ids from corresponding values
    values_to_process = [t[0] for t in true_tuples]

    # gets names of the videos with given video ids
    processed_values = ytvideolistnames(values_to_process)

    queuearray = []
    queueelem = ""
    # NOTE: processed_values can be SHORTER than the number of YT entries if the
    # YouTube API fails to resolve some IDs (deleted/region-locked videos are
    # omitted from the response). pop(0) in lockstep then runs off the end and
    # raised IndexError (seen 3x in the Aug 31 logs). Use an iterator and fall
    # back to the raw value when names run out.
    name_iter = iter(processed_values)
    # Iterate over the original queue
    for num, item in enumerate(queuelist):
        temp_name = ""
        # if the value is a YT link, get the value from the names list
        if item[1]:
            try:
                temp_name = next(name_iter)
            except StopIteration:
                temp_name = item[0]  # name lookup fell short; use the raw link
        # else just append the value as it is
        else:
            temp_name = item[0]

        # discord has a message character limit of 2000 character, so we separate them by length
        if len(queueelem) + len(f"{num+1}) {temp_name}\n") <= 2000:
            queueelem += f"{num+1}) {temp_name}\n"
        else:
            queuearray.append(queueelem)
            queueelem = ""
    if queueelem != "":
        queuearray.append(queueelem)

    for i in queuearray:
        await ctx.send(i)

    return

@bot.hybrid_command(name='fangs', description="Plays Sheishen by Keylo X FANGS", hidden=True)
async def fangs(ctx: commands.Context):

    # flag to check if bot is connected to a VC
    connect_flag = False
    if ctx.voice_client is None:  # if bot not in vc
        if ctx.author.voice:  # if author in vc then join authors
            await ctx.author.voice.channel.connect()
            connect_flag = True
        else:
            await ctx.send("Join a VC first!")
    elif ctx.author.voice.channel == ctx.voice_client.channel:  # if bot in same vc as author
        connect_flag = True
    else:
        await ctx.send("Join the bot's VC!")

    if connect_flag:
        seishin = "https://youtu.be/gBmxCcHtY2Y"
        time = "3:25"
        source = await audiostream(seishin, loop=bot.loop, stream=True)
        data = source[1]
        title = data['title']
        ytid = data['id']
        ctx.voice_client.play(source[0], after=lambda e: print('Player error: %s' % e) if e else None)
        playerembed.set_image(url=data['thumbnail'])
        playerembed.description = f"[{title}]({ytbase}{ytid}) [{time}]"
        await ctx.send(embed=playerembed)


@bot.hybrid_command(name='fuckoff', description="Try it ;)", pass_context=True)
async def fuckoff(ctx: commands.Context):

    # dont tell anu malik to fuckoff
    fuckoffs = ['Tu hota kaun hai',
                'Anu Malik fuck off nahi hota',
                'Tere baap ka naukar hu kya',
                'Tu fuckoff',
                "Teri himmat kaise hui?",
                "Bhag yahaan se, chirkut.",
                "Jaa na, bakwaas mat kar.",
                "Aise kaise?",
                "Aukat mein reh.",
                "Kya ukhaad lega tu?",
                "Bhool ja, tere level ka nahi hai.",
                "Chal nikal, time waste mat kar."]
    await ctx.send(random.choice(fuckoffs))

@bot.hybrid_command(name="shuffle", description="Shuffle the playlist", pass_context=True)
async def shuffle(ctx: commands.Context):
    # check if queue exists
    if not GuildQueue.exists(ctx.guild.id):
        return await ctx.send("No songs in queue.")
    else:
        # if yes then initialise the variable to it
        Queue_Object = GuildQueue.instances[ctx.guild.id]

    shuffle_status = Queue_Object.shuffle()
    if shuffle_status is None:
        return await ctx.send("Queue empty!")

    return await ctx.send("Queue shuffled!")

@bot.hybrid_command(name="clear", description="Clears the playlist", pass_context=True)
async def clearqueue(ctx: commands.Context):
    # check if queue exists
    if not GuildQueue.exists(ctx.guild.id):
        return await ctx.send("No songs in queue.")
    else:
        # if yes then initialise the variable to it
        Queue_Object = GuildQueue.instances[ctx.guild.id]

    clear_status = Queue_Object.clearqueue()
    if clear_status is None:
        return await ctx.send("Queue already empty!")

    return await ctx.send("Queue Cleared!")


@bot.hybrid_command(name="help", description="Shows help message", pass_context=True)
async def help(ctx: commands.Context):
    helpembed = discord.Embed()
    helpembed.set_thumbnail(url=bot.user.avatar)
    helpembed.title = "Annu Commands"
    helpembed.color = discord.Colour(0x7289DA)
    helpembed.description = (
    "`play [baja]:` Plays song/playlist from YouTube\n"
    "`irshad [sher]:` Get an authentic Annu Malik shayari!\n"
    "`queue:` Shows the current queue\n"
    "`skip [next, agla] <number>:` Goes to next song or to the index specified\n"
    "`join [connect]:` Connects to your voice channel\n"
    "`pause [ruk]:` Pauses playback\n"
    "`resume [chal]:` Resumes playback\n"
    "`shuffle`: Shuffles queue\n"
    "`clear`: Clears queue\n"
    "`disconnect [nikal, leave]:` Disconnect from voice channel\n"
    "`config [setchannel, setadminrole ...]:` Admin: configure working channel + admin roles\n"
    "`fuckoff:` Don't do this.\n"
    "`help:` Shows this message"
)
    await ctx.send(embed=helpembed)

if __name__ == '__main__':
    # log_handler=None: logging is configured above. discord.py's own handler
    # would duplicate every discord.* line in a second format.
    bot.run(DISCORD_TOKEN, log_handler=None)
