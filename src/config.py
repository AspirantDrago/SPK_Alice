"""Application configuration, read once when the application starts."""

from dataclasses import dataclass
import math
import os
from urllib.parse import urlsplit

DEFAULT_URL = "https://schedule.str-spc.ru/hg.htm"


@dataclass(frozen=True)
class Settings:
    url: str = DEFAULT_URL
    time_interval_minutes: float = 5.0
    max_cache_age_hours: float = 12.0

    def __post_init__(self):
        parts = urlsplit(self.url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("URL must be an absolute http:// or https:// URL")
        if not math.isfinite(self.time_interval_minutes * 60) or self.time_interval_minutes <= 0:
            raise ValueError("TIME_INTERVAL_MINUTES must be a finite number greater than zero")
        if not math.isfinite(self.max_cache_age_hours * 3600) or self.max_cache_age_hours <= 0:
            raise ValueError("MAX_CACHE_AGE_HOURS must be a finite number greater than zero")

    @classmethod
    def from_env(cls):
        try:
            interval = float(os.environ.get("TIME_INTERVAL_MINUTES", "5"))
        except ValueError as exc:
            raise ValueError("TIME_INTERVAL_MINUTES must be a number greater than zero") from exc
        try:
            max_age = float(os.environ.get("MAX_CACHE_AGE_HOURS", "12"))
        except ValueError as exc:
            raise ValueError("MAX_CACHE_AGE_HOURS must be a number greater than zero") from exc
        return cls(
            url=os.environ.get("URL", DEFAULT_URL).strip(),
            time_interval_minutes=interval,
            max_cache_age_hours=max_age,
        )
