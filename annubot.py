import youtube_dl
import yt_dlp
import requests
from dotenv import load_dotenv
import os
import random
import discord
import asyncio
from discord.ext import commands
from discord import app_commands

#setup
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
SPOTIFY_ID = os.getenv('SPOTIFY_ID')
SPOTIFY_SECRET = os.getenv('SPOTIFY_SECRET')
API_KEY = os.getenv('YT_KEY')
AUTH_URL = 'https://accounts.spotify.com/api/token'
ytbase = "https://www.youtube.com/watch?v="
spotify_base = 'https://api.spotify.com/v1/tracks/{id}'

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

# yt_dlp options
ytdl = yt_dlp.YoutubeDL(yt_dlp_opts)

# audio driver
async def audiostream(url,*,loop=None, stream=False):
    loop = loop or asyncio.get_event_loop()
    data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
    if 'entries' in data:
        data = data['entries'][0]
    filename = data['url'] if stream else ytdl.prepare_filename(data)
    return (discord.FFmpegPCMAudio(filename),data)

# get video info from YouTube API
def ytpull(query, is_youtube_id=False):

    # search for song by song ID if query is not a valid youtube ID
    if not is_youtube_id:
        get_video_id = requests.get('https://youtube.googleapis.com/youtube/v3/search?q={}&key={}'.format(query,API_KEY))
        query = get_video_id.json()['items'][0]['id']['videoId'] # change query to youtube ID

    # get video info for first search result
    get_video_details = requests.get('https://youtube.googleapis.com/youtube/v3/videos?part=contentDetails&id={}&key={}'.format(query, API_KEY))
    video_data = get_video_details.json()

    link = ytbase + video_data['items'][0]['id'] # video link
    video_length = video_data['items'][0]['contentDetails']['duration'][2:] # playtime
    time = ''
    hour = False
    minute = False

    # length string formatting
    if(video_length.find('H') != -1 and video_length != ""):
        hours = video_length[:video_length.find('H')]
        if len(hours) == 1:
            hours = '0' + hours
        time = time + hours + ':'
        video_length = video_length[video_length.find('H')+1:]
        hour = True


    if(video_length.find('M') != -1 and video_length != ""):
        minutes = video_length[:video_length.find('M')]
        if len(minutes) == 1:
            minutes = '0' + minutes
        time = time + minutes + ':'
        video_length = video_length[video_length.find('M')+1:]
        minute = True

    elif(hour==True):
        time = time + "00:"


    if(video_length.find('S') != -1 and video_length != ""):
        seconds = video_length[:video_length.find('S')]
        if len(seconds) == 1:
            seconds = '0' + seconds
        time = time + seconds
    elif(hour==True and minute==False):
        time = time + "00"
    else:
        time = time + "00"

    if(hour==False and minute==False):
        time = ":" + time

    return(link, time)


# get song name from spotify URI using spotify API
def spotifypull(uri):
    auth_response = requests.post(AUTH_URL, {
    'grant_type': 'client_credentials',
    'client_id': SPOTIFY_ID,
    'client_secret': SPOTIFY_SECRET,
    })

    auth_response_data = auth_response.json()
    access_token = auth_response_data['access_token']
    headers = {
        'Authorization': 'Bearer {token}'.format(token=access_token)
    }

    r = requests.get(spotify_base.format(id=uri), headers=headers)
    r = r.json()
    # return song and artist name
    return (r['name']+" "+r['artists'][0]['name'])


# main request formatting and redirecting
def request(query,lyrical_video):
    link = ""
    time = ""

    if query.find("https://") != -1: # if request is a link

        if query.find("youtube.com/watch") != -1 or query.find("youtu.be/") != -1: # if request is a youtube link get video ID from link
            videoId = ''
            if query.find("youtube.com/watch") != -1 and query.find("&ab_channel") != -1:
                videoId = query[query.find("/watch?v=")+9:query.find("&ab_channel")]
            elif query.find("youtube.com/watch") != -1:
                videoId = query[query.find("/watch?v=")+9:]
            elif query.find("youtu.be/") != -1 and query.find("?t=") !=-1:
                videoId = query[query.find("youtu.be/")+9:query.find("?t=")]
            elif query.find("youtu.be/") != -1:
                videoId = query[query.find("youtu.be/")+9:]

            # get video info from youtube
            link, time = ytpull(videoId, is_youtube_id=True)


    if query.find("spotify:track") !=-1: # if request is a spotify track link

        uri = query[14:] # extract uri from link

        name = spotifypull(uri) # get name of track from spotify api

        if lyrical_video==True:
            link, time = ytpull(name+" lyrics")
        else:
            link, time = ytpull(name)

    elif query.find("spotify") !=-1: # if request is a spotify link

        uri = query[31:53] # extract uri from link

        name = spotifypull(uri) # get name of song from spotify API

        if lyrical_video == True:
            link, time = ytpull(name+" lyrics")
        else:
            link, time = ytpull(name)

    else: # if request is a general query

        if lyrical_video==True:
            link, time = ytpull(query+" lyrics")
        else:
            link, time = ytpull(query)
    return link, time

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='annu ', intents=intents)

