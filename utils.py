import requests
import os
from dotenv import load_dotenv
import re
from urllib.parse import urlparse, parse_qs
import logging

load_dotenv()

YT_KEY = os.getenv('YT_KEY')
SPOTIFY_ID = os.getenv('SPOTIFY_ID')
SPOTIFY_SECRET = os.getenv('SPOTIFY_SECRET')

auth_url = 'https://accounts.spotify.com/api/token'
ytbase = "https://www.youtube.com/watch?v="
spotify_base = 'https://api.spotify.com/v1/{link_type}/{id}'

def ytvideolistnames(video_ids) -> list:
    query = '&id='+'&id='.join(video_ids)
    r = requests.get(f"https://youtube.googleapis.com/youtube/v3/videos?part=snippet{query}&key={YT_KEY}").json()
    names = []
    for item in r['items']:
        names.append(item['snippet']['title'])
    return names

# get video info from YouTube API
def ytpull(query, is_video_id=False):
    # search for song by song ID if query is not a valid youtube ID
    if not is_video_id:
        # get video info for first search result
        get_video_id = requests.get('https://youtube.googleapis.com/youtube/v3/search?q={}&key={}'.format(query+" explicit audio",YT_KEY))
        try:
            query = get_video_id.json()['items'][0]['id']['videoId'] # change query to youtube ID
        except:
            return None, None

    # get video info from video_id
    get_video_details = requests.get('https://youtube.googleapis.com/youtube/v3/videos?part=contentDetails&id={}&key={}'.format(query, YT_KEY))
    video_data = get_video_details.json()

    link = ytbase + video_data['items'][0]['id'] # video link
    video_length = video_data['items'][0]['contentDetails']['duration'][2:] # playtime
    time = re.sub(r'(\d+)([A-Za-z]*)', r'\1:', video_length) # remove HMS markers
    time = re.sub(r':(\d):', r':0\1:', time).rstrip(':') # pad zero for single digits
    if time.isdigit(): # if only seconds then add colon to left
        time = ":"+time
    return link, time


# get song name from spotify URI using spotify API
def spotifypull(uri, link_type) -> list:
    auth_response = requests.post(auth_url, {
    'grant_type': 'client_credentials',
    'client_id': SPOTIFY_ID,
    'client_secret': SPOTIFY_SECRET,
    })

    auth_response_data = auth_response.json()
    access_token = auth_response_data['access_token']
    headers = {
        'Authorization': 'Bearer {token}'.format(token=access_token)
    }
    # gets response based on type of spotify link
    r = requests.get(spotify_base.format(link_type=link_type, id=uri), headers=headers)
    r = r.json()
    # return song based on link type
    song_names = []

    if link_type == 'tracks':
        artist_names = ', '.join(artist['name'] for artist in r['artists'])
        song_names.append(f"{r['name']} by {artist_names}")
    elif link_type == 'playlists':
        for i in r['tracks']['items']:
            artist_names = ', '.join(artist['name'] for artist in i['track']['artists'])
            song_names.append(f"{i['track']['name']} by {artist_names}")
    elif link_type == 'albums':
        for i in r['tracks']['items']:
            artist_names = ', '.join(artist['name'] for artist in i['artists'])
            song_names.append(f"{i['name']} by {artist_names}")

    return song_names

# checks if link is a youtube link
def is_youtube_link(query) -> bool:
    parsed_url = urlparse(query)
    return parsed_url.netloc == 'www.youtube.com' or parsed_url.netloc == 'youtube.com' or parsed_url.netloc == 'youtu.be'

# checks if link is a spotify link
def is_spotify_link(query) -> bool:
    parsed_url = urlparse(query)
    return parsed_url.netloc == 'open.spotify.com' or parsed_url.netloc == 'spotify'

# takes a query and returns the video id(for YT links) or names to be searched in youtube(non-YT links) as an array,
# and a boolean that signifies if the query was a YT link or not
def request(query) -> (list,bool):

    if is_youtube_link(query): # if request is a youtube link
        video_id = None
        playlist_id = None

        # Extract video_id and playlist_id based on different URL types
        if "youtu.be" in query:
            video_id = re.search("youtu\.be/([^\?&]+)?", query)[1]
        elif "start_radio" in query:
            playlist_id = query.split("list=")[-1].split("&")[0]
        elif "index" in query and "pp" in query:
            video_id = query.split("v=")[-1].split("&")[0]
        elif "pp=" in query:
            video_id = query.split("v=")[-1].split("&")[0]
        elif "list=" in query:
            playlist_id = query.split("list=")[-1].split("&")[0]
        
        video_links = []
        # get items in playlist
        if playlist_id is not None:
            r = requests.get(f"https://youtube.googleapis.com/youtube/v3/playlistItems?part=content_details&maxResults=50&playlistId={playlist_id}&key={YT_KEY}").json()
            for items in r['items']:
                video_links.append(items['contentDetails']['videoId'])
        elif video_id is not None:
            video_links.append(video_id)

        return video_links, True

    elif is_spotify_link(query): # if request is a spotify link
        patterns = [
        (r'^https://open\.spotify\.com/track/([a-zA-Z0-9]+)', 'tracks'),
        (r'^https://open\.spotify\.com/album/([a-zA-Z0-9]+)', 'albums'),
        (r'^https://open\.spotify\.com/playlist/([a-zA-Z0-9]+)', 'playlists'),
        (r'^spotify:track:([a-zA-Z0-9]+)', 'tracks'),
        (r'^spotify:album:([a-zA-Z0-9]+)', 'albums'),
        (r'^spotify:playlist:([a-zA-Z0-9]+)', 'playlists')
        ]
        uri = None
        # finds type of spotify link (track, album or playlist)
        for pattern, link_type in patterns:
            match = re.match(pattern, query)
            if match:
                uri = match.group(1)
                break
        
        names = spotifypull(uri, link_type) # get names from spotify api

        return names, False

    else: # if request is a general query
        logging.info("General Query found")
        return [query], False