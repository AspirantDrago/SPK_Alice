from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import date, datetime
import os
from pathlib import Path
import re
from threading import Event
import unittest
from unittest.mock import MagicMock, Mock, patch

import requests

from main import create_app
from src.api import Lession, ScheduleDocument, ScheduleParseError, get_schedule, parse_schedule
from src.clock import UFA_TIMEZONE, today_in_ufa
from src.config import DEFAULT_URL, Settings
from src.presentation import Answer, describe_lesson, paginate, schedule_blocks, subgroup_slots
from src.recognition import GroupResolver, normalize
from src.speech import cardinal, group_to_speech, noun, numbers_to_words, ordinal, spoken_date
from src.utils import (Api, CacheExpired, GroupNeedsClarification, GroupSchedule,
                       ScheduleOutdated, ScheduleUnavailable)

HTML = (Path(__file__).parent / "fixtures/schedule.html").read_bytes()
SPLIT_HTML = (Path(__file__).parent / "fixtures/right_subgroup_only.html").read_bytes()
DAY = date(2026, 10, 22)


def payload(new=False, text="ТМ2-4", state=None):
    result = {"session": {"new": new, "session_id": "test"}, "version": "1.0",
              "request": {"original_utterance": text}}
    if state is not None:
        result["state"] = {"session": state}
    return result


