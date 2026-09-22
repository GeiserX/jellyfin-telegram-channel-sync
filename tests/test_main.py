"""The cycle: read the world, decide, apply, tell the owner."""

import asyncio
from dataclasses import dataclass, field

import pytest

from app import bot, db, main, sync
from app.jellyfin import JellyfinUser

NOW = 1_780_000_000  # a real clock: epoch 0 must look ancient, not recent
GRACE = 72 * 3600


@dataclass
class FakeConfig:
    owner_id: int = 555000111
    dry_run: bool = False
    grace_hours: int = 72
    interval: int = 0
    threshold_entries: int = 1
    inactive_days: int = 0  # the inactivity rule is off unless a test wants it
    exempt_users: frozenset = frozenset()

    @property
    def grace_seconds(self):
        return self.grace_hours * 3600

    @property
    def inactive_seconds(self):
        return self.inactive_days * 86400


class FakeJellyfin:
    def __init__(self, users=None, fail_on=None):
        self.users = users or {
            "examplealpha": JellyfinUser("examplealpha", "jf-a", False, False, NOW),
            "examplebravo": JellyfinUser("examplebravo", "jf-b", True, False, NOW),
        }
        self.calls = []
        self.fail_on = fail_on

    def users_by_name(self):
        return dict(self.users)

    def set_user_disabled(self, user_id, disabled):
        if self.fail_on == user_id:
            raise RuntimeError("Jellyfin is unreachable")
        self.calls.append((user_id, disabled))


@dataclass
class Harness:
    ctx: bot.BotContext
    jellyfin: FakeJellyfin
    notes: list = field(default_factory=list)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(main.time, "time", lambda: NOW)
    conn = db.connect(str(tmp_path / "sync.db"))
    db.migrate(conn, now=NOW)
    jellyfin = FakeJellyfin()
    notes = []

    async def notify(text):
        notes.append(text)

    async def participants():
        return {"111": {"username": "exampleone", "name": "Example One"}}

    # 111 is in the channel; every other linked id is out.
    async def check_presence(ids):
        return {i: (sync.PRESENT if i == "111" else sync.ABSENT) for i in ids}

    ctx = bot.BotContext(
        conn=conn,
        jellyfin=jellyfin,
        config=FakeConfig(),
        resolve_telegram_id=None,
        fetch_participants=participants,
        run_cycle=None,
        channel_title="ExampleChannel",
        notify=notify,
        check_presence=check_presence,
    )
    yield Harness(ctx=ctx, jellyfin=jellyfin, notes=notes)
    conn.close()


def cycle(harness):
    return asyncio.run(main.run_cycle(harness.ctx))


def test_a_quiet_cycle_changes_nothing(harness):
    db.add_link(harness.ctx.conn, "111", "examplealpha")
    summary = cycle(harness)
    assert harness.jellyfin.calls == []
    assert harness.notes == []
    assert "Disabled 0, re-enabled 0" in summary
    assert db.get_last_sync(harness.ctx.conn) == NOW


def test_an_absent_member_starts_the_clock_without_a_notification(harness):
    db.add_link(harness.ctx.conn, "222", "examplealpha")
    summary = cycle(harness)
    assert db.get_states(harness.ctx.conn)["examplealpha"].absent_since == NOW
    assert harness.notes == []
    assert "newly absent 1" in summary


def test_a_member_absent_past_the_grace_window_is_disabled_and_announced(harness):
    db.add_link(harness.ctx.conn, "222", "examplealpha")
    db.set_absent_since(harness.ctx.conn, "examplealpha", NOW - GRACE)

    summary = cycle(harness)

    assert harness.jellyfin.calls == [("jf-a", True)]
    assert harness.notes == ["examplealpha left ExampleChannel, account disabled"]
    assert db.get_states(harness.ctx.conn)["examplealpha"].disabled_by_us is True
    assert "Disabled 1" in summary


def test_a_returning_member_is_re_enabled_and_announced(harness):
    db.add_link(harness.ctx.conn, "111", "examplebravo")  # bravo is disabled in Jellyfin
    db.set_disabled_by_us(harness.ctx.conn, "examplebravo", True)
    db.set_absent_since(harness.ctx.conn, "examplebravo", NOW - GRACE)

    cycle(harness)

    assert harness.jellyfin.calls == [("jf-b", False)]
    assert harness.notes == ["examplebravo rejoined ExampleChannel, account re-enabled"]
    assert db.get_states(harness.ctx.conn)["examplebravo"] == db.UserState(None, False)


