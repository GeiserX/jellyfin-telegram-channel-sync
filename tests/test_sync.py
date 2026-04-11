"""Tests for jellyfin-telegram-channel-sync sync module."""

import os
import sys
import sqlite3
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

# Add app directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Set required environment variables for module import."""
    monkeypatch.setenv('TELEGRAM_API_ID', '12345')
    monkeypatch.setenv('TELEGRAM_API_HASH', 'fakehash')
    monkeypatch.setenv('TELEGRAM_CHANNEL', '-1001234567890')
    monkeypatch.setenv('THRESHOLD_ENTRIES', '5')
    monkeypatch.setenv('JELLYFIN_URL', 'http://jellyfin:8096')
    monkeypatch.setenv('JELLYFIN_API_KEY', 'fakeapikey')
    monkeypatch.setenv('SCRIPT_INTERVAL', '60')


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Create a temporary SQLite database with users table."""
    db_path = str(tmp_path / "jellyfin_users.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (ID TEXT, JellyfinUser TEXT PRIMARY KEY, Enabled INTEGER DEFAULT 1)")
    conn.execute("INSERT INTO users (ID, JellyfinUser, Enabled) VALUES ('111', 'alice', 1)")
    conn.execute("INSERT INTO users (ID, JellyfinUser, Enabled) VALUES ('222 333', 'bob', 1)")
    conn.execute("INSERT INTO users (ID, JellyfinUser, Enabled) VALUES ('444', 'charlie', 0)")
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# get_jellyfin_users
# ---------------------------------------------------------------------------

class TestGetJellyfinUsers:
    @patch('sync.requests.get')
    def test_returns_user_dict(self, mock_get):
        import sync
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {'Name': 'alice', 'Id': 'id-alice', 'Policy': {'IsDisabled': False}},
            {'Name': 'bob', 'Id': 'id-bob', 'Policy': {'IsDisabled': True}},
            {'Name': 'root', 'Id': 'id-root', 'Policy': {'IsDisabled': False}},
        ]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = sync.get_jellyfin_users()
        assert 'alice' in result
        assert 'bob' in result
        assert 'root' not in result  # root is filtered out
        assert result['alice']['Id'] == 'id-alice'
        assert result['alice']['IsDisabled'] is False
        assert result['bob']['IsDisabled'] is True

    @patch('sync.requests.get')
    def test_raises_on_http_error(self, mock_get):
        import sync
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("HTTP 401")
        mock_get.return_value = mock_response

        with pytest.raises(Exception, match="HTTP 401"):
            sync.get_jellyfin_users()


# ---------------------------------------------------------------------------
# set_jellyfin_user_enabled
# ---------------------------------------------------------------------------

class TestSetJellyfinUserEnabled:
    @patch('sync.requests.post')
    def test_enable_user(self, mock_post):
        import sync
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response

        sync.set_jellyfin_user_enabled('user-id-123', 'alice', True)

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]['json'] == {"IsDisabled": False}

    @patch('sync.requests.post')
    def test_disable_user(self, mock_post):
        import sync
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response

        sync.set_jellyfin_user_enabled('user-id-123', 'alice', False)

        call_kwargs = mock_post.call_args
        assert call_kwargs[1]['json'] == {"IsDisabled": True}

    @patch('sync.requests.post')
    def test_handles_error_status(self, mock_post, capsys):
        import sync
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Server Error"
        mock_post.return_value = mock_response

        sync.set_jellyfin_user_enabled('user-id-123', 'alice', True)
        captured = capsys.readouterr()
        assert "Error" in captured.out


# ---------------------------------------------------------------------------
# fetch_telegram_users
# ---------------------------------------------------------------------------

class TestFetchTelegramUsers:
    @patch('sync.TelegramClient')
    def test_returns_users_dict(self, mock_client_cls):
        import sync
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.is_user_authorized.return_value = True

        user1 = MagicMock()
        user1.id = 111
        user1.username = 'alice_tg'
        user1.first_name = 'Alice'
        user1.last_name = 'Smith'

        user2 = MagicMock()
        user2.id = 222
        user2.username = None
        user2.first_name = 'Bob'
        user2.last_name = None

        mock_client.get_participants.return_value = [user1, user2]

        # Need enough users to pass threshold
        sync.threshold_guardrail = 1
        result = sync.fetch_telegram_users()
        assert result is not None
        assert '111' in result
        assert '222' in result
        assert result['111']['username'] == 'alice_tg'

    @patch('sync.TelegramClient')
    def test_returns_none_below_threshold(self, mock_client_cls):
        import sync
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.is_user_authorized.return_value = True
        mock_client.get_participants.return_value = [MagicMock(id=1, username='x', first_name='X', last_name='')]

        sync.threshold_guardrail = 100  # Way above actual count
        result = sync.fetch_telegram_users()
        assert result is None

    @patch('sync.TelegramClient')
    def test_exits_if_not_authorized(self, mock_client_cls):
        import sync
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.is_user_authorized.return_value = False

        with pytest.raises(SystemExit):
            sync.fetch_telegram_users()


# ---------------------------------------------------------------------------
# main sync logic
# ---------------------------------------------------------------------------

class TestMainSync:
    @patch('sync.fetch_telegram_users')
    @patch('sync.get_jellyfin_users')
    @patch('sync.set_jellyfin_user_enabled')
    def test_enables_user_when_present_in_channel(self, mock_set_enabled, mock_jf_users, mock_tg_users, temp_db):
        import sync
        sync.db_file = temp_db

        # charlie (id=444) is disabled (Enabled=0), but present in channel
        mock_jf_users.return_value = {
            'charlie': {'Id': 'jf-charlie', 'IsDisabled': True},
            'alice': {'Id': 'jf-alice', 'IsDisabled': False},
            'bob': {'Id': 'jf-bob', 'IsDisabled': False},
        }
        mock_tg_users.return_value = {
            '111': {'username': 'alice_tg', 'first_name': 'Alice', 'last_name': ''},
            '444': {'username': 'charlie_tg', 'first_name': 'Charlie', 'last_name': ''},
            '222': {'username': 'bob_tg', 'first_name': 'Bob', 'last_name': ''},
        }

        sync.main()

        # charlie should be enabled
        mock_set_enabled.assert_any_call('jf-charlie', 'charlie', True)

    @patch('sync.fetch_telegram_users')
    @patch('sync.get_jellyfin_users')
    @patch('sync.set_jellyfin_user_enabled')
    def test_disables_user_when_absent_from_channel(self, mock_set_enabled, mock_jf_users, mock_tg_users, temp_db):
        import sync
        sync.db_file = temp_db

        # alice (id=111) is enabled but NOT in channel
        mock_jf_users.return_value = {
            'alice': {'Id': 'jf-alice', 'IsDisabled': False},
            'bob': {'Id': 'jf-bob', 'IsDisabled': False},
        }
        mock_tg_users.return_value = {
            '222': {'username': 'bob_tg', 'first_name': 'Bob', 'last_name': ''},
        }

        sync.main()

        # alice should be disabled
        mock_set_enabled.assert_any_call('jf-alice', 'alice', False)

    @patch('sync.fetch_telegram_users')
    @patch('sync.get_jellyfin_users')
    @patch('sync.set_jellyfin_user_enabled')
    def test_multi_id_user_present_if_any_id_matches(self, mock_set_enabled, mock_jf_users, mock_tg_users, temp_db):
        import sync
        sync.db_file = temp_db

        # bob has IDs "222 333". Only 333 is present in channel.
        mock_jf_users.return_value = {
            'alice': {'Id': 'jf-alice', 'IsDisabled': False},
            'bob': {'Id': 'jf-bob', 'IsDisabled': False},
        }
        mock_tg_users.return_value = {
            '111': {'username': 'alice_tg', 'first_name': 'Alice', 'last_name': ''},
            '333': {'username': 'bob_alt', 'first_name': 'Bob', 'last_name': 'Alt'},
        }

        sync.main()

        # bob should remain enabled (no change), so set_jellyfin_user_enabled should NOT be called for bob
        for call in mock_set_enabled.call_args_list:
            assert call[0][1] != 'bob'  # bob should have no status change

    @patch('sync.fetch_telegram_users')
    @patch('sync.get_jellyfin_users')
    def test_detects_unknown_telegram_ids(self, mock_jf_users, mock_tg_users, temp_db, capsys):
        import sync
        sync.db_file = temp_db

        mock_jf_users.return_value = {
            'alice': {'Id': 'jf-alice', 'IsDisabled': False},
        }
        mock_tg_users.return_value = {
            '111': {'username': 'alice_tg', 'first_name': 'Alice', 'last_name': ''},
            '222': {'username': 'bob_tg', 'first_name': 'Bob', 'last_name': ''},
            '999': {'username': 'unknown_tg', 'first_name': 'Unknown', 'last_name': 'User'},
        }

        sync.main()

        captured = capsys.readouterr()
        assert '999' in captured.out


# ---------------------------------------------------------------------------
# main_loop
# ---------------------------------------------------------------------------

class TestMainLoop:
    @patch('sync.time.sleep', side_effect=KeyboardInterrupt)
    @patch('sync.main')
    def test_main_loop_calls_main(self, mock_main, mock_sleep):
        import sync
        with pytest.raises(KeyboardInterrupt):
            sync.main_loop()
        mock_main.assert_called_once()

    @patch('sync.time.sleep', side_effect=KeyboardInterrupt)
    @patch('sync.main', side_effect=Exception("test error"))
    def test_main_loop_handles_exception(self, mock_main, mock_sleep, capsys):
        import sync
        with pytest.raises(KeyboardInterrupt):
            sync.main_loop()
        captured = capsys.readouterr()
        assert "error" in captured.out.lower()
