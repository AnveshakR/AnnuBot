import youtube_dl
import requests
from selenium import webdriver 
from selenium.webdriver.common.by import By 
from selenium.webdriver.support.ui import WebDriverWait 
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from dotenv import load_dotenv
import os
import random
import discord
from discord.ext import commands
chrome_path = r"D:\Projects\Chromedriver\chromedriver.exe"
save_path = r"D:\Projects\Anveshak Projects\AnnuBot\temp"


#spotify setup
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
SPOTIFY_ID = os.getenv('SPOTIFY_ID')
SPOTIFY_SECRET = os.getenv('SPOTIFY_SECRET')
AUTH_URL = 'https://accounts.spotify.com/api/token'
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



def mp3download(link):
    ydl_opts = {
        'format': "bestaudio/best",
        'extractaudio':True,
        'outtmpl': r'D:\Projects\Anveshak Projects\AnnuBot\temp\%(title)s.%(ext)s',
        'postprocessors':[{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'ffmpeg-location':r'ffmpeg.exe',
        'quiet':True
    }
    title = ""
    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        meta = ydl.extract_info(link)
        if(int(meta['duration'])<600):
            title = meta['title']
            return title
        else:
            return False



def ytpull(song):
    ytbase = "https://www.youtube.com/results?search_query="
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_experimental_option('excludeSwitches', ['enable-logging'])
    driver = webdriver.Chrome(chrome_path, options=options)
    my_url = ytbase+song+"+lyrics"
    driver.get(my_url)

    user_data = driver.find_elements_by_xpath('//*[@id="video-title"]')
    link = user_data[0].get_attribute('href')
    return(link)



def spotifypull(uri):
    r = requests.get(spotify_base.format(id=uri), headers=headers)
    r = r.json()
    name = r['name']+" "+r['artists'][0]['name']
    return ytpull(name)



def request(query):
    name = ""
    if query.find("https://") != -1:

        if query.find("youtube.com/watch") != -1 or query.find("youtu.be/") != -1:
            name = mp3download(query)

        if query.find("spotify") !=-1:
            uri = query[31:53]
            name = mp3download(spotifypull(uri))

    elif query.find("spotify:track") !=-1:
        uri = query[14:]
        name = mp3download(spotifypull(uri))

    else:
        name = mp3download(ytpull(query))
    return name

bot = commands.Bot(command_prefix='annu ')

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

@bot.command(name = 'fuckoff', pass_context=True)
async def remove(ctx):
    await ctx.channel.send('Anu Malik fuck off nahi hota')


@bot.command(name = 'irshad', aliases=['sher'], pass_context=True)
async def remove(ctx):
    sher = ['“Tumne bachchon ke saath dance kiya ringa-ringa rosy, Oh my God, I was feeling so cozy!!!”',
            '“Jungle ka raja hota hai ek sher, jaldi karo pack up, ho gayi hai der…”',
            '“Tera loha ka badan hai bada kaam ka, aa gaya hai season aam ka…”',
            '“Chura ke dil mera goriya chali, tere jaise singers ghumte hai gali gali…”',
            '“Tan tana tan tan tan tara, singer banne ka sochna bhi mat dobara…”',
            '“Tumse mile dil me utha dard karara, agar gala thik karna hai toh roz karo garara…”',
            '“Aaj tune kara aisa act, jispe karna pada mujhe react, this was one of the best yahi hai fact…”',
            '“Oonchi hai building, lift teri band hai, chhad ke nahi aa sakta, pairon me bada dard hai…”',
            '“Sur, lay, taal, sab bhatki hui hai, nirmal baba se pata karo ki kripya kaha atki hui hai…”',
            '“Tum ho paanch sundariyaan, aur main akela ladka, ise kehte haim daal me tadka…”']
    await ctx.channel.send('Annu says: {}'.format(random.choice(sher)))



@bot.command(name='play', pass_context=True)
async def play(ctx, *, query):

    title = request(query)
    if ctx.voice_client is None:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()

    #source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(r'C:\Users\anves\Documents\Python Scripts\YT audio puller\Sieshin.mp3'))
    source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(os.path.join(save_path,(title+".mp3"))))
    ctx.voice_client.play(source, after=lambda e: print('Player error: %s' % e) if e else None)
    await ctx.send('Now playing: {}'.format(title))
    print(os.path.join(save_path,(title+".mp3")))


bot.run(DISCORD_TOKEN)
