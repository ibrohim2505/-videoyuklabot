from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class ChannelManageState(StatesGroup):
    waiting_for_link = State()


class AdminManageState(StatesGroup):
    waiting_for_user = State()


class BroadcastState(StatesGroup):
    waiting_for_content = State()
    waiting_for_buttons = State()
    waiting_for_confirm = State()


class SettingsState(StatesGroup):
    waiting_for_start_text = State()
    waiting_for_subscription_text = State()
    waiting_for_share_button_text = State()
    waiting_for_share_button_url = State()
