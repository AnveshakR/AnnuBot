import asyncio
import discord
import yt_dlp
import logging
from dotenv import load_dotenv
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

yt_dlp_opts = {
    'format': 'ba',
    'extract-audio': True,
    'audio-format': 'mp3',
    'audio-quality': 0,
    'quiet': True,
    'no_warnings': True,
}

ffmpeg_opts = {
    'before_options': '-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    logger.info(f"Test bot logged in as {client.user}")
    
    # find the guild and General voice channel
    target_guild = None
    target_channel = None
    
    for guild in client.guilds:
        logger.info(f"Guild: {guild.name} (ID: {guild.id})")
        for vc in guild.voice_channels:
            logger.info(f"  Voice channel: {vc.name}")
            if vc.name.lower() == 'general':
                target_guild = guild
                target_channel = vc
                break
        if target_channel:
            break
    
    if not target_channel:
        logger.error("No 'General' voice channel found. Aborting.")
        await client.close()
        return
    
    logger.info(f"Connecting to {target_channel.name} in {target_guild.name}...")
    voice = await target_channel.connect()
    logger.info("Connected to voice channel.")
    
    # search for Sweden by C418
    logger.info("Fetching audio for Sweden by C418...")
    ydl = yt_dlp.YoutubeDL(yt_dlp_opts)
    try:
        data = ydl.extract_info(
            'https://www.youtube.com/results?search_query=sweden+c418',
            download=False
        )
        if 'entries' in data and data['entries']:
            entry = data['entries'][0]
            url = entry['url']
            title = entry.get('title', 'unknown')
            logger.info(f"Found: {title}")
        else:
            logger.error("No results found.")
            await voice.disconnect()
            await client.close()
            return
    except Exception as e:
        logger.error(f"yt-dlp failed: {e}")
        await voice.disconnect()
        await client.close()
        return
    
    # play
    logger.info("Playing audio...")
    source = discord.FFmpegPCMAudio(url, **ffmpeg_opts)
    
    def after_error(error):
        if error:
            logger.error(f"Playback error: {error}")
        logger.info("Playback finished.")
        asyncio.run_coroutine_threadsafe(voice.disconnect(), client.loop)
        asyncio.run_coroutine_threadsafe(client.close(), client.loop)
    
    voice.play(source, after=after_error)
    logger.info("Now playing Sweden by C418... waiting 10 seconds then stopping.")
    
    await asyncio.sleep(10)
    logger.info("Stopping playback after 10 seconds...")
    voice.stop()
    await asyncio.sleep(1)
    await voice.disconnect()
    logger.info("Disconnected. Test complete.")
    await client.close()

client.run(DISCORD_TOKEN)
