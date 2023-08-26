import string
from tokenize import String
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
    data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
    if 'entries' in data:
        data = data['entries'][0]
    filename = data['url'] if stream else ytdl.prepare_filename(data)
    return (discord.FFmpegPCMAudio(filename, **ffmpeg_opts),data)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='annu ', intents=intents)

playerembed = discord.Embed(
    title = "Now Playing",
    color = discord.Colour(0x7289DA)
)

class GuildQueue:

    instances = {}

    def __init__(self, guild_id):
        self.guild_queue = queue.Queue(-1)
        self.guild_id = guild_id
        GuildQueue.instances[guild_id] = self

    @classmethod
    def exists(cls, guild_id):
        return guild_id in cls.instances

    def is_queue_empty(self):
        return self.guild_queue.empty()

    def put_in_queue(self, song):
        return self.guild_queue.put(song)

    def get_latest_from_queue(self):
        if not self.is_queue_empty():
            return self.guild_queue.get()
        else:
            return None
    
    def display_queue(self):
        if not self.is_queue_empty():
            queue_text = ""
            for num, elem in enumerate(list(self.guild_queue.queue)):
                queue_text += str(num) + ") " + str(elem) + "\n"
            return queue_text
        else:
            return None
        
@bot.event
async def on_ready():
    # Bot presence
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="annu help"))
    await bot.tree.sync()

@bot.hybrid_command(name = 'join', description = "Joins your voice channel", aliases=['connect'], pass_context=True)
async def join(ctx, bot_voice=None, loading_msg=None, called=False):

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
async def dc(ctx):

    # getting bot's voice channel object
    bot_voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    # if bot not in any VC
    if bot_voice is None:
        await ctx.send("Bot not in any voice channel!")
    
    # if author and bot are in same VC
    elif ctx.author.voice.channel == bot_voice.channel:
        await ctx.send(f"Leaving {bot_voice.channel}!")
        await bot_voice.disconnect()
    
    # if author and bot are in different VCs
    else:
        await ctx.send("You cannot make the bot leave.")

@bot.hybrid_command(name = 'irshad',description = "Delivers a true-blue Anu Malik shayari", aliases=['sher'], pass_context=True)
async def shayari(ctx):

    # random shayri
    await ctx.send('Annu says: {}'.format(random.choice(sher)))
    
# play song based on youtube or spotify links, or a general query
@bot.hybrid_command(name='play', description = "Plays your song by name/YT/Spotify URL", pass_context=True)
async def play(ctx, *, query=None):

    # errors if no query given
    if query is None or query.strip() == "":
        return await ctx.send("No query given!")

    loading_msg = await ctx.send("Loading...") # need to send a placeholder text else slash interaction times out
    bot_voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    connect_flag, message = await join(ctx, bot_voice=bot_voice, loading_msg=loading_msg, called=True)
    # connects bot to vc if not there

    if connect_flag:
        # if connection succeeds then searches if the guild already has an active queue
        if not GuildQueue.exists(ctx.guild.id):
            # if not then creates a queue and registers it
            Queue_Object = GuildQueue(ctx.guild.id)
        else:
            # if yes then initialise the variable to it
            Queue_Object = GuildQueue.instances[ctx.guild.id]
        
        # if player is active then add query to queue
        if not Queue_Object.is_queue_empty() or ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            Queue_Object.put_in_queue(query)
            print(Queue_Object.display_queue())
            return await loading_msg.edit(content = "Added to queue!")
        # if it is the first song then play immediately
        # NOTE: might have to change this to add to queue regardless to avoid conflicts if new song is added before first song starts playing
        else:
            await loading_msg.edit(content = "Now Playing!")
            return await play_audio(ctx, query)
    else:
        # if connection fails then prints reason
        await loading_msg.edit(message)
    return

async def play_audio(ctx, query):
    # plays audio and sends the embed into chat
    url,time = request(query)
    source = await audiostream(url, loop=bot.loop, stream=True)
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
    await play_next_song(ctx)

async def play_next_song(ctx):
    # plays next song if available in that guild's queue
    Queue_Object = GuildQueue.instances[ctx.guild.id]
    while ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        # if a song is playing, sleeps asynchronously till it is finished
        await asyncio.sleep(1)
    if not Queue_Object.is_queue_empty():
        # after sleep finishes, gets latest song from queue and plays
        query = Queue_Object.get_latest_from_queue()
        return await play_audio(ctx, query)
    else:
        # if end of queue is reached
        await ctx.send("End of queue reached!")

# pauses music
@bot.hybrid_command(name='pause', description = "Pauses playback")
async def pause(ctx):
    if ctx.voice_client:
        if ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            ctx.send("Paused!")
        else:
            await ctx.send("Music already paused. Do you mean to resume?")
    else:
        await ctx.send("Nothing is playing.")

# resumes music
@bot.hybrid_command(name='resume', description = "Resumes playback")
async def resume(ctx):
    if ctx.voice_client:
        if ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            ctx.send("Resumed!")
        else:
            await ctx.send("Music already playing. Do you mean to pause?")
    else:
        await ctx.send("Nothing is playing.")

@bot.hybrid_command(name = 'fangs', description = "Plays Sheishen by Keylo X FANGS", hidden=True)
async def fangs(ctx):
    
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


@bot.hybrid_command(name = 'fuckoff',description = "Try it ;)", aliases=['fuck off'], pass_context=True)
async def fuckoff(ctx):

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

bot.run(DISCORD_TOKEN)