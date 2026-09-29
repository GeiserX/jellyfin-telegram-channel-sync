# Configuration

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
| `INACTIVE_DAYS` | No | `365` | Days without using Jellyfin before an account is disabled. `0` switches the rule off. |
| `EXEMPT_USERS` | No | none | Comma-separated Jellyfin usernames neither rule may touch. Case does not matter. |
| `DRY_RUN` | No | `true` | Report what would happen without changing anything. `/dryrun` overrides it once set. |
| `DATA_DIR` | No | `/app/data` | Where the database and both session files live |

