"""Thread-safe, process-local schedule cache."""

import logging
from threading import Lock
from time import monotonic

import requests

from src.api import ScheduleGroup, ScheduleParseError, get_schedule
from src.config import Settings

logger = logging.getLogger(__name__)


class ScheduleUnavailable(RuntimeError):
    """No successfully downloaded schedule is available yet."""


class Api:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings if settings is not None else Settings.from_env()
        self._interval_seconds = self.settings.time_interval_minutes * 60
        self._schedule: dict[str, ScheduleGroup] | None = None
        self._next_update = 0.0
        self._lock = Lock()
        # Access to this Session is serialized by _lock.
        self._session = requests.Session()

    @staticmethod
    def normalize(title: str) -> str:
        return "".join(c for c in title.casefold() if c.isalnum())

    def _update(self):
        try:
            schedule = get_schedule(self.settings.url, session=self._session)
            indexed = {}
            for name, lessons in schedule.items():
                key = self.normalize(name)
                if not key or key in indexed:
                    raise ScheduleParseError("Ambiguous or empty normalized group name")
                indexed[key] = lessons
            if not indexed:
                raise ScheduleParseError("The schedule contains no groups")
        except (requests.RequestException, ScheduleParseError):
            logger.exception("Schedule refresh failed; retaining the last successful cache")
            # Failed downloads must not cause a request storm or replace valid data.
            self._next_update = monotonic() + min(30.0, self._interval_seconds)
        else:
            self._schedule = indexed
            self._next_update = monotonic() + self._interval_seconds
            logger.info("Schedule updated: %d groups", len(indexed))

    def get_by_group(self, group_name: str) -> ScheduleGroup | None:
        with self._lock:
            if monotonic() >= self._next_update:
                self._update()
            if self._schedule is None:
                raise ScheduleUnavailable("Schedule source is currently unavailable")
            schedule = self._schedule.get(self.normalize(group_name))
            # Callers cannot mutate the cached lists; lessons themselves are frozen.
            return None if schedule is None else [list(pair) if pair else None for pair in schedule]
