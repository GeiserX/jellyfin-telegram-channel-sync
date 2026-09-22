"""Per-person membership, asked of the channel's admin bot.

Telegram caps the member listing of a broadcast channel at 200, whatever you
ask for: `get_participants()`, `aggressive=True`, `iter_participants(limit=None)`
and a raw `ChannelParticipantsRecent` request all stop there, and the count the
server reports stops there too. On a channel with more subscribers than that,
"not in the listing" does not mean "left", so the listing can never decide who
loses access.

The Bot API answers per user instead. `getChatMember` works for any user id as
long as the bot is an administrator of the channel, with no entity cache and no
listing involved.
"""

from __future__ import annotations

import logging
import time

import requests

from .sync import ABSENT, PRESENT, UNKNOWN

log = logging.getLogger(__name__)

# Statuses that mean the account still has access.
IN_CHANNEL = frozenset({"creator", "administrator", "member", "restricted"})
# Statuses that mean it does not. Anything else is UNKNOWN, never absent.
OUT_OF_CHANNEL = frozenset({"left", "kicked"})

API_ROOT = "https://api.telegram.org"


class MembershipChecker:
    def __init__(self, bot_token, chat_id, session=None, timeout=15, delay=0.05, api_root=API_ROOT):
        self._url = f"{api_root}/bot{bot_token}/getChatMember"
        self.chat_id = chat_id
        self.session = session or requests.Session()
        self.timeout = timeout
        self.delay = delay

    def status(self, telegram_id: str) -> str:
        """PRESENT, ABSENT, or UNKNOWN when the answer cannot be trusted.

        Every failure lands on UNKNOWN on purpose. A timeout, a 429, a deleted
        account and a status Telegram adds next year all mean "we do not know",
        and not knowing must never cost somebody their access.
        """
        try:
            response = self.session.get(
                self._url,
                params={"chat_id": self.chat_id, "user_id": telegram_id},
                timeout=self.timeout,
            )
        except Exception as error:
            log.warning("Membership lookup for %s failed: %s", telegram_id, error)
            return UNKNOWN

        if response.status_code != 200:
            log.warning(
                "Membership lookup for %s returned HTTP %s", telegram_id, response.status_code
            )
            return UNKNOWN

        try:
            payload = response.json()
        except Exception as error:
            log.warning("Membership lookup for %s returned no JSON: %s", telegram_id, error)
            return UNKNOWN

        if not isinstance(payload, dict) or not payload.get("ok"):
            log.warning(
                "Membership lookup for %s was refused: %s",
                telegram_id,
                (payload or {}).get("description") if isinstance(payload, dict) else payload,
            )
            return UNKNOWN

        result = payload.get("result")
        status = result.get("status") if isinstance(result, dict) else None
        if status in IN_CHANNEL:
            return PRESENT
        if status in OUT_OF_CHANNEL:
            return ABSENT
        log.warning("Membership lookup for %s returned status %r", telegram_id, status)
        return UNKNOWN

    def check_all(self, telegram_ids) -> dict[str, str]:
        """One lookup per linked id, gently paced."""
        presence: dict[str, str] = {}
        for index, telegram_id in enumerate(sorted(telegram_ids)):
            if index and self.delay:
                time.sleep(self.delay)
            presence[str(telegram_id)] = self.status(str(telegram_id))
        unknown = sum(1 for state in presence.values() if state == UNKNOWN)
        log.info(
            "Checked %d linked accounts: %d present, %d absent, %d unknown",
            len(presence),
            sum(1 for state in presence.values() if state == PRESENT),
            sum(1 for state in presence.values() if state == ABSENT),
            unknown,
        )
        return presence
