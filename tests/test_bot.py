"""Bot commands, and the gate that keeps everyone but the owner out."""

import asyncio
import time
from dataclasses import dataclass, field

import pytest

from app import bot, db
from app.jellyfin import JellyfinUser

OWNER = 555000111
STRANGER = 555000222
NOW = 1_000_000


@dataclass
class FakeConfig:
    owner_id: int = OWNER
    dry_run: bool = True
    grace_hours: int = 72
    interval: int = 3600
    threshold_entries: int = 100
    inactive_days: int = 365
    exempt_users: frozenset = frozenset()

    @property
    def inactive_seconds(self):
        return self.inactive_days * 86400


class FakeJellyfin:
    def __init__(self, users=None):
        self.users = users or {
            "examplealpha": JellyfinUser("examplealpha", "jf-a", False, False),
            "examplebravo": JellyfinUser("examplebravo", "jf-b", False, False),
            "exampleadmin": JellyfinUser("exampleadmin", "jf-admin", False, True),
        }
        self.calls = []

    def users_by_name(self):
        return self.users

    def set_user_disabled(self, user_id, disabled):
        self.calls.append((user_id, disabled))


@dataclass
class FakeEvent:
    raw_text: str
    sender_id: int = OWNER
    is_private: bool = True
    replies: list = field(default_factory=list)

    async def reply(self, text):
        self.replies.append(text)


@pytest.fixture
def ctx(tmp_path):
    conn = db.connect(str(tmp_path / "sync.db"))
    db.migrate(conn, now=NOW)

    async def resolve(handle):
        if handle == "@exampleuser":
            return 777000111
        if handle == "@nosuchuser":
            raise ValueError("No user has @nosuchuser")
        return int(handle)

    async def participants():
        return {
            "111": {"username": "exampleone", "name": "Example One"},
            "777000111": {"username": "exampleuser", "name": "Example User"},
        }

    async def run_cycle():
        return "Sync done. 2 channel members, 0 linked Jellyfin users."

    context = bot.BotContext(
        conn=conn,
        jellyfin=FakeJellyfin(),
        config=FakeConfig(),
        resolve_telegram_id=resolve,
        fetch_participants=participants,
        run_cycle=run_cycle,
        channel_title="ExampleChannel",
    )
    yield context
    conn.close()


def say(ctx, text):
    return asyncio.run(bot.handle_command(ctx, text))


# --- parsing -------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("/links", ("links", [])),
        ("  /link @exampleuser examplealpha  ", ("link", ["@exampleuser", "examplealpha"])),
        ("/LINK 111 examplealpha", ("link", ["111", "examplealpha"])),
        ("/status@ExampleBot", ("status", [])),
        ("/dryrun on", ("dryrun", ["on"])),
    ],
)
def test_parse_command(text, expected):
    assert bot.parse_command(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "hello", "not /a command", "/", None])
def test_non_commands_are_not_parsed(text):
    assert bot.parse_command(text) is None


def test_plain_chat_gets_no_reply(ctx):
    assert say(ctx, "hello there") is None


def test_an_unknown_command_answers_with_the_help(ctx):
    reply = say(ctx, "/nope")
    assert "Unknown command /nope" in reply
    assert "/link" in reply


# --- owner gate ----------------------------------------------------------

def test_the_owner_is_answered(ctx):
    event = FakeEvent("/links")
    assert asyncio.run(bot.on_message(ctx, event)) is not None
    assert len(event.replies) == 1


def test_plain_chat_from_the_owner_gets_no_reply(ctx):
    event = FakeEvent("good morning")
    assert asyncio.run(bot.on_message(ctx, event)) is None
    assert event.replies == []


def test_anyone_else_is_ignored_without_a_reply(ctx):
    event = FakeEvent("/links", sender_id=STRANGER)
    assert asyncio.run(bot.on_message(ctx, event)) is None
    assert event.replies == []


def test_a_stranger_cannot_change_anything(ctx):
    asyncio.run(bot.on_message(ctx, FakeEvent("/dryrun off", sender_id=STRANGER)))
    asyncio.run(bot.on_message(ctx, FakeEvent("/link 111 examplealpha", sender_id=STRANGER)))
    assert db.get_dry_run(ctx.conn, default=True) is True
    assert db.get_links(ctx.conn) == {}


