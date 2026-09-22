"""Schema, the migration from the pre-1.0 table, and applying actions."""

import asyncio
import sqlite3

import pytest

from app import db, sync
from app.jellyfin import JellyfinUser

NOW = 1_000_000


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(str(tmp_path / "sync.db"))
    db.migrate(connection, now=NOW)
    yield connection
    connection.close()


def make_legacy(path, rows):
    legacy = sqlite3.connect(str(path))
    legacy.execute(
        "CREATE TABLE users (ID TEXT, JellyfinUser TEXT PRIMARY KEY, Enabled INTEGER DEFAULT 1)"
    )
    legacy.executemany("INSERT INTO users (ID, JellyfinUser, Enabled) VALUES (?, ?, ?)", rows)
    legacy.commit()
    legacy.close()


# --- schema --------------------------------------------------------------

def test_migrate_stamps_the_schema_version(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_migrate_is_idempotent(conn):
    db.add_link(conn, "111", "examplename", now=NOW)
    assert db.migrate(conn) == 0
    assert db.get_links(conn) == {"111": "examplename"}


def test_connect_creates_the_parent_directory(tmp_path):
    connection = db.connect(str(tmp_path / "nested" / "deeper" / "sync.db"))
    db.migrate(connection)
    connection.close()
    assert (tmp_path / "nested" / "deeper" / "sync.db").exists()


# --- migration from the pre-1.0 users table ------------------------------

def test_the_old_table_in_the_old_file_becomes_links(tmp_path):
    make_legacy(
        tmp_path / "jellyfin_users.db",
        [("111", "examplealpha", 1), ("222 333", "examplebravo", 1), ("444", "examplecharlie", 0)],
    )
    connection = db.connect(str(tmp_path / "sync.db"))

    imported = db.migrate(connection, legacy_path=str(tmp_path / "jellyfin_users.db"), now=NOW)

    assert imported == 4
    assert db.get_links(connection) == {
        "111": "examplealpha",
        "222": "examplebravo",
        "333": "examplebravo",
        "444": "examplecharlie",
    }
    assert db.links_by_user(connection)["examplebravo"] == {"222", "333"}
    connection.close()


def test_a_disabled_row_carries_over_as_disabled_by_us(tmp_path):
    make_legacy(tmp_path / "jellyfin_users.db", [("111", "examplealpha", 1), ("444", "examplecharlie", 0)])
    connection = db.connect(str(tmp_path / "sync.db"))

    db.migrate(connection, legacy_path=str(tmp_path / "jellyfin_users.db"), now=NOW)

    states = db.get_states(connection)
    assert states["examplecharlie"].disabled_by_us is True
    assert "examplealpha" not in states  # nothing to remember about an enabled user
    connection.close()


def test_the_old_table_inside_the_same_file_is_migrated_too(tmp_path):
    path = tmp_path / "sync.db"
    make_legacy(path, [("111 222", "examplealpha", 1)])
    connection = db.connect(str(path))

    assert db.migrate(connection, now=NOW) == 2
    assert db.links_by_user(connection)["examplealpha"] == {"111", "222"}
    connection.close()


def test_odd_whitespace_and_empty_ids_survive_the_migration(tmp_path):
    make_legacy(
        tmp_path / "jellyfin_users.db",
        [
            ("  111\t222  \n", "examplealpha", 1),
            ("", "exampleempty", 1),
            (None, "examplenull", 1),
            ("999", "", 1),
        ],
    )
    connection = db.connect(str(tmp_path / "sync.db"))

    assert db.migrate(connection, legacy_path=str(tmp_path / "jellyfin_users.db"), now=NOW) == 2
    assert db.get_links(connection) == {"111": "examplealpha", "222": "examplealpha"}
    connection.close()


def test_no_legacy_database_is_not_an_error(tmp_path):
    connection = db.connect(str(tmp_path / "sync.db"))
    assert db.migrate(connection, legacy_path=str(tmp_path / "missing.db")) == 0
    connection.close()


def test_a_legacy_file_without_the_users_table_is_ignored(tmp_path):
    other = sqlite3.connect(str(tmp_path / "other.db"))
    other.execute("CREATE TABLE something_else (x INTEGER)")
    other.commit()
    other.close()
    connection = db.connect(str(tmp_path / "sync.db"))

    assert db.migrate(connection, legacy_path=str(tmp_path / "other.db")) == 0
    connection.close()


# --- links ---------------------------------------------------------------

def test_a_jellyfin_user_may_hold_several_telegram_ids(conn):
    db.add_link(conn, "111", "examplealpha")
    db.add_link(conn, "222", "examplealpha")
    assert db.links_by_user(conn) == {"examplealpha": {"111", "222"}}


def test_relinking_an_id_moves_it(conn):
    db.add_link(conn, "111", "examplealpha")
    db.add_link(conn, "111", "examplebravo")
    assert db.get_links(conn) == {"111": "examplebravo"}


def test_unlinking_returns_the_user_it_pointed_at(conn):
    db.add_link(conn, "111", "examplealpha")
    assert db.remove_link(conn, "111") == "examplealpha"
    assert db.remove_link(conn, "111") is None
    assert db.get_links(conn) == {}


# --- state, settings, audit ----------------------------------------------

def test_state_round_trips(conn):
    db.set_absent_since(conn, "examplealpha", NOW)
    db.set_disabled_by_us(conn, "examplealpha", True)
    assert db.get_states(conn)["examplealpha"] == db.UserState(absent_since=NOW, disabled_by_us=True)

    db.set_absent_since(conn, "examplealpha", None)
    db.set_disabled_by_us(conn, "examplealpha", False)
    assert db.get_states(conn)["examplealpha"] == db.UserState(absent_since=None, disabled_by_us=False)


def test_dry_run_falls_back_to_the_environment_until_it_is_set(conn):
    assert db.get_dry_run(conn, default=True) is True
    assert db.get_dry_run(conn, default=False) is False
    db.set_dry_run(conn, False)
    assert db.get_dry_run(conn, default=True) is False
    db.set_dry_run(conn, True)
    assert db.get_dry_run(conn, default=False) is True


def test_last_sync_round_trips(conn):
    assert db.get_last_sync(conn) is None
    db.set_last_sync(conn, NOW)
    assert db.get_last_sync(conn) == NOW


def test_audit_rows_are_newest_first(conn):
    db.record_audit(conn, "link", "examplealpha", "telegram_id 111", now=NOW)
    db.record_audit(conn, "disable", "examplealpha", "grace elapsed", dry_run=True, now=NOW + 1)
    rows = db.recent_audit(conn)
    assert [row["action"] for row in rows] == ["disable", "link"]
    assert rows[0]["dry_run"] == 1
    assert db.count_audit(conn) == 2
    assert db.count_audit(conn, "disable") == 1


def test_get_setting_default(conn):
    assert db.get_setting(conn, "nothing", "fallback") == "fallback"


# --- applying actions ----------------------------------------------------

class FakeJellyfin:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def set_user_disabled(self, user_id, disabled):
        if self.fail:
            raise RuntimeError("Jellyfin is unreachable")
        self.calls.append((user_id, disabled))


def apply(conn, jellyfin, action, dry_run, now):
    return asyncio.run(sync.apply_action(conn, jellyfin, action, dry_run, now))


def test_applying_a_disable_writes_jellyfin_and_takes_ownership(conn):
    jellyfin = FakeJellyfin()
    action = sync.Action(sync.DISABLE, "examplealpha", "jf-1", notify="x", detail="grace elapsed")

    apply(conn, jellyfin, action, dry_run=False, now=NOW)

    assert jellyfin.calls == [("jf-1", True)]
    assert db.get_states(conn)["examplealpha"].disabled_by_us is True
    assert db.recent_audit(conn)[0]["action"] == "disable"
    assert db.recent_audit(conn)[0]["dry_run"] == 0


def test_a_dry_run_disable_changes_nothing_in_jellyfin(conn):
    jellyfin = FakeJellyfin()
    action = sync.Action(sync.DISABLE, "examplealpha", "jf-1")

    apply(conn, jellyfin, action, dry_run=True, now=NOW)

    assert jellyfin.calls == []
    assert db.get_states(conn) == {}
    assert db.recent_audit(conn)[0]["dry_run"] == 1


def test_applying_an_enable_releases_ownership_and_clears_the_clock(conn):
    db.set_absent_since(conn, "examplealpha", NOW - 100)
    db.set_disabled_by_us(conn, "examplealpha", True)
    jellyfin = FakeJellyfin()

    apply(conn, jellyfin, sync.Action(sync.ENABLE, "examplealpha", "jf-1"), False, NOW)

    assert jellyfin.calls == [("jf-1", False)]
    assert db.get_states(conn)["examplealpha"] == db.UserState(absent_since=None, disabled_by_us=False)


def test_absence_bookkeeping_still_runs_in_a_dry_run(conn):
    # Otherwise a dry run could never reach the end of the grace window, and
    # the "would be disabled" message would never arrive.
    jellyfin = FakeJellyfin()
    apply(conn, jellyfin, sync.Action(sync.MARK_ABSENT, "examplealpha", "jf-1"), True, NOW)
    assert db.get_states(conn)["examplealpha"].absent_since == NOW
    assert jellyfin.calls == []

    apply(conn, jellyfin, sync.Action(sync.CLEAR_ABSENT, "examplealpha", "jf-1"), True, NOW)
    assert db.get_states(conn)["examplealpha"].absent_since is None


def test_an_unknown_action_is_refused(conn):
    with pytest.raises(ValueError, match="nonsense"):
        apply(conn, FakeJellyfin(), sync.Action("nonsense", "examplealpha"), False, NOW)


def test_the_end_to_end_shape_of_a_disable(conn):
    """From links to a changed Jellyfin account, with nothing mocked but the HTTP client."""
    db.add_link(conn, "111", "examplealpha")
    db.set_absent_since(conn, "examplealpha", NOW - 72 * 3600 - 1)
    jellyfin = FakeJellyfin()
    users = {"examplealpha": JellyfinUser("examplealpha", "jf-1", False, False)}

    actions = sync.decide(
        db.links_by_user(conn),
        {"111": sync.ABSENT},
        users,
        db.get_states(conn),
        NOW,
        72 * 3600,
        "ExampleChannel",
    )
    for action in actions:
        apply(conn, jellyfin, action, False, NOW)

    assert jellyfin.calls == [("jf-1", True)]
    assert db.get_states(conn)["examplealpha"].disabled_by_us is True


# --- the two ways Jellyfin and our ownership record can diverge ------------

def test_a_failed_jellyfin_disable_leaves_no_ownership_claim(conn):
    """A claim on an account we did not disable would lock the user out.

    If the write to Jellyfin fails the claim is rolled back, and no audit row
    says the disable happened.
    """
    jellyfin = FakeJellyfin(fail=True)
    action = sync.Action(sync.DISABLE, "examplealpha", "jf-1", detail="grace elapsed")

    with pytest.raises(RuntimeError, match="unreachable"):
        apply(conn, jellyfin, action, False, NOW)

    assert db.get_states(conn).get("examplealpha", db.UserState()).disabled_by_us is False
    assert db.count_audit(conn, "disable") == 0


def test_ownership_is_claimed_before_the_jellyfin_call(conn, monkeypatch):
    """The database write must not be the thing that can fail last.

    If Jellyfin succeeds and the claim is written afterwards, a failing write
    leaves the account disabled and unowned, and it can never be re-enabled.
    """
    order = []
    real_set = db.set_disabled_by_us
    monkeypatch.setattr(
        sync.db, "set_disabled_by_us",
        lambda conn_, user, owned, reason=None: (
            order.append(f"claim={owned}"),
            real_set(conn_, user, owned, reason),
        )[1],
    )

    class Recording(FakeJellyfin):
        def set_user_disabled(self, user_id, disabled):
            order.append("jellyfin")
            super().set_user_disabled(user_id, disabled)

    apply(conn, Recording(), sync.Action(sync.DISABLE, "examplealpha", "jf-1"), False, NOW)

    assert order == ["claim=True", "jellyfin"]
    assert db.get_states(conn)["examplealpha"].disabled_by_us is True


def test_applying_a_release_drops_the_ownership_claim(conn):
    db.set_disabled_by_us(conn, "examplealpha", True)
    jellyfin = FakeJellyfin()

    apply(conn, jellyfin, sync.Action(sync.RELEASE, "examplealpha", "jf-1"), False, NOW)

    assert db.get_states(conn)["examplealpha"].disabled_by_us is False
    assert jellyfin.calls == []  # nothing is written to Jellyfin
    assert db.recent_audit(conn)[0]["action"] == "release"


# --- schema 2: why an account was disabled -------------------------------

def test_a_1x_database_gains_the_reason_column(tmp_path):
    """Upgrading must not lose the accounts 1.x already disabled."""
    path = tmp_path / "sync.db"
    old = sqlite3.connect(str(path))
    old.executescript(
        """
        CREATE TABLE links (telegram_id TEXT PRIMARY KEY, jellyfin_user TEXT NOT NULL,
                            created_at INTEGER NOT NULL);
        CREATE TABLE user_state (jellyfin_user TEXT PRIMARY KEY, absent_since INTEGER,
                                 disabled_by_us INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
                            action TEXT NOT NULL, jellyfin_user TEXT, detail TEXT,
                            dry_run INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        PRAGMA user_version = 1;
        """
    )
    old.execute("INSERT INTO links VALUES ('111', 'examplealpha', 1)")
    old.execute("INSERT INTO user_state VALUES ('examplealpha', 100, 1)")
    old.execute("INSERT INTO user_state VALUES ('examplebravo', NULL, 0)")
    old.commit()
    old.close()

    connection = db.connect(str(path))
    db.migrate(connection, now=NOW)

    states = db.get_states(connection)
    # What 1.x disabled, it disabled for leaving the channel.
    assert states["examplealpha"].disabled_reason == db.LEFT_CHANNEL
    assert states["examplealpha"].disabled_by_us is True
    assert states["examplealpha"].absent_since == 100
    assert states["examplebravo"].disabled_reason is None
    assert db.get_links(connection) == {"111": "examplealpha"}
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    connection.close()


def test_the_1x_upgrade_runs_only_once(tmp_path):
    path = tmp_path / "sync.db"
    old = sqlite3.connect(str(path))
    old.executescript(
        """
        CREATE TABLE user_state (jellyfin_user TEXT PRIMARY KEY, absent_since INTEGER,
                                 disabled_by_us INTEGER NOT NULL DEFAULT 0);
        PRAGMA user_version = 1;
        """
    )
    old.execute("INSERT INTO user_state VALUES ('examplealpha', NULL, 1)")
    old.commit()
    old.close()

    connection = db.connect(str(path))
    db.migrate(connection, now=NOW)
    db.set_disabled_by_us(connection, "examplealpha", True, db.INACTIVE)
    db.migrate(connection, now=NOW)

    assert db.get_states(connection)["examplealpha"].disabled_reason == db.INACTIVE
    connection.close()


def test_the_reason_round_trips(conn):
    db.set_disabled_by_us(conn, "examplealpha", True, db.INACTIVE)
    assert db.get_states(conn)["examplealpha"].disabled_reason == db.INACTIVE

    db.set_disabled_by_us(conn, "examplealpha", False)
    assert db.get_states(conn)["examplealpha"].disabled_reason is None


def test_the_inactive_mark_round_trips(conn):
    db.set_inactive_mark(conn, "examplealpha", NOW)
    assert db.get_states(conn)["examplealpha"].inactive_mark == NOW
    db.set_inactive_mark(conn, "examplealpha", None)
    assert db.get_states(conn)["examplealpha"].inactive_mark is None


def test_an_inactivity_disable_records_the_reason_and_the_mark(conn):
    jellyfin = FakeJellyfin()
    action = sync.Action(
        sync.DISABLE, "examplealpha", "jf-1", reason=db.INACTIVE, last_used=NOW - 400 * 86400
    )

    apply(conn, jellyfin, action, False, NOW)

    state = db.get_states(conn)["examplealpha"]
    assert state.disabled_by_us is True
    assert state.disabled_reason == db.INACTIVE
    assert state.inactive_mark == NOW - 400 * 86400


def test_a_failed_inactivity_disable_leaves_no_mark_to_block_a_retry(conn):
    jellyfin = FakeJellyfin(fail=True)
    action = sync.Action(
        sync.DISABLE, "examplealpha", "jf-1", reason=db.INACTIVE, last_used=NOW - 400 * 86400
    )

    with pytest.raises(RuntimeError):
        apply(conn, jellyfin, action, False, NOW)

    state = db.get_states(conn).get("examplealpha", db.UserState())
    assert state.disabled_by_us is False
    assert state.inactive_mark is None


def test_a_channel_disable_records_its_own_reason(conn):
    apply(
        conn,
        FakeJellyfin(),
        sync.Action(sync.DISABLE, "examplealpha", "jf-1", reason=db.LEFT_CHANNEL),
        False,
        NOW,
    )
    state = db.get_states(conn)["examplealpha"]
    assert state.disabled_reason == db.LEFT_CHANNEL
    assert state.inactive_mark is None
