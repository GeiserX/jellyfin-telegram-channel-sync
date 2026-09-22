"""Asking the admin bot whether one person is still in the channel.

Every answer that is not a plain yes or no has to come back UNKNOWN, because
UNKNOWN is the only verdict that cannot cost somebody their access.
"""

import json

import pytest

from app import sync
from app.membership import MembershipChecker

CHAT = -1001234567890


class FakeResponse:
    def __init__(self, payload=None, status_code=200, body=None):
        self._payload = payload
        self._body = body
        self.status_code = status_code

    def json(self):
        if self._body is not None:
            return json.loads(self._body)  # raises the way requests does
        return self._payload


class FakeSession:
    def __init__(self, responses=None, raises=None):
        self.responses = responses or {}
        self.raises = raises
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self.raises:
            raise self.raises
        key = str(params["user_id"])
        return self.responses.get(key, FakeResponse({"ok": True, "result": {"status": "member"}}))


def checker(session, delay=0):
    return MembershipChecker("12345:token", CHAT, session=session, delay=delay)


def ok(status):
    return FakeResponse({"ok": True, "result": {"status": status}})


# --- the statuses Telegram documents -------------------------------------

@pytest.mark.parametrize("status", ["creator", "administrator", "member", "restricted"])
def test_a_status_that_means_still_in_the_channel(status):
    assert checker(FakeSession({"111": ok(status)})).status("111") == sync.PRESENT


@pytest.mark.parametrize("status", ["left", "kicked"])
def test_a_status_that_means_gone(status):
    assert checker(FakeSession({"111": ok(status)})).status("111") == sync.ABSENT


@pytest.mark.parametrize("status", ["banned", "", None, "something_new"])
def test_an_unrecognised_status_is_unknown(status):
    assert checker(FakeSession({"111": ok(status)})).status("111") == sync.UNKNOWN


# --- everything that can go wrong ----------------------------------------

@pytest.mark.parametrize("code", [400, 401, 403, 429, 500, 502])
def test_an_http_error_is_unknown(code):
    session = FakeSession({"111": FakeResponse({"ok": False}, status_code=code)})
    assert checker(session).status("111") == sync.UNKNOWN


def test_a_timeout_is_unknown():
    session = FakeSession(raises=TimeoutError("timed out"))
    assert checker(session).status("111") == sync.UNKNOWN


def test_a_connection_error_is_unknown():
    session = FakeSession(raises=ConnectionError("no route to host"))
    assert checker(session).status("111") == sync.UNKNOWN


def test_a_malformed_body_is_unknown():
    session = FakeSession({"111": FakeResponse(body="not json at all")})
    assert checker(session).status("111") == sync.UNKNOWN


def test_a_refusal_from_telegram_is_unknown():
    # What a deleted account, or a bot that is not an administrator, looks like.
    session = FakeSession(
        {"111": FakeResponse({"ok": False, "description": "Bad Request: user not found"})}
    )
    assert checker(session).status("111") == sync.UNKNOWN


def test_a_body_that_is_not_an_object_is_unknown():
    assert checker(FakeSession({"111": FakeResponse(["surprise"])})).status("111") == sync.UNKNOWN


def test_a_result_without_a_status_is_unknown():
    session = FakeSession({"111": FakeResponse({"ok": True, "result": {}})})
    assert checker(session).status("111") == sync.UNKNOWN


def test_a_result_that_is_not_an_object_is_unknown():
    session = FakeSession({"111": FakeResponse({"ok": True, "result": "member"})})
    assert checker(session).status("111") == sync.UNKNOWN


# --- how the request is made ---------------------------------------------

def test_the_request_names_the_chat_and_the_user():
    session = FakeSession({"111": ok("member")})
    checker(session).status("111")
    call = session.calls[0]
    assert call["url"].endswith("/bot12345:token/getChatMember")
    assert call["params"] == {"chat_id": CHAT, "user_id": "111"}
    assert call["timeout"] == 15


def test_the_token_stays_out_of_the_query_string():
    session = FakeSession({"111": ok("member")})
    checker(session).status("111")
    assert "token" not in json.dumps(session.calls[0]["params"])


# --- a whole cycle's worth of lookups ------------------------------------

def test_every_linked_id_is_asked_about_exactly_once():
    session = FakeSession({"111": ok("member"), "222": ok("left"), "333": FakeResponse({}, 429)})

    presence = checker(session).check_all({"333", "111", "222"})

    assert presence == {"111": sync.PRESENT, "222": sync.ABSENT, "333": sync.UNKNOWN}
    assert len(session.calls) == 3
    assert [call["params"]["user_id"] for call in session.calls] == ["111", "222", "333"]


def test_no_linked_ids_means_no_requests():
    session = FakeSession()
    assert checker(session).check_all(set()) == {}
    assert session.calls == []


def test_the_lookups_are_paced(monkeypatch):
    slept = []
    monkeypatch.setattr("app.membership.time.sleep", lambda seconds: slept.append(seconds))
    session = FakeSession({"111": ok("member"), "222": ok("member"), "333": ok("member")})

    MembershipChecker("t", CHAT, session=session, delay=0.05).check_all({"111", "222", "333"})

    assert slept == [0.05, 0.05]  # between the calls, not before the first


def test_the_bot_token_never_reaches_the_log(caplog):
    # requests puts the request URL in its exception text, and the URL carries
    # the bot token.
    token = "12345:AAHsecrettokenvalue"
    session = FakeSession(
        raises=ConnectionError(f"HTTPSConnectionPool: /bot{token}/getChatMember failed")
    )
    with caplog.at_level("WARNING"):
        MembershipChecker(token, CHAT, session=session, delay=0).status("111")

    assert token not in caplog.text
    assert "<bot token>" in caplog.text


def test_a_malformed_body_error_is_redacted_too(caplog):
    token = "12345:AAHsecrettokenvalue"

    class Exploding(FakeResponse):
        def json(self):
            raise ValueError(f"no JSON from /bot{token}/getChatMember")

    with caplog.at_level("WARNING"):
        MembershipChecker(token, CHAT, session=FakeSession({"111": Exploding()}), delay=0).status("111")

    assert token not in caplog.text
