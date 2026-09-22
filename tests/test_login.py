"""The one-off sign-in writes two different sessions, the second as a bot."""

import asyncio
from types import SimpleNamespace

from app import login
from app.config import TelegramLogin


class FakeClient:
    created = []

    def __init__(self, session, api_id, api_hash):
        self.session = session
        self.started_with = None
        self.disconnected = False
        FakeClient.created.append(self)

    async def start(self, bot_token=None):
        self.started_with = bot_token
        return self

    async def get_me(self):
        return SimpleNamespace(id=777000111, username="ExampleBot")

    async def disconnect(self):
        self.disconnected = True


def test_both_sessions_are_created_and_only_the_second_uses_the_token(tmp_path, capsys):
    FakeClient.created = []
    credentials = TelegramLogin(api_id=1, api_hash="h", bot_token="12345:token", data_dir=str(tmp_path))

    assert asyncio.run(login.run_login(credentials, client_factory=FakeClient)) == 0

    user_client, bot_client = FakeClient.created
    assert user_client.session == str(tmp_path / "session_name")
    assert bot_client.session == str(tmp_path / "bot_session")
    assert user_client.started_with is None
    assert bot_client.started_with == "12345:token"
    assert user_client.disconnected and bot_client.disconnected

    printed = capsys.readouterr().out
    assert "User session ready" in printed
    assert "Bot session ready" in printed
    assert "12345:token" not in printed  # never print the token back


def test_the_data_directory_is_created(tmp_path):
    FakeClient.created = []
    target = tmp_path / "data"
    credentials = TelegramLogin(api_id=1, api_hash="h", bot_token="t", data_dir=str(target))
    asyncio.run(login.run_login(credentials, client_factory=FakeClient))
    assert target.is_dir()


def test_a_missing_variable_is_reported_instead_of_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr(login, "load_telegram_login", lambda: (_ for _ in ()).throw(
        login.ConfigError("TELEGRAM_BOT_TOKEN is required")))
    assert login.main() == 2
    assert "TELEGRAM_BOT_TOKEN is required" in capsys.readouterr().out
