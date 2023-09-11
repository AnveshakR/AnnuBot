<img align="right" src="images/annumalik.jpg" height="200" width="200">

# Annu Music

Discord music player with a bit of spice :hot_pepper::hot_pepper:

Prefix for this bot is **annu**

> *Now supports slash commands!*

## Commands
- `play [baja]:`Plays song/playlist/album from YT and Spotify
- `irshad [sher]:` Get an authentic Annu Malik shayari!
- `queue:` Shows the current queue
- `skip [next, agla] <number>:` Goes to next song or to the index specified
- `join [connect]:` Connects to your voice channel
- `pause [ruk]:` Pauses playback
- `resume [chal]:` Resumes playback
- `shuffle`: Shuffles queue\n
- `clear`: Clears queue\n
- `disconnect [nikal, leave]:` Disconnect from voice channel
- `fuckoff:` Don't do this.
- `help:` Shows this message

## Example
/play https://www.youtube.com/watch?v=dQw4w9WgXcQ

*OR*

annu play https://www.youtube.com/watch?v=dQw4w9WgXcQ

## Invite link
Invite Annubot to your server with [this link](https://discord.com/api/oauth2/authorize?client_id=826187328774733844&permissions=281894054160&scope=bot).

## Run it Locally!
- After cloning this repo, place an .env file in the repo folder to be able to run it locally. The file format should be:
```
DISCORD_TOKEN = <your discord token>
SPOTIFY_ID = <your spotify id>
SPOTIFY_SECRET = <your spotify secret>
YT_KEY = <your google/youtube API key>
```
- You will also need the FFMPEG binary (not the python module) accessible through PATH in your respective OS.
- *annubot.py* is the main runfile.

**Alternatively**
- You can use the Dockerfile to run annubot locally. You will need the .env file regardless ***in the same directory*** as the Dockerfile.
- Run this code in a terminal:
```
docker build --no-cache --build-arg KEY=value -t annubot .
docker run -e KEY=value --env-file .env annubot
```

---
**Jungle ka raja hota hai ek sher, jaldi karo pack up, ho gayi hai der…**
