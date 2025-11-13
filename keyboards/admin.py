from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


def admin_main_reply_kb() -> ReplyKeyboardMarkup:
    """Return nicely arranged reply keyboard for admin panel."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="📊 Statistika")
    builder.button(text="📢 Kanal boshqaruvi")
    builder.button(text="👑 Admin boshqaruvi")
    builder.button(text="📨 Xabar yuborish")
    builder.button(text="📝 Start xabarini tahrirlash")
    builder.button(text="🔗 Ulashish tugmasi")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup(resize_keyboard=True)


def channels_management_kb(channels: list[dict]) -> InlineKeyboardMarkup:
    """Build inline keyboard for managing subscription channels."""
    builder = InlineKeyboardBuilder()
    
    # Add main channel management buttons
    builder.button(text="❌ Obunani o'chirish", callback_data="admin_disable_subscription")
    builder.button(text="📋 Kanallar ro'yxati", callback_data="admin_channels_list")
    builder.button(text="➕ Kanal qo'shish", callback_data="admin_channel_add")
    builder.button(text="🗑️ Kanal o'chirish", callback_data="admin_channel_delete")
    builder.button(text="📝 Obuna xabarini tahrirlash", callback_data="admin_edit_subscription_text")
    
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def channels_list_kb(channels: list[dict]) -> InlineKeyboardMarkup:
    """Build inline keyboard showing list of channels for deletion."""
    builder = InlineKeyboardBuilder()
    if channels:
        for channel in channels:
            label = channel.get("title") or channel.get("link")
            # Truncate long labels
            if len(label) > 25:
                label = label[:22] + "..."
            builder.button(
                text=f"❌ {label}",
                callback_data=f"admin_channel_remove:{channel['channel_id']}",
            )
    builder.button(text="🔙 Orqaga", callback_data="admin_channels_back")
    builder.adjust(1)
    return builder.as_markup()


def admins_management_kb(admins: list[dict], can_manage: bool) -> InlineKeyboardMarkup:
    """Inline keyboard with admin list and management controls."""
    builder = InlineKeyboardBuilder()
    for admin in admins:
        label = admin.get("username") or str(admin.get("user_id"))
        text = f"👤 {label}"
        callback_data = "noop"
        if can_manage and admin.get("user_id"):
            callback_data = f"admin_remove:{admin['user_id']}"
            text = f"❌ {label}"
        builder.button(text=text, callback_data=callback_data)
    if can_manage:
        builder.button(text="➕ Admin qo'shish", callback_data="admin_add")
    builder.adjust(1)
    return builder.as_markup()


def confirm_keyboard(yes_callback: str, no_callback: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Ha", callback_data=yes_callback)
    builder.button(text="❌ Bekor", callback_data=no_callback)
    builder.adjust(2)
    return builder.as_markup()


def share_button_settings_kb(enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "✅ Tugmani o'chirish" if enabled else "▶️ Tugmani yoqish"
    builder.button(text=toggle_text, callback_data="admin_share_toggle")
    builder.button(text="✏️ Matnni tahrirlash", callback_data="admin_share_text")
    builder.button(text="🔗 Havolani tahrirlash", callback_data="admin_share_url")
    builder.button(text="🔙 Orqaga", callback_data="admin_share_back")
    builder.adjust(1)
    return builder.as_markup()
