from pprint import pprint
from dataclasses import dataclass
from itertools import islice

import requests
import bs4

URL = 'https://schedule.str-spc.ru/hg.htm'


@dataclass
class Lession:
    title: str
    teacher: str
    korpus: str | None = None
    cabinet: str | None = None


type ScheduleGroup = list[None | list[Lession]]
type Schedule = dict[str, ScheduleGroup]


def get_schedule() -> Schedule:
    data = dict()
    try:
        r = requests.get(URL, timeout=1)
        soup = bs4.BeautifulSoup(r.content, 'html.parser')
        table = soup.find('div', class_='lrg2').table
        count_lessons = 0
        group_name = None
        for row in islice(table.find_all('tr'), 1, None):
            if row.find('td', class_='hd0'):
                continue
            # Если новая группа
            if count_lessons == 0:
                tds = row.find_all('td')
                assert len(tds) >= 3
                assert tds[0].find('a')
                group_name = tds[0].find('a').text
                count_lessons = int(tds[0].get('rowspan', 1))
                data[group_name] = [None] * count_lessons

            if count_lessons > 0:
                count_lessons -= 1
                if row.find('td', class_='nul'):
                    continue
                number_lesson = int(row.find_all('td', class_='hd')[-1].text)
                sub_tds = row.find_all('td', class_='ur')
                assert len(sub_tds)
                sub_lessons = []
                for td in sub_tds:
                    lession = Lession(
                        title=td.find('a', class_='z1').text,
                        teacher=td.find('a', class_='z3').text,
                    )
                    if cab := td.find('a', class_='z2'):
                        if '/' in cab.text:
                            lession.cabinet = cab.text.split('/')[-1]
                            lession.korpus = cab.text.split('/')[0]
                        else:
                            lession.cabinet = cab.text.split('/')

                    sub_lessons.append(lession)
                data[group_name][number_lesson - 1] = sub_lessons

        return data
    except requests.exceptions.RequestException:
        return data


if __name__ == '__main__':
    pprint(get_schedule())
