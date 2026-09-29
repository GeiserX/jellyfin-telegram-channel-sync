# How it works

## How it decides

Every cycle asks the bot about each linked Telegram account in turn, reads what Jellyfin thinks right now, and reads what this service did last time. Then, for each Jellyfin user you have linked:

- **In the channel.** Nothing happens, unless the account is disabled *and this service is the one that disabled it for leaving*, in which case it is enabled again.
- **Not in the channel.** A clock starts. Once the account has been missing for `GRACE_HOURS` (default 72), the service disables it and messages you. The clock lives in the database, so restarting the container does not reset it.
- **No answer from Telegram.** Nothing happens to that person, this cycle or any cycle until an answer comes back. A timeout, a rate limit, a deleted account and a status Telegram invents next year all mean the same thing: we do not know, and not knowing must never cost somebody their access. `/status` counts how many went unanswered.
- **Several Telegram accounts on one Jellyfin user.** Present if any one of them is in the channel. Absent only if every one of them is explicitly out. One unanswered account is enough to leave the person alone.
- **Disabled by a person, not by this service.** Left alone, in both directions. The service only ever undoes its own work, and it drops its claim as soon as somebody re-enables an account by hand.
- **An administrator.** Never touched, linked or not.
- **Not linked.** The channel rule never touches it. The inactivity rule still does, since that one does not care about Telegram.
- **Named in `EXEMPT_USERS`.** Never touched by either rule.

`DRY_RUN` is on by default. The messages arrive, the grace clock runs, and no Jellyfin account changes. Leave it on until `/links` looks right. The whole decision is one pure function in [app/sync.py](../app/sync.py), so that list is exactly what the tests enumerate.

## Second rule: a year without use

Leaving the channel is not the only way an account goes stale. An account nobody has opened for `INACTIVE_DAYS` (default 365) is disabled too, whether or not it is linked to Telegram.

The clock is the later of Jellyfin's `LastActivityDate` and `LastLoginDate`. If Jellyfin has neither, the account is left alone and counted instead: no recorded use is missing data, not evidence of a year of silence. `/status` and `/inactive` both say how many accounts fall in that bucket.

This rule and the channel rule disable in the same way, but they undo differently:

- **Disabled for leaving the channel.** Enabled again the moment the person is back in the channel.
- **Disabled for a year of silence.** Stays disabled. Walking back into the channel does not revive it, because being in a Telegram channel is not using Jellyfin. Re-enable it yourself when you want it back, and the daemon drops its claim and will not disable it again until the account has actually been used and then gone quiet for another year.

Set `EXEMPT_USERS` to a comma-separated list of Jellyfin usernames that neither rule may ever touch. Administrators are exempt anyway.

Before switching dry run off, `/inactive` shows you exactly who this rule would catch and when each of them last used the server.

## Why presence is checked one person at a time

Telegram will not list a broadcast channel past 200 members. Not with `get_participants()`, not with `aggressive=True`, not with `iter_participants(limit=None)`, not with a raw recent-participants request: all of them stop at 200, and the count the server reports alongside them stops there too. On a channel with more subscribers than that, "not in the listing" simply does not mean "left", and reading it that way would disable people at random.

So the decision never touches the listing. For each linked Telegram account, the bot asks Telegram [`getChatMember`](https://core.telegram.org/bots/api#getchatmember), which answers for any user id as long as **the bot is an administrator of the channel**. `creator`, `administrator`, `member` and `restricted` mean the person is in. `left` and `kicked` mean they are out. Anything else, including every error, means we do not know, and nothing happens to them.

That is one request per linked account per cycle, paced with a short pause. At the default hourly interval a few hundred members is well inside Telegram's limits.

## Why two Telegram sessions

The **bot** answers your commands, sends your notifications, and checks membership. It never posts into the channel, and it never answers anyone but you.

A **user session**, yours as the channel's creator, is still needed for the two things a bot cannot do: listing members to populate `/unknown`, and resolving an `@username` to a numeric id for `/link`.

Both sessions are files in `/app/data`, created once by [app/login.py](../app/login.py).

## What is stored

Everything is one SQLite file, `/app/data/sync.db`, with four tables defined in [app/db.py](../app/db.py). `links` holds one row per Telegram id. `user_state` records when someone was first seen missing, whether this service disabled them and why. `audit` holds one row per action, dry runs included. `settings` holds the `/dryrun` state and the last sync time.

Upgrading from 1.x adds the reason column in place and marks everything 1.x had disabled as having left the channel, which is the only thing it could have meant.

