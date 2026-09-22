"""Configuration is parsed in a function, so a missing variable is an error
message instead of an import-time crash."""

import pytest

from app import config


BASE_ENV = {
    "TELEGRAM_API_ID": "12345",
    "TELEGRAM_API_HASH": "fakehash",
    "TELEGRAM_CHANNEL": "-1001234567890",
    "TELEGRAM_BOT_TOKEN": "12345:fake-bot-token",
    "OWNER_ID": "555000111",
    "JELLYFIN_URL": "http://jellyfin:8096/",
    "JELLYFIN_API_KEY": "fakekey",
    "THRESHOLD_ENTRIES": "100",
}


def test_defaults():
    cfg = config.load_config(dict(BASE_ENV))
    assert cfg.api_id == 12345
    assert cfg.channel == -1001234567890
    assert cfg.owner_id == 555000111
    assert cfg.jellyfin_url == "http://jellyfin:8096"  # trailing slash dropped
    assert cfg.interval == 3600
    assert cfg.grace_hours == 72
    assert cfg.grace_seconds == 72 * 3600
    assert cfg.dry_run is True  # safe by default


def test_paths_live_under_the_data_dir():
    cfg = config.load_config({**BASE_ENV, "DATA_DIR": "/app/data"})
    assert cfg.db_path == "/app/data/sync.db"
    assert cfg.legacy_db_path == "/app/data/jellyfin_users.db"
    # Existing deployments already hold this session file; the name must not move.
    assert cfg.user_session == "/app/data/session_name"
    assert cfg.bot_session == "/app/data/bot_session"
    assert cfg.user_session != cfg.bot_session


def test_importing_the_module_does_not_read_the_environment(monkeypatch):
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    import importlib

    importlib.reload(config)  # would raise if parsing happened at import time


@pytest.mark.parametrize("missing", sorted(BASE_ENV))
def test_every_required_variable_is_reported_by_name(missing):
    env = {key: value for key, value in BASE_ENV.items() if key != missing}
    with pytest.raises(config.ConfigError, match=missing):
        config.load_config(env)


@pytest.mark.parametrize(
    "raw,expected",
    [("true", True), ("1", True), ("YES", True), ("on", True),
     ("false", False), ("0", False), ("no", False), ("OFF", False), ("", True)],
)
def test_dry_run_parsing(raw, expected):
    assert config.load_config({**BASE_ENV, "DRY_RUN": raw}).dry_run is expected


def test_rejects_a_nonsense_boolean():
    with pytest.raises(config.ConfigError, match="DRY_RUN"):
        config.load_config({**BASE_ENV, "DRY_RUN": "maybe"})


def test_rejects_a_nonsense_integer():
    with pytest.raises(config.ConfigError, match="SCRIPT_INTERVAL"):
        config.load_config({**BASE_ENV, "SCRIPT_INTERVAL": "one hour"})


@pytest.mark.parametrize(
    "raw,expected",
    [("-1001234567890", -1001234567890), ("1234", 1234), ("@examplechannel", "@examplechannel")],
)
def test_channel_may_be_an_id_or_a_username(raw, expected):
    assert config.parse_channel(raw) == expected


def test_login_config_needs_no_jellyfin():
    login = config.load_telegram_login(
        {"TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h", "TELEGRAM_BOT_TOKEN": "t"}
    )
    assert login.user_session == "/app/data/session_name"
    assert login.bot_session == "/app/data/bot_session"
