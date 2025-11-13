from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import core as db
from keyboards.admin import (
    admin_main_reply_kb,
    admins_management_kb,
    channels_management_kb,
    channels_list_kb,
    share_button_settings_kb,
)
from utils.config import get_settings
from states import AdminManageState, BroadcastState, ChannelManageState, SettingsState
from utils.backup import create_database_backup
from utils.stats import build_stats_overview
from utils.system import format_uptime
from utils.profile import update_bot_monthly_users_badge

admin_router = Router()


def _build_admins_list() -> str:
    """Format admins list for display."""
    admins = db.get_admins()
    if not admins:
        return "Adminlar ro'yxati bo'sh."
    
    header = "👥 <b>Adminlar ro'yxati:</b>\n\n"
    lines = []
    for index, admin in enumerate(admins, start=1):
        user_id = html.escape(str(admin.get("user_id", "-")))
        username = admin.get("username")
        if username:
            username_text = f"@{html.escape(username)}"
        else:
            username_text = "@nomalum"
        lines.append(f"{index}. <b>{username_text}</b> — <code>{user_id}</code>")

    return header + "\n".join(lines)


def _build_channel_management_view() -> tuple[str, InlineKeyboardMarkup]:
    """Return formatted text and keyboard for channel management view."""
    channels = db.get_channels()
    subscription_enabled = _get_subscription_state()

    text = "📺 *Kanal boshqaruvi*\n\n"
    if subscription_enabled:
        text += "🔒 Majburiy obuna: Yoqilgan ✅\n\n"
    else:
        text += "🔒 Majburiy obuna: O'chirilgan ❌\n\n"

    if channels:
        text += "Majburiy obuna kanallari:\n"
        for index, channel in enumerate(channels, 1):
            title = channel.get("title") or "Noma'lum"
            text += f"{index}. {title}\n"
    else:
        text += "Hech qanday majburiy obuna kanali yo'q."

    text += "\n\nAmalni tanlang:"
    keyboard = channels_management_kb(channels)
    return text, keyboard


def _get_subscription_state() -> bool:
    """Read subscription_enabled flag from settings table."""
    raw = db.get_setting("subscription_enabled", "1")
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "ha", "enabled", "on", "yes"}


def _set_subscription_state(value: bool) -> None:
    """Persist subscription_enabled flag."""
    db.set_setting("subscription_enabled", "1" if value else "0")


def _truthy(value: Optional[str], *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "ha", "yes", "on", "enabled"}


def _get_share_button_state() -> tuple[bool, str, str]:
    enabled = _truthy(
        db.get_setting("share_button_enabled", db.DEFAULT_SETTINGS.get("share_button_enabled", "1")),
        default=True,
    )
    text_value = db.get_setting("share_button_text", db.DEFAULT_SETTINGS.get("share_button_text", "")) or ""
    url_value = db.get_setting("share_button_url", db.DEFAULT_SETTINGS.get("share_button_url", "")) or ""
    return enabled, text_value, url_value


def _build_share_button_overview() -> tuple[str, InlineKeyboardMarkup]:
    enabled, text_value, url_value = _get_share_button_state()
    status = "✅ Yoqilgan" if enabled else "❌ O'chirilgan"
    text_display = html.escape(text_value) if text_value else "<i>Matn kiritilmagan</i>"
    if url_value:
        url_display = (
            f"<a href=\"{html.escape(url_value, quote=True)}\">{html.escape(url_value)}</a>"
        )
    else:
        url_display = "<i>Havola kiritilmagan</i>"

    overview = (
        "🔗 <b>Ulashish tugmasi sozlamalari</b>\n\n"
        f"Holati: {status}\n"
        f"Matn: {text_display}\n"
        f"Havola: {url_display}\n\n"
        "✏️ Pastdagi tugmalar orqali matn, havola yoki holatini o'zgartiring."
    )

    keyboard = share_button_settings_kb(enabled)
    return overview, keyboard


def _normalize_button_url(raw: str) -> Optional[str]:
    value = raw.strip()
    if not value:
        return None
    if value.startswith(("http://", "https://", "tg://")):
        return value
    if value.startswith("t.me/"):
        return f"https://{value}"
    return None


@admin_router.message(Command("admin"))
async def admin_panel_entry(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    user_name = message.from_user.first_name if message.from_user else "Admin"
    welcome_text = (
        f"👋 Assalomu alaykum, <b>{html.escape(user_name)}</b>!\n\n"
        "🔧 <b>Admin panelga xush kelibsiz</b>\n"
        "Quyidagi bo'limlardan birini tanlang:\n\n"
        "📊 <i>Statistika</i> - Bot statistikalarini ko'rish\n"
        " <i>Kanallar</i> - Majburiy obuna kanallarini boshqarish\n"
        "👥 <i>Adminlar</i> - Admin huquqlarini boshqarish\n"
    "📨 <i>Xabar yuborish</i> - Barcha foydalanuvchilarga xabar\n"
    "📝 <i>Start matni</i> - Bot xush kelibsiz matnini o'zgartirish\n"
    "🔗 <i>Ulashish tugmasi</i> - Yuklab olingan video tugmasini sozlash"
    )
    await message.answer(welcome_text, reply_markup=admin_main_reply_kb(), parse_mode="HTML")


@admin_router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery) -> None:
    """Handle statistics button callback."""
    if not await _ensure_admin(callback):
        return
    await callback.answer()
    
    try:
        # Get basic statistics only to avoid length issues
        overview = build_stats_overview()
        counts = db.get_user_counts()
        
        # Simple, consistent statistics format
        stats_text = (
            f"📊 *Bot statistikalari*\n\n"
            f"👥 Jami foydalanuvchilar: {counts.get('total', 0):,}\n"
            f"📈 Faol foydalanuvchilar: {counts.get('active', 0):,}\n"
            f"🆕 Yangi foydalanuvchilar: {counts.get('new', 0):,}\n"
            f"🚫 Bloklangan: {counts.get('blocked', 0):,}\n\n"
            f"{overview}"
        )
        
        if callback.message:
            await callback.message.edit_text(stats_text, parse_mode="Markdown")
            
    except Exception as error:
        logging.exception("Statistikalarni olishda xato")
        if callback.message:
            await callback.message.edit_text(f"❌ Xato yuz berdi: {error}")

@admin_router.callback_query(F.data == "admin_channels")
async def admin_channels_callback(callback: CallbackQuery) -> None:
    """Handle channel management button callback."""
    await admin_manage_channels(callback)


