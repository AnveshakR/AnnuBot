"""Tests for YouTube link parsing in utils.request.

Only a real playlist page (youtube.com/playlist?list=...) may add a playlist.
watch?v=... links with tracking params (si=, t=, index=, pp=, list=RD...
&start_radio=1) must add just the single video. The playlist branch's API
call is faked; the single-video branch makes no network calls.
"""
import utils as U


class FakeResp:
    def __init__(self, payload): self.payload = payload

    def json(self): return self.payload


def fake_playlist_api(calls):
    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        # Two pages: the second ends the chain.
        if params.get('pageToken') == 'PAGE2':
            return FakeResp({'items': [{'contentDetails': {'videoId': 'p2'}}]})
        return FakeResp({'items': [{'contentDetails': {'videoId': 'p1'}}],
                         'nextPageToken': 'PAGE2'})
    U.requests.get = fake_get
    return fake_get


def main():
    # The case from the bug report: video link copied from a desktop browser
    # with the radio playlist + tracking params riding along.
    assert U.request('https://www.youtube.com/watch?v=abc123&list=RDeh5QW29R&start_radio=1&si=x') == (['abc123'], True)
    # A plain watch link.
    assert U.request('https://www.youtube.com/watch?v=abc123') == (['abc123'], True)
    # Video in the context of a REAL playlist: plays the video, not the list.
    assert U.request('https://www.youtube.com/watch?v=abc123&list=PLxyz&index=3&pp=0') == (['abc123'], True)
    # youtu.be short link with tracking params.
    assert U.request('https://youtu.be/abc123?si=x') == (['abc123'], True)
    assert U.request('https://youtu.be/abc123?t=42') == (['abc123'], True)
    # Start-time param (t=) must not turn a video into a playlist.
    assert U.request('https://www.youtube.com/watch?v=abc123&t=90') == (['abc123'], True)

    # A real playlist PAGE adds the playlist (API paginated: two pages).
    calls = []
    fake_playlist_api(calls)
    assert U.request('https://www.youtube.com/playlist?list=PLxyz123') == (['p1', 'p2'], True)
    assert len(calls) == 2 and calls[1].get('pageToken') == 'PAGE2', calls

    # An RD mix opened as a playlist page IS a playlist page: adds it.
    calls = []
    fake_playlist_api(calls)
    assert U.request('https://www.youtube.com/playlist?list=RDeh5QW29R&si=x') == (['p1', 'p2'], True)
    assert calls[0]['playlistId'] == 'RDeh5QW29R', calls

    # Non-YT links fall through to the search path (name to be looked up).
    assert U.request('some song name') == (['some song name'], False)

    print("ALL PLAYURL TESTS PASSED")


if __name__ == "__main__":
    main()
