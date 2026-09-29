<p align="center">
  <img src="https://raw.githubusercontent.com/GeiserX/jellyfin-telegram-channel-sync/main/docs/images/banner.svg" alt="jellyfin-telegram-channel-sync banner" width="900"/>
</p>

<p align="center">
  <a href="https://github.com/GeiserX/jellyfin-telegram-channel-sync/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/GeiserX/jellyfin-telegram-channel-sync/ci.yml?style=flat-square&label=CI" alt="CI"></a>
  <a href="https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/LICENSE"><img src="https://img.shields.io/github/license/GeiserX/jellyfin-telegram-channel-sync?style=flat-square&color=6B4C9A" alt="License"></a>
  <a href="https://hub.docker.com/r/drumsergio/jellyfin-telegram-channel-sync"><img src="https://img.shields.io/docker/pulls/drumsergio/jellyfin-telegram-channel-sync?style=flat-square&logo=docker&color=0088CC" alt="Docker Pulls"></a>
  <a href="https://github.com/awesome-jellyfin/awesome-jellyfin#readme"><img src="https://img.shields.io/badge/listed%20on-awesome--jellyfin-00a4dc?style=flat-square&logo=jellyfin&logoColor=white" alt="listed on awesome-jellyfin"></a>
</p>

A daemon that keeps Jellyfin accounts in step with a Telegram channel. If you hand out Jellyfin access through a private channel, that channel is your list of who should have access. Leave it and your account is disabled. Come back and it is enabled again.

You say who is who from a Telegram bot, in your own private chat with it. The bot also tells you every time an account changes.

## Features

- Disables a Jellyfin account once its person has been out of the channel for `GRACE_HOURS` (72 by default), and enables it again when they rejoin.
- Checks each linked person with the Bot API [`getChatMember`](https://core.telegram.org/bots/api#getchatmember), so it works on channels past Telegram's 200-member listing cap.
- Never acts on an unanswered lookup. It only undoes its own changes and never touches administrators or `EXEMPT_USERS`.
- A second rule disables accounts nobody has used for `INACTIVE_DAYS` (365 by default).
- Links several Telegram accounts to one Jellyfin user. Present if any of them is in the channel.
- Managed from your private chat with the bot (`/link`, `/unknown`, `/status`, `/inactive`, `/dryrun`), which also reports every change.
- Starts in dry-run mode. State, the grace clock and an audit log live in one SQLite file.

## Quick start

Write the compose file from [Installation](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/installation.md), then:

```bash
docker compose run --rm jellytelegram-sync python -m app.login
docker compose up -d
```

Send `/start` to your bot, link people with `/link`, and send `/dryrun off` once `/links` looks right.

## Documentation

- [How it works](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/how-it-works.md): the decision rules, the inactivity rule, why two Telegram sessions, what is stored
- [Installation](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/installation.md): prerequisites, the five setup steps, upgrading from 0.x
- [Bot commands](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/commands.md)
- [Configuration](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/configuration.md): environment variables
- [Troubleshooting](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/troubleshooting.md)
- [Development](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/docs/development.md)

## Related projects

Other Jellyfin projects by GeiserX:

- [quality-gate](https://github.com/GeiserX/quality-gate) — Restrict users to specific media versions based on configurable path-based policies
- [smart-covers](https://github.com/GeiserX/smart-covers) — Cover extraction for books, audiobooks, comics, magazines, and music libraries with online fallback
- [whisper-subs](https://github.com/GeiserX/whisper-subs) — Automatic subtitle generation using local AI models powered by whisper.cpp
- [quality-gate-encoder](https://github.com/GeiserX/quality-gate-encoder) (formerly jellyfin-encoder) — Automatic 720p HEVC/AV1 transcoding service with hardware acceleration

Other Telegram projects by GeiserX:

- [paperless-telegram-bot](https://github.com/GeiserX/paperless-telegram-bot) — Manage Paperless-NGX documents through Telegram
- [AskePub](https://github.com/GeiserX/AskePub) — Telegram bot for ePub annotation with GPT-4
- [telegram-delay-channel-cloner](https://github.com/GeiserX/telegram-delay-channel-cloner) — Relay messages between channels with delay
- [telegram-slskd-local-bot](https://github.com/GeiserX/telegram-slskd-local-bot) — Automated music discovery and download via Telegram

## License

[GNU General Public License v3.0](https://github.com/GeiserX/jellyfin-telegram-channel-sync/blob/main/LICENSE).
