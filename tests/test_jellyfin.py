"""Flipping IsDisabled must not wipe the rest of the user's policy.

POST /Users/{id}/Policy replaces the whole UserPolicy object, so a body of
just {"IsDisabled": true} resets every other field to its type default. This
is the regression test for that.
"""

import pytest

from app.jellyfin import JellyfinClient, JellyfinError

# A policy with the shape Jellyfin 10.9+ returns. The point is breadth: if the
# round-trip drops a field, one of these assertions fails.
FULL_POLICY = {
    "IsAdministrator": True,
    "IsHidden": False,
    "EnableCollectionManagement": True,
    "EnableSubtitleManagement": True,
    "EnableLyricManagement": False,
    "IsDisabled": False,
    "MaxParentalRating": 13,
    "BlockedTags": ["horror"],
    "AllowedTags": [],
    "EnableUserPreferenceAccess": True,
    "AccessSchedules": [{"DayOfWeek": "Sunday", "StartHour": 8, "EndHour": 22}],
    "BlockUnratedItems": ["Movie"],
    "EnableRemoteControlOfOtherUsers": True,
    "EnableSharedDeviceControl": True,
    "EnableRemoteAccess": True,
    "EnableLiveTvManagement": True,
    "EnableLiveTvAccess": True,
    "EnableMediaPlayback": True,
    "EnableAudioPlaybackTranscoding": True,
    "EnableVideoPlaybackTranscoding": False,
    "EnablePlaybackRemuxing": True,
    "ForceRemoteSourceTranscoding": False,
    "EnableContentDeletion": False,
    "EnableContentDeletionFromFolders": ["folder-id-1"],
    "EnableContentDownloading": True,
    "EnableSyncTranscoding": True,
    "EnableMediaConversion": True,
    "EnabledDevices": ["device-1"],
    "EnableAllDevices": False,
    "EnabledChannels": ["channel-1"],
    "EnableAllChannels": False,
    "EnabledFolders": ["library-movies", "library-music"],
    "EnableAllFolders": False,
    "InvalidLoginAttemptCount": 3,
    "LoginAttemptsBeforeLockout": 5,
    "MaxActiveSessions": 2,
    "EnablePublicSharing": False,
    "BlockedMediaFolders": ["library-adult"],
    "BlockedChannels": [],
    "RemoteClientBitrateLimit": 8000000,
    "AuthenticationProviderId": "Jellyfin.Server.Implementations.Users.DefaultAuthenticationProvider",
    "PasswordResetProviderId": "Jellyfin.Server.Implementations.Users.DefaultPasswordResetProvider",
    "SyncPlayAccess": "CreateAndJoinGroups",
}


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Records every call and serves canned responses."""

    def __init__(self, users=None, user=None, post_status=204):
        self.users = users or []
        self.user = user
        self.post_status = post_status
        self.gets = []
        self.posts = []

    def get(self, url, headers=None, timeout=None):
        self.gets.append((url, headers))
        if url.endswith("/Users"):
            return FakeResponse(self.users)
        return FakeResponse(self.user)

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append({"url": url, "headers": headers, "json": json})
        return FakeResponse(status_code=self.post_status, text="denied")


@pytest.fixture
def client_and_session():
    session = FakeSession(user={"Id": "user-1", "Name": "examplename", "Policy": dict(FULL_POLICY)})
    return JellyfinClient("http://jellyfin:8096/", "fakekey", session=session), session


def test_disabling_preserves_every_other_policy_field(client_and_session):
    client, session = client_and_session

    client.set_user_disabled("user-1", True)

    assert len(session.posts) == 1
    sent = session.posts[0]["json"]
    assert sent["IsDisabled"] is True
    for field, value in FULL_POLICY.items():
        if field == "IsDisabled":
            continue
        assert sent[field] == value, f"{field} was not preserved"
    assert set(sent) == set(FULL_POLICY)  # nothing dropped, nothing invented


def test_the_policy_is_read_back_before_it_is_written(client_and_session):
    client, session = client_and_session

    client.set_user_disabled("user-1", True)

    assert session.gets[0][0] == "http://jellyfin:8096/Users/user-1"
    assert session.posts[0]["url"] == "http://jellyfin:8096/Users/user-1/Policy"


def test_enabling_clears_the_flag_and_keeps_the_admin_bit(client_and_session):
    client, session = client_and_session
    session.user["Policy"]["IsDisabled"] = True

    client.set_user_disabled("user-1", False)

    sent = session.posts[0]["json"]
    assert sent["IsDisabled"] is False
    assert sent["IsAdministrator"] is True


def test_the_cached_policy_is_not_mutated_in_place(client_and_session):
    client, session = client_and_session
    client.set_user_disabled("user-1", True)
    assert session.user["Policy"]["IsDisabled"] is False  # the source dict is untouched


def test_a_rejected_write_raises_instead_of_printing(client_and_session):
    client, session = client_and_session
    session.post_status = 403

    with pytest.raises(JellyfinError, match="403"):
        client.set_user_disabled("user-1", True)


def test_a_user_without_a_policy_is_an_error():
    session = FakeSession(user={"Id": "user-1", "Name": "examplename"})
    client = JellyfinClient("http://jellyfin:8096", "fakekey", session=session)

    with pytest.raises(JellyfinError, match="no policy"):
        client.set_user_disabled("user-1", True)


def test_list_users_reports_the_admin_flag_and_the_disabled_flag():
    session = FakeSession(
        users=[
            {"Name": "examplename", "Id": "id-1", "Policy": {"IsDisabled": False, "IsAdministrator": False}},
            {"Name": "exampleadmin", "Id": "id-2", "Policy": {"IsDisabled": False, "IsAdministrator": True}},
            {"Name": "exampleblocked", "Id": "id-3", "Policy": {"IsDisabled": True, "IsAdministrator": False}},
        ]
    )
    users = JellyfinClient("http://jellyfin:8096", "fakekey", session=session).users_by_name()

    assert users["examplename"].id == "id-1"
    assert users["exampleadmin"].is_administrator is True
    assert users["exampleblocked"].disabled is True
    # The old code filtered a hardcoded "root" name; the admin flag is the real signal.
    assert users["examplename"].is_administrator is False


def test_the_api_key_travels_in_the_authorization_header():
    # Jellyfin 12 answers 401 to X-Emby-Token and to X-MediaBrowser-Token.
    session = FakeSession(users=[])
    JellyfinClient("http://jellyfin:8096", "fakekey", session=session).list_users()
    assert session.gets[0][1] == {"Authorization": 'MediaBrowser Token="fakekey"'}


def test_the_old_emby_header_is_not_sent_any_more():
    session = FakeSession(user={"Id": "user-1", "Name": "examplename", "Policy": dict(FULL_POLICY)})
    client = JellyfinClient("http://jellyfin:8096", "fakekey", session=session)
    client.set_user_disabled("user-1", True)
    for _, headers in session.gets:
        assert "X-Emby-Token" not in headers
        assert "X-MediaBrowser-Token" not in headers
    assert "X-Emby-Token" not in session.posts[0]["headers"]


def test_an_http_error_on_the_user_list_propagates():
    class Failing(FakeSession):
        def get(self, url, headers=None, timeout=None):
            return FakeResponse(status_code=401)

    with pytest.raises(RuntimeError, match="401"):
        JellyfinClient("http://jellyfin:8096", "fakekey", session=Failing()).list_users()


def test_an_http_url_warns_that_the_api_key_travels_in_cleartext(caplog):
    with caplog.at_level("WARNING"):
        JellyfinClient("http://jellyfin:8096", "fakekey", session=FakeSession())
    assert "cleartext" in caplog.text


def test_an_https_url_warns_about_nothing(caplog):
    with caplog.at_level("WARNING"):
        JellyfinClient("https://jellyfin.example.invalid", "fakekey", session=FakeSession())
    assert caplog.text == ""


# --- reading the two timestamps Jellyfin keeps ---------------------------

@pytest.mark.parametrize(
    "raw",
    [
        "2026-09-01T12:34:56.7890000Z",  # what Jellyfin actually sends: 7 digits
        "2026-09-01T12:34:56Z",
        "2026-09-01T12:34:56.789Z",
        "2026-09-01T12:34:56",  # no zone: read as UTC, which is what Jellyfin means
    ],
)
def test_the_timestamp_formats_jellyfin_sends(raw):
    from app.jellyfin import parse_date

    assert parse_date(raw) == 1788266096


def test_an_offset_is_honoured():
    from app.jellyfin import parse_date

    assert parse_date("2026-09-01T12:34:56+02:00") == 1788266096 - 7200


@pytest.mark.parametrize("raw", ["", "   ", None, "not a date", 12345, {}, "2026-13-45T00:00:00Z"])
def test_anything_that_is_not_a_timestamp_reads_as_no_record(raw):
    from app.jellyfin import parse_date

    # None, never a number: an account with no recorded use must not look
    # like one last used in 1970.
    assert parse_date(raw) is None


def test_the_more_recent_of_the_two_timestamps_wins():
    session = FakeSession(
        users=[
            {
                "Name": "examplename",
                "Id": "id-1",
                "Policy": {"IsDisabled": False, "IsAdministrator": False},
                "LastActivityDate": "2026-09-01T00:00:00Z",
                "LastLoginDate": "2026-01-01T00:00:00Z",
            }
        ]
    )
    user = JellyfinClient("http://jellyfin:8096", "fakekey", session=session).users_by_name()
    assert user["examplename"].last_used_date == "2026-09-01"


def test_one_timestamp_is_enough():
    session = FakeSession(
        users=[
            {
                "Name": "examplename",
                "Id": "id-1",
                "Policy": {"IsDisabled": False, "IsAdministrator": False},
                "LastLoginDate": "2026-01-01T00:00:00Z",
            }
        ]
    )
    user = JellyfinClient("http://jellyfin:8096", "fakekey", session=session).users_by_name()
    assert user["examplename"].last_used_date == "2026-01-01"


def test_neither_timestamp_means_no_record():
    session = FakeSession(
        users=[
            {"Name": "examplename", "Id": "id-1", "Policy": {"IsDisabled": False, "IsAdministrator": False}}
        ]
    )
    user = JellyfinClient("http://jellyfin:8096", "fakekey", session=session).users_by_name()
    assert user["examplename"].last_used is None
    assert user["examplename"].last_used_date == "never"
