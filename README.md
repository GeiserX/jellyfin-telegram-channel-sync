<p align="center">
  <img src="docs/images/banner.svg" alt="jellyfin-telegram-channel-sync banner" width="900"/>
</p>

<p align="center">
  <a href="https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/LICENSE"><img src="https://img.shields.io/github/license/GeiserX/jellyfin-telegram-channel-sync?style=flat-square&color=6B4C9A" alt="License"></a>
  <a href="https://hub.docker.com/r/drumsergio/jellytelegram-sync"><img src="https://img.shields.io/docker/pulls/drumsergio/jellytelegram-sync?style=flat-square&logo=docker&color=0088CC" alt="Docker Pulls"></a>
  <a href="https://hub.docker.com/r/drumsergio/jellytelegram-sync"><img src="https://img.shields.io/docker/image-size/drumsergio/jellytelegram-sync/latest?style=flat-square&color=6B4C9A" alt="Docker Image Size"></a>
  <img src="https://img.shields.io/badge/python-3.13-0088CC?style=flat-square&logo=python&logoColor=white" alt="Python 3.13">
  <a href="https://codecov.io/gh/GeiserX/jellyfin-telegram-channel-sync"><img src="https://codecov.io/gh/GeiserX/jellyfin-telegram-channel-sync/graph/badge.svg" alt="codecov"></a>
</p>

---

A lightweight daemon that **automatically syncs Jellyfin user access with Telegram channel membership**. When a user leaves (or is removed from) your Telegram channel, their Jellyfin account is disabled. When they rejoin, it is re-enabled. All state is tracked in a local SQLite database.

This is useful for communities that distribute Jellyfin access through a private Telegram channel -- the channel becomes the single source of truth for who should have access.

## Features

- **Automatic access control** -- Jellyfin accounts are enabled or disabled based on Telegram channel presence.
- **Multi-ID support** -- A single Jellyfin user can be linked to multiple Telegram IDs (useful for users with multiple Telegram accounts).
- **Threshold guardrail** -- If the number of fetched Telegram members drops below a configurable threshold, the sync cycle is skipped entirely. This prevents mass-disabling users due to a Telegram API hiccup or network issue.
- **Unknown user detection** -- Telegram members not yet mapped in the database are logged with their ID, name, and username for easy onboarding.
- **Persistent state** -- SQLite database and Telegram session file are stored on a bind-mounted volume, surviving container restarts.
- **Configurable interval** -- The sync loop interval is controlled via an environment variable (default: 1 hour).
- **Small footprint** -- Built on `python:3.13-slim`, with only two runtime dependencies (`telethon`, `requests`).

## Prerequisites

1. **Telegram API credentials** -- Obtain an `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org).
2. **Telegram session file** -- You must generate a Telethon session file by authenticating once (see [Database and Session Setup](#database-and-session-setup) below).
3. **Jellyfin API key** -- Generate one from your Jellyfin dashboard under **Administration > API Keys**.
4. **Telegram channel** -- The numeric channel ID (e.g., `-1001234567890`) that serves as your access list.

## Quick Start

### Docker Compose (recommended)

Create a `docker-compose.yml`:

```yaml
services:
  jellytelegram-sync:
    image: drumsergio/jellytelegram-sync:0.0.10
    container_name: jellytelegram-sync
    environment:
      - TELEGRAM_API_ID=your_telegram_api_id
      - TELEGRAM_API_HASH=your_telegram_api_hash
      - TELEGRAM_CHANNEL=-1001234567890
      - THRESHOLD_ENTRIES=100
      - JELLYFIN_URL=http://your_jellyfin_url:8096
      - JELLYFIN_API_KEY=your_jellyfin_api_key
      - SCRIPT_INTERVAL=3600
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

