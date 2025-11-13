from __future__ import annotations

from datetime import datetime, timezone

BOT_START_TIME = datetime.now(timezone.utc)


def mark_bot_started() -> None:
    """Reset the bot start time marker."""
    global BOT_START_TIME
    BOT_START_TIME = datetime.now(timezone.utc)


def format_uptime() -> str:
    """Return human-readable uptime string in Uzbek."""
    delta = datetime.now(timezone.utc) - BOT_START_TIME
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days} kun")
    if hours:
        parts.append(f"{hours} soat")
    if minutes:
        parts.append(f"{minutes} daqiqa")
    if not parts:
        parts.append(f"{seconds} soniya")
    return " ".join(parts)
