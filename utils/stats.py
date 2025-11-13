from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from database.core import get_user_counts, get_users_join_dates


@dataclass(slots=True)
class StatsOverview:
    total_users: int
    active_today: int
    active_week: int
    active_month: int
    total_downloads: int
    growth_chart: str


def build_stats_overview(days: int = 14) -> StatsOverview:
    counts = get_user_counts()
    growth_chart = build_growth_chart(days)
    return StatsOverview(
        total_users=counts["total_users"],
        active_today=counts["active_today"],
        active_week=counts["active_week"],
        active_month=counts["active_month"],
        total_downloads=counts["total_downloads"],
        growth_chart=growth_chart,
    )


def build_growth_chart(days: int = 14) -> str:
    records: List[Dict] = get_users_join_dates(days)
    if not records:
        return "So'nggi kunlarda yangi foydalanuvchilar qo'shilmagan."

    frame = pd.DataFrame(records)
    frame["join_day"] = pd.to_datetime(frame["join_day"])
    grouped = frame.groupby(frame["join_day"].dt.date).size()

    chart_lines: List[str] = []
    max_value = grouped.max()
    scale = max(1, max_value // 20)

    for day, count in grouped.sort_index().items():
        bars = "#" * max(1, count // scale)
        chart_lines.append(f"{day} | {bars} ({count})")

    return "\n".join(chart_lines)
