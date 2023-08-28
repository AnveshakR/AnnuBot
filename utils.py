import requests
import os
from dotenv import load_dotenv

load_dotenv()

YT_KEY = os.getenv('YT_KEY')
SPOTIFY_ID = os.getenv('SPOTIFY_ID')
SPOTIFY_SECRET = os.getenv('SPOTIFY_SECRET')

auth_url = 'https://accounts.spotify.com/api/token'
ytbase = "https://www.youtube.com/watch?v="
spotify_base = 'https://api.spotify.com/v1/tracks/{id}'

# get video info from YouTube API
def ytpull(query, is_youtube_id=False):

    # search for song by song ID if query is not a valid youtube ID
    if not is_youtube_id:
        get_video_id = requests.get('https://youtube.googleapis.com/youtube/v3/search?q={}&key={}'.format(query,YT_KEY))
        query = get_video_id.json()['items'][0]['id']['videoId'] # change query to youtube ID

    # get video info for first search result
    get_video_details = requests.get('https://youtube.googleapis.com/youtube/v3/videos?part=contentDetails&id={}&key={}'.format(query, YT_KEY))
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

    r = requests.get(spotify_base.format(id=uri), headers=headers)
    r = r.json()
    # return song and artist name
    return (r['name']+" "+r['artists'][0]['name'])


# main request formatting and redirecting
def request(query):
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

        link, time = ytpull(name+" explicit audio")

    elif query.find("spotify") !=-1: # if request is a spotify link

        uri = query[31:53] # extract uri from link

        name = spotifypull(uri) # get name of song from spotify API

        link, time = ytpull(name+" explicit audio")

    else: # if request is a general query

        link, time = ytpull(query+" explicit audio")

    return link, time