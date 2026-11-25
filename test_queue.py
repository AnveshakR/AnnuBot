"""Tests for the /queue embed display and its arrow-reaction paging.

Drives the real command callback and reaction listener with fake Discord
objects; ytvideolistnames is stubbed (no network).
"""
import asyncio
import os
import tempfile
import types

os.environ.setdefault('ANNUBOT_QUEUE_PATH', os.path.join(tempfile.mkdtemp(), 'queue.json'))

import annubot as A


BOT_USER = types.SimpleNamespace(id=1, bot=True)
USER = types.SimpleNamespace(id=2, bot=False)
A.bot._connection.user = BOT_USER  # discord.py 2.7: Client.user reads from the connection


class Msg:
    _next_id = 1

    def __init__(self, guild, author, content=None, embed=None):
        self.id = Msg._next_id
        Msg._next_id += 1
        self.guild, self.author = guild, author
        self.content, self.embed = content, embed
        self.reactions, self.edits = [], []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def edit(self, content=None, embeds=None, **_):
        if embeds is not None:
            self.embed = embeds[0]
        if content is not None:
            self.content = content
        self.edits.append(self.embed)


class Guild:
    def __init__(self, gid):
        self.id = gid


class Ctx:
    def __init__(self, guild):
        self.guild, self.sent = guild, []

    async def send(self, content=None, **kw):
        msg = Msg(self.guild, BOT_USER, content=content,
                  embed=kw.get('embed'))
        self.sent.append(msg)
        return msg


def run(coro): return asyncio.get_event_loop().run_until_complete(coro)


def fresh_guild(gid):
    A._states.pop(gid, None)
    A.GuildQueue.instances.pop(gid, None)
    A._autopaused.discard(gid)
    A.queue_view_pages.clear()
    return Guild(gid)


def fill_queue(gid, n):
    for i in range(n):
        A._guild_queue(gid).put_in_queue((f'song{i}', False))


def reaction(msg, emoji, user=USER):
    return types.SimpleNamespace(emoji=emoji, message=msg)


def main():
    asyncio.set_event_loop(asyncio.new_event_loop())
    A.ytvideolistnames = lambda ids: [f"Title-{i}" for i in range(len(ids))]

    # 1. empty queue: plain text, no embed
    g = fresh_guild(1); ctx = Ctx(g)
    run(A.display_queue.callback(ctx))
    assert ctx.sent[0].embed is None and ctx.sent[0].content == "No songs in queue.", ctx.sent[0].content
    print("PASS 1: empty queue replies with plain text")

    # 2. 25 songs: embed with the first 20, arrow reactions added
    g = fresh_guild(2); ctx = Ctx(g); fill_queue(2, 25)
    run(A.display_queue.callback(ctx))
    msg = ctx.sent[0]
    assert msg.embed is not None, "queue should be an embed"
    assert "25 songs" in msg.embed.title and "page 1/2" in msg.embed.title, msg.embed.title
    assert "1) song0" in msg.embed.description and "20) song19" in msg.embed.description
    assert "21) song20" not in msg.embed.description
    assert set(msg.reactions) == {"◀️", "▶️"}, msg.reactions
    print("PASS 2: embed shows page 1 of 2; arrow reactions present")

    # 3. ▶️ on page 1: edits the same message to page 2
    run(A._on_queue_reaction(reaction(msg, "▶️"), USER))
    assert len(msg.edits) == 1, "message should be edited in place"
    assert "page 2/2" in msg.embed.title, msg.embed.title
    assert "21) song20" in msg.embed.description and "1) song0" not in msg.embed.description
    print("PASS 3: right arrow pages to 2 (in-place edit)")

    # 4. ◀️ back to page 1
    run(A._on_queue_reaction(reaction(msg, "◀️"), USER))
    assert "page 1/2" in msg.embed.title and "1) song0" in msg.embed.description
    print("PASS 4: left arrow pages back to 1")

    # 5. edge arrows are no-ops (no extra edit)
    edits_before = len(msg.edits)
    run(A._on_queue_reaction(reaction(msg, "◀️"), USER))
    assert len(msg.edits) == edits_before, "left arrow on page 1 must not edit"
    print("PASS 5: arrow at the first page is a no-op")

    # 6. unknown message (from before a restart) is ignored
    old = Msg(g, BOT_USER, embed=object())
    run(A._on_queue_reaction(reaction(old, "▶️"), USER))
    assert old.edits == [], "messages not in the view map must be ignored"
    print("PASS 6: arrow on a pre-restart message is ignored")

    # 7. bots and other emojis are ignored
    run(A._on_queue_reaction(reaction(msg, "👍"), USER))
    run(A._on_queue_reaction(reaction(msg, "▶️"), types.SimpleNamespace(id=3, bot=True)))
    assert len(msg.edits) == edits_before, "irrelevant reactions must not edit"
    print("PASS 7: bot users and non-arrow reactions are ignored")

    # 8. queue empties while the embed is up: arrows show 'empty'
    A.GuildQueue.instances[2].clearqueue()
    run(A._on_queue_reaction(reaction(msg, "▶️"), USER))
    assert msg.embed.title == "Queue: empty" and msg.embed.description == "No songs in queue."
    assert 2 not in [k for k in A.queue_view_pages], "dead view should be dropped"
    print("PASS 8: empty queue updates the embed in place")

    print("\nALL QUEUE DISPLAY TESTS PASSED")


if __name__ == "__main__":
    main()
