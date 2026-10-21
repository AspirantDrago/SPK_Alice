"""Separate readable display text from speech with declined Russian numerals."""

from dataclasses import dataclass
from datetime import date, timedelta
from src.api import Lession
from src.clock import today_in_ufa
from src.speech import cardinal, noun, numbers_to_words, ordinal, spoken_date, written_date
from src.utils import GroupSchedule

LIMIT = 1024
FOOTER = "Продолжение — скажите «дальше»."
CONTINUED = "Продолжение расписания."


@dataclass(frozen=True)
class Answer:
    text: str
    tts: str


def plain_answer(text: str) -> Answer:
    return Answer(text, numbers_to_words(text))


def day_label(value: date, today: date) -> Answer:
    if value == today:
        return Answer("Сегодня", "Сегодня")
    if value == today + timedelta(days=1):
        return Answer("Завтра", "Завтра")
    return Answer("На " + written_date(value), "На " + spoken_date(value))


def describe_lesson(lesson: Lession) -> Answer:
    title = lesson.title.rstrip(".")
    text = [title + "."]
    speech = [numbers_to_words(title) + "."]
    places = []
    if lesson.cabinet:
        places.append("Кабинет " + lesson.cabinet)
        speech.append("Кабинет номер " + numbers_to_words(lesson.cabinet).rstrip(".") + ".")
    if lesson.korpus:
        places.append("корпус " + lesson.korpus)
        speech.append("Корпус номер " + numbers_to_words(lesson.korpus) + ".")
    if places:
        location = ", ".join(places)
        text.append(location[0].upper() + location[1:] + ".")
    if lesson.teacher:
        teacher = lesson.teacher.rstrip(".")
        text.append("Преподаватель: " + teacher + ".")
        speech.append("Преподаватель " + numbers_to_words(teacher) + ".")
    return Answer(" ".join(text), " ".join(speech))


def schedule_blocks(schedule: GroupSchedule, today: date | None = None) -> list[Answer]:
    label = day_label(schedule.schedule_date, today if today is not None else today_in_ufa())
    occupied = [i for i, pair in enumerate(schedule.lessons) if pair]
    blocks = []
    if schedule.stale:
        blocks.append(plain_answer("Источник временно недоступен. Это последнее сохранённое расписание."))
    if not occupied:
        blocks.append(Answer(label.text + " занятий нет.", label.tts + " занятий нет."))
        return blocks
    count = len(occupied)
    word = noun(count, "пара", "пары", "пар")
    blocks.append(Answer(f"{label.text} у вас {count} {word}.",
                         f"{label.tts} у вас {cardinal(count, gender='f')} {word}."))
    first, last = occupied[0], occupied[-1]
    if first:
        number = first + 1
        preposition = "со" if number == 2 else "с"
        blocks.append(Answer(f"Занятия начинаются {preposition} {number}-й пары.",
                             f"Занятия начинаются {preposition} {ordinal(number, case='g')} пары."))
    for index in range(first, last + 1):
        number = index + 1
        pair = schedule.lessons[index]
        written = f"{number}-я пара"
        spoken = ordinal(number).capitalize() + " пара"
        if not pair:
            blocks.append(Answer(written + " — окно.", spoken + " — окно."))
        elif len(pair) == 1:
            lesson = describe_lesson(pair[0])
            blocks.append(Answer(written + ". " + lesson.text, spoken + ". " + lesson.tts))
        else:
            # Preserve the repository's separate numbered subgroup lines.
            blocks.append(Answer(written + ":", spoken + "."))
            for n, item in enumerate(pair, 1):
                lesson = describe_lesson(item)
                blocks.append(Answer(f"Подгруппа {n}: {lesson.text}",
                                     f"{ordinal(n).capitalize()} подгруппа. {lesson.tts}"))
    return blocks


def _split_words(text: str, limit: int) -> list[str]:
    parts = []
    while len(text) > limit:
        cut = text.rfind(" ", 0, limit + 1)
        if cut < 1:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def paginate(blocks: list[Answer]) -> list[Answer]:
    # Reserve room for the continuation label and footer in both channels.
    budget = LIMIT - len(CONTINUED) - len(FOOTER) - 8
    small = []
    for block in blocks:
        text = _split_words(block.text, budget)
        tts = _split_words(block.tts, budget)
        for i in range(max(len(text), len(tts))):
            small.append(Answer(text[i] if i < len(text) else "",
                                tts[i] if i < len(tts) else ""))
    pages = []
    current = Answer("", "")
    for block in small:
        candidate = Answer("\n\n".join(filter(None, (current.text, block.text))),
                           " ".join(filter(None, (current.tts, block.tts))))
        if (current.text or current.tts) and (len(candidate.text) > budget or len(candidate.tts) > budget):
            pages.append(current)
            current = block
        else:
            current = candidate
    if current.text or current.tts:
        pages.append(current)
    result = []
    for i, page in enumerate(pages):
        text, tts = page.text, page.tts
        if i:
            text = CONTINUED + "\n\n" + text
            tts = CONTINUED + " " + tts
        if i + 1 < len(pages):
            text += "\n\n" + FOOTER
            tts += " " + FOOTER
        result.append(Answer(text, tts))
    return result