```bash
docker compose up -d
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_API_ID` | Yes | -- | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `TELEGRAM_API_HASH` | Yes | -- | Telegram API hash from [my.telegram.org](https://my.telegram.org) |
| `TELEGRAM_CHANNEL` | Yes | -- | Numeric Telegram channel ID (e.g., `-1001234567890`) |
| `THRESHOLD_ENTRIES` | Yes | -- | Minimum number of members expected. If fewer are returned, the sync cycle is skipped to prevent accidental mass-disabling. Set this to a value safely below your actual member count. |
| `JELLYFIN_URL` | Yes | -- | Base URL of your Jellyfin server (e.g., `http://jellyfin:8096`) |
| `JELLYFIN_API_KEY` | Yes | -- | Jellyfin API key |
| `SCRIPT_INTERVAL` | No | `3600` | Seconds between sync cycles |

## Database and Session Setup

The container expects a bind-mounted volume at `/app/data` containing two files:

### 1. SQLite Database (`jellyfin_users.db`)

Create the database and populate it with your user mappings:

```bash
sqlite3 data/jellyfin_users.db <<'SQL'
CREATE TABLE IF NOT EXISTS users (
    ID TEXT,
    JellyfinUser TEXT PRIMARY KEY,
    Enabled INTEGER DEFAULT 1
);
SQL
```

Insert your users. The `ID` column holds one or more Telegram user IDs (space-separated if multiple):

```bash
sqlite3 data/jellyfin_users.db "INSERT INTO users (ID, JellyfinUser, Enabled) VALUES ('123456789', 'alice', 1);"
sqlite3 data/jellyfin_users.db "INSERT INTO users (ID, JellyfinUser, Enabled) VALUES ('987654321 111222333', 'bob', 1);"
```

The second example shows a user (`bob`) mapped to two Telegram accounts.

### 2. Telegram Session (`session_name.session`)

Generate the Telethon session file by running an interactive authentication once:

```bash
docker run -it --rm \
  -e TELEGRAM_API_ID=your_api_id \
  -e TELEGRAM_API_HASH=your_api_hash \
  -e TELEGRAM_CHANNEL=0 \
  -e THRESHOLD_ENTRIES=0 \
  -e JELLYFIN_URL=http://localhost \
  -e JELLYFIN_API_KEY=dummy \
  -v ./data:/app/data \
  drumsergio/jellytelegram-sync:0.0.10 \
  python -c "
from telethon.sync import TelegramClient
client = TelegramClient('/app/data/session_name', $(echo $TELEGRAM_API_ID), '$(echo $TELEGRAM_API_HASH)')
client.start()
print('Session created successfully.')
client.disconnect()
"
```

Follow the prompts to enter your phone number and verification code. The session file will be saved to your `data/` directory.

## How It Works

Each sync cycle follows this sequence:

1. **Fetch Jellyfin users** -- All non-root users are retrieved from the Jellyfin API.
2. **Load the database** -- The SQLite mapping table is read, associating Telegram IDs with Jellyfin usernames.
3. **Fetch Telegram members** -- All participants of the configured channel are retrieved via the Telethon client.
4. **Threshold check** -- If the member count is below `THRESHOLD_ENTRIES`, the cycle is aborted as a safety measure.
5. **Sync loop** -- For each database entry:
   - If the user's Telegram ID(s) are found in the channel but their account is disabled, it is **re-enabled**.
   - If none of the user's Telegram ID(s) are found in the channel but their account is enabled, it is **disabled**.
   - If the state matches, no action is taken.
6. **Unknown ID detection** -- Any Telegram IDs present in the channel but absent from the database are logged, so you can add new users.
7. **Sleep** -- The daemon waits for `SCRIPT_INTERVAL` seconds before repeating.

## Troubleshooting

| Problem | Cause | Solution |
|---|---|---|
| `Telegram client is not authorized` | Missing or expired session file | Re-generate the session file (see above) |
| All users disabled at once | `THRESHOLD_ENTRIES` set too low, or Telegram API returned partial results | Increase the threshold to a safe value below your actual member count |
| User not being synced | Telegram ID not in the database | Check logs for "Unrecognized Telegram users" and add the mapping |
| `User 'X' has no Telegram IDs in DB` | Empty `ID` field in the database row | Update the row: `UPDATE users SET ID = 'telegram_id' WHERE JellyfinUser = 'X';` |
| Jellyfin API errors (401/403) | Invalid or expired API key | Regenerate the API key in Jellyfin admin panel |
| `root` user not appearing | Filtered out by design | The `root` admin account is always excluded from sync |

## Other Jellyfin Projects by GeiserX

- [quality-gate](https://github.com/GeiserX/quality-gate) — Restrict users to specific media versions based on configurable path-based policies
- [smart-covers](https://github.com/GeiserX/smart-covers) — Cover extraction for books, audiobooks, comics, magazines, and music libraries with online fallback
- [whisper-subs](https://github.com/GeiserX/whisper-subs) — Automatic subtitle generation using local AI models powered by whisper.cpp
- [jellyfin-encoder](https://github.com/GeiserX/jellyfin-encoder) — Automatic 720p HEVC/AV1 transcoding service with hardware acceleration

## Other Telegram Projects by GeiserX

- [paperless-telegram-bot](https://github.com/GeiserX/paperless-telegram-bot) — Manage Paperless-NGX documents through Telegram
- [AskePub](https://github.com/GeiserX/AskePub) — Telegram bot for ePub annotation with GPT-4
- [telegram-delay-channel-cloner](https://github.com/GeiserX/telegram-delay-channel-cloner) — Relay messages between channels with delay
- [telegram-slskd-local-bot](https://github.com/GeiserX/telegram-slskd-local-bot) — Automated music discovery and download via Telegram

## License

This project is licensed under the [GNU Lesser General Public License v2.1](LICENSE).
