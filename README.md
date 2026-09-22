<p align="center">
  <img src="docs/images/banner.svg" alt="jellyfin-telegram-channel-sync banner" width="900"/>
</p>

<p align="center">
  <a href="https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/LICENSE"><img src="https://img.shields.io/github/license/GeiserX/jellyfin-telegram-channel-sync?style=flat-square&color=6B4C9A" alt="License"></a>
  <a href="https://hub.docker.com/r/drumsergio/jellyfin-telegram-channel-sync"><img src="https://img.shields.io/docker/pulls/drumsergio/jellyfin-telegram-channel-sync?style=flat-square&logo=docker&color=0088CC" alt="Docker Pulls"></a>
  <a href="https://hub.docker.com/r/drumsergio/jellyfin-telegram-channel-sync"><img src="https://img.shields.io/docker/image-size/drumsergio/jellyfin-telegram-channel-sync/latest?style=flat-square&color=6B4C9A" alt="Docker Image Size"></a>
  <img src="https://img.shields.io/badge/python-3.13-0088CC?style=flat-square&logo=python&logoColor=white" alt="Python 3.13">
  <a href="https://codecov.io/gh/GeiserX/jellyfin-telegram-channel-sync"><img src="https://codecov.io/gh/GeiserX/jellyfin-telegram-channel-sync/graph/badge.svg" alt="codecov"></a>
  <a href="https://github.com/awesome-jellyfin/awesome-jellyfin#readme"><img src="https://img.shields.io/badge/listed%20on-awesome--jellyfin-00a4dc?style=flat-square&logo=jellyfin&logoColor=white" alt="listed on awesome-jellyfin"></a>
</p>

---

A daemon that keeps Jellyfin accounts in step with a Telegram channel. If you hand out Jellyfin access through a private channel, that channel is your list of who should have access. Leave it and your account is disabled. Come back and it is enabled again.

You say who is who from a Telegram bot, in your own private chat with it. The bot also tells you every time an account changes.

## How it decides

Every cycle asks the bot about each linked Telegram account in turn, reads what Jellyfin thinks right now, and reads what this service did last time. Then, for each Jellyfin user you have linked:

- **In the channel.** Nothing happens, unless the account is disabled *and this service is the one that disabled it*, in which case it is enabled again.
- **Not in the channel.** A clock starts. Once the account has been missing for `GRACE_HOURS` (default 72), the service disables it and messages you. The clock lives in the database, so restarting the container does not reset it.
- **No answer from Telegram.** Nothing happens to that person, this cycle or any cycle until an answer comes back. A timeout, a rate limit, a deleted account and a status Telegram invents next year all mean the same thing: we do not know, and not knowing must never cost somebody their access. `/status` counts how many went unanswered.
- **Several Telegram accounts on one Jellyfin user.** Present if any one of them is in the channel. Absent only if every one of them is explicitly out. One unanswered account is enough to leave the person alone.
- **Disabled by a person, not by this service.** Left alone, in both directions. The service only ever undoes its own work, and it drops its claim as soon as somebody re-enables an account by hand.
- **An administrator.** Never touched, linked or not.
- **Not linked.** Never touched. A Jellyfin account with no link is invisible to the sync.

`DRY_RUN` is on by default. The messages arrive, the grace clock runs, and no Jellyfin account changes. Leave it on until `/links` looks right. The whole decision is one pure function in [app/sync.py](app/sync.py), so that list is exactly what the tests enumerate.

## Why presence is checked one person at a time

Telegram will not list a broadcast channel past 200 members. Not with `get_participants()`, not with `aggressive=True`, not with `iter_participants(limit=None)`, not with a raw recent-participants request: all of them stop at 200, and the count the server reports alongside them stops there too. On a channel with more subscribers than that, "not in the listing" simply does not mean "left", and reading it that way would disable people at random.

