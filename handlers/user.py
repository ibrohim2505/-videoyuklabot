from __future__ import annotations

import html
import logging
import re
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramEntityTooLarge
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User as TelegramUser,
)

from database.core import (
    DEFAULT_SETTINGS,
    add_log,
    add_or_update_user,
    get_channels,
    get_setting,
    increment_downloads,
    update_last_active,
)
from keyboards.common import subscription_keyboard
from utils.download import (
    DownloadError,
    SUPPORTED_DOMAINS,
    cleanup_file,
    download_video,
    is_supported_url,
)
from utils.subscription import ensure_user_subscription

user_router = Router(name="user")

SUPPORTED_DOMAINS_PATTERN = "|".join(map(re.escape, SUPPORTED_DOMAINS))
SUPPORTED_URL_RE = re.compile(
    rf"https?://[^\s]*({SUPPORTED_DOMAINS_PATTERN})[^\s]*",
    flags=re.IGNORECASE,
)


@user_router.message(Command("start"))
async def handle_start(message: Message, bot: Bot) -> None:
    """Send welcome text and enforce subscription requirements."""
    if not message.from_user:
        return

    user = message.from_user
    add_or_update_user(user.id, user.username, user.first_name)

    allowed, missing = await ensure_user_subscription(bot, user.id)
    if not allowed:
        prompt = get_setting("subscribe_text", DEFAULT_SETTINGS["subscribe_text"])
        keyboard = subscription_keyboard(missing if missing else get_channels())
        await message.answer(prompt, reply_markup=keyboard)
        add_log(user.id, "subscription_prompt")
        return

    welcome = get_setting("start_text", DEFAULT_SETTINGS["start_text"])
    await message.answer(welcome)
    add_log(user.id, "start_command")


