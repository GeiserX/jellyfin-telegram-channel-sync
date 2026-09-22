"""One-time interactive sign-in for both Telegram sessions.

    docker compose run --rm jellytelegram-sync python -m app.login

Writes ``session_name.session`` (your user account, which lists the channel
members) and ``bot_session.session`` into the data volume.
"""

from __future__ import annotations

import asyncio
import os

from telethon import TelegramClient

from .config import ConfigError, load_telegram_login


async def run_login(login, client_factory=TelegramClient) -> int:
    os.makedirs(login.data_dir, exist_ok=True)

    print("Signing in your user account. Telegram will ask for your phone number, then a code.")
    user_client = client_factory(login.user_session, login.api_id, login.api_hash)
    await user_client.start()
    me = await user_client.get_me()
    print(f"User session ready: {login.user_session}.session (signed in as id {me.id})")
    await user_client.disconnect()

    print("Signing in the bot.")
    bot_client = client_factory(login.bot_session, login.api_id, login.api_hash)
    await bot_client.start(bot_token=login.bot_token)
    me = await bot_client.get_me()
    print(f"Bot session ready: {login.bot_session}.session (signed in as @{me.username})")
    await bot_client.disconnect()

    print("Both sessions are in the data volume. Start the daemon normally now.")
    return 0


def main() -> int:
    try:
        login = load_telegram_login()
    except ConfigError as error:
        print(f"Configuration error: {error}")
        return 2
    return asyncio.run(run_login(login))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
