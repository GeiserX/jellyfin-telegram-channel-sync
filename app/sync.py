"""The decision logic.

:func:`decide` is a pure function over plain data: links, the channel
participant set, current Jellyfin state and the clock in, a list of actions
out. Nothing in here talks to Telegram, Jellyfin or SQLite, which is what
makes the whole policy testable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from . import db
from .db import INACTIVE, LEFT_CHANNEL
from .jellyfin import JellyfinUser

DISABLE = "disable"
ENABLE = "enable"
MARK_ABSENT = "mark_absent"
CLEAR_ABSENT = "clear_absent"
RELEASE = "release"

# What we know about one linked Telegram account this cycle.
PRESENT = "present"
ABSENT = "absent"
UNKNOWN = "unknown"

# Bookkeeping runs before the Jellyfin writes, so that releasing a stale
# ownership claim cannot undo the claim a disable makes in the same cycle.
_ORDER = {RELEASE: 0, CLEAR_ABSENT: 1, MARK_ABSENT: 2, ENABLE: 3, DISABLE: 4}


@dataclass(frozen=True)
class Action:
    kind: str
    jellyfin_user: str
    user_id: str | None = None
    notify: str | None = None
    detail: str = ""
    reason: str | None = None
    last_used: int | None = None


def _sort_key(action: Action) -> tuple[str, int]:
    return (action.jellyfin_user, _ORDER[action.kind])


def presence_of(telegram_ids: set[str], presence: dict[str, str]) -> str:
    """One verdict for a person who may hold several Telegram accounts.

    Present if any one of their accounts is in the channel. Absent only if
    every one of them is explicitly out. If any answer is missing or
    untrusted, the verdict is UNKNOWN and nothing happens to them.
    """
    states = {presence.get(telegram_id, UNKNOWN) for telegram_id in telegram_ids}
    if PRESENT in states:
        return PRESENT
    if states and states == {ABSENT}:
        return ABSENT
    return UNKNOWN


def decide(
    links_by_user: dict[str, set[str]],
    presence: dict[str, str],
    jellyfin_users: dict[str, JellyfinUser],
    states: dict[str, db.UserState],
    now: int,
    grace_seconds: int,
    channel_title: str,
    dry_run: bool = False,
    inactive_seconds: int = 0,
    exempt: frozenset = frozenset(),
) -> list[Action]:
    """Decide what should happen this cycle.

    Two rules run. The channel rule disables a linked person who left, after
    the grace window. The inactivity rule disables anybody, linked or not, who
    has not used Jellyfin in ``inactive_seconds``. An account that trips both
    in one cycle is disabled once, as having left the channel, because that
    rule was already counting the days.

    ``presence`` maps a linked Telegram id to PRESENT, ABSENT or UNKNOWN. An
    id nobody answered for is UNKNOWN, and an UNKNOWN never costs anyone
    their access. ``inactive_seconds`` of 0 switches the inactivity rule off.
    """
    actions: list[Action] = []

    for jellyfin_user, telegram_ids in links_by_user.items():
        user = jellyfin_users.get(jellyfin_user)
        if user is None:
            # Linked to a Jellyfin account that no longer exists.
            continue
        if _off_limits(user, exempt):
            continue

        state = states.get(jellyfin_user, db.UserState())
        verdict = presence_of(telegram_ids, presence)

        if verdict == UNKNOWN:
            # Telegram did not give us a trustworthy answer for this person.
            continue

        if verdict == PRESENT:
            if state.absent_since is not None:
                actions.append(
                    Action(CLEAR_ABSENT, jellyfin_user, user.id, detail="back in the channel")
                )
            if user.disabled and state.disabled_by_us and state.disabled_reason != INACTIVE:
                # An account disabled for inactivity is not revived by walking
                # back into the channel. That one waits for a human.
                verb = "would be re-enabled" if dry_run else "re-enabled"
                actions.append(
                    Action(
                        ENABLE,
                        jellyfin_user,
                        user.id,
                        notify=f"{jellyfin_user} rejoined {channel_title}, account {verb}",
                        detail="present in the channel",
                    )
                )
            continue

        # Absent from the channel.
        if user.disabled:
            # Already disabled, by us or by an administrator: leave it alone.
            continue
        if state.absent_since is None:
            actions.append(
                Action(MARK_ABSENT, jellyfin_user, user.id, detail=f"first seen absent at {now}")
            )
            continue
        if now - state.absent_since >= grace_seconds:
            verb = "would be disabled" if dry_run else "disabled"
            actions.append(
                Action(
                    DISABLE,
                    jellyfin_user,
                    user.id,
                    reason=LEFT_CHANNEL,
                    notify=f"{jellyfin_user} left {channel_title}, account {verb}",
                    detail=f"absent since {state.absent_since}, grace {grace_seconds}s elapsed",
                )
            )

    disabled_this_cycle = {
        action.jellyfin_user for action in actions if action.kind == DISABLE
    }

    for jellyfin_user, user in jellyfin_users.items():
        if _off_limits(user, exempt):
            continue
        state = states.get(jellyfin_user, db.UserState())

        if state.disabled_by_us and not user.disabled:
            # Somebody re-enabled the account by hand. Stop claiming it, or a
            # later human disable would be undone on the next cycle.
            actions.append(
                Action(RELEASE, jellyfin_user, user.id, detail="re-enabled outside this service")
            )

        if not inactive_seconds or user.disabled or jellyfin_user in disabled_this_cycle:
            continue
        if user.last_used is None:
            # Jellyfin has no record of this account ever being used. That is
            # missing data, not evidence of a year of silence.
            continue
        if state.inactive_mark is not None and user.last_used <= state.inactive_mark:
            # Already acted on this exact last-used date. Somebody re-enabled
            # the account afterwards, and that decision stands until the
            # account is actually used again.
            continue
        if now - user.last_used >= inactive_seconds:
            verb = "would be disabled" if dry_run else "disabled"
            actions.append(
                Action(
                    DISABLE,
                    jellyfin_user,
                    user.id,
                    reason=INACTIVE,
                    last_used=user.last_used,
                    notify=(
                        f"{jellyfin_user} has not used Jellyfin since "
                        f"{user.last_used_date}, account {verb}"
                    ),
                    detail=f"last used {user.last_used_date}, unused for {inactive_seconds}s",
                )
            )

    return sorted(actions, key=_sort_key)


def _off_limits(user: JellyfinUser, exempt: frozenset) -> bool:
    """Administrators and named exemptions are never touched by either rule."""
    return user.is_administrator or user.name.lower() in exempt


def inactive_users(
    jellyfin_users: dict[str, JellyfinUser],
    now: int,
    inactive_seconds: int,
    exempt: frozenset = frozenset(),
) -> tuple[list[tuple[str, str]], int]:
    """Enabled accounts past the threshold, and how many have no recorded use.

    The count matters as much as the list: it says how much of the library
    this rule simply cannot judge.
    """
    past: list[tuple[str, str]] = []
    unknown = 0
    for name, user in sorted(jellyfin_users.items()):
        if _off_limits(user, exempt) or user.disabled:
            continue
        if user.last_used is None:
            unknown += 1
            continue
        if inactive_seconds and now - user.last_used >= inactive_seconds:
            past.append((name, user.last_used_date))
    return past, unknown


def unknown_participants(
    links: dict[str, str], participants_info: dict[str, dict]
) -> list[dict]:
    """Channel members with no link, newest id last."""
    return [
        {"id": telegram_id, **info}
        for telegram_id, info in sorted(participants_info.items())
        if telegram_id not in links
    ]


def unlinked_jellyfin_users(
    links_by_user: dict[str, set[str]], jellyfin_users: dict[str, JellyfinUser]
) -> list[str]:
    """Enabled, non-administrator Jellyfin accounts nobody is linked to."""
    return sorted(
        name
        for name, user in jellyfin_users.items()
        if not user.is_administrator and not user.disabled and name not in links_by_user
    )


async def apply_action(conn, jellyfin_client, action: Action, dry_run: bool, now: int) -> None:
    """Persist and perform one action. In dry run no Jellyfin account changes.

    Absence bookkeeping still runs in dry run, so the grace clock is real and
    the "would be disabled" message arrives when it really would have.

    The Jellyfin calls are synchronous `requests`, so they go to a worker
    thread. The SQLite connection stays on the calling thread, which owns it.
    """
    if action.kind == MARK_ABSENT:
        db.set_absent_since(conn, action.jellyfin_user, now)
    elif action.kind == CLEAR_ABSENT:
        db.set_absent_since(conn, action.jellyfin_user, None)
    elif action.kind == RELEASE:
        db.set_disabled_by_us(conn, action.jellyfin_user, False)
    elif action.kind == DISABLE:
        if not dry_run:
            # Claim ownership first. If the write to Jellyfin succeeds but the
            # database write does not, the account would be disabled with
            # nobody owning it, and this service could never re-enable it.
            # Claiming first fails the harmless way instead: a claim on an
            # enabled account, which the next cycle releases.
            db.set_disabled_by_us(conn, action.jellyfin_user, True, action.reason)
            if action.reason == INACTIVE:
                db.set_inactive_mark(conn, action.jellyfin_user, action.last_used)
            try:
                await asyncio.to_thread(jellyfin_client.set_user_disabled, action.user_id, True)
            except Exception:
                db.set_disabled_by_us(conn, action.jellyfin_user, False)
                if action.reason == INACTIVE:
                    # Otherwise the rule would never try this account again.
                    db.set_inactive_mark(conn, action.jellyfin_user, None)
                raise
    elif action.kind == ENABLE:
        if not dry_run:
            await asyncio.to_thread(jellyfin_client.set_user_disabled, action.user_id, False)
            db.set_disabled_by_us(conn, action.jellyfin_user, False)
            db.set_absent_since(conn, action.jellyfin_user, None)
    else:
        raise ValueError(f"unknown action {action.kind!r}")

    db.record_audit(
        conn,
        action.kind,
        action.jellyfin_user,
        action.detail,
        dry_run=dry_run and action.kind in (DISABLE, ENABLE),
        now=now,
    )