@user_router.callback_query(F.data == "check_subscription")
async def process_subscription_check(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.from_user:
        await callback.answer("Foydalanuvchi aniqlanmadi.", show_alert=True)
        return

    user = callback.from_user
    allowed, missing = await ensure_user_subscription(bot, user.id)
    if allowed:
        text = get_setting("start_text", DEFAULT_SETTINGS["start_text"])
        if callback.message:
            await callback.message.edit_text(text)
        await callback.answer("Rahmat! Obuna tasdiqlandi.")
        add_log(user.id, "subscription_confirmed")
    else:
        add_log(user.id, "subscription_pending")
    await callback.answer("⚠️ Kanalga a'zo bo'lmagansiz.", show_alert=True)


@user_router.message(F.text.regexp(SUPPORTED_URL_RE))
async def handle_text_video_request(message: Message, bot: Bot) -> None:
    await _process_download(message, bot, message.text)


@user_router.message(F.caption.regexp(SUPPORTED_URL_RE))
async def handle_media_caption_request(message: Message, bot: Bot) -> None:
    await _process_download(message, bot, message.caption)


@user_router.message()
async def handle_fallback(message: Message) -> None:
    if message.from_user and message.from_user.is_bot:
        return
    await message.answer(
        "Instagram, TikTok, Snapchat, Likee yoki YouTube havolasini yuboring, men esa siz uchun videoni yuklab beraman."
    )


async def _process_download(message: Message, bot: Bot, text: Optional[str]) -> None:
    if not message.from_user:
        return

    user = message.from_user
    add_or_update_user(user.id, user.username, user.first_name)

    allowed, missing = await ensure_user_subscription(bot, user.id)
    if not allowed:
        prompt = get_setting("subscribe_text", DEFAULT_SETTINGS["subscribe_text"])
        keyboard = subscription_keyboard(missing if missing else get_channels())
        await message.answer(prompt, reply_markup=keyboard)
        return

    url = _extract_supported_url(text)
    if not url:
        return

    status = await message.answer("⏳ Yuklab olinmoqda, biroz kuting...")
    try:
        result = await download_video(url)
        bot_info = await bot.get_me()
        bot_username = bot_info.username or bot_info.full_name
        caption = _build_caption(result.title, result.duration, user, bot_username)

        file_size = result.file_path.stat().st_size
        max_size_bytes = 50 * 1024 * 1024  # Telegram bot limits
        if file_size > max_size_bytes:
            size_mb = file_size / (1024 * 1024)
            await status.edit_text(
                "Video hajmi {size:.1f} MB. Telegram botlarida 50 MB dan katta fayllarni yuborib bo'lmaydi."
                .format(size=size_mb)
            )
            add_log(user.id, f"file_too_large:{url}")
            return

        sent_message: Optional[Message] = None

        try:
            if result.media_type == "photo":
                photo = FSInputFile(result.file_path)
                sent_message = await message.answer_photo(
                    photo,
                    caption=caption,
                )
            else:
                video = FSInputFile(result.file_path)
                if result.ext.lower() == "mp4":
                    sent_message = await message.answer_video(
                        video,
                        caption=caption,
                        supports_streaming=True,
                    )
                else:
                    sent_message = await message.answer_document(
                        video,
                        caption=caption,
                    )
        except TelegramEntityTooLarge:
            size_mb = file_size / (1024 * 1024)
            await status.edit_text(
                "Video hajmi {size:.1f} MB ekan. Telegram botlarida 50 MB dan katta fayllarni yuborib bo'lmaydi."
                .format(size=size_mb)
            )
            add_log(user.id, f"file_too_large:{url}")
            return

        share_markup = _build_share_keyboard()
        if share_markup and sent_message:
            try:
                await sent_message.edit_reply_markup(reply_markup=share_markup)
            except TelegramBadRequest:
                logging.debug("Share markupni qo'shib bo'lmadi", exc_info=True)
        
        increment_downloads(user.id)
        update_last_active(user.id)
        add_log(user.id, f"download:{url}")
    except DownloadError as error:
        await status.edit_text(str(error))
    except TelegramBadRequest as error:
        logging.exception("Medianii yuborishda xato", exc_info=error)
        await status.edit_text("Media yuborishda xatolik yuz berdi. Keyinroq urinib ko'ring.")
    except Exception as error:  # pragma: no cover
        logging.exception("Kutilmagan xato", exc_info=error)
        await status.edit_text("Kutilmagan xato yuz berdi. Keyinroq urinib ko'ring.")
    else:
        try:
            await status.delete()
        except TelegramBadRequest:
            pass
    finally:
        if 'result' in locals():
            await cleanup_file(result.file_path)


def _extract_supported_url(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    for match in SUPPORTED_URL_RE.finditer(text):
        candidate = match.group(0)
        if is_supported_url(candidate):
            return candidate
    return None


def _build_caption(
    title: str,
    duration: Optional[float],
    user: TelegramUser,
    bot_username: str,
) -> str:
    safe_title = html.escape(title)
    user_display = _format_user(user)
    bot_display_raw = (
        f"@{bot_username}" if bot_username and not bot_username.startswith("@") else bot_username
    )
    bot_display = html.escape(bot_display_raw) if bot_display_raw else ""

    parts = [f"🎬 <b>{safe_title}</b>"]
    if duration:
        parts.append(f"⏱ Davomiyligi: {format_duration(duration)}")
    parts.append("🤖 <b>Video yuklash tugallandi!</b>")
    parts.append(f"👤 Yuklovchi: {user_display}")
    if bot_display:
        parts.append(f"🔗 {bot_display} orqali yuklandi.")
    return "\n".join(parts)


def _is_truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "ha", "yes", "on", "enabled"}


def _build_share_keyboard() -> Optional[InlineKeyboardMarkup]:
    enabled_raw = get_setting(
        "share_button_enabled",
        DEFAULT_SETTINGS.get("share_button_enabled", "1"),
    )
    if not _is_truthy(enabled_raw):
        return None

    text_value = get_setting(
        "share_button_text",
        DEFAULT_SETTINGS.get("share_button_text", "♻️ Do'stlarga ulashish"),
    )
    url_value = get_setting(
        "share_button_url",
        DEFAULT_SETTINGS.get("share_button_url", ""),
    )

    button_text = (text_value or "").strip() or DEFAULT_SETTINGS.get(
        "share_button_text", "♻️ Do'stlarga ulashish"
    )
    button_url = (url_value or "").strip()

    if not button_url:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=button_text,
                    url=button_url,
                )
            ]
        ]
    )


def _format_user(user: TelegramUser) -> str:
    if user.username:
        return f"@{html.escape(user.username)}"
    full_name = " ".join(
        filter(None, [user.first_name, user.last_name])
    ) or str(user.id)
    return html.escape(full_name)


def format_duration(seconds: float) -> str:
    total_seconds = int(seconds)
    minutes, sec = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"
