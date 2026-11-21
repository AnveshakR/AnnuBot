"""Behaviour tests for /play, /resume and /join voice handling.

Drives the real command callbacks with fake Discord objects; network-facing
pieces (request, audiostream, play_next_song) are stubbed.
"""
import asyncio
import os
import tempfile

os.environ.setdefault('ANNUBOT_QUEUE_PATH', os.path.join(tempfile.mkdtemp(), 'queue.json'))

import annubot as A


class Msg:
    def __init__(self, log, text):
        self.log, self.text = log, text

    async def edit(self, content=None, **_):
        self.log.append(content)


class FakeVC:
    def __init__(self, guild, channel, connected=True):
        self.guild, self.channel, self._connected = guild, channel, connected
        self.playing = self.paused = False
        self.played = []

    def is_connected(self): return self._connected
    def is_playing(self): return self.playing
    def is_paused(self): return self.paused
    def resume(self): self.paused, self.playing = False, True
    def pause(self): self.paused, self.playing = True, False
    def stop(self): self.playing = self.paused = False

    def play(self, source, after=None):
        self.playing = True
        self.played.append(source)

    async def disconnect(self, force=False):
        self._connected = False
        self.guild.vc = None


class VoiceChannel:
    def __init__(self, guild, name):
        self.guild, self.name = guild, name

    async def connect(self):
        self.guild.vc = FakeVC(self.guild, self)
        return self.guild.vc

    def __str__(self): return self.name


class Guild:
    def __init__(self, gid):
        self.id, self.vc = gid, None


class Voice:
    def __init__(self, channel): self.channel = channel


class Author:
    def __init__(self, channel): self.voice = Voice(channel) if channel else None


class Ctx:
    def __init__(self, guild, author_channel):
        self.guild = guild
        self.author = Author(author_channel)
        self.log = []

    @property
    def voice_client(self): return self.guild.vc

    async def send(self, content=None, **_):
        self.log.append(content)
        return Msg(self.log, content)


def run(coro): return asyncio.get_event_loop().run_until_complete(coro)


def stub(calls):
    async def fake_audiostream(url, *, loop=None, stream=True, start=0.0):
        calls.append(('audiostream', url, round(start)))
        return ('SRC', {'id': 'x', 'duration': 200})

    async def fake_next(ctx):
        calls.append(('play_next_song',))

    A.audiostream = fake_audiostream
    A.play_next_song = fake_next
    A.request = lambda q: (['vid1', 'vid2', 'vid3'], True) if 'list=' in q else (([q], False) if q != 'bad' else ([], False))


def fresh_guild(gid):
    A._states.pop(gid, None)
    A.GuildQueue.instances.pop(gid, None)
    A._autopaused.discard(gid)
    return Guild(gid)


def paused_song(gid, pos=73.0):
    st = A._bump_generation(gid)
    st.current = {'url': 'https://youtu.be/x', 'ytid': 'x', 'title': 'Song X', 'duration': 200}
    st.pos.seek_to(pos)
    st.pos.pause()
    return st


