import yt_dlp
from utils import *
from dotenv import load_dotenv
import os
import random
import discord
import asyncio
from discord.ext import commands
import queue

#setup
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
ytbase = "https://www.youtube.com/watch?v="

filepath = 'sher.txt'
with open(filepath,encoding='utf8') as fp:
   line = fp.readline()
   cnt = 1
   sher = []
   while line:
       sher.append(line.strip())
       line = fp.readline()
       cnt += 1

yt_dlp_opts = {
    'format': 'ba',
    'extract-audio': True,
    'audio-format': 'mp3',
    'audio-quality': 0,
    'buffer-size': 1024*32,
    'http-chunk-size': 1024*32
}

ffmpeg_opts = {
    'before_options': '-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
    }

# yt_dlp options
ytdl = yt_dlp.YoutubeDL(yt_dlp_opts)

# audio driver
async def audiostream(url,*,loop=None, stream=True):
    loop = loop or asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
    except:
        return None
    if 'entries' in data:
        data = data['entries'][0]
    filename = data['url'] if stream else ytdl.prepare_filename(data)
    return (discord.FFmpegPCMAudio(filename, **ffmpeg_opts),data)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='annu ', intents=intents, help_command=None)

playerembed = discord.Embed(
    title = "Now Playing",
    color = discord.Colour(0x7289DA)
)

class GuildQueue:

    # keeps track of object instances per guild
    instances = {}

    def __init__(self, guild_id):
        self.guild_queue = queue.Queue(-1)
        self.guild_id = guild_id
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

@bot.hybrid_command(name = 'join', description = "Joins your voice channel", aliases=['connect'], pass_context=True)
async def join(ctx:commands.Context, bot_voice=None, loading_msg=None, called=False):

    if loading_msg is None:
        loading_msg = await ctx.send("Loading...")

    # getting bot's voice channel object
    bot_voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    # if user not in VC
    if ctx.author.voice is None:
        await loading_msg.edit(content = "You are not connected to a voice channel.")
        return False, "You are not connected to a voice channel."
    
    # if bot not in VC but author in VC
    elif bot_voice is None and ctx.author.voice:
        await loading_msg.edit(content=f"Joining {ctx.author.voice.channel}!")
        await ctx.author.voice.channel.connect()
        return True, "Success"

    # if author and bot in same VC but wasnt called by another function
    elif ctx.author.voice.channel == bot_voice.channel and not called:
        await loading_msg.edit(content = "Already in your voice channel!")
        return True, "Success"
    
    elif ctx.author.voice.channel == bot_voice.channel and called:
        return True, "Success"

    # if bot and author in different VCs
    elif ctx.author.voice.channel != bot_voice.channel and ctx.author.voice:
        await loading_msg.edit(content = "Bot already in another voice channel!")
        return False, "Bot already in another voice channel!"

@bot.hybrid_command(name='disconnect', description = "Leaves your voice channel", aliases=['nikal','leave'])
async def dc(ctx:commands.Context):

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

@bot.hybrid_command(name = 'irshad',description = "Delivers a true-blue Anu Malik shayari", aliases=['sher'], pass_context=True)
async def shayari(ctx:commands.Context):

    # random shayri
    await ctx.send('Annu says: {}'.format(random.choice(sher)))
    
# play song based on youtube or spotify links, or a general query
@bot.hybrid_command(name='play', description = "Plays your song by name/YT/Spotify URL or resumes playing from queue if no query given", aliases=['baja'], pass_context=True)
async def play(ctx:commands.Context, *, query=None):

    loading_msg = await ctx.send("Loading...") # need to send a placeholder text else slash interaction times out
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

async def play_audio(ctx:commands.Context, query, is_video_id):
    # plays audio and sends the embed into chat
    url,time = ytpull(query, is_video_id)
    if url==None:
        await ctx.send(f"{ytvideolistnames([query])[0] if is_video_id else query} not found, skipping to next song")
        return await play_next_song(ctx)

    source = await audiostream(url, loop=bot.loop, stream=True)
    if source is None:
        await ctx.send(f"{ytvideolistnames([query])[0] if is_video_id else query} not found, skipping to next song")
        return await play_next_song(ctx)
    data = source[1]
    title = data['title']
    ytid = data['id']

    def after_play(e):
        if e:
            print("'Player error: %s' % e")

    ctx.voice_client.play(source[0], after=after_play)
    playerembed.set_image(url=data['thumbnail'])
    playerembed.description="[{}]({}) [{}]".format(title,ytbase+ytid,time)
    await ctx.send(content=None, embed=playerembed)
    return await play_next_song(ctx)

