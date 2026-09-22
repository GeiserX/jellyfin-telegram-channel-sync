"""Telegram access: the user session lists the channel, the bot talks to the
owner.

Two separate Telethon clients and two separate session files.

The listing here is best effort and is only used to answer `/unknown`. Telegram
stops a broadcast channel listing at 200 members however you ask, so on a larger
channel it cannot see everybody. Who actually still has access is decided per
person in `app/membership.py`, never from this listing.
"""

from __future__ import annotations

import asyncio
import logging

from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest

log = logging.getLogger(__name__)


class TelegramNotAuthorized(Exception):
    """The user session file is missing or no longer valid."""


def build_user_client(config) -> TelegramClient:
    return TelegramClient(config.user_session, config.api_id, config.api_hash)


def build_bot_client(config) -> TelegramClient:
    return TelegramClient(config.bot_session, config.api_id, config.api_hash)


async def ensure_authorized(client) -> None:
    if not await client.is_user_authorized():
        raise TelegramNotAuthorized(
            "The Telegram user session is not authorized. Run `python -m app.login` once."
        )


def describe(user) -> dict:
    """The fields the owner needs to recognise a member."""
    first = getattr(user, "first_name", None) or ""
    last = getattr(user, "last_name", None) or ""
    return {
        "username": getattr(user, "username", None) or "",
        "name": f"{first} {last}".strip(),
    }


async def channel_title(client, channel) -> str:
    entity = await client.get_entity(channel)
    return getattr(entity, "title", None) or str(channel)


# One search per letter widens the listing past the 200 Telegram will return
# for a plain request. Accented letters are in here because member names are.
SEARCH_LETTERS = "abcdefghijklmnopqrstuvwxyz\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1"


async def participants_count(client, channel) -> int | None:
    """How many subscribers the channel says it has, listing limits aside."""
    try:
        full = await client(GetFullChannelRequest(channel))
        return int(full.full_chat.participants_count)
    except Exception as error:
        log.warning("Could not read the channel's subscriber count: %s", error)
        return None


async def fetch_participants(
    client, channel, threshold: int, delay: float = 0.2
) -> dict[str, dict] | None:
    """As many members as Telegram will show, or ``None`` if it showed too few.

    A plain listing of a broadcast channel stops at 200, so this unions it with
    one search per letter. The result is still best effort: use it to find
    people to link, never to decide that somebody left.

    Returning ``None`` rather than a short list is the guardrail, so a bad
    answer cannot be mistaken for a complete one.
    """
    members = {
        str(user.id): describe(user) for user in await client.get_participants(channel)
    }
    for index, letter in enumerate(SEARCH_LETTERS):
        if index and delay:
            await asyncio.sleep(delay)
        try:
            found = await client.get_participants(channel, search=letter)
        except Exception as error:
            log.warning("Member search for %r failed: %s", letter, error)
            continue
        for user in found:
            members.setdefault(str(user.id), describe(user))
    if len(members) < threshold:
        log.warning(
            "The listing saw %d channel members, below the threshold of %d",
            len(members),
            threshold,
        )
        return None
    log.info("The listing saw %d channel members", len(members))
    return members


async def resolve_telegram_id(client, handle: str) -> int:
    """Turn ``@username`` (or a numeric id) into a numeric Telegram id."""
    handle = handle.strip()
    if handle.lstrip("-").isdigit():
        return int(handle)
    entity = await client.get_entity(handle.lstrip("@"))
    return int(entity.id)


async def send_owner(bot, owner_id: int, text: str) -> None:
    await bot.send_message(owner_id, text)
