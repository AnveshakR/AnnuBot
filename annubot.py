import yt_dlp
from utils import *
from dotenv import load_dotenv
import os
import random
import discord
import asyncio
from discord.ext import commands
import queue
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Also write to /logs/annubot/logs-<unix timestamp>.log (Docker mount).
# Falls back to stderr-only if the dir can't be created (local, non-root).
try:
    os.makedirs('/logs/annubot', exist_ok=True)
    _log_path = f'/logs/annubot/logs-{int(time.time())}.log'
    _fh = logging.FileHandler(_log_path)
    _fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
    logging.getLogger().addHandler(_fh)
    logger.info(f"Logging to {_log_path}")
except Exception as e:
    logger.warning(f"File log unavailable ({e}); using stderr only")

# Fix: load libopus directly since the symlink may be missing
import discord.opus as opus
if not opus.is_loaded():
    try:
        opus.load_opus('/usr/lib/x86_64-linux-gnu/libopus.so.0')
        logger.info("Loaded libopus from /usr/lib/x86_64-linux-gnu/libopus.so.0")
    except Exception as e:
        logger.warning(f"Failed to load libopus: {e} — voice may not work")

#setup
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
ytbase = "https://www.youtube.com/watch?v="

filepath = 'sher.txt'
with open(filepath, encoding='utf8') as fp:
    sher = [line.strip() for line in fp if line.strip()]

yt_dlp_opts = {
    'quiet': True,
    'no_warnings': True,
    'socket_timeout': 30,
}

ffmpeg_opts = {
    # -rw_timeout 5000000 (5s, microseconds): make ffmpeg FAIL FAST on a dead
    # stream instead of hanging forever. A real break (CDN drops the signed URL)
    # is permanent, so there is nothing to "wait out" — fast failure is what
    # triggers the after-callback resume. Sub-second network blips are absorbed
    # by -max_delay before this ever fires.
    #
    # NOTE: the old -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5
    # flags are for LIVE streams. On a signed YouTube URL they make ffmpeg issue
    # Range requests the CDN rejects with HTTP 416 ("Requested range not
    # satisfiable") — that produced 54 "Will reconnect ... error=Input/output
    # error" lines in the Aug 31 logs. They can never work here. Removed.
    #
    # -max_delay 500000 = 500ms jitter buffer: absorbs network stalls so the
    # player doesn't underrun (lag) then rush to catch up (audio speeds up).
    'before_options': '-nostdin -rw_timeout 5000000 -max_delay 500000',
    'options': '-vn',
}

# audio driver - stream directly from the source URL (no temp file).
# yt-dlp resolves a fresh signed URL per song. `start` (seconds) does an input
# seek (-ss before -i) so a resume can jump straight to the break point.
# Verified on dellarch (production yt-dlp + ffmpeg): a FRESH signed URL returns
# 206 Partial Content to a Range request, so the seek lands directly with no
# download-from-0 penalty (~0.13s to first audio byte).
async def audiostream(url, *, loop=None, stream=True, start=0.0):
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
    opts = dict(ffmpeg_opts)
    if start and start > 0:
        # input seek: -ss must come BEFORE -i (see FFmpegPCMAudio arg order).
        opts['before_options'] = f"{ffmpeg_opts['before_options']} -ss {start:.3f}"
    return (discord.FFmpegPCMAudio(stream_url, **opts), data)


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

    def __init__(self, guild_id):
        self.guild_queue = queue.Queue(-1)
        self.guild_id = guild_id
        self.play_lock = asyncio.Lock()
        GuildQueue.instances[guild_id] = self

    # check if the guild id has an associated queue object
    @classmethod
    def exists(cls, guild_id):
        return guild_id in cls.instances

    # returns True if queue is empty
    def is_queue_empty(self) -> bool:
        return self.guild_queue.empty()

    # adds item to bottom of queue
    def put_in_queue(self, song):
        return self.guild_queue.put(song)

    # pulls item from top of queue
    def get_latest_from_queue(self):
        if not self.is_queue_empty():
            return self.guild_queue.get()
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
            return True
        else:
            return None

    # resets queue
    def clearqueue(self):
        if not self.is_queue_empty():
            self.guild_queue = queue.Queue(-1)
            return True
        else:
            return None