def main():
    asyncio.set_event_loop(asyncio.new_event_loop())

    # 1. bad link: error reply, bot does NOT join voice
    calls = []; stub(calls)
    g = fresh_guild(1); vch = VoiceChannel(g, "General")
    ctx = Ctx(g, vch)
    run(A.play.callback(ctx, query='bad'))
    assert any("Couldn't find anything" in (m or "") for m in ctx.log), ctx.log
    assert g.vc is None, "bad link should not join voice"
    print("PASS 1: bad link replies with an error and doesn't join")

    # 2. /resume out of voice with a paused song: joins + restarts at the frozen second
    calls = []; stub(calls)
    g = fresh_guild(2); vch = VoiceChannel(g, "General")
    paused_song(2, 73.0)
    ctx = Ctx(g, vch)
    run(A.resume.callback(ctx))
    assert g.vc is not None and g.vc.channel is vch, "resume should join the author's VC"
    assert ('audiostream', 'https://youtu.be/x', 73) in calls, calls
    assert g.vc.played, "song should be playing"
    assert ('play_next_song',) not in calls, "must not pop the queue over the paused song"
    print("PASS 2: /resume joins and restarts the paused song at 1:13")

    # 3. /resume when the bot is in a different VC: generic refusal, no channel name
    calls = []; stub(calls)
    g = fresh_guild(3)
    other, mine = VoiceChannel(g, "SecretRoom"), VoiceChannel(g, "General")
    g.vc = FakeVC(g, other)
    ctx = Ctx(g, mine)
    run(A.resume.callback(ctx))
    assert ctx.log == ["Can't join your voice channel right now."], ctx.log
    print("PASS 3: different VC -> \"Can't join your voice channel right now.\" (no name)")

    # 4. /resume out of voice, no paused song, queue has songs: joins + plays queue
    calls = []; stub(calls)
    g = fresh_guild(4); vch = VoiceChannel(g, "General")
    A._guild_queue(4).put_in_queue(('vidA', True))
    ctx = Ctx(g, vch)
    run(A.resume.callback(ctx))
    assert g.vc is not None and ('play_next_song',) in calls, calls
    print("PASS 4: /resume with only a queue joins and continues the queue")

    # 5. /resume with nothing at all: doesn't join
    calls = []; stub(calls)
    g = fresh_guild(5); vch = VoiceChannel(g, "General")
    ctx = Ctx(g, vch)
    run(A.resume.callback(ctx))
    assert g.vc is None and "Nothing to resume" in ctx.log[-1], ctx.log
    print("PASS 5: /resume with nothing to play doesn't join")

    # 6. /play <song> with a paused song pending: queues the new song, resumes the paused one first
    calls = []; stub(calls)
    g = fresh_guild(6); vch = VoiceChannel(g, "General")
    paused_song(6, 40.0)
    ctx = Ctx(g, vch)
    run(A.play.callback(ctx, query='new song'))
    q = A.GuildQueue.instances[6].display_queue()
    assert q == [('new song', False)], q
    assert ('audiostream', 'https://youtu.be/x', 40) in calls and ('play_next_song',) not in calls, calls
    print("PASS 6: /play queues the new song and picks up the paused one first")

    # 7. stale (disconnected) voice client is replaced on resume
    calls = []; stub(calls)
    g = fresh_guild(7); vch = VoiceChannel(g, "General")
    g.vc = FakeVC(g, vch, connected=False)
    paused_song(7, 10.0)
    ctx = Ctx(g, vch)
    run(A.resume.callback(ctx))
    assert g.vc is not None and g.vc.is_connected() and g.vc.played, "stale client should be replaced"
    print("PASS 7: a stale voice client from a dropped connection is torn down and replaced")

    # 8. /join no longer starts the queue (raced play() -> lost songs)
    calls = []; stub(calls)
    g = fresh_guild(8); vch = VoiceChannel(g, "General")
    A._guild_queue(8).put_in_queue(('vidA', True))
    ctx = Ctx(g, vch)
    run(A.join.callback(ctx))
    assert ('play_next_song',) not in calls, calls
    print("PASS 8: /join only joins")

    # 9. /play <playlist> shuffle while a song is already playing: queues, doesn't start another
    calls = []; stub(calls)
    g = fresh_guild(9); vch = VoiceChannel(g, "General")
    g.vc = FakeVC(g, vch); g.vc.playing = True
    ctx = Ctx(g, vch)
    run(A.play.callback(ctx, query='https://youtube.com/playlist?list=abc shuffle'))
    q = A.GuildQueue.instances[9].display_queue()
    assert sorted(x[0] for x in q) == ['vid1', 'vid2', 'vid3'], q
    assert ('play_next_song',) not in calls, calls
    assert "shuffled" in ctx.log[-1], ctx.log
    print("PASS 9: playlist added shuffled while playing; no second song started")

    # 10. slash shuffle option + mix dedupe (YouTube RD mixes repeat songs across pages)
    calls = []; stub(calls)
    A.request = lambda q: (['a', 'b', 'c', 'a', 'b', 'c', 'a'], True)
    g = fresh_guild(10); vch = VoiceChannel(g, "General")
    g.vc = FakeVC(g, vch); g.vc.playing = True
    ctx = Ctx(g, vch)
    run(A.play.callback(ctx, query='https://youtube.com/watch?v=a&list=RDa', shuffle=True))
    q = [x[0] for x in A.GuildQueue.instances[10].display_queue()]
    assert sorted(q) == ['a', 'b', 'c'], q
    assert ctx.log[-1] == "Added 3 songs and shuffled them (4 repeats skipped).", ctx.log
    print("PASS 10: shuffle option works; mix repeats skipped; reply says so")

    print("\nALL PLAY/RESUME TESTS PASSED")


if __name__ == "__main__":
    main()
