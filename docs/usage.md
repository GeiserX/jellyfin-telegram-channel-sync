# Usage

## Bot commands

All of these work only in your private chat with the bot, and only for `OWNER_ID`. Anyone else is ignored without a reply.

| Command | What it does |
|---|---|
| `/link <telegram_id\|@username> <jellyfin_user>` | Link a Telegram account to a Jellyfin user. The Jellyfin name is checked against the server. |
| `/unlink <telegram_id>` | Remove one link. |
| `/links` | Every link, grouped by Jellyfin user. |
| `/unknown` | Channel members with no link: id, name, username. Says how many the listing could see against the channel's real subscriber count. |
| `/unlinked` | Enabled Jellyfin users with no link. |
| `/inactive` | Enabled accounts nobody has used since the threshold, with the date each last used the server. |
| `/status` | Last sync, link counts, how many are inside the grace window, the inactivity numbers, dry-run state. |
| `/sync` | Run a cycle now instead of waiting. |
| `/dryrun on\|off` | Whether changes are really applied. Stored in the database. |
| `/help` | The list above. |

## What `/unknown` can and cannot see

`/unknown` lists channel members who have no link yet. It builds that list from the member listing, so it inherits the 200 cap: a plain listing, plus one name search per letter (a to z, and the accented letters Spanish names use), unioned together. On the channel this was built for that reaches a little past 200, not all the way.

The reply says what it saw, for example `The listing saw 208 of 210 subscribers`. Anyone it cannot see is still perfectly linkable, by numeric id or by `@username`, and once linked they are checked like everybody else. The cap only limits discovery, never the decision.

