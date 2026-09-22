"""The Telegram side: the threshold guardrail, and turning members into rows."""

import asyncio
from types import SimpleNamespace

import pytest

from app import telegram


class FakeUser(SimpleNamespace):
    pass


class FakeClient:
    def __init__(self, participants=None, authorized=True, entity=None,
                 by_letter=None, search_error=None, full=None, full_error=None):
        self._participants = participants or []
        self._authorized = authorized
        self._entity = entity
        self._by_letter = by_letter or {}
        self._search_error = search_error
        self._full = full
        self._full_error = full_error
        self.searches = []
        self.sent = []

    async def get_participants(self, channel, aggressive=False, search=None):
        if search is None:
            return self._participants
        self.searches.append(search)
        if self._search_error:
            raise self._search_error
        return self._by_letter.get(search, [])

    async def __call__(self, request):
        if self._full_error:
            raise self._full_error
        return self._full

    async def is_user_authorized(self):
        return self._authorized

    async def get_entity(self, key):
        if self._entity is None:
            raise ValueError(f"Cannot find {key}")
        return self._entity

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


def members(count):
    return [
        FakeUser(id=1000 + index, username=f"example{index}", first_name="Example", last_name=str(index))
        for index in range(count)
    ]


def test_participants_are_keyed_by_string_id():
    client = FakeClient(members(3))
    result = asyncio.run(telegram.fetch_participants(client, -1001234567890, threshold=2, delay=0))
    assert set(result) == {"1000", "1001", "1002"}
    assert result["1000"] == {"username": "example0", "name": "Example 0"}


def test_a_short_member_list_returns_none_instead_of_a_short_list():
    client = FakeClient(members(3))
    assert asyncio.run(telegram.fetch_participants(client, -1001234567890, threshold=100, delay=0)) is None


def test_the_threshold_boundary_is_accepted():
    client = FakeClient(members(5))
    assert asyncio.run(telegram.fetch_participants(client, -1001234567890, threshold=5, delay=0)) is not None


def test_a_member_with_no_name_or_username_still_has_a_row():
    client = FakeClient([FakeUser(id=1, username=None, first_name=None, last_name=None)])
    result = asyncio.run(telegram.fetch_participants(client, -1001234567890, threshold=1, delay=0))
    assert result == {"1": {"username": "", "name": ""}}


def test_an_unauthorized_session_raises_a_message_that_says_what_to_run():
    client = FakeClient(authorized=False)
    with pytest.raises(telegram.TelegramNotAuthorized, match="app.login"):
        asyncio.run(telegram.ensure_authorized(client))


def test_an_authorized_session_passes():
    assert asyncio.run(telegram.ensure_authorized(FakeClient())) is None


def test_the_channel_title_comes_from_the_entity():
    client = FakeClient(entity=SimpleNamespace(title="ExampleChannel"))
    assert asyncio.run(telegram.channel_title(client, -1001234567890)) == "ExampleChannel"


def test_a_channel_without_a_title_falls_back_to_its_id():
    client = FakeClient(entity=SimpleNamespace())
    assert asyncio.run(telegram.channel_title(client, -1001234567890)) == "-1001234567890"


@pytest.mark.parametrize("handle", ["777000111", " 777000111 "])
def test_a_numeric_handle_needs_no_lookup(handle):
    client = FakeClient()  # get_entity would raise
    assert asyncio.run(telegram.resolve_telegram_id(client, handle)) == 777000111


@pytest.mark.parametrize("handle", ["@exampleuser", "exampleuser"])
def test_an_at_username_is_resolved(handle):
    client = FakeClient(entity=SimpleNamespace(id=777000111))
    assert asyncio.run(telegram.resolve_telegram_id(client, handle)) == 777000111


def test_an_unknown_username_raises():
    with pytest.raises(ValueError):
        asyncio.run(telegram.resolve_telegram_id(FakeClient(), "@nosuchuser"))


def test_the_owner_is_messaged_directly():
    bot = FakeClient()
    asyncio.run(telegram.send_owner(bot, 555000111, "hello"))
    assert bot.sent == [(555000111, "hello")]


def test_build_clients_use_two_different_session_files(tmp_path):
    config = SimpleNamespace(
        api_id=1,
        api_hash="h",
        user_session=str(tmp_path / "session_name"),
        bot_session=str(tmp_path / "bot_session"),
    )
    user_client = telegram.build_user_client(config)
    bot_client = telegram.build_bot_client(config)
    assert user_client.session.filename != bot_client.session.filename
    user_client.session.close()
    bot_client.session.close()


# --- the listing is best effort on a broadcast channel --------------------

def test_the_letter_searches_widen_the_listing_past_the_plain_request():
    # Telegram stops the plain listing at 200, so a search per letter is the
    # only way to see anybody beyond it.
    client = FakeClient(
        participants=members(3),
        by_letter={"z": [FakeUser(id=9001, username="zeta", first_name="Zeta", last_name="")]},
    )

    result = asyncio.run(
        telegram.fetch_participants(client, -1001234567890, threshold=1, delay=0)
    )

    assert "9001" in result
    assert set(result) == {"1000", "1001", "1002", "9001"}
    assert client.searches == list(telegram.SEARCH_LETTERS)


def test_accented_letters_are_searched_too():
    assert "ñ" in telegram.SEARCH_LETTERS
    assert "á" in telegram.SEARCH_LETTERS


def test_a_duplicate_from_a_search_is_not_counted_twice():
    client = FakeClient(participants=members(2), by_letter={"e": members(2)})
    result = asyncio.run(
        telegram.fetch_participants(client, -1001234567890, threshold=1, delay=0)
    )
    assert len(result) == 2


def test_one_failing_letter_search_does_not_lose_the_whole_listing():
    client = FakeClient(participants=members(3), search_error=RuntimeError("flood wait"))
    result = asyncio.run(
        telegram.fetch_participants(client, -1001234567890, threshold=1, delay=0)
    )
    assert len(result) == 3


def test_the_searches_are_paced():
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    import app.telegram as module

    original = module.asyncio.sleep
    module.asyncio.sleep = fake_sleep
    try:
        asyncio.run(
            telegram.fetch_participants(
                FakeClient(participants=members(3)), -1001234567890, threshold=1, delay=0.2
            )
        )
    finally:
        module.asyncio.sleep = original

    assert slept == [0.2] * (len(telegram.SEARCH_LETTERS) - 1)


# --- how many subscribers the channel says it has -------------------------

def test_the_subscriber_count_comes_from_the_full_channel():
    client = FakeClient(full=SimpleNamespace(full_chat=SimpleNamespace(participants_count=210)))
    assert asyncio.run(telegram.participants_count(client, -1001234567890)) == 210


def test_a_failed_subscriber_count_is_none_rather_than_an_error():
    client = FakeClient(full_error=RuntimeError("no access"))
    assert asyncio.run(telegram.participants_count(client, -1001234567890)) is None
