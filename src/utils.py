"""A background refresher publishes snapshots; webhook reads never do network I/O."""

from dataclasses import dataclass
from datetime import date
import logging
from threading import Event, Lock, Thread
from time import monotonic

import requests

from src.api import ScheduleDocument, ScheduleGroup, ScheduleParseError, get_schedule
from src.clock import today_in_ufa
from src.config import Settings
from src.recognition import GroupResolver

logger = logging.getLogger(__name__)


class ScheduleUnavailable(RuntimeError):
    """No successful snapshot is available."""


class CacheExpired(ScheduleUnavailable):
    """The last successful download is older than the configured maximum."""


class ScheduleOutdated(ScheduleUnavailable):
    def __init__(self, schedule_date: date):
        self.schedule_date = schedule_date
        super().__init__(f"Source date is in the past: {schedule_date}")


class GroupNeedsClarification(ValueError):
    def __init__(self, candidates: tuple[str, ...]):
        self.candidates = candidates
        super().__init__("Please confirm the group name")


@dataclass(frozen=True)
class GroupSchedule:
    name: str
    schedule_date: date
    lessons: ScheduleGroup
    stale: bool = False


@dataclass(frozen=True)
class CachedSchedule:
    document: ScheduleDocument
    resolver: GroupResolver
    loaded_at: float


class Api:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings if settings is not None else Settings.from_env()
        self._interval_seconds = self.settings.time_interval_minutes * 60
        self._max_age_seconds = self.settings.max_cache_age_hours * 3600
        self._snapshot: CachedSchedule | None = None
        self._refresh_failed = False
        self._state_lock = Lock()
        self._download_lock = Lock()
        self._lifecycle_lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._session = requests.Session()

    def refresh_once(self) -> bool:
        """Only the background worker (or an explicit maintenance/test call) downloads."""
        with self._download_lock:
            try:
                document = get_schedule(self.settings.url, session=self._session)
                if not document.groups:
                    raise ScheduleParseError("The schedule contains no groups")
                snapshot = CachedSchedule(document, GroupResolver(document.groups), monotonic())
            except (requests.RequestException, ScheduleParseError):
                logger.exception("Schedule refresh failed; retaining the last successful snapshot")
                with self._state_lock:
                    self._refresh_failed = True
                return False
            with self._state_lock:
                self._snapshot = snapshot
                self._refresh_failed = False
            logger.info("Schedule updated: date=%s groups=%d", document.schedule_date, len(document.groups))
            return True

    def _refresh_loop(self):
        try:
            while not self._stop.is_set():
                try:
                    success = self.refresh_once()
                except Exception:
                    # An unexpected error must not silently kill the maintenance thread.
                    logger.exception("Unexpected background refresh error")
                    with self._state_lock:
                        self._refresh_failed = True
                    success = False
                delay = self._interval_seconds if success else min(30.0, self._interval_seconds)
                if self._stop.wait(delay):
                    break
        finally:
            self._session.close()

    def start_background_refresh(self):
        """Start once in a worker, after fork; the initial download is asynchronous."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(target=self._refresh_loop, name="schedule-refresh", daemon=True)
            self._thread.start()

    def stop_background_refresh(self, timeout: float = 8.0):
        with self._lifecycle_lock:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=timeout)
            else:
                self._session.close()

    def get_by_group(self, utterance: str) -> GroupSchedule | None:
        with self._state_lock:
            snapshot = self._snapshot
            failed = self._refresh_failed
        if snapshot is None:
            raise ScheduleUnavailable("The initial download has not completed")
        if monotonic() - snapshot.loaded_at >= self._max_age_seconds:
            raise CacheExpired("Maximum cache age reached")
        # Recompute Ufa's current day on every lookup, including across midnight.
        if snapshot.document.schedule_date < today_in_ufa():
            raise ScheduleOutdated(snapshot.document.schedule_date)
        recognition = snapshot.resolver.resolve(utterance)
        if recognition.name is None:
            if recognition.candidates:
                raise GroupNeedsClarification(recognition.candidates)
            return None
        lessons = snapshot.document.groups[recognition.name]
        return GroupSchedule(
            recognition.name, snapshot.document.schedule_date,
            [list(pair) if pair else None for pair in lessons], stale=failed,
        )
