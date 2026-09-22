"""Entry point: one sync cycle every interval, plus the owner's bot."""

from __future__ import annotations

import asyncio
import logging
import time

from . import bot, db, sync, telegram
from .config import ConfigError, load_config
from .jellyfin import JellyfinClient

log = logging.getLogger("jellytelegram")


async def run_cycle(ctx: bot.BotContext) -> str:
    """One pass: read the world, decide, apply, report."""
    now = int(time.time())
    dry_run = db.get_dry_run(ctx.conn, ctx.config.dry_run)
    participants = await ctx.fetch_participants()

    if participants is None:
        db.record_audit(ctx.conn, "guardrail", None, "member list below threshold", now=now)
        db.set_last_sync(ctx.conn, now)
        return "Member list came back below THRESHOLD_ENTRIES. Nothing was changed."

    jellyfin_users = ctx.jellyfin.users_by_name()
    links = db.links_by_user(ctx.conn)
    actions = sync.decide(
        links_by_user=links,
        participants=set(participants),
        jellyfin_users=jellyfin_users,
        states=db.get_states(ctx.conn),
        now=now,
        grace_seconds=ctx.config.grace_seconds,
        channel_title=ctx.channel_title,
        dry_run=dry_run,
    )

    counts: dict[str, int] = {}
    for action in actions:
        try:
            sync.apply_action(ctx.conn, ctx.jellyfin, action, dry_run, now)
        except Exception as error:  # one bad account must not stop the cycle
            log.exception("Action %s on %s failed", action.kind, action.jellyfin_user)
            db.record_audit(ctx.conn, "error", action.jellyfin_user, f"{action.kind}: {error}", now=now)
            await _notify(ctx, f"Could not {action.kind} {action.jellyfin_user}: {error}")
            continue
        counts[action.kind] = counts.get(action.kind, 0) + 1
        log.info("%s %s (%s)", action.kind, action.jellyfin_user, action.detail)
        if action.notify:
            await _notify(ctx, action.notify)

    db.set_last_sync(ctx.conn, now)
    summary = (
        f"Sync done{' (dry run)' if dry_run else ''}. "
        f"{len(participants)} channel members, {len(links)} linked Jellyfin users. "
        f"Disabled {counts.get(sync.DISABLE, 0)}, re-enabled {counts.get(sync.ENABLE, 0)}, "
        f"newly absent {counts.get(sync.MARK_ABSENT, 0)}, back {counts.get(sync.CLEAR_ABSENT, 0)}."
    )
    return summary


async def _notify(ctx: bot.BotContext, text: str) -> None:
    if ctx.notify is None:
        return
    try:
        await ctx.notify(text)
    except Exception:  # a failed DM must not abort the cycle
        log.exception("Could not send the owner a message")


async def periodic(ctx: bot.BotContext, cycles: int | None = None) -> None:
    """Run cycles forever (or ``cycles`` times, for tests)."""
    remaining = cycles
    while remaining is None or remaining > 0:
        try:
            log.info("%s", await run_cycle(ctx))
        except Exception as error:
            log.exception("Sync cycle failed")
            await _notify(ctx, f"Sync cycle failed: {error}")
        if remaining is not None:
            remaining -= 1
            if remaining <= 0:
                return
        await asyncio.sleep(ctx.config.interval)


async def build_context(config, conn, user_client, bot_client) -> bot.BotContext:
    title = await telegram.channel_title(user_client, config.channel)
    context = bot.BotContext(
        conn=conn,
        jellyfin=JellyfinClient(config.jellyfin_url, config.jellyfin_api_key),
        config=config,
        resolve_telegram_id=lambda handle: telegram.resolve_telegram_id(user_client, handle),
        fetch_participants=lambda: telegram.fetch_participants(
            user_client, config.channel, config.threshold_entries
        ),
        run_cycle=None,
        channel_title=title,
        notify=lambda text: telegram.send_owner(bot_client, config.owner_id, text),
    )
    context.run_cycle = lambda: run_cycle(context)
    return context


async def amain(config) -> int:  # pragma: no cover - bootstrap
    conn = db.connect(config.db_path)
    imported = db.migrate(conn, config.legacy_db_path)
    if imported:
        log.info("Imported %d links from the pre-1.0 users table", imported)

    user_client = telegram.build_user_client(config)
    await user_client.connect()
    try:
        await telegram.ensure_authorized(user_client)
        bot_client = telegram.build_bot_client(config)
        await bot_client.start(bot_token=config.bot_token)
        context = await build_context(config, conn, user_client, bot_client)
        bot.register(bot_client, context)
        await _notify(
            context,
            f"Sync daemon started for {context.channel_title}. "
            f"Dry run is {'on' if db.get_dry_run(conn, config.dry_run) else 'off'}. /help for commands.",
        )
        asyncio.create_task(periodic(context))
        await bot_client.run_until_disconnected()
    finally:
        await user_client.disconnect()
        conn.close()
    return 0


def main() -> int:  # pragma: no cover - bootstrap
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    try:
        config = load_config()
    except ConfigError as error:
        log.error("Configuration error: %s", error)
        return 2
    try:
        return asyncio.run(amain(config))
    except telegram.TelegramNotAuthorized as error:
        log.error("%s", error)
        return 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
