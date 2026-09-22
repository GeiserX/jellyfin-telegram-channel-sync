"""One pass over a real HTTP server, with the real requests client.

Every other Jellyfin test injects a fake session. This one runs the actual
`requests` calls against a stdlib HTTP server that behaves the way Jellyfin
does, including replacing the whole policy on POST. It is the check that the
header name, the URLs and the JSON body are right, not just consistent with
the fakes.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import bot, db, main, sync
from app.jellyfin import JellyfinClient

NOW = 1_000_000
GRACE = 72 * 3600

FULL_POLICY = {
    "IsAdministrator": False,
    "IsDisabled": False,
    "EnableAllFolders": False,
    "EnabledFolders": ["library-movies"],
    "MaxActiveSessions": 2,
    "RemoteClientBitrateLimit": 8000000,
    "SyncPlayAccess": "CreateAndJoinGroups",
}


EXPECTED_AUTH = 'MediaBrowser Token="fakekey"'


class FakeJellyfinServer(BaseHTTPRequestHandler):
    """Behaves like Jellyfin 12: only the Authorization scheme is accepted."""

    users = {}
    tokens = []

    def log_message(self, *args):
        pass

    def _authorized(self):
        auth = self.headers.get("Authorization")
        FakeJellyfinServer.tokens.append(auth)
        if self.headers.get("X-Emby-Token") or self.headers.get("X-MediaBrowser-Token"):
            self._send({"error": "deprecated token header"}, status=401)
            return False
        if auth != EXPECTED_AUTH:
            self._send({"error": "unauthorized"}, status=401)
            return False
        return True

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._authorized():
            return
        if self.path == "/Users":
            self._send(list(FakeJellyfinServer.users.values()))
        elif self.path.startswith("/Users/"):
            self._send(FakeJellyfinServer.users[self.path.split("/")[2]])
        else:  # pragma: no cover - the client asks for nothing else
            self._send({}, status=404)

    def do_POST(self):
        if not self._authorized():
            return
        length = int(self.headers.get("Content-Length", 0))
        sent = json.loads(self.rfile.read(length))
        user_id = self.path.split("/")[2]
        # Jellyfin replaces the entire policy with the request body.
        FakeJellyfinServer.users[user_id]["Policy"] = sent
        self.send_response(204)
        self.end_headers()


@pytest.fixture
def jellyfin_server():
    FakeJellyfinServer.users = {
        "jf-a": {"Id": "jf-a", "Name": "examplealpha", "Policy": dict(FULL_POLICY)},
        "jf-admin": {
            "Id": "jf-admin",
            "Name": "exampleadmin",
            "Policy": {**FULL_POLICY, "IsAdministrator": True},
        },
    }
    FakeJellyfinServer.tokens = []
    server = HTTPServer(("127.0.0.1", 0), FakeJellyfinServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)


def test_a_real_cycle_disables_an_absent_member_without_losing_their_policy(
    jellyfin_server, tmp_path, monkeypatch
):
    monkeypatch.setattr(main.time, "time", lambda: NOW)
    conn = db.connect(str(tmp_path / "sync.db"))
    db.migrate(conn, now=NOW)
    db.add_link(conn, "222", "examplealpha")
    db.add_link(conn, "333", "exampleadmin")
    db.set_absent_since(conn, "examplealpha", NOW - GRACE)
    db.set_absent_since(conn, "exampleadmin", NOW - GRACE)
    notes = []

    async def notify(text):
        notes.append(text)

    async def participants():
        return {"111": {"username": "exampleone", "name": "Example One"}}

    async def check_presence(ids):
        return {i: (sync.PRESENT if i == "111" else sync.ABSENT) for i in ids}

    class Config:
        owner_id = 555000111
        dry_run = False
        grace_hours = 72
        interval = 3600
        threshold_entries = 1
        grace_seconds = GRACE
        inactive_days = 0
        inactive_seconds = 0
        exempt_users = frozenset()

    ctx = bot.BotContext(
        conn=conn,
        jellyfin=JellyfinClient(jellyfin_server, "fakekey"),
        config=Config(),
        resolve_telegram_id=None,
        fetch_participants=participants,
        run_cycle=None,
        channel_title="ExampleChannel",
        notify=notify,
        check_presence=check_presence,
    )

    summary = asyncio.run(main.run_cycle(ctx))

    stored = FakeJellyfinServer.users["jf-a"]["Policy"]
    assert stored["IsDisabled"] is True
    assert stored["EnabledFolders"] == ["library-movies"]  # not wiped by the write
    assert stored["MaxActiveSessions"] == 2
    assert stored["SyncPlayAccess"] == "CreateAndJoinGroups"

    # The administrator was linked and absent past the grace window, and is untouched.
    assert FakeJellyfinServer.users["jf-admin"]["Policy"]["IsDisabled"] is False

    assert notes == ["examplealpha left ExampleChannel, account disabled"]
    assert "Disabled 1" in summary
    assert set(FakeJellyfinServer.tokens) == {EXPECTED_AUTH}
    conn.close()


def test_a_real_cycle_re_enables_a_returning_member(jellyfin_server, tmp_path, monkeypatch):
    monkeypatch.setattr(main.time, "time", lambda: NOW)
    FakeJellyfinServer.users["jf-a"]["Policy"]["IsDisabled"] = True
    conn = db.connect(str(tmp_path / "sync.db"))
    db.migrate(conn, now=NOW)
    db.add_link(conn, "111", "examplealpha")
    db.set_disabled_by_us(conn, "examplealpha", True)
    db.set_absent_since(conn, "examplealpha", NOW - GRACE)

    async def participants():
        return {"111": {"username": "exampleone", "name": "Example One"}}

    async def check_presence(ids):
        return {i: (sync.PRESENT if i == "111" else sync.ABSENT) for i in ids}

    class Config:
        owner_id = 555000111
        dry_run = False
        grace_hours = 72
        interval = 3600
        threshold_entries = 1
        grace_seconds = GRACE
        inactive_days = 0
        inactive_seconds = 0
        exempt_users = frozenset()

    ctx = bot.BotContext(
        conn=conn,
        jellyfin=JellyfinClient(jellyfin_server, "fakekey"),
        config=Config(),
        resolve_telegram_id=None,
        fetch_participants=participants,
        run_cycle=None,
        channel_title="ExampleChannel",
        check_presence=check_presence,
    )

    asyncio.run(main.run_cycle(ctx))

    assert FakeJellyfinServer.users["jf-a"]["Policy"]["IsDisabled"] is False
    assert FakeJellyfinServer.users["jf-a"]["Policy"]["EnabledFolders"] == ["library-movies"]
    assert db.get_states(conn)["examplealpha"] == db.UserState(None, False)
    conn.close()
