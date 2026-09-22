"""Telegram access: the user session enumerates the channel, the bot talks
to the owner.

Two separate Telethon clients and two separate session files. A bot account
cannot list the members of a broadcast channel, which is why the user session
stays the enumeration path. The bot never writes anywhere except the owner's
private chat.
"""

from __future__ import annotations

import logging

from telethon import TelegramClient

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


async def fetch_participants(client, channel, threshold: int) -> dict[str, dict] | None:
    """Every member of the channel, or ``None`` if the fetch looks wrong.

    Returning ``None`` (rather than a short list) is the guardrail: a partial
    answer from Telegram must never be read as "everybody left".
    """
    participants = await client.get_participants(channel, aggressive=True)
    members = {str(user.id): describe(user) for user in participants}
    if len(members) < threshold:
        log.warning(
            "Fetched %d channel members, below the threshold of %d; skipping this cycle",
            len(members),
            threshold,
        )
        return None
    log.info("Fetched %d channel members", len(members))
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