@bot.event
async def on_ready():
    # Bot presence
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="annu help"))
    await bot.tree.sync()
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"Try again in {error.retry_after:.1f}s.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing required argument. Use `annu help` for usage.")
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
        await ctx.send(f"Leaving {bot_voice.channel}!")
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
            # if not then creates a queue and registers it
            Queue_Object = GuildQueue(ctx.guild.id)
            # if there is nothing in queue and play command is given without query then error out
            if query is None or query.strip() == "":
                return await ctx.send("No query given!")
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
    url, time = ytpull(query, is_video_id)
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
    ytid = data['id']

    # per-song state: position clock + recovery retry count + generation guard.
    # Bumping the generation here (and in skip/play) invalidates any in-flight
    # recovery or stale after-callback from the previous player, so a user
    # action can't double-advance the queue.
    state = _bump_generation(ctx.guild.id)
    gen = state.generation
    state.pos.seek_to(0.0)   # fresh song starts at 0
    state.retries = 0

    async def on_finished():
        # Stream ended normally OR the user skipped (skip calls vc.stop(), which
        # fires after(None)). Either way: advance the queue. NOT guarded by
        # generation — the generation guard lives on the *starting* side (skip/
        # play bump it before stopping the old player, so the old player's
        # callback is the one that no-ops, not this one).
        await play_next_song(ctx)

    async def on_stream_error(error):
        # The stream broke mid-song. Re-resolve a FRESH signed URL and resume
        # from the break point. A fresh URL is byte-seekable (verified: 206 to
        # Range requests), so -ss <pos> lands directly with no download-from-0
        # penalty and no temp file.
        st = _states.get(ctx.guild.id)
        if st is None or st.generation != gen:
            return  # a user action (skip/play) superseded this song
        vc = ctx.voice_client
        if vc is None:
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
        try:
            await ctx.send(f"Stream hiccup — recovering (~{start:.0f}s in)…")
        except Exception:
            pass
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
            coro = on_finished() if error is None else on_stream_error(error)
            try:
                asyncio.run_coroutine_threadsafe(coro, bot.loop)
            except Exception:
                logger.exception("failed to schedule after-callback")
        return after

    ctx.voice_client.play(source[0], after=_make_after(ctx, gen, on_finished, on_stream_error))
    playerembed.set_image(url=data['thumbnail'])
    playerembed.description = f"[{title}]({ytbase}{ytid}) [{time}]"
    await ctx.send(content=None, embed=playerembed)
    return True

async def play_next_song(ctx: commands.Context):
    # plays next song if available in that guild's queue
    Queue_Object = GuildQueue.instances[ctx.guild.id]

    async with Queue_Object.play_lock:
        # wait for current song to finish with timeout and disconnect check
        while True:
            try:
                if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
                    break
                await asyncio.sleep(1)
            except (discord.ClientException, AttributeError):
                # voice client disconnected or became invalid
                break
            # safety timeout - if we've been waiting too long, break out
            # (song is likely stuck or errored silently)

        # Bounded loop: keep advancing while songs fail to resolve (the old
        # recursive play_audio -> play_next_song call deadlocked here on a
        # "not found" song because play_lock is not re-entrant). A song that
        # actually starts playing returns True and ends the loop; an empty
        # queue ends it too.
        while True:
            if Queue_Object.is_queue_empty():
                # if end of queue is reached
                await ctx.send("End of queue reached!")
                return
            # gets latest song from queue and plays
            query, is_video_id = Queue_Object.get_latest_from_queue()
            if await play_audio(ctx, query, is_video_id):
                return

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
    "`fuckoff:` Don't do this.\n"
    "`help:` Shows this message"
)
    await ctx.send(embed=helpembed)

if __name__ == '__main__':
    bot.run(DISCORD_TOKEN)
