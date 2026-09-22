"""The decision table.

`decide` is pure, so the whole policy can be enumerated: present or absent,
enabled or disabled, disabled by this service or by a human, inside or past
the grace window, dry run or not, administrator, unlinked, and the threshold
guardrail.
"""

import pytest

from app import sync
from app.db import UserState
from app.jellyfin import JellyfinUser

NOW = 1_000_000
GRACE = 72 * 3600
CHANNEL = "ExampleChannel"


def user(name="examplename", disabled=False, admin=False, user_id="jf-1"):
    return JellyfinUser(name=name, id=user_id, disabled=disabled, is_administrator=admin)


def seen(present=(), absent=(), unknown=()):
    """Build a presence map the way the bot would answer."""
    return {
        **{str(i): sync.PRESENT for i in present},
        **{str(i): sync.ABSENT for i in absent},
        **{str(i): sync.UNKNOWN for i in unknown},
    }


def run(
    links=None,
    presence=None,
    users=None,
    states=None,
    now=NOW,
    dry_run=False,
):
    return sync.decide(
        links_by_user=links if links is not None else {"examplename": {"111"}},
        presence=seen(present=["111"]) if presence is None else presence,
        jellyfin_users=users if users is not None else {"examplename": user()},
        states=states or {},
        now=now,
        grace_seconds=GRACE,
        channel_title=CHANNEL,
        dry_run=dry_run,
    )


def kinds(actions):
    return [action.kind for action in actions]


# --- present in the channel ---------------------------------------------

def test_present_and_enabled_is_left_alone():
    assert run() == []


def test_present_clears_a_pending_absence():
    actions = run(states={"examplename": UserState(absent_since=NOW - 10)})
    assert kinds(actions) == [sync.CLEAR_ABSENT]


def test_present_and_disabled_by_us_is_re_enabled():
    actions = run(
        users={"examplename": user(disabled=True)},
        states={"examplename": UserState(disabled_by_us=True)},
    )
    assert kinds(actions) == [sync.ENABLE]
    assert actions[0].notify == "examplename rejoined ExampleChannel, account re-enabled"


def test_present_and_disabled_by_a_human_is_left_alone():
    actions = run(
        users={"examplename": user(disabled=True)},
        states={"examplename": UserState(disabled_by_us=False)},
    )
    assert actions == []


def test_returning_after_a_disable_clears_the_absence_and_re_enables():
    actions = run(
        users={"examplename": user(disabled=True)},
        states={"examplename": UserState(absent_since=NOW - GRACE, disabled_by_us=True)},
    )
    assert kinds(actions) == [sync.CLEAR_ABSENT, sync.ENABLE]


def test_any_one_of_several_telegram_ids_counts_as_present():
    actions = run(
        links={"examplename": {"111", "222", "333"}},
        presence=seen(present=["333"], absent=["111", "222"]),
    )
    assert actions == []


# --- absent from the channel --------------------------------------------

def test_absent_and_enabled_starts_the_grace_clock():
    actions = run(presence=seen(absent=["111"]))
    assert kinds(actions) == [sync.MARK_ABSENT]
    assert actions[0].notify is None  # the owner is not pinged for this


def test_absent_inside_the_grace_window_does_nothing():
    actions = run(
        presence=seen(absent=["111"]),
        states={"examplename": UserState(absent_since=NOW - GRACE + 1)},
    )
    assert actions == []


def test_absent_past_the_grace_window_is_disabled():
    actions = run(
        presence=seen(absent=["111"]),
        states={"examplename": UserState(absent_since=NOW - GRACE - 1)},
    )
    assert kinds(actions) == [sync.DISABLE]
    assert actions[0].notify == "examplename left ExampleChannel, account disabled"


def test_the_grace_boundary_itself_disables():
    actions = run(
        presence=seen(absent=["111"]),
        states={"examplename": UserState(absent_since=NOW - GRACE)},
    )
    assert kinds(actions) == [sync.DISABLE]


def test_absent_and_already_disabled_by_us_is_not_disabled_twice():
    actions = run(
        presence=seen(absent=["111"]),
        users={"examplename": user(disabled=True)},
        states={"examplename": UserState(absent_since=NOW - GRACE - 1, disabled_by_us=True)},
    )
    assert actions == []


def test_absent_and_disabled_by_a_human_is_left_alone():
    actions = run(
        presence=seen(absent=["111"]),
        users={"examplename": user(disabled=True)},
        states={"examplename": UserState(absent_since=NOW - GRACE - 1)},
    )
    assert actions == []


# --- the accounts this service must never touch --------------------------

def test_an_administrator_is_never_disabled():
    actions = run(
        presence=seen(absent=["111"]),
        users={"examplename": user(admin=True)},
        states={"examplename": UserState(absent_since=NOW - GRACE - 1)},
    )
    assert actions == []


