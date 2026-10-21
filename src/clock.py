"""Ufa uses the Asia/Yekaterinburg time zone (UTC+5)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

UFA_TIMEZONE = ZoneInfo("Asia/Yekaterinburg")


def today_in_ufa() -> date:
    return datetime.now(UFA_TIMEZONE).date()