def test_a_group_or_channel_message_is_ignored_even_from_the_owner(ctx):
    # The bot must never post into the channel it is watching.
    event = FakeEvent("/links", is_private=False)
    assert asyncio.run(bot.on_message(ctx, event)) is None
    assert event.replies == []


# --- /link and /unlink ---------------------------------------------------

def test_link_by_numeric_id(ctx):
    assert say(ctx, "/link 111 examplealpha") == "Linked 111 to examplealpha."
    assert db.get_links(ctx.conn) == {"111": "examplealpha"}
    assert db.recent_audit(ctx.conn)[0]["action"] == "link"


def test_link_resolves_an_at_username(ctx):
    assert say(ctx, "/link @exampleuser examplealpha") == "Linked 777000111 to examplealpha."
    assert db.get_links(ctx.conn) == {"777000111": "examplealpha"}


def test_link_reports_a_username_it_cannot_resolve(ctx):
    reply = say(ctx, "/link @nosuchuser examplealpha")
    assert "Could not resolve @nosuchuser" in reply
    assert db.get_links(ctx.conn) == {}


def test_link_refuses_a_jellyfin_user_that_does_not_exist(ctx):
    reply = say(ctx, "/link 111 nosuchjellyfinuser")
    assert "No Jellyfin user named 'nosuchjellyfinuser'" in reply
    assert db.get_links(ctx.conn) == {}


def test_link_accepts_a_jellyfin_username_in_the_wrong_case(ctx):
    assert say(ctx, "/link 111 EXAMPLEALPHA") == "Linked 111 to examplealpha."
    assert db.get_links(ctx.conn) == {"111": "examplealpha"}


def test_a_jellyfin_user_can_hold_several_telegram_ids(ctx):
    say(ctx, "/link 111 examplealpha")
    say(ctx, "/link 222 examplealpha")
    assert db.links_by_user(ctx.conn) == {"examplealpha": {"111", "222"}}


def test_relinking_an_id_says_what_it_replaced(ctx):
    say(ctx, "/link 111 examplealpha")
    assert say(ctx, "/link 111 examplebravo") == "Linked 111 to examplebravo (was examplealpha)."


@pytest.mark.parametrize("text", ["/link", "/link 111", "/link 111 examplealpha extra"])
def test_link_usage(ctx, text):
    assert say(ctx, text).startswith("Usage: /link")


def test_unlink(ctx):
    say(ctx, "/link 111 examplealpha")
    assert say(ctx, "/unlink 111") == "Unlinked 111 from examplealpha."
    assert db.get_links(ctx.conn) == {}


def test_unlink_an_id_that_is_not_linked(ctx):
    assert say(ctx, "/unlink 111") == "No link for 111."


def test_unlink_usage(ctx):
    assert say(ctx, "/unlink").startswith("Usage: /unlink")


# --- reports -------------------------------------------------------------

def test_links_when_there_are_none(ctx):
    assert "No links yet" in say(ctx, "/links")


def test_links_groups_by_jellyfin_user(ctx):
    say(ctx, "/link 111 examplealpha")
    say(ctx, "/link 222 examplealpha")
    say(ctx, "/link 333 examplebravo")
    reply = say(ctx, "/links")
    assert "2 linked Jellyfin users" in reply
    assert "examplealpha: 111, 222" in reply
    assert "examplebravo: 333" in reply


def test_unknown_lists_channel_members_with_no_link(ctx):
    say(ctx, "/link 111 examplealpha")
    reply = say(ctx, "/unknown")
    assert "1 unlinked channel members" in reply
    assert "777000111 - Example User - @exampleuser" in reply
    assert "Example One" not in reply  # 111 is linked, so it is not listed


def test_unknown_when_everyone_the_listing_can_see_is_linked(ctx):
    say(ctx, "/link 111 examplealpha")
    say(ctx, "/link 777000111 examplebravo")
    assert say(ctx, "/unknown").startswith("Every member the listing can see is linked.")