def test_a_dry_run_announces_what_would_happen_and_touches_nothing(harness):
    harness.ctx.config.dry_run = True
    db.add_link(harness.ctx.conn, "222", "examplealpha")
    db.set_absent_since(harness.ctx.conn, "examplealpha", NOW - GRACE)

    summary = cycle(harness)

    assert harness.jellyfin.calls == []
    assert harness.notes == ["examplealpha left ExampleChannel, account would be disabled"]
    assert db.get_states(harness.ctx.conn)["examplealpha"].disabled_by_us is False
    assert "(dry run)" in summary
    assert db.recent_audit(harness.ctx.conn)[0]["dry_run"] == 1


def test_the_stored_dry_run_setting_beats_the_environment(harness):
    harness.ctx.config.dry_run = False
    db.set_dry_run(harness.ctx.conn, True)
    db.add_link(harness.ctx.conn, "222", "examplealpha")
    db.set_absent_since(harness.ctx.conn, "examplealpha", NOW - GRACE)

    cycle(harness)

    assert harness.jellyfin.calls == []


def test_unanswered_lookups_disable_nobody_and_are_counted(harness):
    async def all_unknown(ids):
        return {i: sync.UNKNOWN for i in ids}

    harness.ctx.check_presence = all_unknown
    db.add_link(harness.ctx.conn, "222", "examplealpha")
    db.set_absent_since(harness.ctx.conn, "examplealpha", NOW - GRACE)

    summary = cycle(harness)

    assert harness.jellyfin.calls == []
    assert "(1 unanswered)" in summary
    assert db.get_setting(harness.ctx.conn, "last_unknown") == "1"
    assert db.recent_audit(harness.ctx.conn)[0]["action"] == "unanswered"
    assert db.get_last_sync(harness.ctx.conn) == NOW


def test_one_failing_account_does_not_stop_the_others(harness):
    harness.jellyfin.fail_on = "jf-a"
    harness.jellyfin.users["examplecharlie"] = JellyfinUser("examplecharlie", "jf-c", False, False)
    for telegram_id, name in [("222", "examplealpha"), ("333", "examplecharlie")]:
        db.add_link(harness.ctx.conn, telegram_id, name)
        db.set_absent_since(harness.ctx.conn, name, NOW - GRACE)

    summary = cycle(harness)

    assert harness.jellyfin.calls == [("jf-c", True)]
    assert any("Could not disable examplealpha" in note for note in harness.notes)
    assert "Disabled 1" in summary
    assert [row["action"] for row in db.recent_audit(harness.ctx.conn)].count("error") == 1


def test_a_failing_notification_does_not_abort_the_cycle(harness):
    async def broken(text):
        raise RuntimeError("Telegram is down")

    harness.ctx.notify = broken
    db.add_link(harness.ctx.conn, "222", "examplealpha")
    db.set_absent_since(harness.ctx.conn, "examplealpha", NOW - GRACE)

    summary = cycle(harness)

    assert harness.jellyfin.calls == [("jf-a", True)]
    assert "Disabled 1" in summary


def test_a_context_without_notifications_still_runs(harness):
    harness.ctx.notify = None
    db.add_link(harness.ctx.conn, "222", "examplealpha")
    db.set_absent_since(harness.ctx.conn, "examplealpha", NOW - GRACE)
    assert "Disabled 1" in cycle(harness)


def test_the_loop_survives_a_failing_cycle_instead_of_exiting(harness):
    # The old code called exit(1) from inside the loop.
    calls = []

    async def exploding(ids):
        calls.append(1)
        raise RuntimeError("Telegram hiccup")

    harness.ctx.check_presence = exploding
    asyncio.run(main.periodic(harness.ctx, cycles=3))

    assert len(calls) == 3
    assert any("Sync cycle failed" in note for note in harness.notes)


def test_the_loop_runs_the_requested_number_of_cycles(harness):
    db.add_link(harness.ctx.conn, "111", "examplealpha")
    asyncio.run(main.periodic(harness.ctx, cycles=2))
    assert db.count_audit(harness.ctx.conn, "guardrail") == 0