@admin_router.message(F.text == "📊 Statistika")
async def admin_show_stats(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    
    try:
        # Get basic statistics only to avoid length issues
        overview = build_stats_overview()
        counts = db.get_user_counts()
        
        # Simple, consistent statistics format
        stats_text = (
            f"📊 <b>BOT STATISTIKASI</b>\n"
            f"{'='*30}\n\n"
            
            f"👥 <b>Foydalanuvchilar:</b>\n"
            f"   • Jami: <code>{overview.total_users:,}</code>\n"
            f"   • Bugun faol: <code>{overview.active_today:,}</code>\n"
            f"   • Haftalik faol: <code>{overview.active_week:,}</code>\n"
            f"   • Oylik faol: <code>{overview.active_month:,}</code>\n\n"
            
            f"📥 <b>Yuklab olishlar:</b>\n"
            f"   • Jami: <code>{overview.total_downloads:,}</code>\n\n"
            
            f"📈 <b>O'sish (so'nggi kunlar):</b>\n"
            f"<pre>{overview.growth_chart[:200]}...</pre>\n\n"
            
            f"⏰ <b>Yangilangan:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        await message.answer(stats_text)
        
    except Exception as error:
        logging.exception("Statistika olishda xato")
        await message.answer(f"❌ Statistika olishda xato: {error}")
        


@admin_router.message(F.text == "🔗 Ulashish tugmasi")
async def admin_share_button_menu(message: Message) -> None:
    if not await _ensure_admin(message):
        return

    overview, keyboard = _build_share_button_overview()
    await message.answer(
        overview,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@admin_router.message(F.text == "🗓 Oylik foydalanuvchilar")
async def admin_monthly_users(message: Message, bot: Bot) -> None:
    if not await _ensure_admin(message):
        return

    try:
        users = db.get_monthly_active_users(limit=25)
        if not users:
            await message.answer("❌ So'nggi 30 kun ichida faol foydalanuvchilar topilmadi.")
            return

        lines: list[str] = []
        for index, user in enumerate(users, start=1):
            name = user.get("first_name") or "❓ Noma'lum"
            username = user.get("username")
            if username:
                name = f"<a href='tg://user?id={user.get('user_id')}'>{name}</a> (@{username})"
            else:
                name = f"<a href='tg://user?id={user.get('user_id')}'>{name}</a>"
            
            last_active = _format_last_active(user.get("last_active"))
            downloads = user.get("downloads_count") or 0
            
            lines.append(
                f"{index}. {name}\n"
                f"   📅 <i>{last_active}</i> • 📥 <i>{downloads} yuklab olish</i>"
            )

        # Update bot profile with current monthly count
        await update_bot_monthly_users_badge(bot)
        
        header = (
            f"🗓 <b>So'nggi 30 kun ichida faol foydalanuvchilar</b>\n"
            f"📊 Jami: {len(users)} ta (eng faollari)\n\n"
        )
        
        # Split into chunks if too long
        text = header + "\n".join(lines)
        if len(text) > 4000:
            text = header + "\n".join(lines[:15]) + "\n\n<i>... va boshqalar</i>"
            
        await message.answer(text)
        
    except Exception as error:
        logging.exception("Oylik foydalanuvchilarni olishda xato")
        await message.answer(f"❌ Xato yuz berdi: {error}")


@admin_router.callback_query(F.data == "admin_manage_channels")
async def admin_manage_channels(callback: CallbackQuery) -> None:
    """Handle channels management."""
    if not await _ensure_admin(callback):
        return
    await callback.answer()
    text, keyboard = _build_channel_management_view()

    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        except TelegramBadRequest:
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@admin_router.message(F.text.func(lambda text: text and "Kanal boshqaruvi" in text))
async def admin_manage_channels_text(message: Message) -> None:
    """Show channel management via plain text command."""
    if not await _ensure_admin(message):
        return
    text, keyboard = _build_channel_management_view()
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@admin_router.callback_query(F.data == "admin_disable_subscription")
async def admin_disable_subscription(callback: CallbackQuery) -> None:
    """Toggle subscription requirement."""
    if not await _ensure_admin(callback):
        return
    current_state = _get_subscription_state()
    new_state = not current_state
    _set_subscription_state(new_state)
    
    if new_state:
        status_text = "yoqildi ✅"
    else:
        status_text = "o'chirildi ❌"
    text = f"🔒 Majburiy obuna {status_text}"

    text_view, keyboard = _build_channel_management_view()

    if callback.message:
        try:
            await callback.message.edit_text(text_view, reply_markup=keyboard, parse_mode="Markdown")
        except TelegramBadRequest:
            await callback.message.answer(text_view, reply_markup=keyboard, parse_mode="Markdown")

    await callback.answer(text, show_alert=True)


@admin_router.callback_query(F.data == "admin_channels_list")
async def admin_channels_list(callback: CallbackQuery) -> None:
    """Show detailed channels list."""
    if not await _ensure_admin(callback):
        return
    await callback.answer()
    
    channels = db.get_channels()

    text = "📋 <b>Kanallar ro'yxati</b>\n\n"

    if channels:
        for index, channel in enumerate(channels, 1):
            title_raw = channel.get("title") or "Noma'lum"
            link_raw = channel.get("link") or "Havola yo'q"
            channel_id = channel.get("channel_id", "")

            title = html.escape(title_raw)
            link_display = html.escape(link_raw)
            link_href = html.escape(link_raw, quote=True)
            channel_id_display = html.escape(str(channel_id))

            text += (
                f"{index}. <b>{title}</b>\n"
                f"&ensp;🆔 ID: <code>{channel_id_display}</code>\n"
                f"&ensp;🔗 Havola: <a href=\"{link_href}\">{link_display}</a>\n\n"
            )
    else:
        text += "Hech qanday kanal qo'shilmagan."

    keyboard = channels_list_kb(channels)

    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)
        except TelegramBadRequest as error:
            logging.debug("Channel list render failed, sending new message: %s", error)
            try:
                await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)
            except TelegramBadRequest:
                await callback.answer("Ro'yxatni ko'rsatib bo'lmadi.", show_alert=True)


@admin_router.callback_query(F.data == "admin_channels_back")
async def admin_channels_back(callback: CallbackQuery) -> None:
    """Go back to channel management."""
    await admin_manage_channels(callback)


@admin_router.callback_query(F.data == "admin_channel_delete")
async def admin_channel_delete(callback: CallbackQuery) -> None:
    """Show channels for deletion."""
    if not await _ensure_admin(callback):
        return
    await callback.answer()
    
    channels = db.get_channels()
    
    text = "🗑 *Kanal o'chirish*\n\n"
    
    if channels:
        text += "O'chirish uchun kanalni tanlang:"
        keyboard = channels_list_kb(channels)
    else:
        text += "O'chirish uchun kanallar yo'q."
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Orqaga", callback_data="admin_channels_back")
        keyboard = builder.as_markup()
    
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        except TelegramBadRequest:
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@admin_router.callback_query(F.data.startswith("admin_channel_remove:"))
async def admin_channel_remove_confirm(callback: CallbackQuery) -> None:
    """Handle channel removal."""
    if not await _ensure_admin(callback):
        return
    
    channel_id = callback.data.split(":", 1)[1]
    
    # Get channel info before deletion
    channels = db.get_channels()
    channel_to_remove = None
    for channel in channels:
        if str(channel.get('channel_id', '')) == channel_id:
            channel_to_remove = channel
            break
    
    if channel_to_remove:
        try:
            db.remove_channel(channel_id)
            title = channel_to_remove.get('title') or 'Noma\'lum kanal'
            await callback.answer(f"✅ {title} kanali o'chirildi", show_alert=True)
        except Exception as e:
            await callback.answer("❌ Kanal o'chirishda xato", show_alert=True)
    else:
        await callback.answer("❌ Kanal topilmadi", show_alert=True)
    
    # Return to channel management
    await admin_manage_channels(callback)


@admin_router.callback_query(F.data == "admin_channel_add")
async def admin_channel_add(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(callback):
        return
    await state.set_state(ChannelManageState.waiting_for_link)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "📡 Yangi kanal qo'shish uchun kanal havolasini yuboring yoki kanal postini forward qiling."
        )


@admin_router.message(ChannelManageState.waiting_for_link)
async def process_channel_link(message: Message, state: FSMContext, bot: Bot) -> None:
    if not await _ensure_admin(message):
        return
    chat = message.forward_from_chat
    identifier: Optional[str] = None

    if chat and chat.type == "channel":
        identifier = str(chat.id)
        title = chat.title or ""
        link = message.text or (f"https://t.me/{chat.username}" if chat.username else "")
        if not link:
            await message.answer(
                "Taklif havolasini ham yuboring. Kanal yopiq bo'lsa, 'https://t.me/+....' ko'rinishidagi havolani kiriting."
            )
            return
    else:
        raw = (message.text or "").strip()
        if not raw:
            await message.answer("Havola topilmadi, qayta yuboring.")
            return
        cleaned = raw.split("?", 1)[0].strip()
        cleaned = cleaned.replace("https://t.me/", "@").replace("http://t.me/", "@")
        cleaned = cleaned.replace("tg://resolve?domain=", "@")
        cleaned = cleaned.replace("t.me/", "@")
        cleaned = cleaned.strip()

        lookup = cleaned
        if cleaned.startswith("-100"):
            lookup = cleaned  # numeric channel id
        elif cleaned.startswith("@"):
            lookup = cleaned
        else:
            lookup = f"@{cleaned}"

        try:
            chat_info = await bot.get_chat(lookup)
        except TelegramBadRequest:
            await message.answer("Kanal topilmadi. Havolani tekshirib ko'ring.")
            return
        identifier = str(chat_info.id)
        title = chat_info.title or raw
        link = f"https://t.me/{chat_info.username}" if chat_info.username else raw
        if not link:
            await message.answer("Havola aniqlanmadi, iltimos to'g'ri havolani yuboring.")
            return

    if not identifier:
        await message.answer("Kanal ma'lumotlari aniqlanmadi.")
        return

    db.add_channel(identifier, title, link)
    db.add_log(message.from_user.id if message.from_user else None, f"channel_add:{identifier}")
    await state.clear()
    channels = db.get_channels()
    await message.answer(
        "✅ Kanal muvaffaqiyatli qo'shildi.",
        reply_markup=channels_management_kb(channels),
    )


async def _prompt_subscription_text_edit(message: Message, state: FSMContext) -> None:
    """Send current subscription text and ask for replacement."""
    current = db.get_setting("subscribe_text", db.DEFAULT_SETTINGS["subscribe_text"])
    await state.set_state(SettingsState.waiting_for_subscription_text)
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Bekor qilish", callback_data="admin_edit_subscription_cancel")
    builder.adjust(1)

    escaped_current = html.escape(current)
    logging.debug("Prompting subscription text edit; length=%d", len(current))

    try:
        await message.answer(
            "🔔 <b>Obuna talabi xabarini tahrirlash</b>\n\n"
            "<b>Amaldagi matn:</b>\n"
            f"<pre>{escaped_current}</pre>\n\n"
            "✏️ Yangi matnni yuboring.\n"
            "ℹ️ HTML teglari (&lt;b&gt;, &lt;i&gt;, &lt;code&gt; va boshqalar) ishlatishingiz mumkin.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as error:
        logging.warning("HTML render failed in subscription edit prompt: %s", error)
        await message.answer(
            "🔔 Obuna talabi xabarini tahrirlash\n\n"
            "Amaldagi matn:\n"
            f"{current}\n\n"
            "Yangi matnni yuboring. HTML teglari ishlatishingiz mumkin.",
            reply_markup=builder.as_markup(),
        )


@admin_router.callback_query(F.data.startswith("admin_channel_remove"))
async def admin_channel_remove(callback: CallbackQuery) -> None:
    if not await _ensure_admin(callback):
        return
    parts = callback.data.split(":", maxsplit=1)
    if len(parts) != 2:
        await callback.answer("Noto'g'ri ma'lumot.", show_alert=True)
        return
    channel_id = parts[1]
    db.remove_channel(channel_id)
    db.add_log(callback.from_user.id if callback.from_user else None, f"channel_remove:{channel_id}")
    await callback.answer("Kanal o'chirildi.")
    channels = db.get_channels()
    if callback.message:
        text = "Majburiy obuna kanallari ro'yxati:" if channels else "Royxat bo'sh."
        try:
            await callback.message.edit_text(text, reply_markup=channels_management_kb(channels))
        except TelegramBadRequest:
            pass


@admin_router.message(F.text.in_({"👥 Adminlar", "👑 Admin boshqaruvi"}))
async def admin_manage_admins(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    
    # Build inline keyboard with 4 buttons
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Adminlar ro'yxati", callback_data="admin_admins_list")
    builder.button(text="➕ Admin qo'shish", callback_data="admin_add")
    builder.button(text="❌ Admin o'chirish", callback_data="admin_remove_select")
    builder.button(text="⚙️ Huquqlar", callback_data="admin_permissions_select")
    builder.adjust(2, 2)
    
    text = (
        "👑 <b>Admin boshqaruvi</b>\n\n"
        "Quyidagi amallardan birini tanlang:"
    )
    
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


async def _prompt_start_text_edit(message: Message, state: FSMContext) -> None:
    """Send current start text and ask for replacement."""
    current = db.get_setting("start_text", db.DEFAULT_SETTINGS["start_text"])
    await state.set_state(SettingsState.waiting_for_start_text)
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Bekor qilish", callback_data="admin_edit_start_cancel")
    builder.adjust(1)

    # Escape HTML entities to display correctly
    escaped_current = html.escape(current)
    logging.debug("Prompting start text edit; current length=%d", len(current))

    try:
        await message.answer(
            "📝 <b>Start xabarini tahrirlash</b>\n\n"
            "<b>Amaldagi matn:</b>\n"
            f"<pre>{escaped_current}</pre>\n\n"
            "✏️ Yangi matnni shu yerga yuboring.\n"
            "ℹ️ HTML teglari (&lt;b&gt;, &lt;i&gt;, &lt;code&gt; va boshqalar) ishlatishingiz mumkin.\n"
            "❗ Bekor qilish uchun pastdagi tugmani bosing yoki 'Bekor' deb yozing.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        # Fallback to plain text if HTML parsing fails for any reason
        logging.warning("HTML render failed in start edit prompt: %s. Falling back to plain text.", e)
        await message.answer(
            "📝 Start xabarini tahrirlash\n\n"
            "Amaldagi matn:\n"
            f"{current}\n\n"
            "Yangi matnni shu yerga yuboring.\n"
            "HTML teglari (<b>, <i>, <code> ...) ishlatishingiz mumkin.",
            reply_markup=builder.as_markup(),
        )


@admin_router.callback_query(F.data == "admin_edit_subscription_text")
async def admin_edit_subscription_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(callback):
        return
    await callback.answer()
    if callback.message:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
        await _prompt_subscription_text_edit(callback.message, state)


@admin_router.callback_query(F.data == "admin_edit_subscription_cancel")
async def admin_edit_subscription_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(callback):
        return
    await state.clear()
    await callback.answer("Bekor qilindi", show_alert=False)
    if callback.message:
        try:
            await callback.message.edit_text("🔔 Obuna xabari tahrirlash bekor qilindi.")
        except TelegramBadRequest:
            await callback.message.answer("🔔 Obuna xabari tahrirlash bekor qilindi.")


@admin_router.message(F.text == "📝 Start xabarini tahrirlash")
async def admin_edit_start_text(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        return
    await _prompt_start_text_edit(message, state)


@admin_router.callback_query(F.data == "admin_edit_start")
async def admin_edit_start_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(callback):
        return
    await callback.answer()
    if callback.message:
        await _prompt_start_text_edit(callback.message, state)


@admin_router.callback_query(F.data == "admin_edit_start_cancel")
async def admin_edit_start_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(callback):
        return
    await state.clear()
    await callback.answer("Bekor qilindi", show_alert=False)
    if callback.message:
        try:
            await callback.message.edit_text("📝 Start matni tahrirlash bekor qilindi.")
        except TelegramBadRequest:
            await callback.message.answer("📝 Start matni tahrirlash bekor qilindi.")


@admin_router.callback_query(F.data == "admin_share_toggle")
async def admin_share_toggle(callback: CallbackQuery) -> None:
    if not await _ensure_admin(callback):
        return

    enabled, _, _ = _get_share_button_state()
    new_state = not enabled
    db.set_setting("share_button_enabled", "1" if new_state else "0")
    db.add_log(callback.from_user.id if callback.from_user else None, f"share_button_toggle:{int(new_state)}")

    status_text = "yoqildi ✅" if new_state else "o'chirildi ❌"
    await callback.answer(f"Tugma {status_text}.", show_alert=True)

    if callback.message:
        overview, keyboard = _build_share_button_overview()
        try:
            await callback.message.edit_text(
                overview,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except TelegramBadRequest:
            await callback.message.answer(
                overview,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )


@admin_router.callback_query(F.data == "admin_share_text")
async def admin_share_text(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(callback):
        return

    await state.set_state(SettingsState.waiting_for_share_button_text)
    await callback.answer()

    current_text = db.get_setting(
        "share_button_text",
        db.DEFAULT_SETTINGS.get("share_button_text", "♻️ Do'stlarga ulashish"),
    ) or ""

    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Bekor qilish", callback_data="admin_share_text_cancel")
    builder.adjust(1)

    escaped = html.escape(current_text) if current_text else ""

    if callback.message:
        try:
            await callback.message.answer(
                "✏️ <b>Tugma matnini tahrirlash</b>\n\n"
                "Amaldagi matn:\n"
                f"<pre>{escaped or '---'}</pre>\n\n"
                "Yangi matnni yuboring (64 ta belgigacha).\n"
                "➡️ Standart matnga qaytish uchun <code>default</code> deb yozing.",
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            await callback.message.answer(
                "✏️ Tugma matnini tahrirlash\n\n"
                f"Amaldagi matn:\n{current_text or '-'}\n\n"
                "Yangi matnni yuboring (64 belgi).",
                reply_markup=builder.as_markup(),
            )


@admin_router.callback_query(F.data == "admin_share_text_cancel")
async def admin_share_text_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(callback):
        return
    await state.clear()
    await callback.answer("Bekor qilindi.", show_alert=False)

    if callback.message:
        try:
            await callback.message.edit_text("✏️ Tugma matnini tahrirlash bekor qilindi.")
        except TelegramBadRequest:
            await callback.message.answer("✏️ Tugma matnini tahrirlash bekor qilindi.")
        overview, keyboard = _build_share_button_overview()
        await callback.message.answer(
            overview,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


@admin_router.callback_query(F.data == "admin_share_url")
async def admin_share_url(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(callback):
        return

    await state.set_state(SettingsState.waiting_for_share_button_url)
    await callback.answer()

    current_url = db.get_setting(
        "share_button_url",
        db.DEFAULT_SETTINGS.get("share_button_url", ""),
    ) or ""

    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Bekor qilish", callback_data="admin_share_url_cancel")
    builder.adjust(1)

    display_url = html.escape(current_url) if current_url else "<i>Havola kiritilmagan</i>"

    if callback.message:
        try:
            await callback.message.answer(
                "🔗 <b>Tugma havolasini tahrirlash</b>\n\n"
                f"Amaldagi havola: {display_url}\n\n"
                "Yangi havolani yuboring (http/https/tg://).\n"
                "➡️ Havolani o'chirish uchun <code>o'chirish</code> deb yozing.",
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except TelegramBadRequest:
            await callback.message.answer(
                "🔗 Tugma havolasini tahrirlash\n\n"
                f"Amaldagi havola: {current_url or '-'}\n\n"
                "Yangi havolani yuboring (http/https/tg://).",
                reply_markup=builder.as_markup(),
            )


@admin_router.callback_query(F.data == "admin_share_url_cancel")
async def admin_share_url_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(callback):
        return
    await state.clear()
    await callback.answer("Bekor qilindi.", show_alert=False)

    if callback.message:
        try:
            await callback.message.edit_text("🔗 Tugma havolasini tahrirlash bekor qilindi.")
        except TelegramBadRequest:
            await callback.message.answer("🔗 Tugma havolasini tahrirlash bekor qilindi.")
        overview, keyboard = _build_share_button_overview()
        await callback.message.answer(
            overview,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


@admin_router.callback_query(F.data == "admin_share_back")
async def admin_share_back(callback: CallbackQuery) -> None:
    if not await _ensure_admin(callback):
        return
    await callback.answer("Sozlamalar yopildi.")
    if callback.message:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            try:
                await callback.message.edit_text("🔗 Ulashish tugmasi sozlamalari yopildi.")
            except TelegramBadRequest:
                pass
@admin_router.callback_query(F.data == "admin_add")
async def admin_add_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(callback):
        return
    if not _is_main_admin(callback.from_user.id if callback.from_user else 0):
        await callback.answer("Faqat asosiy admin qo'sha oladi.", show_alert=True)
        return
    await state.set_state(AdminManageState.waiting_for_user)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "👥 Qo'shmoqchi bo'lgan foydalanuvchini ID ko'rinishida yuboring yoki uning xabarini forward qiling."
        )


@admin_router.message(AdminManageState.waiting_for_user)
async def process_admin_add(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        return
    if not _is_main_admin(message.from_user.id if message.from_user else 0):
        await message.answer("Faqat asosiy admin o'zgartira oladi.")
        await state.clear()
        return

    target_user_id: Optional[int] = None
    username: Optional[str] = None

    if message.forward_from:
        target_user_id = message.forward_from.id
        username = message.forward_from.username
    else:
        text = (message.text or "").strip()
        if text.isdigit():
            target_user_id = int(text)
        elif text.startswith("@"):
            username = text[1:]
        else:
            await message.answer("ID yoki @username formatida yuboring.")
            return

    if target_user_id is None and username:
        await message.answer(
            "@username orqali ID olish uchun foydalanuvchi xabarini forward qiling yoki ID ni kiriting."
        )
        return

    if target_user_id is None:
        await message.answer("Foydalanuvchi aniqlanmadi.")
        return

    db.add_admin(target_user_id, username)
    db.add_log(message.from_user.id if message.from_user else None, f"admin_add:{target_user_id}")
    await message.answer("✅ Admin muvaffaqiyatli qo'shildi.")
    await state.clear()


@admin_router.callback_query(F.data.startswith("admin_remove:"))
async def admin_remove_callback(callback: CallbackQuery) -> None:
    if not await _ensure_admin(callback):
        return
    if not _is_main_admin(callback.from_user.id if callback.from_user else 0):
        await callback.answer("Faqat asosiy admin o'chira oladi.", show_alert=True)
        return
    _, _, user_id_str = callback.data.partition(":")
    if not user_id_str or not user_id_str.isdigit():
        await callback.answer("Noto'g'ri ma'lumot.", show_alert=True)
        return
    user_id = int(user_id_str)
    main_admin_id = get_settings().main_admin_id
    if user_id == main_admin_id:
        await callback.answer("Asosiy adminni o'chirish mumkin emas.", show_alert=True)
        return
    db.remove_admin(user_id)
    db.add_log(callback.from_user.id if callback.from_user else None, f"admin_remove:{user_id}")
    await callback.answer("✅ Admin o'chirildi.", show_alert=True)
    admins = db.get_admins()
    main_admin_id = get_settings().main_admin_id
    
    text = "❌ <b>Admin o'chirish</b>\n\nO'chirmoqchi bo'lgan adminni tanlang:"
    
    builder = InlineKeyboardBuilder()
    for admin in admins:
        admin_id = admin.get("user_id")
        if admin_id == main_admin_id:
            continue  # Skip main admin
        username = admin.get("username") or str(admin_id)
        builder.button(
            text=f"❌ {username}",
            callback_data=f"admin_remove:{admin_id}"
        )
    builder.button(text="🔙 Orqaga", callback_data="admin_back_to_admin_menu")
    builder.adjust(1)
    
    if callback.message:
        try:
            await callback.message.edit_text(
                text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass


@admin_router.message(F.text == "📨 Xabar yuborish")
async def admin_broadcast_entry(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        return
    await state.set_state(BroadcastState.waiting_for_content)
    cancel_builder = InlineKeyboardBuilder()
    cancel_builder.button(text="❌ Bekor qilish", callback_data="admin_broadcast_cancel")
    cancel_builder.adjust(1)
    await message.answer(
        "📨 <b>Ommaviy xabar yuborish</b>\n\n"
        "Jo'natmoqchi bo'lgan xabaringizni shu yerga yuboring. Bot xabarni barcha foydalanuvchilarga yetkazadi.\n"
        "✅ Qabul qilinadigan formatlar: matn, foto, video, fayl.\n"
        "❌ Jarayonni to'xtatish uchun pastdagi <code>Bekor</code> tugmasini bosing yoki shu so'zni yozing.",
        parse_mode="HTML",
        reply_markup=cancel_builder.as_markup(),
    )


@admin_router.message(BroadcastState.waiting_for_content)
async def admin_broadcast_collect(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        await state.clear()
        return
    if (message.text or "").lower() == "bekor":
        await message.answer("✅ Jo'natish bekor qilindi.")
        await state.clear()
        return

    await state.update_data(
        source_chat=message.chat.id,
        source_message=message.message_id,
        buttons=[],
        preview_chat=None,
        preview_message=None,
    )
    await state.set_state(BroadcastState.waiting_for_buttons)
    await message.answer(
        _broadcast_buttons_instructions(),
        reply_markup=_broadcast_cancel_keyboard(),
    )


@admin_router.callback_query(F.data == "admin_broadcast_cancel")
async def admin_broadcast_cancel(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Cancel broadcast flow from inline button."""
    if not await _ensure_admin(callback):
        return
    data = await state.get_data()
    await _cleanup_broadcast_preview(bot, data or {})
    await state.clear()
    await callback.answer("Jarayon bekor qilindi.", show_alert=False)
    if callback.message:
        try:
            await callback.message.edit_text("📨 Jo'natish bekor qilindi.")
        except TelegramBadRequest:
            await callback.message.answer("📨 Jo'natish bekor qilindi.")


@admin_router.message(BroadcastState.waiting_for_buttons)
async def admin_broadcast_buttons(message: Message, state: FSMContext, bot: Bot) -> None:
    if not await _ensure_admin(message):
        await state.clear()
        return

    buttons_text = (message.text or "").strip()
    lowered = buttons_text.lower()

    if lowered == "bekor":
        await message.answer("✅ Jo'natish bekor qilindi.")
        await state.clear()
        return

    skip_tokens = {"yo'q", "yoq", "no"}
    button_rows: list[list[dict[str, str]]] = []
    if buttons_text and lowered not in skip_tokens:
        try:
            button_rows = _parse_buttons(buttons_text)
        except ValueError as error:
            await message.answer(str(error))
            return

    data = await state.get_data()
    source_chat = data.get("source_chat")
    source_message = data.get("source_message")
    if source_chat is None or source_message is None:
        await message.answer("Manba xabari topilmadi, jarayon bekor qilindi.")
        await state.clear()
        return

    reply_markup = _build_buttons_markup(button_rows)

    try:
        preview = await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=source_chat,
            message_id=source_message,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest:
        logging.exception("Broadcast preview yaratishda xato")
        await message.answer("Xabarni ko'rsatib bo'lmadi. Iltimos, qaytadan urinib ko'ring.")
        await state.clear()
        return

    await state.update_data(
        buttons=button_rows,
        preview_chat=preview.chat.id,
        preview_message=preview.message_id,
    )

    confirm_builder = InlineKeyboardBuilder()
    confirm_builder.button(text="✅ Tasdiqlash", callback_data="admin_broadcast_confirm")
    confirm_builder.button(text="♻️ Tugmalarni qayta kiritish", callback_data="admin_broadcast_retry")
    confirm_builder.button(text="❌ Bekor qilish", callback_data="admin_broadcast_cancel")
    confirm_builder.adjust(1)

    await state.set_state(BroadcastState.waiting_for_confirm)
    await message.answer(
        "Yuqoridagi xabar barcha foydalanuvchilarga yuboriladi. Tasdiqlaysizmi?",
        reply_markup=confirm_builder.as_markup(),
    )


@admin_router.callback_query(F.data == "admin_broadcast_confirm")
async def admin_broadcast_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not await _ensure_admin(callback):
        return

    current_state = await state.get_state()
    if current_state != BroadcastState.waiting_for_confirm.state:
        await callback.answer("Tasdiqlash uchun xabar topilmadi.", show_alert=True)
        return

    data = await state.get_data()
    source_chat = data.get("source_chat")
    source_message = data.get("source_message")
    if source_chat is None or source_message is None:
        await callback.answer("Manba xabari topilmadi.", show_alert=True)
        await state.clear()
        return

    button_rows = data.get("buttons") or []
    reply_markup = _build_buttons_markup(button_rows)

    await _cleanup_broadcast_preview(bot, data)
    await state.clear()

    if callback.message:
        try:
            await callback.message.edit_text("📨 Jo'natish boshlandi...")
        except TelegramBadRequest:
            pass

    await callback.answer("Jo'natish boshlandi.")

    await _execute_broadcast(
        bot=bot,
        admin_chat=callback.message.chat.id if callback.message else callback.from_user.id,
        source_chat=source_chat,
        source_message=source_message,
        reply_markup=reply_markup,
        initiator=callback.from_user.id if callback.from_user else None,
    )


@admin_router.callback_query(F.data == "admin_broadcast_retry")
async def admin_broadcast_retry(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not await _ensure_admin(callback):
        return

    current_state = await state.get_state()
    if current_state != BroadcastState.waiting_for_confirm.state:
        await callback.answer("Hozir qayta kiritish mumkin emas.", show_alert=True)
        return

    data = await state.get_data()
    await _cleanup_broadcast_preview(bot, data)

    await state.update_data(buttons=[], preview_chat=None, preview_message=None)
    await state.set_state(BroadcastState.waiting_for_buttons)

    await callback.answer("Tugmalarni qayta kiriting.")

    if callback.message:
        try:
            await callback.message.edit_text(_broadcast_buttons_instructions(), reply_markup=_broadcast_cancel_keyboard())
        except TelegramBadRequest:
            await callback.message.answer(_broadcast_buttons_instructions(), reply_markup=_broadcast_cancel_keyboard())
    else:
        chat_id = callback.from_user.id if callback.from_user else None
        if chat_id:
            await bot.send_message(chat_id, _broadcast_buttons_instructions(), reply_markup=_broadcast_cancel_keyboard())


@admin_router.message(SettingsState.waiting_for_share_button_text)
async def admin_save_share_button_text(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        await state.clear()
        return

    raw_text = (message.text or "").strip()
    lower_text = raw_text.lower()

    if lower_text in {"bekor", "cancel"}:
        await message.answer("O'zgarishlar bekor qilindi.")
        await state.clear()
        return

    if lower_text in {"default", "standart"}:
        new_text = db.DEFAULT_SETTINGS.get("share_button_text", "♻️ Do'stlarga ulashish")
    else:
        if not raw_text:
            await message.answer("Matn bo'sh bo'lmasligi kerak.")
            return
        if len(raw_text) > 64:
            await message.answer("Matn uzunligi 64 belgidan oshmasligi kerak.")
            return
        new_text = raw_text

    db.set_setting("share_button_text", new_text)
    db.add_log(message.from_user.id if message.from_user else None, "share_button_text_update")
    await message.answer("✅ Tugma matni yangilandi.")
    await state.clear()

    overview, keyboard = _build_share_button_overview()
    await message.answer(
        overview,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@admin_router.message(SettingsState.waiting_for_share_button_url)
async def admin_save_share_button_url(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        await state.clear()
        return

    raw_input = (message.text or "").strip()
    lower_input = raw_input.lower()

    if lower_input in {"bekor", "cancel"}:
        await message.answer("O'zgarishlar bekor qilindi.")
        await state.clear()
        return

    if lower_input in {"o'chirish", "ochirish", "off", "0"}:
        db.set_setting("share_button_url", "")
        db.add_log(message.from_user.id if message.from_user else None, "share_button_url_cleared")
        await message.answer("ℹ️ Havola o'chirildi. Tugma havolasiz ko'rsatilmaydi.")
        await state.clear()
    else:
        prepared = _normalize_button_url(raw_input)
        if not prepared:
            await message.answer(
                "❌ Havola noto'g'ri. U http://, https:// yoki tg:// bilan boshlanishi kerak."
            )
            return
        db.set_setting("share_button_url", prepared)
        db.add_log(message.from_user.id if message.from_user else None, "share_button_url_update")
        await message.answer("✅ Havola muvaffaqiyatli yangilandi.")
        await state.clear()

    overview, keyboard = _build_share_button_overview()
    await message.answer(
        overview,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@admin_router.message(SettingsState.waiting_for_start_text)
async def admin_save_start_text(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        await state.clear()
        return
    text = (message.text or "").strip()
    if text.lower() == "bekor":
        await message.answer("O'zgarishlar bekor qilindi.")
        await state.clear()
        return
    if not text:
        await message.answer("Matn bo'sh bo'lmasligi kerak.")
        return
    db.set_setting("start_text", text)
    db.add_log(message.from_user.id if message.from_user else None, "start_text_update")
    await message.answer("✅ Xush kelibsiz matni yangilandi.")
    await state.clear()


@admin_router.message(SettingsState.waiting_for_subscription_text)
async def admin_save_subscription_text(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        await state.clear()
        return
    text = (message.text or "").strip()
    if text.lower() == "bekor":
        await message.answer("O'zgarishlar bekor qilindi.")
        await state.clear()
        return
    if not text:
        await message.answer("Matn bo'sh bo'lmasligi kerak.")
        return
    db.set_setting("subscribe_text", text)
    db.add_log(message.from_user.id if message.from_user else None, "subscribe_text_update")
    await message.answer("✅ Obuna talabi xabari yangilandi.")
    await state.clear()


@admin_router.message(Command("backup"))
async def admin_backup_command(message: Message) -> None:
    await _admin_backup(message)


@admin_router.message(F.text == "📂 Backup")
async def admin_backup(message: Message) -> None:
    await _admin_backup(message)


async def _admin_backup(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    
    try:
        # Show progress
        status_msg = await message.answer("📂 Zaxira nusxa tayyorlanmoqda...")
        
        backup_path = create_database_backup()
        backup_size = backup_path.stat().st_size / 1024  # KB
        
        # Get database stats for caption
        counts = db.get_user_counts()
        
        caption = (
            f"📂 <b>Bot ma'lumotlari zaxirasi</b>\n\n"
            f"📊 <b>Statistika:</b>\n"
            f"• Foydalanuvchilar: {counts['total_users']}\n"
            f"• Yuklab olishlar: {counts['total_downloads']}\n"
            f"• Fayl hajmi: {backup_size:.1f} KB\n"
            f"• Sanasi: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
            f"💾 Zaxira nusxa muvaffaqiyatli yaratildi!"
        )
        
        await status_msg.delete()
        db.add_log(message.from_user.id if message.from_user else None, "backup_created")
        await message.answer_document(FSInputFile(backup_path), caption=caption)
        
    except FileNotFoundError:
        await message.answer("❌ Ma'lumotlar bazasida hali ma'lumot yo'q.")
    except Exception as error:
        logging.exception("Backup yaratishda xato")
        await message.answer(f"❌ Zaxira nusxa yaratishda xato: {error}")
        


@admin_router.message(F.text == "🕒 Uptime")
async def admin_uptime(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    
    try:
        uptime = format_uptime()
        counts = db.get_user_counts()
        
        uptime_text = (
            f"🕒 <b>Bot holati</b>\n\n"
            f"⏰ <b>Ishlash vaqti:</b> {uptime}\n"
            f"🔄 <b>Holati:</b> ✅ Faol\n"
            f"📊 <b>Tezkor ma'lumot:</b>\n"
            f"   • Jami foydalanuvchilar: {counts['total_users']}\n"
            f"   • Bugun faol: {counts['active_today']}\n"
            f"   • Yuklab olishlar: {counts['total_downloads']}\n\n"
            f"📅 <b>Tekshirilgan:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        await message.answer(uptime_text)
        
    except Exception as error:
        logging.exception("Uptime ma'lumotini olishda xato")
        await message.answer(f"❌ Ma'lumot olishda xato: {error}")
        


@admin_router.message(Command("ping"))
@admin_router.message(Command("uptime"))
async def admin_ping(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    await message.answer(f"✅ Bot faol. Uptime: {format_uptime()}")


@admin_router.message(F.text == "🔙 Ortga")
async def admin_back(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    await message.answer("Bosh menyu.", reply_markup=admin_main_reply_kb())


@admin_router.message(F.text == "🧪 Test")
async def admin_test_functions(message: Message, bot: Bot) -> None:
    if not await _ensure_admin(message):
        return
    
    test_msg = await message.answer("🧪 <b>Admin panel funksiyalari tekshirilmoqda...</b>")
    results = []
    
    # Test database connection
    try:
        db.get_user_counts()
        results.append("✅ Ma'lumotlar bazasi - <b>ISHLAYDI</b>")
    except Exception as e:
        results.append(f"❌ Ma'lumotlar bazasi - <b>XATO:</b> {e}")
    
    # Test statistics
    try:
        build_stats_overview()
        results.append("✅ Statistika tizimi - <b>ISHLAYDI</b>")
    except Exception as e:
        results.append(f"❌ Statistika tizimi - <b>XATO:</b> {e}")
    
    # Test backup system
    try:
        create_database_backup()
        results.append("✅ Backup tizimi - <b>ISHLAYDI</b>")
    except Exception as e:
        results.append(f"❌ Backup tizimi - <b>XATO:</b> {e}")
    
    # Test uptime
    try:
        format_uptime()
        results.append("✅ Uptime tizimi - <b>ISHLAYDI</b>")
    except Exception as e:
        results.append(f"❌ Uptime tizimi - <b>XATO:</b> {e}")
    
    # Test profile update
    try:
        await update_bot_monthly_users_badge(bot)
        results.append("✅ Profil yangilash - <b>ISHLAYDI</b>")
    except Exception as e:
        results.append(f"❌ Profil yangilash - <b>XATO:</b> {e}")
    
    # Test settings
    try:
        db.get_setting("start_text")
        results.append("✅ Sozlamalar tizimi - <b>ISHLAYDI</b>")
    except Exception as e:
        results.append(f"❌ Sozlamalar tizimi - <b>XATO:</b> {e}")
    
    test_result = (
        f"🧪 <b>Admin panel test natijalari</b>\n\n"
        + "\n".join(results) + 
        f"\n\n📅 <b>Test sanasi:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    
    await test_msg.edit_text(test_result)
    db.add_log(message.from_user.id if message.from_user else None, "admin_test_completed")


@admin_router.callback_query(F.data == "noop")
async def admin_noop(callback: CallbackQuery) -> None:
    await callback.answer("Bu amal faqat asosiy admin uchun.", show_alert=True)


async def _ensure_admin(event: Message | CallbackQuery) -> bool:
    user = event.from_user if isinstance(event, CallbackQuery) else event.from_user
    if not user:
        if isinstance(event, CallbackQuery):
            await event.answer("Foydalanuvchi topilmadi.", show_alert=True)
        return False
    if not db.is_admin(user.id):
        text = "Bu bo'lim faqat adminlar uchun."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return False
    return True


def _is_main_admin(user_id: int) -> bool:
    return user_id == get_settings().main_admin_id


def _parse_buttons(text: str) -> list[list[dict[str, str]]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Tugmalar ro'yxati bo'sh.")

    rows: list[list[dict[str, str]]] = []
    for line in lines:
        segments = [segment.strip() for segment in line.split("|") if segment.strip()]
        if not segments:
            continue

        row: list[dict[str, str]] = []
        for segment in segments:
            if "-" not in segment:
                raise ValueError("Har satr 'Sarlavha - havola' formatida bo'lishi kerak.")
            title, url = [part.strip() for part in segment.split("-", 1)]
            if not title:
                raise ValueError("Tugma sarlavhasi bo'sh bo'lishi mumkin emas.")
            if not url.startswith(("http://", "https://")):
                raise ValueError("Havola 'http://' yoki 'https://' bilan boshlanishi kerak.")
            row.append({"text": title, "url": url})

        if row:
            rows.append(row)

    if not rows:
        raise ValueError("Yaroqli tugmalar topilmadi.")

    return rows


def _build_buttons_markup(button_rows: list[list[dict[str, str]]]) -> InlineKeyboardMarkup | None:
    if not button_rows:
        return None

    builder = InlineKeyboardBuilder()
    for row in button_rows:
        buttons = [InlineKeyboardButton(text=button["text"], url=button["url"]) for button in row]
        builder.row(*buttons)
    return builder.as_markup()


def _broadcast_cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Bekor qilish", callback_data="admin_broadcast_cancel")
    builder.adjust(1)
    return builder.as_markup()


def _broadcast_buttons_instructions() -> str:
    return (
        "Agar tugma qo'shmoqchi bo'lsangiz, har qatorni 'Sarlavha - https://link' shaklida yuboring.\n"
        "Bir qatorga bir nechta tugma qo'shish uchun ularni '|' bilan ajrating.\n"
        "Masalan: \n"
        "Aksiya - https://example.com\n"
        "Qo'llab-quvvatlash - https://t.me/support | Kanal - https://t.me/example\n\n"
        "Tugma kerak bo'lmasa, 'Yo'q' deb yozing."
    )


async def _cleanup_broadcast_preview(bot: Bot, data: dict) -> None:
    preview_chat = data.get("preview_chat")
    preview_message = data.get("preview_message")
    if not preview_chat or not preview_message:
        return
    try:
        await bot.delete_message(preview_chat, preview_message)
    except TelegramBadRequest:
        pass


def _format_last_active(value: Optional[str]) -> str:
    if not value:
        return "noma'lum"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone()
    return moment.strftime("%d.%m %H:%M")


async def _execute_broadcast(
    *,
    bot: Bot,
    admin_chat: int,
    source_chat: int,
    source_message: int,
    reply_markup,
    initiator: Optional[int],
) -> None:
    user_ids = list(db.iter_user_ids())
    total = len(user_ids)
    if total == 0:
        await bot.send_message(admin_chat, "Foydalanuvchilar ro'yxati bo'sh.")
        return

    progress = await bot.send_message(admin_chat, f"📨 Jo'natish boshlandi: 0/{total}")
    success = 0
    failed = 0

    for index, user_id in enumerate(user_ids, start=1):
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=source_chat,
                message_id=source_message,
                reply_markup=reply_markup,
            )
            success += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        except Exception as error:  # pragma: no cover
            logging.exception("Broadcast xatosi", exc_info=error)
            failed += 1

        if index % 10 == 0 or index == total:
            try:
                await progress.edit_text(
                    f"📨 Jarayon: {success}/{total} | Xatoliklar: {failed}"
                )
            except TelegramBadRequest:
                pass
        await asyncio.sleep(0.05)

    try:
        await progress.edit_text(
            f"✅ Yuborish yakunlandi. Muvaffaqiyatli: {success}/{total}, xatoliklar: {failed}"
        )
    except TelegramBadRequest:
        pass
    db.add_log(initiator, f"broadcast_sent:{success}:{failed}")


# New callback handlers for admin management
@admin_router.callback_query(F.data == "admin_admins_list")
async def admin_admins_list_callback(callback: CallbackQuery) -> None:
    """Show list of all admins."""
    if not await _ensure_admin(callback):
        return
    await callback.answer()
    
    text = _build_admins_list()
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Orqaga", callback_data="admin_back_to_admin_menu")
    builder.adjust(1)
    
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        except TelegramBadRequest:
            await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@admin_router.callback_query(F.data == "admin_remove_select")
async def admin_remove_select_callback(callback: CallbackQuery) -> None:
    """Show list of admins to remove."""
    if not await _ensure_admin(callback):
        return
    if not _is_main_admin(callback.from_user.id if callback.from_user else 0):
        await callback.answer("Faqat asosiy admin o'chira oladi.", show_alert=True)
        return
    await callback.answer()
    
    admins = db.get_admins()
    main_admin_id = get_settings().main_admin_id
    
    text = "❌ <b>Admin o'chirish</b>\n\nO'chirmoqchi bo'lgan adminni tanlang:"
    
    builder = InlineKeyboardBuilder()
    for admin in admins:
        user_id = admin.get("user_id")
        if user_id == main_admin_id:
            continue  # Skip main admin
        username = admin.get("username") or str(user_id)
        builder.button(
            text=f"❌ {username}",
            callback_data=f"admin_remove:{user_id}"
        )
    builder.button(text="🔙 Orqaga", callback_data="admin_back_to_admin_menu")
    builder.adjust(1)
    
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        except TelegramBadRequest as e:
            # If edit fails, delete old message and send new one
            logging.debug(f"Edit failed, deleting old message: {e}")
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
            await callback.bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )


@admin_router.callback_query(F.data == "admin_back_to_main")
async def admin_back_to_main_callback(callback: CallbackQuery) -> None:
    """Go back to main admin panel."""
    if not await _ensure_admin(callback):
        return
    await callback.answer()
    
    if callback.message and callback.from_user:
        user_name = callback.from_user.first_name
        welcome_text = (
            f"👋 Assalomu alaykum, <b>{html.escape(user_name)}</b>!\n\n"
            "🔧 <b>Admin panelga xush kelibsiz</b>\n"
            "Quyidagi bo'limlardan birini tanlang:\n\n"
            "📊 <i>Statistika</i> - Bot statistikalarini ko'rish\n"
            " <i>Kanallar</i> - Majburiy obuna kanallarini boshqarish\n"
            "👥 <i>Adminlar</i> - Admin huquqlarini boshqarish\n"
            "📨 <i>Xabar yuborish</i> - Barcha foydalanuvchilarga xabar\n"
            "📝 <i>Start matni</i> - Bot xush kelibsiz matnini o'zgartirish"
        )
        try:
            await callback.message.edit_text(welcome_text, reply_markup=admin_main_reply_kb(), parse_mode="HTML")
        except TelegramBadRequest:
            await callback.message.answer(welcome_text, reply_markup=admin_main_reply_kb(), parse_mode="HTML")


@admin_router.callback_query(F.data == "admin_back_to_admin_menu")
async def admin_back_to_admin_menu_callback(callback: CallbackQuery) -> None:
    """Go back to admin management menu."""
    if not await _ensure_admin(callback):
        return
    await callback.answer()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Adminlar ro'yxati", callback_data="admin_admins_list")
    builder.button(text="➕ Admin qo'shish", callback_data="admin_add")
    builder.button(text="❌ Admin o'chirish", callback_data="admin_remove_select")
    builder.button(text="⚙️ Huquqlar", callback_data="admin_permissions_select")
    builder.adjust(2, 2)
    
    text = (
        "👑 <b>Admin boshqaruvi</b>\n\n"
        "Quyidagi amallardan birini tanlang:"
    )
    
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        except TelegramBadRequest:
            await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# --------------------------- Admin Permissions Handlers ------------------------- #


@admin_router.callback_query(F.data == "admin_permissions_select")
async def admin_permissions_select_callback(callback: CallbackQuery) -> None:
    """Show list of admins to manage permissions."""
    if not await _ensure_admin(callback):
        return
    if not _is_main_admin(callback.from_user.id if callback.from_user else 0):
        await callback.answer("Faqat asosiy admin huquqlarni boshqara oladi.", show_alert=True)
        return
    await callback.answer()
    
    admins = db.get_admins()
    main_admin_id = get_settings().main_admin_id
    
    text = "⚙️ <b>Admin huquqlarini boshqarish</b>\n\nHuquqlarini o'zgartirmoqchi bo'lgan adminni tanlang:"
    
    builder = InlineKeyboardBuilder()
    for admin in admins:
        user_id = admin.get("user_id")
        if user_id == main_admin_id:
            continue  # Skip main admin
        username = admin.get("username") or str(user_id)
        builder.button(
            text=f"👤 {username}",
            callback_data=f"admin_perm_manage:{user_id}"
        )
    builder.button(text="🔙 Orqaga", callback_data="admin_back_to_admin_menu")
    builder.adjust(1)
    
    if callback.message:
        # Try to edit the message first
        try:
            await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        except TelegramBadRequest as e:
            # If edit fails, delete old message and send new one
            logging.debug(f"Edit failed, deleting old message: {e}")
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
            await callback.bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )


@admin_router.callback_query(F.data.startswith("admin_perm_manage:"))
async def admin_perm_manage_callback(callback: CallbackQuery) -> None:
    """Show permissions for a specific admin."""
    if not await _ensure_admin(callback):
        return
    if not _is_main_admin(callback.from_user.id if callback.from_user else 0):
        await callback.answer("Faqat asosiy admin huquqlarni boshqara oladi.", show_alert=True)
        return
    
    user_id = int(callback.data.split(":", 1)[1])
    await callback.answer()
    
    # Get admin info
    admins = db.get_admins()
    admin_info = next((a for a in admins if a.get("user_id") == user_id), None)
    if not admin_info:
        await callback.answer("Admin topilmadi", show_alert=True)
        return
    
    username = admin_info.get("username") or str(user_id)
    perms = db.get_admin_permissions(user_id)
    
    # Permission names in Uzbek
    perm_names = {
        'can_manage_users': '👥 Foydalanuvchilarni boshqarish',
        'can_manage_channels': '📢 Kanallarni boshqarish',
        'can_broadcast': '📨 Xabar yuborish',
        'can_view_stats': '📊 Statistikani korish',
        'can_manage_admins': '👑 Adminlarni boshqarish'
    }
    
    text = f"⚙️ <b>Huquqlar: {username}</b>\n\n"
    
    builder = InlineKeyboardBuilder()
    for perm_key, perm_name in perm_names.items():
        has_perm = perms.get(perm_key, 0) == 1
        status = "✅" if has_perm else "❌"
        text += f"{status} {perm_name}\n"
        
        action = "0" if has_perm else "1"  # Toggle
        btn_action = "❌ O'chirish" if has_perm else "✅ Yoqish"
        btn_text = f"{btn_action}: {perm_name.split()[1]}"
        builder.button(
            text=btn_text,
            callback_data=f"admin_perm_toggle:{user_id}:{perm_key}:{action}"
        )
    
    builder.button(text="🔙 Orqaga", callback_data="admin_permissions_select")
    builder.adjust(1)
    
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        except TelegramBadRequest as e:
            logging.warning(f"Failed to edit permission manage message: {e}")


@admin_router.callback_query(F.data.startswith("admin_perm_toggle:"))
async def admin_perm_toggle_callback(callback: CallbackQuery) -> None:
    """Toggle a specific permission for an admin."""
    if not await _ensure_admin(callback):
        return
    if not _is_main_admin(callback.from_user.id if callback.from_user else 0):
        await callback.answer("Faqat asosiy admin huquqlarni o'zgartira oladi.", show_alert=True)
        return
    
    parts = callback.data.split(":")
    user_id = int(parts[1])
    permission = parts[2]
    new_value = parts[3] == "1"
    
    # Permission names in Uzbek for alert
    perm_names_alert = {
        'can_manage_users': 'Foydalanuvchilarni boshqarish',
        'can_manage_channels': 'Kanallarni boshqarish',
        'can_broadcast': 'Xabar yuborish',
        'can_view_stats': 'Statistikani korish',
        'can_manage_admins': 'Adminlarni boshqarish'
    }
    
    perm_name = perm_names_alert.get(permission, permission)
    
    # Update permission
    db.update_admin_permission(user_id, permission, new_value)
    db.add_log(
        callback.from_user.id if callback.from_user else None,
        f"admin_perm_update:{user_id}:{permission}:{new_value}"
    )
    
    # Show popup alert
    status_emoji = "✅" if new_value else "❌"
    status_text = "YOQILDI" if new_value else "O'CHIRILDI"
    alert_text = f"{status_emoji} {perm_name}\n\n{status_text}"
    await callback.answer(alert_text, show_alert=True)
    
    # Get updated admin info
    admins = db.get_admins()
    admin_info = next((a for a in admins if a.get("user_id") == user_id), None)
    if not admin_info:
        return
    
    username = admin_info.get("username") or str(user_id)
    perms = db.get_admin_permissions(user_id)
    
    # Permission names in Uzbek for display
    perm_names = {
        'can_manage_users': '👥 Foydalanuvchilarni boshqarish',
        'can_manage_channels': '📢 Kanallarni boshqarish',
        'can_broadcast': '📨 Xabar yuborish',
        'can_view_stats': '📊 Statistikani korish',
        'can_manage_admins': '👑 Adminlarni boshqarish'
    }
    
    text = f"⚙️ <b>Huquqlar: {username}</b>\n\n"
    
    builder = InlineKeyboardBuilder()
    for perm_key, perm_name_full in perm_names.items():
        has_perm = perms.get(perm_key, 0) == 1
        status = "✅" if has_perm else "❌"
        text += f"{status} {perm_name_full}\n"
        
        action = "0" if has_perm else "1"  # Toggle
        btn_action = "❌ O'chirish" if has_perm else "✅ Yoqish"
        btn_text = f"{btn_action}: {perm_name_full.split()[1]}"
        builder.button(
            text=btn_text,
            callback_data=f"admin_perm_toggle:{user_id}:{perm_key}:{action}"
        )
    
    builder.button(text="🔙 Orqaga", callback_data="admin_permissions_select")
    builder.adjust(1)
    
    # Update the message with new permissions state
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        except TelegramBadRequest:
            pass

