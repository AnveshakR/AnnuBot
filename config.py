"""Persistent per-guild configuration for annubot.

The bot runs as a deploy checkout that is `git reset --hard`'d on every start,
so nothing inside the repo directory survives a restart. This config lives at a
path OUTSIDE the repo (set via ANNUBOT_CONFIG_PATH, e.g. ~/annubot-deploy/
config.json on the deploy box; defaults to ./config.json for local dev).

Shape:
    {
      "guilds": {
        "<guild_id>": {
          "working_channel": 123456789,   # channel the bot works in (option 3)
          "admin_roles": [111, 222]        # role ids treated as admins
        }
      }
    }

Everything is optional. A missing/empty file means "no per-guild config",
which is exactly the legacy behaviour (any channel, owner/admin-only gating).
"""
import json
import os
import tempfile

DEFAULT_PATH = os.environ.get('ANNUBOT_CONFIG_PATH', 'config.json')


class Config:
    def __init__(self, path=DEFAULT_PATH):
        self.path = path
        self.data = {'guilds': {}}
        self.load()

    # ---- persistence -------------------------------------------------------
    def load(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get('guilds'), dict):
                self.data = loaded
            else:
                self.data = {'guilds': {}}
        except FileNotFoundError:
            self.data = {'guilds': {}}
        except (json.JSONDecodeError, OSError):
            # corrupt or unreadable -> start clean rather than crash the bot
            self.data = {'guilds': {}}

    def save(self):
        # atomic: write to a temp file in the same dir, then rename over.
        # a crash mid-write can't leave a half-written config.json.
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.path)),
                                   prefix='.config-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            # best-effort cleanup of the temp file
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- per-guild access --------------------------------------------------
    def guild(self, guild_id):
        """Mutable per-guild dict (auto-created). Caller must call save()."""
        return self.data['guilds'].setdefault(str(guild_id), {})

    def working_channel(self, guild_id):
        return self.guild(guild_id).get('working_channel')

    def set_working_channel(self, guild_id, channel_id):
        self.guild(guild_id)['working_channel'] = channel_id
        self.save()

    def clear_working_channel(self, guild_id):
        self.guild(guild_id).pop('working_channel', None)
        self.save()

    def admin_roles(self, guild_id):
        return self.guild(guild_id).get('admin_roles', [])

    def set_admin_roles(self, guild_id, role_ids):
        self.guild(guild_id)['admin_roles'] = list(dict.fromkeys(role_ids))
        self.save()

    def clear_admin_roles(self, guild_id):
        self.guild(guild_id).pop('admin_roles', None)
        self.save()


# module-level instance so annubot can `from config import CONFIG`
CONFIG = Config()
