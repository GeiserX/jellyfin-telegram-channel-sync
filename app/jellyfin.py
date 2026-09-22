"""Minimal Jellyfin admin client.

The only write this service performs is flipping ``IsDisabled`` on a user
policy, and it does so by reading the whole policy back first -- see
:meth:`JellyfinClient.set_user_disabled`.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)


# Jellyfin sends .NET timestamps: seven fractional digits and a Z, which
# datetime.fromisoformat will not take.
_FRACTION = re.compile(r"\.(\d{1,})")


def parse_date(value) -> int | None:
    """A Jellyfin timestamp as epoch seconds, or None if there is not one.

    Returning None matters: a user with no recorded activity must never be
    read as a user who was last active in 1970.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    text = _FRACTION.sub(lambda match: "." + match.group(1)[:6], text)
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        log.warning("Could not read the Jellyfin timestamp %r", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp())


@dataclass(frozen=True)
class JellyfinUser:
    name: str
    id: str
    disabled: bool
    is_administrator: bool
    last_used: int | None = None

    @property
    def last_used_date(self) -> str:
        """The day the account was last used, for a message to a person."""
        if self.last_used is None:
            return "never"
        return dt.datetime.fromtimestamp(self.last_used, dt.timezone.utc).strftime("%Y-%m-%d")


class JellyfinError(Exception):
    pass


def _last_used(user: dict) -> int | None:
    """The more recent of the two timestamps Jellyfin keeps, if either exists."""
    stamps = [
        parse_date(user.get("LastActivityDate")),
        parse_date(user.get("LastLoginDate")),
    ]
    known = [stamp for stamp in stamps if stamp is not None]
    return max(known) if known else None


class JellyfinClient:
    def __init__(self, base_url: str, api_key: str, session=None, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        if self.base_url.startswith("http://"):
            # Not refused: the documented deployment reaches Jellyfin over a
            # private Docker network. Over anything wider, the API key is
            # readable by anyone on the path.
            log.warning(
                "JELLYFIN_URL is http://, so the Jellyfin API key travels in cleartext. "
                "Use https:// if this connection leaves a trusted network."
            )
        # Jellyfin 12 rejects X-Emby-Token and X-MediaBrowser-Token with 401.
        # The Authorization scheme is the one it still accepts, quoting included.
        self.headers = {"Authorization": f'MediaBrowser Token="{api_key}"'}
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str):
        response = self.session.get(
            f"{self.base_url}{path}", headers=self.headers, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def list_users(self) -> list[JellyfinUser]:
        return [
            JellyfinUser(
                name=user["Name"],
                id=user["Id"],
                disabled=bool(user.get("Policy", {}).get("IsDisabled", False)),
                is_administrator=bool(user.get("Policy", {}).get("IsAdministrator", False)),
                last_used=_last_used(user),
            )
            for user in self._get("/Users")
        ]

    def users_by_name(self) -> dict[str, JellyfinUser]:
        return {user.name: user for user in self.list_users()}

    def get_policy(self, user_id: str) -> dict:
        user = self._get(f"/Users/{user_id}")
        policy = user.get("Policy")
        if not isinstance(policy, dict):
            raise JellyfinError(f"Jellyfin user {user_id} returned no policy object")
        return policy

    def set_user_disabled(self, user_id: str, disabled: bool) -> dict:
        """Flip ``IsDisabled`` while preserving every other policy field.

        ``POST /Users/{id}/Policy`` replaces the *entire* policy: anything the
        request body leaves out comes back as the type default. Posting just
        ``{"IsDisabled": ...}`` therefore wipes the admin flag, library access,
        session limits and the rest. So read the current policy, change the one
        field, and post the whole object back.
        """
        policy = dict(self.get_policy(user_id))
        policy["IsDisabled"] = bool(disabled)
        response = self.session.post(
            f"{self.base_url}/Users/{user_id}/Policy",
            headers=self.headers,
            json=policy,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise JellyfinError(
                f"Jellyfin rejected the policy update for {user_id}: "
                f"{response.status_code} {response.text}"
            )
        return policy
