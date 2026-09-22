"""Minimal Jellyfin admin client.

The only write this service performs is flipping ``IsDisabled`` on a user
policy, and it does so by reading the whole policy back first -- see
:meth:`JellyfinClient.set_user_disabled`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JellyfinUser:
    name: str
    id: str
    disabled: bool
    is_administrator: bool


class JellyfinError(Exception):
    pass


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
