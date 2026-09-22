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
from .jellyfin import JellyfinUser

DISABLE = "disable"
ENABLE = "enable"
MARK_ABSENT = "mark_absent"
CLEAR_ABSENT = "clear_absent"
RELEASE = "release"

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


def _sort_key(action: Action) -> tuple[str, int]:
    return (action.jellyfin_user, _ORDER[action.kind])


def decide(
    links_by_user: dict[str, set[str]],
    participants: set[str] | None,
    jellyfin_users: dict[str, JellyfinUser],
    states: dict[str, db.UserState],
    now: int,
    grace_seconds: int,
    channel_title: str,
    dry_run: bool = False,
) -> list[Action]:
    """Decide what should happen this cycle.

    ``participants`` is ``None`` when the member list could not be trusted
    (the threshold guardrail tripped). In that case nothing happens at all --
    an unreliable fetch must never disable anybody.
    """
    if participants is None:
        return []

    actions: list[Action] = []
    for jellyfin_user, telegram_ids in links_by_user.items():
        user = jellyfin_users.get(jellyfin_user)
        if user is None:
            # Linked to a Jellyfin account that no longer exists.
            continue
        if user.is_administrator:
            # Administrators are never touched, linked or not.
            continue

        state = states.get(jellyfin_user, db.UserState())
        present = bool(telegram_ids & participants)

        if state.disabled_by_us and not user.disabled:
            # Somebody re-enabled the account by hand. Stop claiming it, or a
            # later human disable would be undone on the next cycle.
            actions.append(
                Action(RELEASE, jellyfin_user, user.id, detail="re-enabled outside this service")
            )

        if present:
            if state.absent_since is not None:
                actions.append(
                    Action(CLEAR_ABSENT, jellyfin_user, user.id, detail="back in the channel")
                )
            if user.disabled and state.disabled_by_us:
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
                    notify=f"{jellyfin_user} left {channel_title}, account {verb}",
                    detail=f"absent since {state.absent_since}, grace {grace_seconds}s elapsed",
                )
            )

    return sorted(actions, key=_sort_key)


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
            db.set_disabled_by_us(conn, action.jellyfin_user, True)
            try:
                await asyncio.to_thread(jellyfin_client.set_user_disabled, action.user_id, True)
            except Exception:
                db.set_disabled_by_us(conn, action.jellyfin_user, False)
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
