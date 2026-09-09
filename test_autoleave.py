"""Logic test for the auto-leave-empty-VC state machine.

Imports the real annubot module (import is safe: bot.run is guarded by
__name__ == '__main__') and drives on_voice_state_update with fake objects.

Key modeling detail: in real Discord, channel.members always reflects live
membership (a leaver is removed, a joiner is added). _empty_people reads that,
so the fakes must update channel.members on every state change.
"""
import asyncio
import annubot as A


class FakeMember:
    def __init__(self, guild, bot=False, name="m"):
        self.guild = guild
        self.bot = bot
        self.name = name


class FakeChannel:
    def __init__(self, name="VC", members=None):
        self.name = name
        self.members = members or []


class FakeGuild:
    def __init__(self, gid):
        self.id = gid
        self.name = f"Guild{gid}"
        self.system_channel = None  # no system channel -> skip the notify


class FakeVC:
    def __init__(self, guild, channel):
        self.guild = guild
        self.channel = channel
        self.disconnected = False

    async def disconnect(self):
        self.disconnected = True
        self.channel = None


class State:
    def __init__(self, channel):
        self.channel = channel


def add(channel, member):
    if member not in channel.members:
        channel.members.append(member)


def remove(channel, member):
    if member in channel.members:
        channel.members.remove(member)


async def main():
    guild = FakeGuild(1)
    channel = FakeChannel("General")
    vc = FakeVC(guild, channel)

    # wire the fakes into the real bot. voice_clients is a read-only property
    # backed by _connection._voice_clients (dict keyed by guild_id); user is a
    # plain attribute on _connection.
    A.bot._connection._voice_clients = {guild.id: vc}
    A.bot._connection.user = FakeMember(guild, bot=True, name="annubot")
    A._leave_tasks.clear()

    human = FakeMember(guild, bot=False, name="alice")
    add(channel, A.bot.user)
    add(channel, human)  # bot + alice present

    # ---- Scenario 1: alice leaves and the channel empties -> countdown starts
    remove(channel, human)  # alice leaves (live membership updates)
    await A.on_voice_state_update(human, State(channel), State(None))
    assert A._leave_tasks.get(guild.id) is not None, "countdown should have started"
    assert not vc.disconnected, "should NOT have left yet (still within delay)"
    print("PASS 1: countdown started when channel emptied, not left yet")

    # ---- Scenario 2: alice re-joins within the delay -> countdown cancelled
    add(channel, human)  # alice re-joins
    await A.on_voice_state_update(human, State(None), State(channel))
    task = A._leave_tasks.get(guild.id)
    assert task is None or task.done(), "countdown should be cancelled after re-join"
    print("PASS 2: re-join cancelled the pending leave")

    # ---- Scenario 3: empty again, wait past the delay -> actually leaves
    A.EMPTY_VC_LEAVE_DELAY = 0.05  # shrink delay for the test
    remove(channel, human)  # alice leaves again
    await A.on_voice_state_update(human, State(channel), State(None))
    assert A._leave_tasks.get(guild.id) is not None, "countdown restarted"
    await asyncio.sleep(0.2)
    assert vc.disconnected, "should have left after the delay elapsed"
    print("PASS 3: bot left the empty VC after the delay")

    # ---- Scenario 4: bot-moved branch (bot joins an already-empty channel)
    A._leave_tasks.clear()
    channel2 = FakeChannel("Empty2")
    add(channel2, A.bot.user)  # only the bot
    vc.channel = channel2
    await A.on_voice_state_update(A.bot.user, State(None), State(channel2))
    assert A._leave_tasks.get(guild.id) is not None, "bot-join-empty should start countdown"
    print("PASS 4: bot joining an already-empty channel starts the countdown")

    # ---- Scenario 5: a second human still present -> NO countdown
    A._leave_tasks.clear()
    channel3 = FakeChannel("Busy")
    add(channel3, A.bot.user)
    other = FakeMember(guild, bot=False, name="bob")
    add(channel3, other)
    vc.channel = channel3
    # alice leaves, but bob is still there -> channel not empty -> no countdown
    await A.on_voice_state_update(FakeMember(guild, bot=False, name="alice2"), State(channel3), State(None))
    assert A._leave_tasks.get(guild.id) is None, "should NOT start countdown while a human remains"
    print("PASS 5: no countdown while another human is still in the channel")

    print("\nALL AUTO-LEAVE LOGIC TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