async def play_next_song(ctx:commands.Context):
    # plays next song if available in that guild's queue
    Queue_Object = GuildQueue.instances[ctx.guild.id]
    while ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        # if a song is playing, sleeps asynchronously till it is finished
        await asyncio.sleep(1)
    if not Queue_Object.is_queue_empty():
        # after sleep finishes, gets latest song from queue and plays
        query, is_video_id = Queue_Object.get_latest_from_queue()
        return await play_audio(ctx, query, is_video_id)
    else:
        # if end of queue is reached
        await ctx.send("End of queue reached!")

# pauses music
@bot.hybrid_command(name='pause', description = "Pauses playback", aliases=['ruk'], pass_context=True)
async def pause(ctx:commands.Context):
    if ctx.voice_client:
        if ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("Paused!")
        else:
            await ctx.send("Music already paused. Do you mean to resume?")
    else:
        await ctx.send("Nothing is playing.")

# resumes music
@bot.hybrid_command(name='resume', description = "Resumes playback",aliases=['chal'], pass_context=True)
async def resume(ctx:commands.Context):
    if ctx.voice_client:
        if ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("Resumed!")
        else:
            await ctx.send("Music already playing. Do you mean to pause?")
    else:
        await ctx.send("Nothing is playing. If you want to restart existing queue type just annu play")

# NOTE: skip skips one song extra for some reason, most probably a time race between play_audio awaiting play_next_song at the end and the 
# check if the song is playing in play_next_song

# skips current song
@bot.hybrid_command(name='skip', description = "Skips to next song", aliases=['next', 'agla'], pass_context=True)
async def skip(ctx:commands.Context, *, query=""):

    bot_voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if ctx.author.voice is None or ctx.author.voice.channel != bot_voice.channel:
        return await ctx.send("Join the bot's VC")

    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        # skips if there is a song active on the bot
        ctx.voice_client.stop()
    else:
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
        for _ in range(query-1):
            temp = Queue_Object.get_latest_from_queue()
        
        # next song will be required song
        return await play_next_song(ctx)
    
    # else play the next song
    else:
        print("normal skip")
        return await play_next_song(ctx)

# displays queue
@bot.hybrid_command(name='queue', description = "Displays song queue", pass_context=True)
async def display_queue(ctx:commands.Context):
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
    # Iterate through the original queue
    for num, item in enumerate(queuelist):
        temp_name = ""
        # if the value is a YT link, get the value from the names list
        if item[1]:
            processed_value = processed_values.pop(0)
            temp_name = processed_value
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
        await(ctx.send(i))

    return

@bot.hybrid_command(name = 'fangs', description = "Plays Sheishen by Keylo X FANGS", hidden=True)
async def fangs(ctx:commands.Context):
    
    # flag to check if bot is connected to a VC
    connect_flag = False
    if ctx.voice_client is None: # if bot not in vc
        if ctx.author.voice: # if author in vc then join authors
            await ctx.author.voice.channel.connect()
            connect_flag = True
        else:
            await ctx.send("Join a VC first!")
    elif ctx.author.voice.channel() == ctx.voice_client.channel(): # if bot in same vc as author
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
        playerembed.description="[{}]({}) [{}]".format(title,ytbase+ytid,time)
        await ctx.send(embed=playerembed)


@bot.hybrid_command(name = 'fuckoff',description = "Try it ;)", pass_context=True)
async def fuckoff(ctx:commands.Context):

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

@bot.hybrid_command(name = "shuffle", description = "Shuffle the playlist", pass_context = True)
async def shuffle(ctx:commands.Context):
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
    
@bot.hybrid_command(name = "clear", description = "Clears the playlist", pass_context = True)
async def clearqueue(ctx:commands.Context):
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


@bot.hybrid_command(name = "help", description = "Shows help message", pass_context = True)
async def help(ctx:commands.Context):
    helpembed = discord.Embed()
    helpembed.set_thumbnail(url=bot.user.avatar)
    helpembed.title = "Annu Commands"
    helpembed.color = discord.Colour(0x7289DA)
    helpembed.description = (
    "`play [baja]:`Plays song/playlist/album from YT and Spotify\n"
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

bot.run(DISCORD_TOKEN)