def group(lessons=None, day=DAY, stale=False):
    if lessons is None:
        lessons = parse_schedule(HTML).groups["ТМ2-4"]
    return GroupSchedule("ТМ2-4", day, lessons, stale)


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings.from_env(), Settings(DEFAULT_URL, 5, 12))

    def test_environment(self):
        with patch.dict(os.environ, {"URL": " https://example.org/schedule ",
                                     "TIME_INTERVAL_MINUTES": "0.5", "MAX_CACHE_AGE_HOURS": "1.25"}):
            self.assertEqual(Settings.from_env(), Settings("https://example.org/schedule", 0.5, 1.25))

    def test_reject_invalid_age_and_interval(self):
        for name in ("TIME_INTERVAL_MINUTES", "MAX_CACHE_AGE_HOURS"):
            for value in ("0", "-1", "nan", "inf", "-inf", "", "word", "1e308"):
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                    with self.assertRaises(ValueError):
                        Settings.from_env()

    def test_reject_bad_url(self):
        for value in ("", "example.org", "file:///tmp/test", "https://"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings(url=value)


class ParserTests(unittest.TestCase):
    def test_date_groups_slots_and_subgroups(self):
        data = parse_schedule(HTML)
        self.assertEqual(data.schedule_date, DAY)
        self.assertEqual(set(data.groups), {"ТМ2-4", "ТМ1-1"})
        self.assertEqual(data.groups["ТМ1-1"], [None])
        self.assertEqual(len(data.groups["ТМ2-4"][3]), 2)
        self.assertIsNone(data.groups["ТМ2-4"][2])

    def test_cabinet_and_corpus(self):
        schedule = parse_schedule(HTML).groups["ТМ2-4"]
        self.assertEqual(schedule[1][0].cabinet, "305")
        self.assertEqual((schedule[3][0].korpus, schedule[3][0].cabinet), ("2", "401"))
        self.assertIsNone(schedule[3][1].cabinet)

    def test_lesson_number_can_exceed_rowspan(self):
        html = HTML.decode().replace('<td class="hd">1</td><td class="nul"></td></tr>\n</table>',
                                     '<td class="hd">3</td><td class="nul"></td></tr>\n</table>')
        self.assertEqual(parse_schedule(html).groups["ТМ1-1"], [None, None, None])

    def test_empty_subgroup_does_not_hide_other_lesson(self):
        html = HTML.decode().replace('<td class="ur"><a class="z1">Английский язык</a><a class="z3">Сидоров С.С.</a></td>',
                                     '<td class="nul"></td>')
        self.assertEqual(len(parse_schedule(html).groups["ТМ2-4"][3]), 1)

    def test_invalid_html_is_not_success(self):
        invalid = [b"<html>Error</html>", b'<div class="lrg2"><table></table></div>',
                   HTML.replace(b'rowspan="5"', b'rowspan="6"'),
                   HTML.replace(b'rowspan="5"', b'rowspan="oops"'),
                   HTML.replace(b'class="z3"', b'class="unknown"'),
                   HTML.replace(b'class="hd">2</td>', b'class="hd">0</td>'),
                   HTML.replace(b'class="hd">2</td>', b'class="hd">1</td>')]
        for html in invalid:
            with self.subTest(html=html[:60]), self.assertRaises(ScheduleParseError):
                parse_schedule(html)

    def test_missing_ambiguous_or_impossible_date_is_rejected(self):
        for html in (HTML.replace(b"22.10.2026", b"no date"),
                     HTML.replace(b"22.10.2026", b"31.02.2026"),
                     HTML.replace(b"22.10.2026", b"22.10.2026 23.10.2026")):
            with self.subTest(html=html[:80]), self.assertRaises(ScheduleParseError):
                parse_schedule(html)

    def test_cp1251_source(self):
        html = HTML.decode().replace('charset="utf-8"', 'charset="windows-1251"').encode("cp1251")
        self.assertIn("ТМ2-4", parse_schedule(html).groups)

    def test_download_uses_url_timeouts_and_status_check(self):
        session = MagicMock()
        response = session.get.return_value.__enter__.return_value
        response.content = HTML
        data = get_schedule("https://example.org/custom", session=session)
        session.get.assert_called_once_with("https://example.org/custom", timeout=(2, 5))
        response.raise_for_status.assert_called_once()
        self.assertEqual(data.schedule_date, DAY)

    def test_http_error_is_not_parsed(self):
        session = MagicMock()
        session.get.return_value.__enter__.return_value.raise_for_status.side_effect = requests.HTTPError("503")
        with self.assertRaises(requests.HTTPError):
            get_schedule("https://example.org", session=session)


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.resolver = GroupResolver(["ТМ2-4", "ТМ1-1", "А1-1", "Ан2-2", "21"])

    def test_latin_spaces_and_punctuation(self):
        for text in ("ТМ2-4", "TM2-4", "тм 2 4", "Т М 2—4"):
            with self.subTest(text=text):
                self.assertEqual(self.resolver.resolve(text).name, "ТМ2-4")

    def test_spoken_letters_and_digits(self):
        for text in ("тэ эм два четыре", "тэ эм две четыре", "тм два четыре",
                     "расписание группы тэ эм два дефис четыре", "моя группа ТМ2-4",
                     "покажи пожалуйста расписание для группы ТМ2-4 на завтра"):
            with self.subTest(text=text):
                self.assertEqual(self.resolver.resolve(text).name, "ТМ2-4")

    def test_composite_number(self):
        for text in ("двадцать один", "два один", "21"):
            self.assertEqual(self.resolver.resolve(text).name, "21")
        self.assertEqual(normalize("тм двести двадцать четыре"), "тм224")
        self.assertEqual(normalize("тм два ноль четыре"), "тм204")

    def test_uncertain_typo_is_suggested_not_chosen(self):
        result = self.resolver.resolve("ТМ2-5")
        self.assertIsNone(result.name)
        self.assertIn("ТМ2-4", result.candidates)

    def test_ambiguous_alias_requires_confirmation(self):
        resolver = GroupResolver(["ТМ2-4", "ТМ24"])
        self.assertEqual(resolver.resolve("ТМ2-4").name, "ТМ2-4")
        self.assertEqual(resolver.resolve("ТМ24").name, "ТМ24")
        self.assertIsNone(resolver.resolve("тэ эм два четыре").name)
        self.assertEqual(set(resolver.resolve("тэ эм два четыре").candidates), {"ТМ2-4", "ТМ24"})

    def test_empty_unknown_or_filler_does_not_choose_a_group(self):
        for text in ("", "...", "покажи расписание", "абракадабра"):
            self.assertIsNone(self.resolver.resolve(text).name)


class SpeechTests(unittest.TestCase):
    def test_pair_noun_forms(self):
        for number, expected in ((1, "пара"), (2, "пары"), (4, "пары"), (5, "пар"),
                                 (11, "пар"), (12, "пар"), (14, "пар"), (21, "пара"), (22, "пары")):
            with self.subTest(number=number):
                self.assertEqual(noun(number, "пара", "пары", "пар"), expected)

    def test_feminine_numbers_and_ordinals(self):
        self.assertEqual(cardinal(1, "f"), "одна")
        self.assertEqual(cardinal(2, "f"), "две")
        self.assertEqual(ordinal(2), "вторая")
        self.assertEqual(ordinal(3, case="g"), "третьей")

    def test_codes_leading_zeroes_and_room_numbers(self):
        text = numbers_to_words("МДК.02.01, кабинет 305А, корпус 2, 2026.")
        self.assertNotRegex(text, r"\d")
        self.assertIn("ноль два", text)
        self.assertIn("триста пять", text)
        self.assertIn("две тысячи двадцать шесть", text)

    def test_spoken_date_and_group(self):
        self.assertEqual(spoken_date(DAY), "двадцать второе октября две тысячи двадцать шестого года")
        self.assertEqual(group_to_speech("ТМ2-4"), "тэ эм два четыре")

    def test_source_markup_does_not_inject_tts_tags(self):
        self.assertNotRegex(numbers_to_words('<speaker audio="x">2+2</speaker>'), r"[<>+\d]")

    def test_today_tomorrow_and_absolute_date(self):
        for today, expected in ((DAY, "Сегодня"), (date(2026, 10, 21), "Завтра"),
                                (date(2026, 10, 20), "На 22 октября 2026 года")):
            with self.subTest(today=today):
                answer = schedule_blocks(group(), today=today)[0]
                self.assertTrue(answer.text.startswith(expected))
                self.assertNotRegex(answer.tts, r"\d")

    def test_ufa_midnight_and_noon(self):
        for utc, expected in (("2026-10-21T18:59:59+00:00", date(2026, 10, 21)),
                              ("2026-10-21T19:00:00+00:00", DAY)):
            instant = datetime.fromisoformat(utc)
            local = instant.astimezone(UFA_TIMEZONE)
            self.assertEqual(local.date(), expected)
            with patch("src.clock.datetime") as mocked:
                mocked.now.return_value = local
                self.assertEqual(today_in_ufa(), expected)
                mocked.now.assert_called_once_with(UFA_TIMEZONE)

    def test_tomorrow_across_year_and_month_boundaries(self):
        for actual, today in ((date(2027, 1, 1), date(2026, 12, 31)),
                              (date(2026, 11, 1), date(2026, 10, 31))):
            with self.subTest(actual=actual):
                answer = schedule_blocks(group(day=actual), today=today)[0]
                self.assertTrue(answer.text.startswith("Завтра"))
                self.assertTrue(answer.tts.startswith("Завтра"))

    def test_numbers_subgroups_gaps_and_readability(self):
        blocks = schedule_blocks(group(), today=DAY)
        text = "\n\n".join(block.text for block in blocks)
        speech = " ".join(block.tts for block in blocks)
        self.assertIn("Сегодня у группы 2 пары.", text)
        self.assertIn("Сегодня у группы две пары.", speech)
        self.assertIn("начало со 2-й пары", text)
        self.assertIn("начало со второй пары", speech)
        self.assertIn("3-я пара — окно", text)
        self.assertIn("Подгруппа 1:", text)
        self.assertIn("Подгруппа 2:", text)
        self.assertIn("Первая подгруппа", speech)
        self.assertIn("Вторая подгруппа", speech)
        self.assertNotIn("5-я пара", text)
        self.assertNotRegex(speech, r"\d")

    def test_single_pair_and_empty_day(self):
        answer = schedule_blocks(group([[Lession("Тест", "")]]), today=DAY)[0]
        self.assertEqual(answer.text, "Сегодня у вас 1 пара.")
        self.assertEqual(answer.tts, "Сегодня у вас одна пара.")
        self.assertEqual(schedule_blocks(group([]), today=DAY)[0].text, "Сегодня занятий нет.")

    def test_warning_for_stale_snapshot(self):
        text = schedule_blocks(group(stale=True), today=DAY)[0].text
        self.assertIn("последнее сохранённое", text)

    def test_building_answer_does_not_mutate_cached_slots(self):
        item = group()
        before = repr(item.lessons)
        self.assertEqual(schedule_blocks(item, today=DAY), schedule_blocks(item, today=DAY))
        self.assertEqual(repr(item.lessons), before)

    def test_location_preserves_room_letter_case(self):
        self.assertIn("305А", describe_lesson(Lession("Тест", "", cabinet="305А")).text)

    def test_paging_limits_and_content(self):
        blocks = [Answer("Предмет " + str(i) + " " + "описание " * 50,
                         "Предмет " + cardinal(i) + " " + "описание " * 75) for i in range(15)]
        pages = paginate(blocks)
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page.text), 1024)
            self.assertLessEqual(len(page.tts), 1024)
            self.assertNotRegex(page.tts, r"\d")
        self.assertIn("Предмет 14", " ".join(p.text for p in pages))

    def test_single_oversized_fragment_is_split_without_loss(self):
        pages = paginate([Answer("текст " * 600, "речь " * 1600)])
        self.assertGreater(len(pages), 2)
        self.assertEqual(" ".join(p.text for p in pages).count("текст"), 600)
        self.assertEqual(" ".join(p.tts for p in pages).count("речь"), 1600)
        self.assertTrue(all(max(len(p.text), len(p.tts)) <= 1024 for p in pages))


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.api = Api(Settings("https://example.org/schedule", 5, 12))
        self.now = 100.0
        self.clock = patch("src.utils.monotonic", side_effect=lambda: self.now)
        self.day = patch("src.utils.today_in_ufa", return_value=DAY)
        self.loader = patch("src.utils.get_schedule", return_value=parse_schedule(HTML))
        self.clock.start()
        self.day_mock = self.day.start()
        self.load = self.loader.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.day.stop)
        self.addCleanup(self.loader.stop)
        self.addCleanup(self.api.stop_background_refresh)

    def test_cold_lookup_does_not_download(self):
        with self.assertRaises(ScheduleUnavailable):
            self.api.get_by_group("ТМ24")
        self.load.assert_not_called()

    def test_successful_snapshot_and_repeated_lookups(self):
        self.assertTrue(self.api.refresh_once())
        self.assertEqual(self.api.get_by_group("тэ эм два четыре").name, "ТМ2-4")
        self.api.get_by_group("TM2-4")
        self.load.assert_called_once_with("https://example.org/schedule", session=self.api._session)

    def test_query_after_interval_still_never_downloads(self):
        self.api.refresh_once()
        self.now += 301
        self.assertIsNotNone(self.api.get_by_group("ТМ2-4"))
        self.assertEqual(self.load.call_count, 1)

    def test_returned_data_cannot_mutate_snapshot(self):
        self.api.refresh_once()
        result = self.api.get_by_group("ТМ24")
        result.lessons.pop(0)
        result.lessons[0].clear()
        again = self.api.get_by_group("ТМ24")
        self.assertEqual(again.lessons[1][0].title, "Математика")
        with self.assertRaises(FrozenInstanceError):
            again.lessons[1][0].title = "Changed"

    def test_failure_preserves_snapshot_and_success_clears_warning(self):
        self.api.refresh_once()
        self.load.side_effect = requests.Timeout("test")
        with self.assertLogs("src.utils", level="ERROR"):
            self.assertFalse(self.api.refresh_once())
        self.assertTrue(self.api.get_by_group("ТМ24").stale)
        self.load.side_effect = None
        self.api.refresh_once()
        self.assertFalse(self.api.get_by_group("ТМ24").stale)

    def test_age_limit_boundary_and_recovery(self):
        self.api.refresh_once()
        self.now = 100 + 12 * 3600 - 0.001
        self.assertIsNotNone(self.api.get_by_group("ТМ24"))
        self.now = 100 + 12 * 3600
        with self.assertRaises(CacheExpired):
            self.api.get_by_group("ТМ24")
        self.api.refresh_once()
        self.assertIsNotNone(self.api.get_by_group("ТМ24"))

    def test_failed_or_malformed_refresh_does_not_reset_age(self):
        self.api.refresh_once()
        for error in (requests.HTTPError("503"), ScheduleParseError("changed HTML")):
            self.now += 30000
            self.load.side_effect = error
            with self.assertLogs("src.utils", level="ERROR"):
                self.api.refresh_once()
        with self.assertRaises(CacheExpired):
            self.api.get_by_group("ТМ24")

    def test_date_rechecked_across_midnight_without_refresh(self):
        self.day_mock.return_value = date(2026, 10, 21)
        self.api.refresh_once()
        self.assertIsNotNone(self.api.get_by_group("ТМ24"))
        self.day_mock.return_value = DAY
        self.assertIsNotNone(self.api.get_by_group("ТМ24"))
        self.day_mock.return_value = date(2026, 10, 23)
        with self.assertRaises(ScheduleOutdated):
            self.api.get_by_group("ТМ24")
        self.assertEqual(self.load.call_count, 1)

    def test_ambiguous_recognition_is_not_automatically_used(self):
        self.load.return_value = ScheduleDocument(DAY, {"ТМ2-4": [None], "ТМ24": [None]})
        self.api.refresh_once()
        with self.assertRaises(GroupNeedsClarification):
            self.api.get_by_group("тэ эм два четыре")

    def test_lookup_reads_old_snapshot_during_slow_refresh(self):
        self.api.refresh_once()
        entered, release = Event(), Event()

        def download(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise requests.Timeout("test")
            return parse_schedule(HTML)

        self.load.side_effect = download
        self.api.start_background_refresh()
        try:
            self.assertTrue(entered.wait(2))
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(self.api.get_by_group, "ТМ24")
                self.assertEqual(result.result(timeout=1).name, "ТМ2-4")
        finally:
            release.set()
            self.api.stop_background_refresh()

    def test_background_updates_without_webhook_requests_and_starts_once(self):
        api = Api(Settings(time_interval_minutes=0.0002))
        updated = Event()
        calls = []

        def download(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                updated.set()
            return parse_schedule(HTML)

        with patch("src.utils.get_schedule", side_effect=download):
            try:
                api.start_background_refresh()
                first_thread = api._thread
                api.start_background_refresh()
                self.assertIs(api._thread, first_thread)
                self.assertTrue(updated.wait(2))
            finally:
                api.stop_background_refresh()
        self.assertFalse(first_thread.is_alive())
        self.assertGreaterEqual(len(calls), 2)


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.get_by_group.return_value = group()
        self.client = create_app(api=self.api).test_client()
        today = patch("src.presentation.today_in_ufa", return_value=DAY)
        today.start()
        self.addCleanup(today.stop)

    def post(self, **kwargs):
        return self.client.post("/post", json=payload(**kwargs))

    def test_greeting_and_health_without_source(self):
        self.assertEqual(self.post(new=True).status_code, 200)
        self.assertEqual(self.client.get("/health").json, {"status": "ok"})
        self.api.get_by_group.assert_not_called()

    def test_json_tts_session_and_readable_subgroups(self):
        result = self.post()
        self.assertEqual(result.mimetype, "application/json")
        self.assertEqual(result.json["session"], payload()["session"])
        self.assertEqual(result.json["version"], "1.0")
        text = result.json["response"]
        self.assertFalse(text["end_session"])
        self.assertIn("Подгруппа 1", text["text"])
        self.assertIn("Подгруппа 2", text["text"])
        self.assertNotRegex(text["tts"], r"\d")

    def test_error_cases_have_digit_free_spoken_messages(self):
        for error in (CacheExpired(), ScheduleOutdated(date(2026, 10, 20)), ScheduleUnavailable()):
            self.api.get_by_group.side_effect = error
            with self.subTest(error=error):
                result = self.post()
                self.assertEqual(result.status_code, 200)
                self.assertNotRegex(result.json["response"]["tts"], r"\d")

    def test_missing_group(self):
        self.api.get_by_group.return_value = None
        self.assertIn("Повторите", self.post().json["response"]["text"])

    def test_ambiguous_group_has_buttons_and_spoken_group_names(self):
        self.api.get_by_group.side_effect = GroupNeedsClarification(("ТМ2-4", "ТМ24"))
        answer = self.post().json["response"]
        self.assertEqual(len(answer["buttons"]), 2)
        self.assertIn("тэ эм два четыре", answer["tts"])
        self.assertNotRegex(answer["tts"], r"\d")

    def test_invalid_schema_is_400(self):
        for body in (None, [], "text", {}, {"session": {}},
                     {"session": {"new": "false"}, "version": "1.0"},
                     {"session": {"new": False}, "version": "1.0", "request": {}},
                     {"session": {"new": True}, "version": 1}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/post", json=body).status_code, 400)
        self.assertEqual(self.client.post("/post", data="{broken", content_type="application/json").status_code, 400)

    def test_pagination_continues_using_session_state(self):
        lessons = [[Lession(f"Предмет {i} " + "описание " * 20, "Иванов", cabinet="305")] for i in range(1, 8)]
        self.api.get_by_group.return_value = group(lessons)
        result = self.post().json
        answers = []
        for _ in range(30):
            answer = result["response"]
            self.assertLessEqual(len(answer["text"]), 1024)
            self.assertLessEqual(len(answer["tts"]), 1024)
            self.assertNotRegex(answer["tts"], r"\d")
            answers.append(answer["text"])
            if not result["session_state"]:
                break
            result = self.post(text="дальше", state=result["session_state"]).json
        else:
            self.fail("Pagination did not terminate")
        self.assertGreater(len(answers), 1)
        self.assertIn("Предмет 7", " ".join(answers))
        self.assertEqual(self.api.get_by_group.call_args.args[0], "ТМ2-4")

    def test_continuation_without_valid_state_asks_for_group(self):
        for state in (None, {"schedule": {}}, {"schedule": {"group": [], "next_page": -1}}):
            result = self.post(text="дальше", state=state).json["response"]
            self.assertIn("Назовите группу", result["text"])
        self.api.get_by_group.assert_not_called()

    def test_changed_schedule_restarts_pages(self):
        schedule = group([[Lession("Предмет " + "текст " * 100, "")] for _ in range(7)])
        self.api.get_by_group.return_value = schedule
        first = self.post().json
        saved = first["session_state"]
        schedule.lessons[0][0] = Lession("Новый предмет", "")
        repeated = self.post(text="дальше", state=saved).json["response"]["text"]
        self.assertIn("Новый предмет", repeated)
        self.assertTrue(repeated.startswith("Сегодня"))


class PartialSubgroupRegressionTests(unittest.TestCase):
    def schedule(self, html=SPLIT_HTML):
        document = parse_schedule(html)
        return GroupSchedule("Л2-1", document.schedule_date, document.groups["Л2-1"])

    def test_right_half_only_keeps_its_second_subgroup_number(self):
        schedule = self.schedule()
        for index in (1, 4):
            self.assertEqual(len(schedule.lessons[index]), 1)
            self.assertEqual(schedule.lessons[index][0].subgroups, (2,))
            self.assertEqual(schedule.lessons[index][0].subgroup_count, 2)

    def test_colspan_means_common_lesson_not_two_separate_lessons(self):
        schedule = self.schedule()
        for index in (2, 3):
            self.assertEqual(len(schedule.lessons[index]), 1)
            self.assertIsNone(schedule.lessons[index][0].subgroups)
        self.assertEqual(subgroup_slots(schedule), {1: [3, 4], 2: [2, 3, 4, 5]})

    def test_individual_counts_and_start_times_match_the_screenshot(self):
        blocks = schedule_blocks(self.schedule(), today=DAY)
        text = "\n\n".join(block.text for block in blocks)
        speech = " ".join(block.tts for block in blocks)
        self.assertIn("Подгруппа 1: 2 пары, начало с 3-й пары.", text)
        self.assertIn("Подгруппа 2: 4 пары, начало со 2-й пары.", text)
        self.assertIn("Первая подгруппа: две пары, начало с третьей пары.", speech)
        self.assertIn("Вторая подгруппа: четыре пары, начало со второй пары.", speech)
        self.assertNotIn("Занятия начинаются со 2-й пары", text)
        self.assertNotRegex(speech, r"\d")

    def test_single_subgroup_lessons_are_not_announced_as_common(self):
        text = "\n\n".join(block.text for block in schedule_blocks(self.schedule(), today=DAY))
        second = text.split("2-я пара:", 1)[1].split("3-я пара", 1)[0]
        fifth = text.split("5-я пара:", 1)[1]
        self.assertIn("Подгруппа 1: занятий нет.", second)
        self.assertIn("Подгруппа 2: Иностранный язык.", second)
        self.assertIn("Подгруппа 2: Башкирский язык.", fifth)
        self.assertIn("3-я пара. Литература.", text)
        self.assertIn("4-я пара. Физика.", text)
        self.assertNotIn("вся группа", text.casefold())
        speech = " ".join(block.tts for block in schedule_blocks(self.schedule(), today=DAY))
        self.assertNotIn("вся группа", speech.casefold())

    def test_left_half_only_is_not_relabelled_as_second_subgroup(self):
        html = re.sub(r'(<td class="nul"></td>)(<td class="ur">.*?</td>)',
                      r'\2\1', SPLIT_HTML.decode(), flags=re.S)
        schedule = self.schedule(html)
        self.assertEqual(schedule.lessons[1][0].subgroups, (1,))
        self.assertEqual(subgroup_slots(schedule), {1: [2, 3, 4, 5], 2: [3, 4]})

    def test_a_subgroup_with_no_lessons_is_reported_as_empty(self):
        schedule = group([[Lession("Информатика", "", subgroups=(2,), subgroup_count=2)]])
        text = "\n".join(block.text for block in schedule_blocks(schedule, today=DAY))
        self.assertIn("Подгруппа 1: занятий нет.", text)
        self.assertIn("Подгруппа 2: 1 пара, начало с 1-й пары.", text)

    def test_invalid_colspan_is_rejected(self):
        for value in (b'0', b'-1', b'invalid'):
            with self.subTest(value=value), self.assertRaises(ScheduleParseError):
                parse_schedule(SPLIT_HTML.replace(b'colspan="2"', b'colspan="' + value + b'"'))

    def test_subgroup_information_survives_api_and_webhook(self):
        api = Api(Settings())
        self.addCleanup(api.stop_background_refresh)
        with patch("src.utils.get_schedule", return_value=parse_schedule(SPLIT_HTML)), \
                patch("src.utils.today_in_ufa", return_value=DAY), \
                patch("src.presentation.today_in_ufa", return_value=DAY):
            api.refresh_once()
            response = create_app(api=api).test_client().post("/post", json=payload(text="Л2-1")).json["response"]
        self.assertIn("Подгруппа 1: 2 пары", response["text"])
        self.assertIn("Подгруппа 2: 4 пары", response["text"])
        self.assertIn("Подгруппа 2: Башкирский язык", response["text"])
        self.assertNotRegex(response["tts"], r"\d")


if __name__ == "__main__":
    unittest.main()