def test_unknown_says_how_much_of_the_channel_the_listing_saw(ctx):
    async def count():
        return 210

    ctx.subscriber_count = count
    reply = say(ctx, "/unknown")
    assert "The listing saw 2 of 210 subscribers" in reply
    assert "stops a broadcast listing at 200" in reply


def test_unknown_says_nothing_about_coverage_when_the_count_is_unavailable(ctx):
    async def count():
        return None

    ctx.subscriber_count = count
    reply = say(ctx, "/unknown")
    assert "The listing saw 2." in reply
    assert "subscribers" not in reply


def test_unknown_does_not_nag_when_the_listing_saw_everyone(ctx):
    async def count():
        return 2

    ctx.subscriber_count = count
    assert "stops a broadcast listing" not in say(ctx, "/unknown")


def test_unknown_says_so_when_the_member_list_is_untrustworthy(ctx):
    async def nothing():
        return None

    ctx.fetch_participants = nothing
    assert "THRESHOLD_ENTRIES" in say(ctx, "/unknown")


def test_unlinked_lists_enabled_jellyfin_users_with_no_link(ctx):
    say(ctx, "/link 111 examplealpha")
    reply = say(ctx, "/unlinked")
    assert "1 enabled Jellyfin users with no link" in reply
    assert "examplebravo" in reply
    assert "exampleadmin" not in reply  # administrators are out of scope


def test_unlinked_when_everyone_is_linked(ctx):
    say(ctx, "/link 111 examplealpha")
    say(ctx, "/link 222 examplebravo")
    assert say(ctx, "/unlinked") == "Every enabled Jellyfin user is linked."


def test_status_before_the_first_sync(ctx):
    reply = say(ctx, "/status")
    assert "Channel: ExampleChannel" in reply
    assert "Last sync: never" in reply
    assert "Dry run: on" in reply
    assert "Grace: 72h" in reply


def test_status_after_some_activity(ctx):
    say(ctx, "/link 111 examplealpha")
    say(ctx, "/link 222 examplealpha")
    db.set_last_sync(ctx.conn, 1_700_000_000)
    db.set_absent_since(ctx.conn, "examplebravo", NOW)
    db.set_disabled_by_us(ctx.conn, "examplecharlie", True)

    reply = say(ctx, "/status")
    assert "Links: 2 Telegram ids across 1 Jellyfin users" in reply
    assert "Last sync: 2023-11-14 22:13:20 UTC" in reply
    assert "Absent, inside the grace window: 1" in reply
    assert "Disabled by this service: 1" in reply


# --- /sync and /dryrun ---------------------------------------------------

def test_sync_runs_a_cycle_and_returns_its_summary(ctx):
    assert say(ctx, "/sync").startswith("Sync done.")


@pytest.mark.parametrize("text,expected", [("/dryrun off", False), ("/dryrun ON", True)])
def test_dryrun_is_persisted(ctx, text, expected):
    say(ctx, "/dryrun off" if expected else "/dryrun on")  # start from the other state
    reply = say(ctx, text)
    assert db.get_dry_run(ctx.conn, default=not expected) is expected
    assert ("Dry run is on" if expected else "Dry run is off") in reply


def test_dryrun_survives_a_restart(ctx, tmp_path):
    say(ctx, "/dryrun off")
    ctx.conn.close()
    reopened = db.connect(str(tmp_path / "sync.db"))
    db.migrate(reopened)
    assert db.get_dry_run(reopened, default=True) is False
    reopened.close()


@pytest.mark.parametrize("text", ["/dryrun", "/dryrun maybe", "/dryrun on off"])
def test_dryrun_usage_shows_the_current_state(ctx, text):
    assert say(ctx, text) == "Usage: /dryrun on|off (currently on)"


def test_help_lists_every_command(ctx):
    reply = say(ctx, "/help")
    for command in ["/link", "/unlink", "/links", "/unknown", "/unlinked", "/status", "/sync", "/dryrun"]:
        assert command in reply
    assert say(ctx, "/start") == reply


# --- reports longer than Telegram will accept -----------------------------

def test_a_short_report_is_one_message():
    assert bot.chunk_message("one line") == ["one line"]


