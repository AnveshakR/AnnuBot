import youtube_dl
import requests
from dotenv import load_dotenv
import os
import random
import discord
import asyncio
from discord.ext import commands

#setup
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
SPOTIFY_ID = os.getenv('SPOTIFY_ID')
SPOTIFY_SECRET = os.getenv('SPOTIFY_SECRET')
API_KEY = os.getenv('YT_KEY')
AUTH_URL = 'https://accounts.spotify.com/api/token'
ytbase = "https://www.youtube.com/watch?v="
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

ydl_opts = {
        'format': "bestaudio/best",
        'highWaterMark': 1<<25,
        'extractaudio':True,
        'outtmpl': r'\temp\%(title)s.%(ext)s',
        'postprocessors':[{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'ffmpeg-location':r'ffmpeg.exe',
        'quiet':True
    }
ytdl = youtube_dl.YoutubeDL(ydl_opts)


async def audiostream(url,*,loop=None, stream=False):
    loop = loop or asyncio.get_event_loop()
    data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
    if 'entries' in data:
        data = data['entries'][0]
    filename = data['url'] if stream else ytdl.prepare_filename(data)
    return (discord.FFmpegPCMAudio(filename),data)

def ytpull(song):
    response = requests.get('https://youtube.googleapis.com/youtube/v3/search?q={}&key={}'.format(song,API_KEY))
    data = response.json()
    link = ytbase + data['items'][0]['id']['videoId']
    return(link)



def spotifypull(uri):
    r = requests.get(spotify_base.format(id=uri), headers=headers)
    r = r.json() 
    return (r['name']+" "+r['artists'][0]['name'])



def request(query,bool):
    link = ""
    if query.find("https://") != -1:

        if query.find("youtube.com/watch") != -1 or query.find("youtu.be/") != -1:
            link = query

        if query.find("spotify") !=-1:
            uri = query[31:53]
            name = spotifypull(uri)
            if bool==True:
                link = ytpull(name+" lyrics")
            else:
                link = ytpull(name)

    elif query.find("spotify:track") !=-1:
        uri = query[14:]
        name = spotifypull(uri)
        if bool==True:
            link = ytpull(name+" lyrics")
        else:
            link = ytpull(name)

    else:
        if bool==True:
            link = ytpull(query+" lyrics")
        else:
            link = ytpull(query)
    return link

bot = commands.Bot(command_prefix='annu ')

playerembed = discord.Embed(
    title = "Now Playing",
    color = discord.Colour(0x7289DA)
)

@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="annu"))

@bot.command(name = 'join', aliases=['connect'], pass_context=True)
async def join(ctx):

    if ctx.author.voice:
        await ctx.author.voice.channel.connect()
    else:
        await ctx.send("You are not connected to a voice channel.")
        raise commands.CommandError("Author not connected to a voice channel.")

@bot.command(name='disconnect', aliases=['nikal','leave'])
async def dc(ctx):
    await ctx.voice_client.disconnect()

@bot.command(name = 'fuckoff', aliases=['fuck off'], pass_context=True)
async def remove(ctx):
    await ctx.channel.send('Anu Malik fuck off nahi hota')


@bot.command(name = 'irshad', aliases=['sher'], pass_context=True)
async def remove(ctx):
    await ctx.channel.send('Annu says: {}'.format(random.choice(sher)))

# @bot.command(name='fangs', pass_context=True)
# async def fangs(ctx):
#     if ctx.voice_client is None:
#         if ctx.author.voice:
#             await ctx.author.voice.channel.connect()
#     source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(r"C:\Users\anves\Documents\Python Scripts\YT audio puller\Sieshin.mp3"))
#     ctx.voice_client.play(source, after=lambda e: print('Player error: %s' % e) if e else None)

@bot.command(name='play', pass_context=True)
async def play(ctx, *, query):
    url = request(query,False)
    if ctx.voice_client is None:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()

    source = await audiostream(url, loop=bot.loop, stream=True)
    data = source[1]
    title = data['title']
    ytid = data['id']
    ctx.voice_client.play(source[0], after=lambda e: print('Player error: %s' % e) if e else None)
    playerembed.set_image(url=data['thumbnail'])
    playerembed.description="[{}]({})".format(title,ytbase+ytid)
    await ctx.send(embed=playerembed)

@bot.command(name='lplay', pass_context=True)
async def play(ctx, *, query):
    url = request(query,True)
    if ctx.voice_client is None:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()

    source = await audiostream(url, loop=bot.loop, stream=True)
    data = source[1]
    title = data['title']
    ytid = data['id']
    ctx.voice_client.play(source[0], after=lambda e: print('Player error: %s' % e) if e else None)
    playerembed.set_image(url=data['thumbnail'])
    playerembed.description="[{}]({})".format(title,ytbase+ytid)
    await ctx.send(embed=playerembed)


bot.run(DISCORD_TOKEN)
