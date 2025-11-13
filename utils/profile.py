from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from database.core import get_user_counts


async def update_bot_monthly_users_badge(bot: Bot) -> None:
    """Refresh bot profile description with monthly active user count."""
    # Bu funksiya o'chirildi - bot profilida oylik foydalanuvchilar ko'rsatilmaydi
    pass


async def periodic_profile_updates(bot: Bot, interval_seconds: int = 3600) -> None:
    """Continuously update bot profile at the given interval."""
    # Bu funksiya o'chirildi - bot profilida oylik foydalanuvchilar ko'rsatilmaydi
    while True:
        await asyncio.sleep(max(300, interval_seconds))
