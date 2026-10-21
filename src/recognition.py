"""Conservative Russian group-name recognition; uncertain matches need confirmation."""

from collections import defaultdict
from dataclasses import dataclass
from difflib import get_close_matches
import re
import unicodedata

# Visual equivalents and common Latin spellings of group prefixes.
LATIN = str.maketrans({
    "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к",
    "m": "м", "n": "н", "o": "о", "p": "р", "r": "р", "s": "с",
    "t": "т", "x": "х", "y": "у",
})
LETTERS = {
    "а": "а", "бэ": "б", "б": "б", "вэ": "в", "гэ": "г", "дэ": "д",
    "е": "е", "жэ": "ж", "зэ": "з", "и": "и", "й": "й", "ка": "к",
    "эл": "л", "эль": "л", "эм": "м", "эн": "н", "о": "о", "пэ": "п",
    "эр": "р", "эс": "с", "тэ": "т", "те": "т", "у": "у", "эф": "ф",
    "ха": "х", "цэ": "ц", "че": "ч", "ша": "ш", "ща": "щ", "ы": "ы", "э": "э", "ю": "ю", "я": "я",
}
NUMBERS = {
    "ноль": 0, "нуль": 0, "один": 1, "одна": 1, "одну": 1, "одно": 1,
    "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
    "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
}
FILLER = {
    "расписание", "группа", "группы", "группе", "группу", "для", "на", "сегодня",
    "завтра", "покажи", "покажите", "скажи", "скажите", "пожалуйста", "моя",
    "моей", "мою", "мне", "номер", "дефис", "тире", "минус",
}


def folded(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().replace("ё", "е").translate(LATIN)


def normalize(text: str) -> str:
    tokens = re.findall(r"[а-яa-z]+|[0-9]+", folded(text))
    result = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in FILLER:
            i += 1
            continue
        if token in NUMBERS:
            # Combine a hundred/tens phrase; consecutive single digits stay distinct.
            value = NUMBERS[token]
            i += 1
            if value >= 100 and i < len(tokens) and 10 <= NUMBERS.get(tokens[i], -1) < 100:
                value += NUMBERS[tokens[i]]
                i += 1
            if value >= 20 and value % 10 == 0 and i < len(tokens) and 0 < NUMBERS.get(tokens[i], -1) < 10:
                value += NUMBERS[tokens[i]]
                i += 1
            result.append(str(value))
            continue
        result.append(LETTERS.get(token, token))
        i += 1
    return "".join(result)


@dataclass(frozen=True)
class Recognition:
    name: str | None
    candidates: tuple[str, ...] = ()


class GroupResolver:
    def __init__(self, names):
        self._literal = defaultdict(set)
        self._aliases = defaultdict(set)
        for name in names:
            # Preserve separators here to distinguish "ТМ2-4" from "ТМ24".
            literal = re.sub(r"\s+", "", folded(name))
            self._literal[literal].add(name)
            key = normalize(name)
            if key:
                self._aliases[key].add(name)

    def resolve(self, utterance: str) -> Recognition:
        literal = re.sub(r"\s+", "", folded(utterance))
        names = self._literal.get(literal)
        key = normalize(utterance)
        if not names and key:
            names = self._aliases.get(key)
        if names:
            ordered = tuple(sorted(names))
            return Recognition(ordered[0]) if len(ordered) == 1 else Recognition(None, ordered[:3])
        if not key:
            return Recognition(None)
        # A spelling approximation is only a suggestion, never an automatic choice.
        keys = get_close_matches(key, self._aliases, n=3, cutoff=0.72)
        candidates = tuple(dict.fromkeys(name for k in keys for name in sorted(self._aliases[k])))
        return Recognition(None, candidates[:3])
