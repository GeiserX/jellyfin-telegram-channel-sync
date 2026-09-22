"""One-time interactive login for both Telegram sessions.

    docker compose run --rm jellytelegram-sync python -m app.login
"""

from __future__ import annotations

import os

from telethon import TelegramClient

from .config import ConfigError, load_telegram_login


def main() -> int:  # pragma: no cover - interactive
    try:
        login = load_telegram_login()
    except ConfigError as error:
        print(f"Configuration error: {error}")
        return 2

    os.makedirs(login.data_dir, exist_ok=True)

    print("Signing in the user account (phone number, then the code Telegram sends you).")
    with TelegramClient(login.user_session, login.api_id, login.api_hash) as client:
        me = client.get_me()
        print(f"User session ready: {login.user_session}.session (signed in as id {me.id})")

    print("Signing in the bot.")
    with TelegramClient(login.bot_session, login.api_id, login.api_hash).start(
        bot_token=login.bot_token
    ) as bot:
        me = bot.get_me()
        print(f"Bot session ready: {login.bot_session}.session (@{me.username})")

    print("Both sessions are stored in the data volume. Start the daemon normally now.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