def test_an_administrator_is_never_re_enabled_either():
    actions = run(
        users={"examplename": user(disabled=True, admin=True)},
        states={"examplename": UserState(disabled_by_us=True)},
    )
    assert actions == []


def test_an_unlinked_jellyfin_user_is_never_touched():
    actions = run(
        links={},
        presence=seen(absent=["111"]),
        users={"examplename": user()},
    )
    assert actions == []


def test_a_link_to_a_deleted_jellyfin_account_is_skipped():
    actions = run(links={"ghost": {"111"}}, presence=seen(absent=["111"]), users={})
    assert actions == []


# --- the guardrail -------------------------------------------------------

def test_an_untrusted_member_list_produces_no_actions_at_all():
    # The old code crashed here (None.keys()); before that it would have been
    # read as "everybody left".
    actions = run(
        presence={},
        states={"examplename": UserState(absent_since=NOW - GRACE - 1)},
    )
    assert actions == []


def test_an_empty_but_trusted_member_list_still_only_starts_the_clock():
    actions = run(presence=seen(absent=["111"]))
    assert kinds(actions) == [sync.MARK_ABSENT]


# --- dry run -------------------------------------------------------------

@pytest.mark.parametrize(
    "dry_run,expected",
    [
        (False, "examplename left ExampleChannel, account disabled"),
        (True, "examplename left ExampleChannel, account would be disabled"),
    ],
)
def test_dry_run_only_changes_the_wording_of_the_disable_notice(dry_run, expected):
    actions = run(
        presence=seen(absent=["111"]),
        states={"examplename": UserState(absent_since=NOW - GRACE - 1)},
        dry_run=dry_run,
    )
    assert kinds(actions) == [sync.DISABLE]
    assert actions[0].notify == expected


@pytest.mark.parametrize(
    "dry_run,expected",
    [
        (False, "examplename rejoined ExampleChannel, account re-enabled"),
        (True, "examplename rejoined ExampleChannel, account would be re-enabled"),
    ],
)
def test_dry_run_only_changes_the_wording_of_the_enable_notice(dry_run, expected):
    actions = run(
        users={"examplename": user(disabled=True)},
        states={"examplename": UserState(disabled_by_us=True)},
        dry_run=dry_run,
    )
    assert actions[0].notify == expected


# --- several users at once -----------------------------------------------

def test_a_mixed_channel_is_decided_per_user_and_ordered():
    users = {
        "examplealpha": user("examplealpha", user_id="jf-a"),
        "examplebravo": user("examplebravo", disabled=True, user_id="jf-b"),
        "examplecharlie": user("examplecharlie", user_id="jf-c"),
        "exampleadmin": user("exampleadmin", admin=True, user_id="jf-d"),
    }
    actions = sync.decide(
        links_by_user={
            "examplealpha": {"111"},
            "examplebravo": {"222"},
            "examplecharlie": {"333"},
            "exampleadmin": {"444"},
        },
        presence=seen(present=["222"], absent=["111", "333", "444"]),
        jellyfin_users=users,
        states={
            "examplealpha": UserState(absent_since=NOW - GRACE - 1),
            "examplebravo": UserState(absent_since=NOW - 5, disabled_by_us=True),
        },
        now=NOW,
        grace_seconds=GRACE,
        channel_title=CHANNEL,
    )
    assert [(a.jellyfin_user, a.kind) for a in actions] == [
        ("examplealpha", sync.DISABLE),
        ("examplebravo", sync.CLEAR_ABSENT),
        ("examplebravo", sync.ENABLE),
        ("examplecharlie", sync.MARK_ABSENT),
    ]


# --- the reports the bot reads ------------------------------------------

def test_unknown_participants_are_the_ones_with_no_link():
    unknown = sync.unknown_participants(
        links={"111": "examplename"},
        participants_info={
            "111": {"username": "exampleone", "name": "Example One"},
            "222": {"username": "", "name": "Example Two"},
        },
    )
    assert unknown == [{"id": "222", "username": "", "name": "Example Two"}]


def test_unlinked_jellyfin_users_skip_admins_and_disabled_accounts():
    names = sync.unlinked_jellyfin_users(
        links_by_user={"examplelinked": {"111"}},
        jellyfin_users={
            "examplelinked": user("examplelinked"),
            "exampleloose": user("exampleloose"),
            "exampleadmin": user("exampleadmin", admin=True),
            "exampleoff": user("exampleoff", disabled=True),
        },
    )
    assert names == ["exampleloose"]


# --- reconciling a claim somebody else undid ------------------------------

