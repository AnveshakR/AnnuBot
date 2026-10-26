import requests
import os
import time
from dotenv import load_dotenv
import re
import unicodedata
from urllib.parse import urlparse
import logging
from typing import Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

YT_KEY = os.getenv('YT_KEY')
SPOTIFY_ID = os.getenv('SPOTIFY_ID')
SPOTIFY_SECRET = os.getenv('SPOTIFY_SECRET')
APPLE_MUSIC_TEAM_ID = os.getenv('APPLE_MUSIC_TEAM_ID')
APPLE_MUSIC_KEY_ID = os.getenv('APPLE_MUSIC_KEY_ID')
APPLE_MUSIC_PRIVATE_KEY_PATH = os.getenv('APPLE_MUSIC_PRIVATE_KEY_PATH')
# Optional Netscape-format cookies file (exported from a signed-in, age-verified
# YouTube/Google account) used ONLY as a fallback when the Data API search
# returns zero results. Some queries (e.g. containing certain flagged words)
# are age-gated by YouTube and silently return empty results to unauthenticated
# callers (API key or anonymous yt-dlp) instead of an age-confirmation prompt;
# an authenticated session bypasses this. Never committed — lives outside the
# repo checkout, path supplied via env only. Feature is a no-op if unset.
YT_COOKIES_FILE = os.getenv('ANNUBOT_YT_COOKIES')

auth_url = 'https://accounts.spotify.com/api/token'
ytbase = "https://www.youtube.com/watch?v="
spotify_base = 'https://api.spotify.com/v1/{link_type}/{id}'
apple_music_base = 'https://api.music.apple.com/v1/catalog/{storefront}/{link_type}/{id}'

# invisible/filler codepoints that render as nothing (e.g. Hangul filler U+3164)
# but break markdown link text when used as a title.
_INVISIBLE = {0x3164, 0x115F, 0x1160, 0xFFA0, 0x180E, 0xFEFF,
              0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060}

def clean_title(title, fallback="Untitled"):
    """Return safe, visible link-text for a title.

    Strips invisible/format chars, collapses whitespace, escapes markdown link
    special chars, and falls back to `fallback` if nothing visible remains.
    """
    if not title:
        return fallback
    out = []
    for ch in title:
        if ord(ch) in _INVISIBLE:
            continue
        if unicodedata.category(ch) in ('Cc', 'Cf', 'Co'):
            continue
        out.append(ch)
    text = ' '.join(''.join(out).split())
    text = text.replace('[', '\\[').replace(']', '\\]')
    return text if text else fallback

def ytvideolistnames(video_ids) -> list:
    if not video_ids:
        return []
    query = '&id='+'&id='.join(video_ids)
    try:
        r = requests.get(f"https://youtube.googleapis.com/youtube/v3/videos?part=snippet{query}&key={YT_KEY}", timeout=10).json()
        names = []
        for item in r['items']:
            names.append(item['snippet']['title'])
        return names
    except Exception as e:
        logger.error(f"ytvideolistnames failed: {type(e).__name__}: {_redact(e)}")
        return [f"Video {vid}" for vid in video_ids]

