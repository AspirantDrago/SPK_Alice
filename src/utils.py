import datetime

from src.api import Schedule, ScheduleGroup, get_schedule


class MetaSingleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(MetaSingleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class Api(metaclass=MetaSingleton):
    TIME_INTERVAL_MINUTES = 5

    def __init__(self):
        self._schedule: Schedule | None = None
        self._last_update = datetime.datetime.now()

    def _update(self):
        self._last_update = datetime.datetime.now()
        self._schedule = get_schedule()

    @staticmethod
    def normalize(title: str) -> str:
        return ''.join(c for c in title.lower() if c.isalnum())

    def get_by_group(self, group_name: str) -> ScheduleGroup | None:
        current_time = datetime.datetime.now()
        if (self._schedule is None or
                current_time - self._last_update > datetime.timedelta(minutes=self.TIME_INTERVAL_MINUTES)):
            self._update()
        group_name_normalized = self.normalize(group_name)
        if self._schedule:
            for key in self._schedule.keys():
                if group_name_normalized == self.normalize(key):
                    return self._schedule[key]
        return None


if __name__ == '__main__':
    api = Api()
    print(api.get_by_group('ТМ2-4'))