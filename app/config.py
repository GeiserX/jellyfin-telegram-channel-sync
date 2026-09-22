"""Environment configuration.

Everything is read inside a function so the modules stay importable (and
testable) without a full environment being present.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

DEFAULT_DATA_DIR = "/app/data"
USER_SESSION_NAME = "session_name"  # unchanged: existing deployments reuse it
BOT_SESSION_NAME = "bot_session"
DB_FILENAME = "sync.db"
LEGACY_DB_FILENAME = "jellyfin_users.db"

_INT_RE = re.compile(r"^-?\d+$")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(Exception):
    """Raised when the environment is missing or malformed."""


def _required(env: dict, name: str) -> str:
    value = (env.get(name) or "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _int(env: dict, name: str, default: int | None = None, minimum: int | None = None) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        if default is None:
            raise ConfigError(f"{name} is required")
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be {minimum} or more, got {value}")
    return value


def _bool(env: dict, name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}")


def parse_channel(raw: str) -> int | str:
    """A channel is either a numeric id or an @username."""
    raw = raw.strip()
    if _INT_RE.match(raw):
        return int(raw)
    return raw


@dataclass(frozen=True)
class TelegramLogin:
    """The subset needed to create the session files."""

    api_id: int
    api_hash: str
    bot_token: str
    data_dir: str

    @property
    def user_session(self) -> str:
        return os.path.join(self.data_dir, USER_SESSION_NAME)

    @property
    def bot_session(self) -> str:
        return os.path.join(self.data_dir, BOT_SESSION_NAME)


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    channel: int | str
    bot_token: str
    owner_id: int
    jellyfin_url: str
    jellyfin_api_key: str
    threshold_entries: int
    interval: int
    grace_hours: int
    dry_run: bool
    data_dir: str

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, DB_FILENAME)

    @property
    def legacy_db_path(self) -> str:
        return os.path.join(self.data_dir, LEGACY_DB_FILENAME)

    @property
    def user_session(self) -> str:
        return os.path.join(self.data_dir, USER_SESSION_NAME)

    @property
    def bot_session(self) -> str:
        return os.path.join(self.data_dir, BOT_SESSION_NAME)

    @property
    def grace_seconds(self) -> int:
        return self.grace_hours * 3600


def load_config(env: dict | None = None) -> Config:
    env = os.environ if env is None else env
    return Config(
        api_id=_int(env, "TELEGRAM_API_ID"),
        api_hash=_required(env, "TELEGRAM_API_HASH"),
        channel=parse_channel(_required(env, "TELEGRAM_CHANNEL")),
        bot_token=_required(env, "TELEGRAM_BOT_TOKEN"),
        owner_id=_int(env, "OWNER_ID"),
        jellyfin_url=_required(env, "JELLYFIN_URL").rstrip("/"),
        jellyfin_api_key=_required(env, "JELLYFIN_API_KEY"),
        # 0 would let an empty member list through and disable everybody.
        threshold_entries=_int(env, "THRESHOLD_ENTRIES", minimum=1),
        interval=_int(env, "SCRIPT_INTERVAL", 3600, minimum=1),
        # 0 is allowed: disable on the cycle after the first absence.
        grace_hours=_int(env, "GRACE_HOURS", 72, minimum=0),
        dry_run=_bool(env, "DRY_RUN", True),
        data_dir=(env.get("DATA_DIR") or DEFAULT_DATA_DIR).rstrip("/") or "/",
    )


def load_telegram_login(env: dict | None = None) -> TelegramLogin:
    env = os.environ if env is None else env
    return TelegramLogin(
        api_id=_int(env, "TELEGRAM_API_ID"),
        api_hash=_required(env, "TELEGRAM_API_HASH"),
        bot_token=_required(env, "TELEGRAM_BOT_TOKEN"),
        data_dir=(env.get("DATA_DIR") or DEFAULT_DATA_DIR).rstrip("/") or "/",
    )