def test_an_account_re_enabled_by_hand_is_no_longer_claimed():
    actions = run(
        users={"examplename": user(disabled=False)},
        states={"examplename": UserState(disabled_by_us=True)},
    )
    assert kinds(actions) == [sync.RELEASE]


def test_a_human_disable_after_a_hand_re_enable_is_not_undone():
    """The bug the release action exists to prevent.

    We disable alice. An administrator re-enables her by hand, then later
    disables her deliberately. Without the release, the stale claim makes us
    switch her back on.
    """
    # Cycle one: she is back in the channel and enabled, so the claim goes.
    first = run(
        users={"examplename": user(disabled=False)},
        states={"examplename": UserState(disabled_by_us=True)},
    )
    assert kinds(first) == [sync.RELEASE]

    # Cycle two: an administrator has disabled her. We leave her alone.
    second = run(
        users={"examplename": user(disabled=True)},
        states={"examplename": UserState(disabled_by_us=False)},
    )
    assert second == []


def test_a_stale_claim_is_released_while_the_member_is_absent_too():
    actions = run(
        presence=seen(absent=["111"]),
        states={"examplename": UserState(absent_since=NOW - 10, disabled_by_us=True)},
    )
    assert kinds(actions) == [sync.RELEASE]


def test_a_release_is_ordered_before_the_disable_that_reclaims_it():
    # Absent past the grace window with a stale claim: release must not run
    # after the disable, or it would throw the fresh claim away.
    actions = run(
        presence=seen(absent=["111"]),
        states={"examplename": UserState(absent_since=NOW - GRACE - 1, disabled_by_us=True)},
    )
    assert kinds(actions) == [sync.RELEASE, sync.DISABLE]


def test_an_administrator_with_a_stale_claim_is_still_left_alone():
    actions = run(
        users={"examplename": user(admin=True)},
        states={"examplename": UserState(disabled_by_us=True)},
    )
    assert actions == []


# --- what we know about each linked account, and what we do not ----------

def test_an_unanswered_lookup_never_disables_anybody():
    # A 429, a timeout or a status Telegram invents later all land here. The
    # member has been "missing" long past the grace window, and still nothing
    # happens, because we do not actually know that they left.
    actions = run(
        presence=seen(unknown=["111"]),
        states={"examplename": UserState(absent_since=NOW - GRACE - 1)},
    )
    assert actions == []


def test_an_id_nobody_answered_for_at_all_is_unknown():
    actions = run(
        presence={},
        states={"examplename": UserState(absent_since=NOW - GRACE - 1)},
    )
    assert actions == []


def test_an_unanswered_lookup_does_not_start_the_grace_clock_either():
    assert run(presence=seen(unknown=["111"])) == []


def test_an_unanswered_lookup_does_not_re_enable_anybody():
    actions = run(
        presence=seen(unknown=["111"]),
        users={"examplename": user(disabled=True)},
        states={"examplename": UserState(disabled_by_us=True)},
    )
    assert actions == []


def test_a_stale_ownership_claim_is_still_released_when_presence_is_unknown():
    # Releasing a claim is about Jellyfin's state, not about the channel.
    actions = run(
        presence=seen(unknown=["111"]),
        states={"examplename": UserState(disabled_by_us=True)},
    )
    assert kinds(actions) == [sync.RELEASE]


@pytest.mark.parametrize(
    "present,absent,unknown,expected",
    [
        (["111"], [], [], sync.PRESENT),
        ([], ["111"], [], sync.ABSENT),
        ([], [], ["111"], sync.UNKNOWN),
        (["111"], ["222"], [], sync.PRESENT),
        (["111"], [], ["222"], sync.PRESENT),
        ([], ["111", "222"], [], sync.ABSENT),
        ([], ["111"], ["222"], sync.UNKNOWN),
        ([], [], ["111", "222"], sync.UNKNOWN),
    ],
)
def test_the_multi_id_truth_table(present, absent, unknown, expected):
    ids = {*present, *absent, *unknown}
    assert sync.presence_of(ids, seen(present, absent, unknown)) == expected


def test_a_person_with_no_linked_ids_is_unknown():
    assert sync.presence_of(set(), {}) == sync.UNKNOWN


def test_one_unanswered_id_protects_a_multi_id_user_from_being_disabled():
    actions = run(
        links={"examplename": {"111", "222"}},
        presence=seen(absent=["111"], unknown=["222"]),
        states={"examplename": UserState(absent_since=NOW - GRACE - 1)},
    )
    assert actions == []


def test_a_multi_id_user_is_disabled_only_when_every_id_is_out():
    actions = run(
        links={"examplename": {"111", "222"}},
        presence=seen(absent=["111", "222"]),
        states={"examplename": UserState(absent_since=NOW - GRACE - 1)},
    )
    assert kinds(actions) == [sync.DISABLE]
