from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def subscription_keyboard(channels: list[dict]) -> InlineKeyboardMarkup:
    """Create inline keyboard with required subscription channels."""
    builder = InlineKeyboardBuilder()
    for channel in channels:
        label = channel.get("title") or channel.get("link") or "Kanal"
        builder.button(text=f"➕ {label}", url=channel.get("link"))
    builder.adjust(1)
    builder.button(text="✅ Tekshirish", callback_data="check_subscription")
    builder.adjust(1)
    return builder.as_markup()
