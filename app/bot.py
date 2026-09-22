"""Owner-only bot commands.

Every command is answered in the owner's private chat with the bot. Messages
from anyone else, and messages in any group or channel, are ignored without a
reply -- the bot has no business posting anywhere but that one chat.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from telethon import events

from . import db, sync

HELP = """Commands (owner only):
/link <telegram_id|@username> <jellyfin_user> - link a Telegram account to a Jellyfin user
/unlink <telegram_id> - remove one link
/links - every link, grouped by Jellyfin user
/unknown - channel members with no link
/unlinked - enabled Jellyfin users with no link
/inactive - enabled accounts nobody has used since the threshold
/status - last sync, counts, dry-run state
/sync - run a sync cycle now
/dryrun on|off - whether changes are really applied
/help - this message"""


@dataclass
class BotContext:
    conn: object
    jellyfin: object
    config: object
    resolve_telegram_id: Callable[[str], Awaitable[int]]
    fetch_participants: Callable[[], Awaitable[dict | None]]
    run_cycle: Callable[[], Awaitable[str]]
    check_presence: Callable[[set], Awaitable[dict]] = None
    subscriber_count: Callable[[], Awaitable[int | None]] = None
    channel_title: str = "the channel"
    notify: Callable[[str], Awaitable[None]] = field(default=None)


def parse_command(text: str) -> tuple[str, list[str]] | None:
    """``"/link @someone alice"`` -> ``("link", ["@someone", "alice"])``."""
    if not text:
        return None
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None
    name = parts[0][1:].split("@", 1)[0].lower()
    if not name:
        return None
    return name, parts[1:]


# Telegram refuses a message longer than this.
MESSAGE_LIMIT = 4096


def chunk_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split a report into sendable pieces, at line boundaries where possible.

    `/unknown` on a channel with a few hundred unlinked members runs well past
    the limit, and Telegram rejects the whole message rather than truncating.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single line longer than the limit
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _format_timestamp(value: int | None) -> str:
    if value is None:
        return "never"
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _match_jellyfin_user(jellyfin_users: dict, wanted: str) -> str | None:
    if wanted in jellyfin_users:
        return wanted
    for name in jellyfin_users:
        if name.lower() == wanted.lower():
            return name
    return None


async def _cmd_link(ctx: BotContext, args: list[str]) -> str:
    if len(args) != 2:
        return "Usage: /link <telegram_id|@username> <jellyfin_user>"
    handle, wanted = args
    try:
        telegram_id = await ctx.resolve_telegram_id(handle)
    except Exception as error:
        return f"Could not resolve {handle}: {error}"

    jellyfin_users = await asyncio.to_thread(ctx.jellyfin.users_by_name)
    jellyfin_user = _match_jellyfin_user(jellyfin_users, wanted)
    if jellyfin_user is None:
        return f"No Jellyfin user named {wanted!r}. Check /unlinked."

    previous = db.get_links(ctx.conn).get(str(telegram_id))
    db.add_link(ctx.conn, str(telegram_id), jellyfin_user)
    db.record_audit(ctx.conn, "link", jellyfin_user, f"telegram_id {telegram_id}")
    if previous and previous != jellyfin_user:
        return f"Linked {telegram_id} to {jellyfin_user} (was {previous})."
    return f"Linked {telegram_id} to {jellyfin_user}."


async def _cmd_unlink(ctx: BotContext, args: list[str]) -> str:
    if len(args) != 1:
        return "Usage: /unlink <telegram_id>"
    telegram_id = args[0].lstrip("@")
    removed = db.remove_link(ctx.conn, telegram_id)
    if removed is None:
        return f"No link for {telegram_id}."
    db.record_audit(ctx.conn, "unlink", removed, f"telegram_id {telegram_id}")
    return f"Unlinked {telegram_id} from {removed}."


async def _cmd_links(ctx: BotContext, args: list[str]) -> str:
    grouped = db.links_by_user(ctx.conn)
    if not grouped:
        return "No links yet. Use /unknown to see who is in the channel."
    lines = [
        f"{jellyfin_user}: {', '.join(sorted(ids))}"
        for jellyfin_user, ids in sorted(grouped.items())
    ]
    return f"{len(grouped)} linked Jellyfin users:\n" + "\n".join(lines)


async def _cmd_unknown(ctx: BotContext, args: list[str]) -> str:
    participants = await ctx.fetch_participants()
    if participants is None:
        return "The member list came back below THRESHOLD_ENTRIES, so it is not trustworthy. Try again later."

    total = await ctx.subscriber_count() if ctx.subscriber_count else None
    seen = len(participants)
    coverage = f"The listing saw {seen}"
    if total is not None:
        coverage += f" of {total} subscribers"
        if seen < total:
            coverage += (
                ". Telegram stops a broadcast listing at 200, so the rest cannot be listed;"
                " link those people by id or @username"
            )
    coverage += "."

    unknown = sync.unknown_participants(db.get_links(ctx.conn), participants)
    if not unknown:
        return f"Every member the listing can see is linked. {coverage}"
    lines = [
        f"{member['id']} - {member['name'] or 'no name'}"
        + (f" - @{member['username']}" if member["username"] else "")
        for member in unknown
    ]
    return f"{len(unknown)} unlinked channel members. {coverage}\n" + "\n".join(lines)


async def _cmd_unlinked(ctx: BotContext, args: list[str]) -> str:
    jellyfin_users = await asyncio.to_thread(ctx.jellyfin.users_by_name)
    names = sync.unlinked_jellyfin_users(db.links_by_user(ctx.conn), jellyfin_users)
    if not names:
        return "Every enabled Jellyfin user is linked."
    return f"{len(names)} enabled Jellyfin users with no link:\n" + "\n".join(names)


async def _cmd_inactive(ctx: BotContext, args: list[str]) -> str:
    if not ctx.config.inactive_seconds:
        return "The inactivity rule is off. Set INACTIVE_DAYS to switch it on."
    jellyfin_users = await asyncio.to_thread(ctx.jellyfin.users_by_name)
    past, no_record = sync.inactive_users(
        jellyfin_users, int(time.time()), ctx.config.inactive_seconds, ctx.config.exempt_users
    )
    tail = (
        f"\n{no_record} enabled accounts have no recorded use, so this rule leaves them alone."
        if no_record
        else ""
    )
    if not past:
        return f"No enabled account is more than {ctx.config.inactive_days} days unused.{tail}"
    lines = [f"{name} - last used {when}" for name, when in past]
    return (
        f"{len(past)} enabled accounts unused for {ctx.config.inactive_days} days or more:\n"
        + "\n".join(lines)
        + tail
    )


async def _cmd_status(ctx: BotContext, args: list[str]) -> str:
    links = db.get_links(ctx.conn)
    grouped = db.links_by_user(ctx.conn)
    states = db.get_states(ctx.conn)
    owned = [name for name, state in states.items() if state.disabled_by_us]
    waiting = [name for name, state in states.items() if state.absent_since is not None]
    dry_run = db.get_dry_run(ctx.conn, ctx.config.dry_run)
    return "\n".join(
        [
            f"Channel: {ctx.channel_title}",
            f"Last sync: {_format_timestamp(db.get_last_sync(ctx.conn))}",
            f"Links: {len(links)} Telegram ids across {len(grouped)} Jellyfin users",
            f"Absent, inside the grace window: {len(waiting)}",
            f"Unanswered membership lookups last cycle: {db.get_setting(ctx.conn, 'last_unknown', '0')}",
            _inactivity_line(ctx),
            f"Disabled by this service: {len(owned)}",
            f"Dry run: {'on' if dry_run else 'off'}",
            f"Grace: {ctx.config.grace_hours}h, interval: {ctx.config.interval}s,"
            f" threshold: {ctx.config.threshold_entries}",
        ]
    )


def _inactivity_line(ctx: BotContext) -> str:
    if not ctx.config.inactive_seconds:
        return "Inactivity rule: off"
    return (
        f"Inactivity rule: {ctx.config.inactive_days} days, "
        f"{db.get_setting(ctx.conn, 'last_inactive', '0')} past the threshold, "
        f"{db.get_setting(ctx.conn, 'last_no_record', '0')} with no recorded use"
    )


async def _cmd_sync(ctx: BotContext, args: list[str]) -> str:
    return await ctx.run_cycle()


async def _cmd_dryrun(ctx: BotContext, args: list[str]) -> str:
    if len(args) != 1 or args[0].lower() not in ("on", "off"):
        current = db.get_dry_run(ctx.conn, ctx.config.dry_run)
        return f"Usage: /dryrun on|off (currently {'on' if current else 'off'})"
    value = args[0].lower() == "on"
    db.set_dry_run(ctx.conn, value)
    db.record_audit(ctx.conn, "dryrun", None, "on" if value else "off")
    if value:
        return "Dry run is on. Nothing will be changed in Jellyfin."
    return "Dry run is off. Accounts will really be enabled and disabled."


async def _cmd_help(ctx: BotContext, args: list[str]) -> str:
    return HELP


COMMANDS = {
    "link": _cmd_link,
    "unlink": _cmd_unlink,
    "links": _cmd_links,
    "unknown": _cmd_unknown,
    "unlinked": _cmd_unlinked,
    "inactive": _cmd_inactive,
    "status": _cmd_status,
    "sync": _cmd_sync,
    "dryrun": _cmd_dryrun,
    "help": _cmd_help,
    "start": _cmd_help,
}


async def handle_command(ctx: BotContext, text: str) -> str | None:
    parsed = parse_command(text)
    if parsed is None:
        return None
    name, args = parsed
    handler = COMMANDS.get(name)
    if handler is None:
        return f"Unknown command /{name}.\n\n{HELP}"
    return await handler(ctx, args)


async def on_message(ctx: BotContext, event) -> str | None:
    """Answer the owner in private. Everyone else gets silence."""
    if not getattr(event, "is_private", False):
        return None
    if getattr(event, "sender_id", None) != ctx.config.owner_id:
        return None
    reply = await handle_command(ctx, getattr(event, "raw_text", "") or "")
    if reply is None:
        return None
    for part in chunk_message(reply):
        await event.reply(part)
    return reply


def register(bot, ctx: BotContext) -> None:
    @bot.on(events.NewMessage)
    async def _handler(event):  # pragma: no cover - thin Telethon wiring
        await on_message(ctx, event)
