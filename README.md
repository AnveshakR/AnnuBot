<img align="right" src="images/annumalik.jpg" height="200" width="200">

# Annu Music

Discord music player with a bit of spice :hot_pepper::hot_pepper:

Prefix for this bot is **annu**

> *Now supports slash commands!*

## Commands
- `play [baja]:`Plays song/playlist/album from YT, Spotify and Apple Music
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

*Don't forget to*<br>
Join the Annubot Discord server with [this link](https://discord.gg/2eBN3q3Yvk)!

## Run it Locally!
- After cloning this repo, place an .env file in the repo folder to be able to run it locally. The file format should be:
```
DISCORD_TOKEN = <your discord token>
SPOTIFY_ID = <your spotify client id>
SPOTIFY_SECRET = <your spotify secret>
YT_KEY = <your Youtube API v3 key>
APPLE_MUSIC_TEAM_ID = <your Apple Developer Team ID>
APPLE_MUSIC_KEY_ID = <your MusicKit Key ID>
APPLE_MUSIC_PRIVATE_KEY_PATH = <path to your MusicKit .p8 private key>
```
- Apple Music support requires an Apple Developer account: create a Media ID identifier and
  a Key with the MusicKit service enabled (Certificates, Identifiers & Profiles → Keys), then
  download the `.p8` file it gives you (one-time download) and point
  `APPLE_MUSIC_PRIVATE_KEY_PATH` at it. No app review or extra cost beyond the existing paid
  Developer Program membership is needed — this is catalog-only (public song/album/playlist
  lookups), not user-authenticated access.
- You will also need the FFMPEG binary (not the python module) accessible through PATH in your respective OS.
- *annubot.py* is the main runfile.

---
**Jungle ka raja hota hai ek sher, jaldi karo pack up, ho gayi hai der…**
