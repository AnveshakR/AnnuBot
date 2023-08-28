<img align="right" src="images/annumalik.jpg" height="200" width="200">

# Annu Music

Discord music player with a bit of spice :hot_pepper::hot_pepper:

Prefix for this bot is **annu**

> *Now supports slash commands!*

## Commands
* _play_: Plays your requested song
* _irshad_: Get an authentic Annu Malik shayari!
* _connect_: Connects to voice channel
* _pause_: Pauses playback
* _resume_: Resumes playback
* _fuckoff_: Don't do this.

## Query Sources
- Spotify song URL/URI
- Youtube song link
- General song name

## Example
/play https://www.youtube.com/watch?v=dQw4w9WgXcQ

*OR*

annu play https://www.youtube.com/watch?v=dQw4w9WgXcQ

## Known Issues
- Songs may stop 10-15s before the actual end (SOLVED)

## To-do
- [x] Implement queue for songs

## Invite link
Invite Annubot to your server with [this link](https://discord.com/api/oauth2/authorize?client_id=826187328774733844&permissions=281894054160&scope=bot).

## .env Setup
- After cloning this repo, place an .env file in the repo folder to be able to run it locally. The file format should be:
```
DISCORD_TOKEN = <your discord token>
SPOTIFY_ID = <your spotify id>
SPOTIFY_SECRET = <your spotify secret>
YT_KEY = <your google/youtube API key>
```
- You will also need the FFMPEG binary (not the python module) accessible through PATH in your respective OS.
- *annubot.py* is the main runfile.

---
**Jungle ka raja hota hai ek sher, jaldi karo pack up, ho gayi hai der…**
