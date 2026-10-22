"""Download and parse the existing college schedule HTML format."""

from dataclasses import dataclass
from datetime import date, datetime
from itertools import islice
import re

from bs4 import BeautifulSoup
import requests


@dataclass(frozen=True)
class Lession:
    title: str
    teacher: str
    korpus: str | None = None
    cabinet: str | None = None
    # None means the whole group; otherwise preserve the actual HTML columns.
    subgroups: tuple[int, ...] | None = None
    subgroup_count: int = 1


type ScheduleGroup = list[None | list[Lession]]
type Schedule = dict[str, ScheduleGroup]


@dataclass(frozen=True)
class ScheduleDocument:
    schedule_date: date
    groups: Schedule


class ScheduleParseError(ValueError):
    """The source HTML is incomplete or its structure has changed."""


def parse_schedule(html: bytes | str) -> ScheduleDocument:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("div", class_="lrg2")
    table = container.find("table") if container else None
    if table is None:
        raise ScheduleParseError("Schedule table div.lrg2 > table was not found")

    dates = set()
    for heading in container.select(".zg .zgr"):
        for value in re.findall(r"\b\d{2}\.\d{2}\.\d{4}\b", heading.get_text(" ", strip=True)):
            try:
                dates.add(datetime.strptime(value, "%d.%m.%Y").date())
            except ValueError as exc:
                raise ScheduleParseError("Invalid schedule date") from exc
    if len(dates) != 1:
        raise ScheduleParseError("A unique schedule date was not found in the page heading")

    data: Schedule = {}
    rows_left = 0
    group_name = None
    seen_numbers = set()
    for row in islice(table.find_all("tr"), 1, None):
        if row.find("td", class_="hd0"):
            if rows_left:
                raise ScheduleParseError("Incomplete group before separator")
            continue
        tds = row.find_all("td", recursive=False)
        if not tds:
            continue
        if rows_left == 0:
            anchor = tds[0].find("a")
            if len(tds) < 3 or anchor is None:
                raise ScheduleParseError("Group header was not found")
            group_name = anchor.get_text(" ", strip=True)
            try:
                rows_left = int(tds[0].get("rowspan", 1))
            except (TypeError, ValueError) as exc:
                raise ScheduleParseError("Invalid group rowspan") from exc
            if not group_name or group_name in data or rows_left < 1:
                raise ScheduleParseError("Invalid or duplicate group header")
            data[group_name] = [None] * rows_left
            seen_numbers = set()

        rows_left -= 1
        number_cells = row.find_all("td", class_="hd")
        try:
            number = int(number_cells[-1].get_text(strip=True))
        except (IndexError, ValueError) as exc:
            raise ScheduleParseError("Invalid lesson number") from exc
        if number < 1 or number in seen_numbers:
            raise ScheduleParseError("Invalid or duplicate lesson number")
        seen_numbers.add(number)
        # Rowspan counts rows, not necessarily the largest numbered lesson.
        if number > len(data[group_name]):
            data[group_name].extend([None] * (number - len(data[group_name])))

        lessons = []
        cells = row.find_all("td", class_=["ur", "nul"], recursive=False)
        try:
            spans = [int(td.get("colspan", 1)) for td in cells]
        except (TypeError, ValueError) as exc:
            raise ScheduleParseError("Invalid lesson column span") from exc
        if not cells or any(span < 1 for span in spans):
            raise ScheduleParseError("Missing or invalid lesson columns")
        column_count = sum(spans)
        column = 1
        for td, span in zip(cells, spans):
            scope = None if span == column_count else tuple(range(column, column + span))
            column += span
            if "nul" in td.get("class", []):
                continue
            title = td.find("a", class_="z1")
            teacher = td.find("a", class_="z3")
            if title is None or teacher is None or not title.get_text(strip=True):
                raise ScheduleParseError("Lesson title or teacher was not found")
            cabinet = korpus = None
            location = td.find("a", class_="z2")
            if location is not None:
                text = location.get_text(" ", strip=True)
                if "/" in text:
                    korpus, cabinet = (part.strip() or None for part in text.rsplit("/", 1))
                else:
                    cabinet = text or None
            lessons.append(Lession(
                title=title.get_text(" ", strip=True),
                teacher=teacher.get_text(" ", strip=True),
                korpus=korpus,
                cabinet=cabinet,
                subgroups=scope,
                subgroup_count=column_count,
            ))
        if not lessons and not row.find("td", class_="nul"):
            raise ScheduleParseError("Neither lessons nor an empty slot were found")
        data[group_name][number - 1] = lessons or None

    if rows_left or not data:
        raise ScheduleParseError("Empty or incomplete schedule table")
    return ScheduleDocument(dates.pop(), data)


def get_schedule(url: str, *, session: requests.Session | None = None) -> ScheduleDocument:
    client = session if session is not None else requests
    # No automatic retries inside the webhook: keep upstream failures bounded.
    with client.get(url, timeout=(2, 5)) as response:
        response.raise_for_status()
        return parse_schedule(response.content)
