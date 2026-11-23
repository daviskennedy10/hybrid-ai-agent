"""Load only known environment variables; never evaluate or display secret text."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
KEYS = {'TELEGRAM_BOT_TOKEN', 'TELEGRAM_ALLOWED_USER_IDS', 'TELEGRAM_DEFAULT_PRIVATE'}


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    allowed_users: frozenset[int] = frozenset()
    default_private: bool = True
    max_input_bytes: int = 12000
    max_history_bytes: int = 32000
    max_history_messages: int = 16
    concurrency: int = 2
    max_pending: int = 16
    poll_seconds: int = 20
    api_retries: int = 2

    @classmethod
    def load(cls, env_file=ROOT / 'secrets/telegram.env', *, environ=None, require_users=True):
        values = {}
        path = Path(env_file)
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('export '):
                    line = line[7:]
                key, sep, value = line.partition('=')
                key, value = key.strip(), value.strip()
                if not sep or key not in KEYS:
                    raise ValueError('Invalid Telegram environment file')
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key] = value
        source = os.environ if environ is None else environ
        values.update({key: source[key] for key in KEYS if key in source})
        token = values.get('TELEGRAM_BOT_TOKEN', '')
        if not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,200}', token):
            raise ValueError('Missing or invalid Telegram bot token')
        raw = values.get('TELEGRAM_ALLOWED_USER_IDS', '')
        if raw and not re.fullmatch(r'[0-9]+(?:\s*,\s*[0-9]+)*', raw):
            raise ValueError('Allowed users must be comma-separated positive numeric IDs')
        users = frozenset(int(value.strip()) for value in raw.split(',') if value.strip())
        if len(users) > 64 or any(value <= 0 for value in users) or (require_users and not users):
            raise ValueError('Configure 1–64 TELEGRAM_ALLOWED_USER_IDS before starting the bot')
        private = values.get('TELEGRAM_DEFAULT_PRIVATE', 'true').lower()
        if private not in {'true', 'false'}:
            raise ValueError('TELEGRAM_DEFAULT_PRIVATE must be true or false')
        return cls(token, users, private == 'true')