def test_build_context_wires_the_cycle_and_the_title(tmp_path, monkeypatch):
    from types import SimpleNamespace

    class FakeUserClient:
        async def get_entity(self, key):
            return SimpleNamespace(title="ExampleChannel")

    config = SimpleNamespace(
        channel=-1001234567890,
        bot_token="12345:token",
        jellyfin_url="http://jellyfin:8096",
        jellyfin_api_key="fakekey",
        threshold_entries=1,
        owner_id=555000111,
        dry_run=True,
        grace_hours=72,
        interval=3600,
        grace_seconds=GRACE,
    )
    conn = db.connect(str(tmp_path / "sync.db"))
    db.migrate(conn)
    sent = []

    class FakeBotClient:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    ctx = asyncio.run(main.build_context(config, conn, FakeUserClient(), FakeBotClient()))

    assert ctx.channel_title == "ExampleChannel"
    assert callable(ctx.check_presence)
    assert callable(ctx.run_cycle)
    asyncio.run(ctx.notify("hello"))
    assert sent == [(555000111, "hello")]
    conn.close()


def test_the_summary_counts_every_kind_of_action(harness):
    harness.jellyfin.users["examplecharlie"] = JellyfinUser("examplecharlie", "jf-c", False, False)
    db.add_link(harness.ctx.conn, "111", "examplebravo")  # present, disabled by us -> enable
    db.set_disabled_by_us(harness.ctx.conn, "examplebravo", True)
    db.set_absent_since(harness.ctx.conn, "examplebravo", NOW - 10)
    db.add_link(harness.ctx.conn, "222", "examplealpha")  # absent, past grace -> disable
    db.set_absent_since(harness.ctx.conn, "examplealpha", NOW - GRACE)
    db.add_link(harness.ctx.conn, "333", "examplecharlie")  # absent, first time

    summary = cycle(harness)

    assert "Disabled 1, re-enabled 1, newly absent 1, back 1." in summary
    assert "3 linked Telegram accounts checked (0 unanswered), 3 linked Jellyfin users" in summary


# --- the inactivity rule inside a real cycle -----------------------------

def test_an_unused_account_is_disabled_and_the_owner_is_told(harness):
    harness.ctx.config.inactive_days = 365
    harness.jellyfin.users["examplealpha"] = JellyfinUser(
        "examplealpha", "jf-a", False, False, NOW - 400 * 86400
    )

    summary = cycle(harness)

    assert harness.jellyfin.calls == [("jf-a", True)]
    assert harness.notes == [
        f"examplealpha has not used Jellyfin since "
        f"{JellyfinUser('x', 'y', False, False, NOW - 400 * 86400).last_used_date}, account disabled"
    ]
    state = db.get_states(harness.ctx.conn)["examplealpha"]
    assert state.disabled_reason == "inactive"
    assert state.inactive_mark == NOW - 400 * 86400
    assert "Inactivity: 1 past the threshold" in summary


def test_an_account_with_no_recorded_use_is_counted_not_disabled(harness):
    harness.ctx.config.inactive_days = 365
    harness.jellyfin.users["examplealpha"] = JellyfinUser("examplealpha", "jf-a", False, False, None)

    summary = cycle(harness)

    assert harness.jellyfin.calls == []
    assert "0 past the threshold, 1 with no recorded use" in summary
    assert db.get_setting(harness.ctx.conn, "last_no_record") == "1"


def test_a_dry_run_reports_the_inactivity_disable_without_making_it(harness):
    harness.ctx.config.inactive_days = 365
    harness.ctx.config.dry_run = True
    harness.jellyfin.users["examplealpha"] = JellyfinUser(
        "examplealpha", "jf-a", False, False, NOW - 400 * 86400
    )

    cycle(harness)

    assert harness.jellyfin.calls == []
    assert harness.notes[0].endswith("account would be disabled")
    assert db.get_states(harness.ctx.conn) == {}


def test_an_exempt_account_survives_both_rules_in_a_real_cycle(harness):
    harness.ctx.config.inactive_days = 365
    harness.ctx.config.exempt_users = frozenset({"examplealpha"})
    harness.jellyfin.users["examplealpha"] = JellyfinUser(
        "examplealpha", "jf-a", False, False, NOW - 400 * 86400
    )
    db.add_link(harness.ctx.conn, "222", "examplealpha")
    db.set_absent_since(harness.ctx.conn, "examplealpha", NOW - GRACE)

    cycle(harness)

    assert harness.jellyfin.calls == []
