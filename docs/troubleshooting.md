# Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `The Telegram user session is not authorized` | The session file is missing or was revoked | Run `docker compose run --rm jellytelegram-sync python -m app.login` again |
| `Configuration error: X is required` | A variable is missing | The message names it; the container exits with code 2 |
| The bot ignores you | You are not `OWNER_ID`, or you wrote in a group | Check `OWNER_ID`, and write in the private chat |
| No notifications arrive | Telegram will not let a bot message someone who has never written to it | Send the bot `/start` once |
| `/status` shows unanswered membership lookups | The bot is not an administrator of the channel, or Telegram rate-limited the run | Promote the bot to administrator. A rate limit clears itself; nothing is changed while lookups go unanswered. |
| Jellyfin returns 401 | The API key is wrong, or revoked | Regenerate it. The client authenticates with the `Authorization: MediaBrowser Token=...` header, which Jellyfin 12 requires; the older `X-Emby-Token` is refused. |
| `/unknown` shows fewer members than the channel has | Telegram caps a broadcast listing at 200 | Expected. Link the rest by id or `@username`; presence is checked per person either way. |
| An account was disabled for inactivity and is still disabled after rejoining | By design. Being in a Telegram channel is not using Jellyfin. | Re-enable it yourself. The daemon drops its claim and leaves it alone until the account is used and goes quiet again. |
| Lots of accounts show "no recorded use" | Jellyfin has no `LastActivityDate` or `LastLoginDate` for them | Nothing to do. The inactivity rule never disables on missing data; `/inactive` lists who it can judge. |
| Nothing is ever disabled | Dry run is still on | `/status` shows it; `/dryrun off` |
| `Member list came back below THRESHOLD_ENTRIES` | Telegram returned a partial list, or the threshold is too high | This is the guardrail working. Check the threshold against your real member count. |
| Someone left but is still enabled | The grace window has not elapsed | `/status` shows how many are waiting; lower `GRACE_HOURS` if you want it sooner |
| Jellyfin API errors (401/403) | Invalid or expired API key | Regenerate it in the Jellyfin dashboard |

