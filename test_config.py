"""Tests for persistent per-guild config + admin/working-channel checks.

Points the module-level CONFIG at a temp file (via ANNUBOT_CONFIG_PATH, read at
import time) so the repo's real config.json is never touched. Run:
    .venv/bin/python test_config.py
"""
import os
import sys
import tempfile
import json

# temp config path BEFORE importing annubot/config (env is read at import)
_tmpdir = tempfile.mkdtemp(prefix="annubot-cfg-")
os.environ["ANNUBOT_CONFIG_PATH"] = os.path.join(_tmpdir, "config.json")

REPO = os.path.expanduser("~/Documents/annubot")
sys.path.insert(0, REPO)
os.chdir(REPO)

import annubot as A
from config import Config


# ---- fakes -----------------------------------------------------------------
class FakeRole:
    def __init__(self, rid):
        self.id = rid


class FakePerms:
    def __init__(self, admin=False):
        self.administrator = admin


class FakeAuthor:
    def __init__(self, aid, roles=None, admin=False, voice=None):
        self.id = aid
        self.roles = roles or []
        self.guild_permissions = FakePerms(admin)
        self.voice = voice


class FakeChannel:
    def __init__(self, cid, mention=None):
        self.id = cid
        self.mention = mention or f"<#{cid}>"


class FakeGuild:
    def __init__(self, gid, owner_id, name="TestGuild"):
        self.id = gid
        self.owner_id = owner_id
        self.name = name
        self._channels = {}

    def get_channel(self, cid):
        return self._channels.get(cid)


class FakeCmd:
    def __init__(self, qualified):
        self.qualified_name = qualified


class FakeCtx:
    def __init__(self, guild, author, channel=None, command=None):
        self.guild = guild
        self.author = author
        self.channel = channel
        self.command = command


def check_raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception as e:
        raise AssertionError(f"expected {exc.__name__}, got {type(e).__name__}: {e}")
    raise AssertionError(f"expected {exc.__name__} to be raised, but nothing was")


async def main():
    cfg = A.CONFIG  # module-level, backed by the temp file

    # ---- 1) persistence: working channel round-trips through disk ----------
    cfg.set_working_channel(1, 777)
    on_disk = json.load(open(cfg.path))
    assert on_disk["guilds"]["1"]["working_channel"] == 777
    reloaded = Config(cfg.path)
    assert reloaded.working_channel(1) == 777
    print("ok 1: working_channel persists to disk and reloads")

    # ---- 2) persistence: admin roles round-trip + dedupe --------------------
    cfg.set_admin_roles(1, [555, 555, 666])
    reloaded2 = Config(cfg.path)
    assert reloaded2.admin_roles(1) == [555, 666], reloaded2.admin_roles(1)
    print("ok 2: admin_roles persist, deduped")

    # ---- 3) is_admin: owner / administrator / configured role / normal ------
    guild = FakeGuild(1, owner_id=100)
    cfg.set_admin_roles(1, [555])

    assert A.is_admin(FakeCtx(guild, FakeAuthor(100))) is True            # owner
    assert A.is_admin(FakeCtx(guild, FakeAuthor(200, admin=True))) is True  # admin perm
    assert A.is_admin(FakeCtx(guild, FakeAuthor(300, roles=[FakeRole(555)]))) is True  # configured role
    assert check_raises(lambda: A.is_admin(FakeCtx(guild, FakeAuthor(400, roles=[FakeRole(999)]))), A.NotAdmin)
    assert check_raises(lambda: A.is_admin(FakeCtx(guild, FakeAuthor(401))), A.NotAdmin)  # no roles at all
    print("ok 3: is_admin -> owner/admin-perm/configured-role pass, normal user raises NotAdmin")

    # ---- 4) is_admin: DM (no guild) raises NotAdmin -------------------------
    assert check_raises(lambda: A.is_admin(FakeCtx(None, FakeAuthor(100))), A.NotAdmin)
    print("ok 4: is_admin raises NotAdmin in a DM (no guild)")

    # ---- 5) in_working_channel: unset -> anywhere ---------------------------
    cfg.clear_working_channel(1)
    assert A.in_working_channel(FakeCtx(guild, FakeAuthor(1), FakeChannel(111))) is True
    print("ok 5: no working channel set -> any channel allowed")

    # ---- 6) in_working_channel: set + in that channel -> allowed ------------
    cfg.set_working_channel(1, 777)
    assert A.in_working_channel(FakeCtx(guild, FakeAuthor(1), FakeChannel(777))) is True
    print("ok 6: in the working channel -> allowed")

    # ---- 7) in_working_channel: set + wrong channel -> NotInWorkingChannel --
    assert check_raises(
        lambda: A.in_working_channel(FakeCtx(guild, FakeAuthor(1), FakeChannel(888))),
        A.NotInWorkingChannel,
    )
    print("ok 7: wrong channel -> raises NotInWorkingChannel")

    # ---- 8) in_working_channel: DM -> allowed --------------------------------
    assert A.in_working_channel(FakeCtx(None, FakeAuthor(1), FakeChannel(1))) is True
    print("ok 8: DM (no guild) -> allowed")

    # ---- 9) wiring: global check registered + config group + subcommand checks
    assert A.in_working_channel in A.bot._checks, "in_working_channel not a global check"
    grp = A.bot.get_command("config")
    assert grp is not None, "config group not registered"
    subnames = set(grp.all_commands)
    assert {"setchannel", "clearchannel", "setadminrole", "clearadminrole"} <= subnames, subnames
    # group + every subcommand carry the is_admin check (group checks don't cascade)
    assert A.is_admin in grp.checks, "config group missing is_admin"
    for name in subnames:
        assert A.is_admin in grp.all_commands[name].checks, f"config {name} missing is_admin"
    print("ok 9: global check registered; config group + subcommands all admin-gated")

    # ---- 10) clear_working_channel / clear_admin_roles actually clear --------
    cfg.clear_working_channel(1)
    cfg.clear_admin_roles(1)
    reloaded3 = Config(cfg.path)
    assert reloaded3.working_channel(1) is None
    assert reloaded3.admin_roles(1) == []
    print("ok 10: clear_* remove the keys (persisted)")

    # ---- 11) config command is exempt from the working-channel gate ----------
    cfg.set_working_channel(1, 777)
    # a config subcommand run from a DIFFERENT channel is still allowed (admin
    # can always fix the config, even from the wrong channel)
    assert A.in_working_channel(
        FakeCtx(guild, FakeAuthor(1), FakeChannel(888), command=FakeCmd("config setchannel"))
    ) is True
    # ...but a normal command from that other channel is still blocked
    assert check_raises(
        lambda: A.in_working_channel(
            FakeCtx(guild, FakeAuthor(1), FakeChannel(888), command=FakeCmd("play"))
        ),
        A.NotInWorkingChannel,
    )
    print("ok 11: config command exempt from working-channel gate; other commands not")

    print("\nALL CONFIG/CHECK TESTS PASSED (11/11)")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