So the decision never touches the listing. For each linked Telegram account, the bot asks Telegram [`getChatMember`](https://core.telegram.org/bots/api#getchatmember), which answers for any user id as long as **the bot is an administrator of the channel**. `creator`, `administrator`, `member` and `restricted` mean the person is in. `left` and `kicked` mean they are out. Anything else, including every error, means we do not know, and nothing happens to them.

That is one request per linked account per cycle, paced with a short pause. At the default hourly interval a few hundred members is well inside Telegram's limits.

## Why two Telegram sessions

The **bot** answers your commands, sends your notifications, and checks membership. It never posts into the channel, and it never answers anyone but you.

A **user session**, yours as the channel's creator, is still needed for the two things a bot cannot do: listing members to populate `/unknown`, and resolving an `@username` to a numeric id for `/link`.

Both sessions are files in `/app/data`, created once by [app/login.py](app/login.py).

## Prerequisites

1. **Telegram API credentials.** An `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org).
2. **A Telegram bot, promoted to administrator of the channel.** Create one with [@BotFather](https://t.me/BotFather), keep the token, then add it to the channel as an administrator. Without that it cannot answer whether somebody is still a member, and every cycle will report unanswered lookups and change nothing.
3. **Your own numeric Telegram id.** The only account the bot will obey. [@userinfobot](https://t.me/userinfobot) will tell you yours.
4. **A Jellyfin API key.** Jellyfin dashboard, Administration > API Keys.
5. **The channel id.** The numeric id (e.g. `-1001234567890`) of the channel you use as the access list. Your user account must be able to list its members.

## Quick start

### 1. Write the compose file

```yaml
services:
  jellytelegram-sync:
    image: drumsergio/jellyfin-telegram-channel-sync:1.2.0
    container_name: jellytelegram-sync
    environment:
      - TELEGRAM_API_ID=your_telegram_api_id
      - TELEGRAM_API_HASH=your_telegram_api_hash
      - TELEGRAM_CHANNEL=-1001234567890
      - TELEGRAM_BOT_TOKEN=your_bot_token
      - OWNER_ID=your_numeric_telegram_id
      - JELLYFIN_URL=http://your_jellyfin_url:8096
      - JELLYFIN_API_KEY=your_jellyfin_api_key
      - THRESHOLD_ENTRIES=100
      - SCRIPT_INTERVAL=3600
      - GRACE_HOURS=72
      - DRY_RUN=true
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

### 2. Sign in once, interactively

```bash
docker compose run --rm jellytelegram-sync python -m app.login
```

This asks for your phone number and the code Telegram sends you, then signs the bot in with its token. It writes two session files into `./data`:

- `session_name.session` is your user account, which lists the channel members.
- `bot_session.session` is the bot.

Both are credentials. Keep the `data` directory private and out of git.

### 3. Start it

```bash
docker compose up -d
```

### 4. Link people

Open a private chat with your bot and send `/start`. Then:

```
/unknown                       # everyone in the channel with no link yet
/link 123456789 alice          # by numeric Telegram id
/link @someone bob             # or by @username
/links                         # check your work
/unlinked                      # enabled Jellyfin users nobody is linked to
```

A Jellyfin user can hold several Telegram ids. Link them one at a time. Any one of them being in the channel counts as present.

### 5. Turn it on for real

When `/links` looks right, send `/dryrun off`. That setting is stored in the database, so it survives a restart and outlives the `DRY_RUN` variable.

## Commands

All of these work only in your private chat with the bot, and only for `OWNER_ID`. Anyone else is ignored without a reply.

| Command | What it does |
|---|---|
| `/link <telegram_id\|@username> <jellyfin_user>` | Link a Telegram account to a Jellyfin user. The Jellyfin name is checked against the server. |
| `/unlink <telegram_id>` | Remove one link. |
| `/links` | Every link, grouped by Jellyfin user. |
| `/unknown` | Channel members with no link: id, name, username. Says how many the listing could see against the channel's real subscriber count. |
| `/unlinked` | Enabled Jellyfin users with no link. |
| `/status` | Last sync, link counts, how many are inside the grace window, dry-run state. |
| `/sync` | Run a cycle now instead of waiting. |
| `/dryrun on\|off` | Whether changes are really applied. Stored in the database. |
| `/help` | The list above. |

### What `/unknown` can and cannot see

`/unknown` lists channel members who have no link yet. It builds that list from the member listing, so it inherits the 200 cap: a plain listing, plus one name search per letter (a to z, and the accented letters Spanish names use), unioned together. On the channel this was built for that reaches a little past 200, not all the way.

The reply says what it saw, for example `The listing saw 208 of 210 subscribers`. Anyone it cannot see is still perfectly linkable, by numeric id or by `@username`, and once linked they are checked like everybody else. The cap only limits discovery, never the decision.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_API_ID` | Yes | none | API id from [my.telegram.org](https://my.telegram.org) |
| `TELEGRAM_API_HASH` | Yes | none | API hash from [my.telegram.org](https://my.telegram.org) |
| `TELEGRAM_CHANNEL` | Yes | none | Numeric channel id (e.g. `-1001234567890`) or `@username` |
| `TELEGRAM_BOT_TOKEN` | Yes | none | Bot token from [@BotFather](https://t.me/BotFather) |
| `OWNER_ID` | Yes | none | Your numeric Telegram id. The only account the bot obeys. |
| `JELLYFIN_URL` | Yes | none | Base URL of your Jellyfin server |
| `JELLYFIN_API_KEY` | Yes | none | Jellyfin API key |
| `THRESHOLD_ENTRIES` | Yes | none | Guardrail on the `/unknown` listing only. If the listing returns fewer than this, `/unknown` refuses to answer rather than showing a misleading list. It no longer gates the sync, which works per person. |
| `SCRIPT_INTERVAL` | No | `3600` | Seconds between cycles |
| `GRACE_HOURS` | No | `72` | Hours a member may be missing before the account is disabled |
| `DRY_RUN` | No | `true` | Report what would happen without changing anything. `/dryrun` overrides it once set. |
| `DATA_DIR` | No | `/app/data` | Where the database and both session files live |

## Upgrading from 0.x

Keep your data volume and start 1.1.0. On the first run the migration imports the old `users` table from `jellyfin_users.db` into the new `sync.db`.

- Each space-separated Telegram id becomes its own link row, so multi-id users survive.
- A row with `Enabled = 0` is recorded as *disabled by this service*, so those accounts come back on if the person rejoins.

The migration reads the old file and never writes to it or deletes it. Your user session file keeps its name, so you do not sign in again. You do need the new `TELEGRAM_BOT_TOKEN` and `OWNER_ID` variables, and a bot session, before the daemon will start.

There is no longer any reason to edit the database by hand. `/link` does it.

## What is stored

Everything is one SQLite file, `/app/data/sync.db`, with four tables defined in [app/db.py](app/db.py). `links` holds one row per Telegram id. `user_state` records when someone was first seen missing and whether this service disabled them. `audit` holds one row per action, dry runs included. `settings` holds the `/dryrun` state and the last sync time.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `The Telegram user session is not authorized` | The session file is missing or was revoked | Run `docker compose run --rm jellytelegram-sync python -m app.login` again |
| `Configuration error: X is required` | A variable is missing | The message names it; the container exits with code 2 |
| The bot ignores you | You are not `OWNER_ID`, or you wrote in a group | Check `OWNER_ID`, and write in the private chat |
| No notifications arrive | Telegram will not let a bot message someone who has never written to it | Send the bot `/start` once |
| `/status` shows unanswered membership lookups | The bot is not an administrator of the channel, or Telegram rate-limited the run | Promote the bot to administrator. A rate limit clears itself; nothing is changed while lookups go unanswered. |
| Jellyfin returns 401 | The API key is wrong, or revoked | Regenerate it. The client authenticates with the `Authorization: MediaBrowser Token=...` header, which Jellyfin 12 requires; the older `X-Emby-Token` is refused. |
| `/unknown` shows fewer members than the channel has | Telegram caps a broadcast listing at 200 | Expected. Link the rest by id or `@username`; presence is checked per person either way. |
| Nothing is ever disabled | Dry run is still on | `/status` shows it; `/dryrun off` |
| `Member list came back below THRESHOLD_ENTRIES` | Telegram returned a partial list, or the threshold is too high | This is the guardrail working. Check the threshold against your real member count. |
| Someone left but is still enabled | The grace window has not elapsed | `/status` shows how many are waiting; lower `GRACE_HOURS` if you want it sooner |
| Jellyfin API errors (401/403) | Invalid or expired API key | Regenerate it in the Jellyfin dashboard |

## Development

```bash
pip install -r app/requirements.txt && pip install pytest pytest-cov
pytest --cov=app
```

The tests need no environment variables and no network. [app/config.py](app/config.py) parses the environment inside a function, [app/sync.py](app/sync.py) decides over plain data, and fakes stand in for Jellyfin and Telegram.

## Other Jellyfin projects by GeiserX

- [quality-gate](https://github.com/GeiserX/quality-gate) — Restrict users to specific media versions based on configurable path-based policies
- [smart-covers](https://github.com/GeiserX/smart-covers) — Cover extraction for books, audiobooks, comics, magazines, and music libraries with online fallback
- [whisper-subs](https://github.com/GeiserX/whisper-subs) — Automatic subtitle generation using local AI models powered by whisper.cpp
- [jellyfin-encoder](https://github.com/GeiserX/jellyfin-encoder) — Automatic 720p HEVC/AV1 transcoding service with hardware acceleration

## Other Telegram projects by GeiserX

- [paperless-telegram-bot](https://github.com/GeiserX/paperless-telegram-bot) — Manage Paperless-NGX documents through Telegram
- [AskePub](https://github.com/GeiserX/AskePub) — Telegram bot for ePub annotation with GPT-4
- [telegram-delay-channel-cloner](https://github.com/GeiserX/telegram-delay-channel-cloner) — Relay messages between channels with delay
- [telegram-slskd-local-bot](https://github.com/GeiserX/telegram-slskd-local-bot) — Automated music discovery and download via Telegram

## License

[GNU General Public License v3.0](LICENSE).
