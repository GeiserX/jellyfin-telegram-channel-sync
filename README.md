<p align="center">
  <img src="https://raw.githubusercontent.com/GeiserX/jellyfin-telegram-channel-sync/main/docs/images/banner.svg" alt="jellyfin-telegram-channel-sync" width="900"/>
</p>

<p align="center">
  <a href="https://github.com/GeiserX/jellyfin-telegram-channel-sync/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/GeiserX/jellyfin-telegram-channel-sync/ci.yml?style=flat-square&label=CI" alt="CI"></a>
  <a href="https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/LICENSE"><img src="https://img.shields.io/github/license/GeiserX/jellyfin-telegram-channel-sync?style=flat-square&color=6B4C9A" alt="License"></a>
  <a href="https://hub.docker.com/r/drumsergio/jellyfin-telegram-channel-sync"><img src="https://img.shields.io/docker/pulls/drumsergio/jellyfin-telegram-channel-sync?style=flat-square&logo=docker&color=0088CC" alt="Docker Pulls"></a>
  <a href="https://github.com/awesome-jellyfin/awesome-jellyfin#readme"><img src="https://img.shields.io/badge/listed%20on-awesome--jellyfin-00a4dc?style=flat-square&logo=jellyfin&logoColor=white" alt="listed on awesome-jellyfin"></a>
</p>

A daemon, run in Docker, that keeps Jellyfin accounts in step with a private Telegram channel: leave the channel and your account is disabled, come back and it is enabled again. You link people to accounts from a private chat with a Telegram bot, which also reports every change.

## Features

- Disables a Jellyfin account once its person has been out of the channel for `GRACE_HOURS` (72 by default), and enables it again when they rejoin.
- Checks each linked person with the Bot API [`getChatMember`](https://core.telegram.org/bots/api#getchatmember), so it works on channels past Telegram's 200-member listing cap.
- Never acts on an unanswered lookup. It only undoes its own changes and never touches administrators or `EXEMPT_USERS`.
- A second rule disables accounts nobody has used for `INACTIVE_DAYS` (365 by default).
- Links several Telegram accounts to one Jellyfin user. Present if any of them is in the channel.
- Managed from your private chat with the bot (`/link`, `/unknown`, `/status`, `/inactive`, `/dryrun`), which also reports every change.
- Starts in dry-run mode. State, the grace clock and an audit log live in one SQLite file.

## Quick start

Run this in an empty folder, with Telegram API credentials, a bot that is an administrator of the channel, a Jellyfin API key and the channel id at hand ([Getting started](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/getting-started.md) says where each comes from).

```bash
curl -O https://raw.githubusercontent.com/GeiserX/jellyfin-telegram-channel-sync/main/docker-compose.yml
docker compose run --rm jellytelegram-sync python -m app.login
docker compose up -d
```

Fill in the `environment:` block of `docker-compose.yml` before the second command. Then send `/start` to your bot, link people with `/link`, and send `/dryrun off` once `/links` looks right.

## Documentation

- [Getting started](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/getting-started.md): prerequisites, the five setup steps, upgrading from 0.x
- [Configuration](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/configuration.md): environment variables
- [Usage](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/usage.md): the bot commands
- [How it works](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/how-it-works.md): the decision rules, the inactivity rule, why two Telegram sessions, what is stored
- [Troubleshooting](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/troubleshooting.md)
- [Development](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/development.md)

## Related projects

Part of the same family: [quality-gate](https://github.com/GeiserX/quality-gate), [smart-covers](https://github.com/GeiserX/smart-covers), [whisper-subs](https://github.com/GeiserX/whisper-subs), [quality-gate-encoder](https://github.com/GeiserX/quality-gate-encoder), [paperless-telegram-bot](https://github.com/GeiserX/paperless-telegram-bot), [telegram-slskd-local-bot](https://github.com/GeiserX/telegram-slskd-local-bot). What each one does is in [Related projects](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/related.md).

## License

[GPL-3.0-or-later](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/LICENSE)