def format_duration(iso: str) -> str:
    """Format a YouTube Data API ISO 8601 duration (e.g. PT2H34M, PT2M34S)
    as a human-readable clock time.

    Returns H:MM:SS when the duration is an hour or longer, M:SS otherwise.
    The old two-regex hack mangled hours into the minutes slot (PT2H34M ->
    "2:34" instead of "2:34:00") and never padded single-digit fields.
    """
    m = re.match(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$", iso or "")
    if not m:
        logger.error(f"unparseable duration: {iso!r}")
        return "0:00"
    days, hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    total = ((days * 24 + hours) * 60 + minutes) * 60 + seconds
    h, rem = divmod(total, 3600)
    mi, s = divmod(rem, 60)
    return f"{h}:{mi:02d}:{s:02d}" if h else f"{mi}:{s:02d}"


# get video info from YouTube API
def _redact(text) -> str:
    # request URLs (and so requests' exception messages) carry the API key
    text = str(text)
    return text.replace(YT_KEY, '<YT_KEY>') if YT_KEY else text


def _yt_api_get(endpoint, params, what):
    """GET a YouTube Data API endpoint and return the parsed JSON, or None.

    Never raises: a network error, non-JSON body or API error (quota exceeded,
    bad key, ...) is logged with the API's own error message and returns None.
    """
    try:
        r = requests.get(f'https://youtube.googleapis.com/youtube/v3/{endpoint}',
                         params={**params, 'key': YT_KEY}, timeout=10)
    except Exception as e:
        logger.error(f"ytpull {what}: request failed: {type(e).__name__}: {_redact(e)}")
        return None
    try:
        body = r.json()
    except Exception as e:
        logger.error(f"ytpull {what}: HTTP {r.status_code}, unparseable body ({e}): {r.text[:500]!r}")
        return None
    if r.status_code != 200 or 'error' in body:
        err = body.get('error', {}) if isinstance(body, dict) else {}
        reasons = [x.get('reason') for x in err.get('errors', []) if isinstance(x, dict)]
        logger.error(f"ytpull {what}: HTTP {r.status_code}, API error {err.get('code')} "
                     f"{err.get('message')!r} reasons={reasons}")
        return None
    logger.debug(f"ytpull {what}: HTTP {r.status_code}, {len(body.get('items') or [])} items")
    return body


# Fallback search via yt-dlp's own YouTube search (bypasses the Data API
# entirely), using an authenticated cookies file if one is configured.
#
# Why this exists: some search queries (e.g. containing certain flagged
# words) are age-gated by YouTube. An anonymous/unauthenticated search
# (Data API key, or yt-dlp with no cookies) gets silently served ZERO
# results instead of an age-confirmation prompt, even though the video
# exists, is public, and is trivially findable by a logged-in, age-verified
# browser session. Passing cookies from such a session reproduces that and
# recovers the real result. If YT_COOKIES_FILE isn't configured this is a
# no-op (returns None) and callers fall through to the existing "not found"
# path exactly as before — no behavior change for anyone without cookies set up.
def _ytdlp_search_fallback(query, what):
    if not YT_COOKIES_FILE:
        return None
    try:
        import yt_dlp
    except ImportError:
        logger.error(f"ytpull {what}: yt-dlp fallback unavailable (import failed)")
        return None
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': 'in_playlist',
        'default_search': 'ytsearch5',
        'noplaylist': True,
        'cookiefile': YT_COOKIES_FILE,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
        entries = (info or {}).get('entries') or []
        for e in entries:
            vid = e.get('id')
            # extract_flat entries are a mix of videos/playlists/channels;
            # only a video has an 11-char YouTube ID shaped like this.
            if vid and e.get('_type', 'url') in ('url', None) and len(vid) == 11:
                logger.info(f"ytpull {what}: yt-dlp cookie fallback resolved to video {vid}")
                return vid
        logger.error(f"ytpull {what}: yt-dlp cookie fallback found no usable video entries")
        return None
    except Exception as e:
        logger.error(f"ytpull {what}: yt-dlp cookie fallback failed ({type(e).__name__}: {e})")
        return None


# Returns (link, formatted duration), or (None, None) if the song can't be
# resolved. Never raises: an exception here used to kill queue advancement.
def ytpull(query, is_video_id=False):
    # search for song by song ID if query is not a valid youtube ID
    if not is_video_id:
        # get video info for first search result
        body = _yt_api_get('search', {'q': query + " explicit audio"}, f"search {query!r}")
        resolved = None
        if body is not None:
            try:
                resolved = body['items'][0]['id']['videoId'] # change query to youtube ID
            except (KeyError, IndexError, TypeError) as e:
                logger.error(f"ytpull search {query!r}: no usable result ({type(e).__name__}: {e}); items={body.get('items')!r:.500}")
        if resolved is None:
            # Data API gave nothing (0 items, or no usable videoId in the
            # top hit) — try the authenticated yt-dlp fallback before
            # giving up on the search entirely.
            resolved = _ytdlp_search_fallback(query, f"search {query!r}")
        if resolved is None:
            return None, None
        query = resolved
        logger.debug(f"ytpull search resolved to video {query}")

    # get video info from video_id
    video_data = _yt_api_get('videos', {'part': 'contentDetails', 'id': query}, f"details {query}")
    if video_data is None:
        return None, None
    items = video_data.get('items') or []
    if not items:
        # deleted, private or region-blocked videos are silently omitted
        logger.error(f"ytpull details {query}: video not returned by the API (deleted, private or blocked?)")
        return None, None
    try:
        link = ytbase + items[0]['id'] # video link
        video_length = items[0]['contentDetails']['duration'] # playtime
    except (KeyError, TypeError) as e:
        logger.error(f"ytpull details {query}: malformed item ({type(e).__name__}: {e}): {items[0]!r:.500}")
        return None, None
    time = format_duration(video_length)
    return link, time


# get song name from spotify URI using spotify API
def spotifypull(uri, link_type) -> list:

    # handles abstracted spotify .link links by getting the redirected url
    if link_type == 'any':
        return request(requests.get(f"https://spotify.link/{uri}").url)[0]

    else:
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

# ---- Apple Music -------------------------------------------------------
# Developer token (ES256 JWT) is cached and regenerated when it's within
# _AM_TOKEN_REFRESH_MARGIN of expiry, same idea as the Spotify client-
# credentials flow but entirely local (no network round trip needed to mint
# it — only the catalog lookup itself hits the network).
_AM_TOKEN_TTL = 60 * 60 * 12          # 12h life per token (max allowed: ~6 months)
_AM_TOKEN_REFRESH_MARGIN = 60 * 5     # refresh 5 min before expiry
_am_token_cache = {'token': None, 'exp': 0}


def _apple_music_token() -> str:
    """Return a cached-or-fresh Apple Music developer token (ES256 JWT)."""
    now = int(time.time())
    if _am_token_cache['token'] and now < _am_token_cache['exp'] - _AM_TOKEN_REFRESH_MARGIN:
        return _am_token_cache['token']

    import jwt  # local import: only needed if Apple Music is actually used
    with open(APPLE_MUSIC_PRIVATE_KEY_PATH, encoding='utf-8') as f:
        private_key = f.read()
    token = jwt.encode(
        {'iss': APPLE_MUSIC_TEAM_ID, 'iat': now, 'exp': now + _AM_TOKEN_TTL},
        private_key, algorithm='ES256',
        headers={'kid': APPLE_MUSIC_KEY_ID, 'alg': 'ES256'})
    _am_token_cache['token'] = token
    _am_token_cache['exp'] = now + _AM_TOKEN_TTL
    return token


# get song name(s) from an Apple Music catalog id using the Apple Music API
def applemusicpull(storefront, uri, link_type) -> list:
    headers = {'Authorization': f'Bearer {_apple_music_token()}'}
    url = apple_music_base.format(storefront=storefront, link_type=link_type, id=uri)
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()['data'][0]
    except Exception as e:
        logger.error(f"applemusicpull failed: {e}")
        return []

    song_names = []
    attrs = data.get('attributes', {})

    if link_type == 'songs':
        song_names.append(f"{attrs.get('name')} by {attrs.get('artistName')}")
    elif link_type in ('playlists', 'albums'):
        tracks = data.get('relationships', {}).get('tracks', {}).get('data', [])
        for t in tracks:
            tattrs = t.get('attributes', {})
            song_names.append(f"{tattrs.get('name')} by {tattrs.get('artistName')}")

    return song_names

# checks if link is a youtube link
def is_youtube_link(query) -> bool:
    parsed_url = urlparse(query)
    return parsed_url.netloc == 'www.youtube.com' or parsed_url.netloc == 'youtube.com' or parsed_url.netloc == 'youtu.be'

# checks if link is a spotify link
def is_spotify_link(query) -> bool:
    parsed_url = urlparse(query)
    return parsed_url.netloc == 'open.spotify.com' or parsed_url.netloc == 'spotify' or parsed_url.netloc == 'spotify.link'

# checks if link is an apple music link
def is_apple_music_link(query) -> bool:
    parsed_url = urlparse(query)
    return parsed_url.netloc == 'music.apple.com'

# takes a query and returns the video id(for YT links) or names to be searched in youtube(non-YT links) as an array,
# and a boolean that signifies if the query was a YT link or not
def request(query) -> Tuple[list, bool]:

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
        elif "v=" in query:
            video_id = query.split("v=")[-1].split("&")[0]
        
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
        (r'^spotify:playlist:([a-zA-Z0-9]+)', 'playlists'),
        (r'https://spotify.link/([a-zA-Z0-9]+)', 'any')
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

    elif is_apple_music_link(query): # if request is an apple music link
        # URL shapes:
        #   https://music.apple.com/{storefront}/song/{slug}/{id}
        #   https://music.apple.com/{storefront}/album/{slug}/{id}            (whole album)
        #   https://music.apple.com/{storefront}/album/{slug}/{id}?i={track}  (one track from an album)
        #   https://music.apple.com/{storefront}/playlist/{slug}/{id}
        path_parts = [p for p in urlparse(query).path.split('/') if p]
        storefront = path_parts[0] if path_parts else 'us'
        type_map = {'song': 'songs', 'album': 'albums', 'playlist': 'playlists'}
        link_type = None
        catalog_id = None
        if len(path_parts) >= 3 and path_parts[1] in type_map:
            link_type = type_map[path_parts[1]]
            catalog_id = path_parts[3] if len(path_parts) > 3 else path_parts[2]

        query_params = dict(p.split('=', 1) for p in urlparse(query).query.split('&') if '=' in p)
        track_id = query_params.get('i')
        if link_type == 'albums' and track_id:
            # ?i= on an album link is a single-track share link
            link_type, catalog_id = 'songs', track_id

        if link_type is None or catalog_id is None:
            logger.error(f"could not parse apple music link: {query}")
            return [], False

        names = applemusicpull(storefront, catalog_id, link_type)
        return names, False

    else: # if request is a general query
        logging.info("General Query found")
        return [query], False