def test_a_long_report_is_split_at_line_boundaries():
    lines = [f"{700000000 + index} - Example {index}" for index in range(400)]
    text = "\n".join(lines)
    assert len(text) > bot.MESSAGE_LIMIT

    parts = bot.chunk_message(text)

    assert len(parts) > 1
    assert all(len(part) <= bot.MESSAGE_LIMIT for part in parts)
    assert "\n".join(parts) == text  # nothing lost, nothing duplicated
    assert all(not part.startswith("\n") for part in parts)


def test_a_single_line_longer_than_the_limit_is_still_sent():
    text = "x" * (bot.MESSAGE_LIMIT * 2 + 5)
    parts = bot.chunk_message(text)
    assert all(len(part) <= bot.MESSAGE_LIMIT for part in parts)
    assert "".join(parts) == text


def test_an_oversized_line_after_a_normal_one_does_not_swallow_it():
    text = "short first line\n" + "x" * (bot.MESSAGE_LIMIT + 10)
    parts = bot.chunk_message(text)
    assert parts[0] == "short first line"
    assert all(len(part) <= bot.MESSAGE_LIMIT for part in parts)
    assert "".join(parts[1:]) == "x" * (bot.MESSAGE_LIMIT + 10)


def test_the_owner_receives_every_part_of_a_long_report(ctx):
    for index in range(400):
        db.add_link(ctx.conn, str(700000000 + index), f"exampleuser{index}")
    event = FakeEvent("/links")

    asyncio.run(bot.on_message(ctx, event))

    assert len(event.replies) > 1
    assert all(len(reply) <= bot.MESSAGE_LIMIT for reply in event.replies)
    assert "exampleuser399" in "\n".join(event.replies)


# --- the second rule, from the owner's chat ------------------------------

def stale_users(days=400):
    from app.jellyfin import JellyfinUser

    now = int(time.time())
    return {
        "examplealpha": JellyfinUser("examplealpha", "jf-a", False, False, now - days * 86400),
        "examplebravo": JellyfinUser("examplebravo", "jf-b", False, False, now - 10),
        "exampleblank": JellyfinUser("exampleblank", "jf-c", False, False, None),
        "exampleadmin": JellyfinUser("exampleadmin", "jf-admin", False, True, now - days * 86400),
    }


def test_inactive_lists_the_accounts_past_the_threshold(ctx):
    ctx.jellyfin.users = stale_users()
    reply = say(ctx, "/inactive")
    assert "1 enabled accounts unused for 365 days or more" in reply
    assert "examplealpha - last used" in reply
    assert "examplebravo" not in reply
    assert "exampleadmin" not in reply
    assert "1 enabled accounts have no recorded use" in reply


def test_inactive_when_everybody_is_recent(ctx):
    from app.jellyfin import JellyfinUser

    ctx.jellyfin.users = {
        "examplealpha": JellyfinUser("examplealpha", "jf-a", False, False, int(time.time()))
    }
    assert say(ctx, "/inactive") == "No enabled account is more than 365 days unused."


def test_inactive_respects_the_exemptions(ctx):
    ctx.jellyfin.users = stale_users()
    ctx.config.exempt_users = frozenset({"examplealpha"})
    assert say(ctx, "/inactive").startswith("No enabled account is more than 365 days unused.")


def test_inactive_says_so_when_the_rule_is_off(ctx):
    ctx.config.inactive_days = 0
    assert say(ctx, "/inactive") == "The inactivity rule is off. Set INACTIVE_DAYS to switch it on."


def test_status_reports_the_inactivity_numbers(ctx):
    db.set_setting(ctx.conn, "last_inactive", "3")
    db.set_setting(ctx.conn, "last_no_record", "7")
    reply = say(ctx, "/status")
    assert "Inactivity rule: 365 days, 3 past the threshold, 7 with no recorded use" in reply


def test_status_says_when_the_inactivity_rule_is_off(ctx):
    ctx.config.inactive_days = 0
    assert "Inactivity rule: off" in say(ctx, "/status")


def test_help_mentions_the_new_command(ctx):
    assert "/inactive" in say(ctx, "/help")
