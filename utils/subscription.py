from __future__ import annotations

import logging
from typing import Iterable, List, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from database.core import get_channels, get_setting


def _is_subscription_required() -> bool:
    raw = get_setting("subscription_enabled", "1")
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "ha", "enabled", "on", "yes"}


async def check_user_subscription(
    bot: Bot,
    user_id: int,
    channels: Iterable[dict],
) -> Tuple[bool, List[dict]]:
    """Return subscription status and list of channels the user still needs to join."""
    missing: List[dict] = []
    for channel in channels:
        channel_id = channel["channel_id"]
        try:
            member = await bot.get_chat_member(channel_id, user_id)
            if member.status in {"left", "kicked"}:
                missing.append(channel)
        except TelegramForbiddenError:
            logging.warning("Bot kanalga qo'shilmagan: %s", channel_id)
            missing.append(channel)
        except TelegramBadRequest:
            logging.error("Kanal topilmadi yoki kirish imkonsiz: %s", channel_id)
            missing.append(channel)
    return len(missing) == 0, missing


async def ensure_user_subscription(bot: Bot, user_id: int) -> Tuple[bool, List[dict]]:
    if not _is_subscription_required():
        return True, []

    channels = get_channels()
    if not channels:
        return True, []
    return await check_user_subscription(bot, user_id, channels)