playerembed = discord.Embed(
    title = "Now Playing",
    color = discord.Colour(0x7289DA)
)

@bot.event
async def on_ready():
    # Bot presence
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="annu help"))
    await bot.tree.sync()

@bot.hybrid_command(name = 'join', description = "Joins your voice channel", aliases=['connect'], pass_context=True)
async def join(ctx):

    # getting bot's voice channel object
    bot_voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    # if user not in VC
    if ctx.author.voice is None:
        await ctx.send("You are not connected to a voice channel.")
        raise commands.CommandError("Author not connected to a voice channel.")
    
    # if bot not in VC but author in VC
    elif bot_voice is None and ctx.author.voice:
        await ctx.send(f"Joining {ctx.author.voice.channel}!")
        await ctx.author.voice.channel.connect()

    # if author and bot in same VC
    elif ctx.author.voice.channel == bot_voice.channel:
        await ctx.send("Already in your voice channel!")

    # if bot and author in different VCs
    elif ctx.author.voice.channel != bot_voice.channel and ctx.author.voice:
        await ctx.send("Bot already in another voice channel!")

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

@bot.hybrid_command(name = 'fuckoff',description = "Try it ;)", aliases=['fuck off'], pass_context=True)
async def fuckoff(ctx):

    # dont tell anu malik to fuckoff
    fuckoffs = ['Tu hota kaun hai','Anu Malik fuck off nahi hota','Tere baap ka naukar hu kya','Tu fuckoff']
    await ctx.send(random.choice(fuckoffs))

@bot.hybrid_command(name = 'irshad',description = "Delivers a true-blue Anu Malik shayari", aliases=['sher'], pass_context=True)
async def shayari(ctx):

    # random shayri
    await ctx.send('Annu says: {}'.format(random.choice(sher)))

    
@bot.hybrid_command(name = 'fangs', description = "Plays Sheishen by Keylo X FANGS", hidden=True)
async def fangs(ctx):
    
    # flag to check if bot is connected to a VC
    connect_flag = False
    if ctx.voice_client is None:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
            connect_flag = True
        else:
            await ctx.send("Join a VC first")

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
    
# play song based on youtube or spotify links, or a general query
@bot.hybrid_command(name='play', description = "Plays your song by name/YT/Spotify URL", pass_context=True)
async def play(ctx, *, query):
    
    url,time = request(query,False)

    # flag to check if bot is connected to a VC
    connect_flag = False
    if ctx.voice_client is None:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
            connect_flag = True
        else:
            await ctx.send("Join a VC first!")

    if connect_flag:
        source = await audiostream(url, loop=bot.loop, stream=True)
        data = source[1]
        title = data['title']
        ytid = data['id']
        ctx.voice_client.play(source[0], after=lambda e: print('Player error: %s' % e) if e else None)
        playerembed.set_image(url=data['thumbnail'])
        playerembed.description="[{}]({}) [{}]".format(title,ytbase+ytid,time)
        await ctx.send(embed=playerembed)

# same as annu play, but searches for lyrical version
@bot.hybrid_command(name='lplay',description = "Same as plays but searches for lyric version", pass_context=True)
async def play(ctx, *, query):

    url,time = request(query,True)
    # flag to check if bot is connected to a VC
    connect_flag = False

    if ctx.bot_voice is None:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
            connect_flag = True
        else:
            await ctx.send("Join a voice channel!")

    if connect_flag:
        source = await audiostream(url, loop=bot.loop, stream=True)
        data = source[1]
        title = data['title']
        ytid = data['id']
        ctx.bot_voice.play(source[0], after=lambda e: print('Player error: %s' % e) if e else None)
        playerembed.set_image(url=data['thumbnail'])
        playerembed.description="[{}]({}) [{}]".format(title,ytbase+ytid,time)
        await ctx.send(embed=playerembed)

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

bot.run(DISCORD_TOKEN)