# Getting started

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
    image: drumsergio/jellyfin-telegram-channel-sync:1.3.0
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

## Upgrading from 0.x

Keep your data volume and start 1.1.0. On the first run the migration imports the old `users` table from `jellyfin_users.db` into the new `sync.db`.

- Each space-separated Telegram id becomes its own link row, so multi-id users survive.
- A row with `Enabled = 0` is recorded as *disabled by this service*, so those accounts come back on if the person rejoins.

The migration reads the old file and never writes to it or deletes it. Your user session file keeps its name, so you do not sign in again. You do need the new `TELEGRAM_BOT_TOKEN` and `OWNER_ID` variables, and a bot session, before the daemon will start.

There is no longer any reason to edit the database by hand. `/link` does it.

