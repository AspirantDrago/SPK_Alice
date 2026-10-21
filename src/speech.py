"""Russian numerals and readable speech; TTS output contains no digits."""

from datetime import date
import re

from num2words import num2words

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря")
LETTER_SOUNDS = {
    "А": "а", "Б": "бэ", "В": "вэ", "Г": "гэ", "Д": "дэ", "Е": "е",
    "Ё": "ё", "Ж": "жэ", "З": "зэ", "И": "и", "Й": "й", "К": "ка",
    "Л": "эль", "М": "эм", "Н": "эн", "О": "о", "П": "пэ", "Р": "эр",
    "С": "эс", "Т": "тэ", "У": "у", "Ф": "эф", "Х": "ха", "Ц": "цэ",
    "Ч": "че", "Ш": "ша", "Щ": "ща", "Э": "э", "Ю": "ю", "Я": "я",
}


def cardinal(number: int, gender="m") -> str:
    return num2words(number, lang="ru", gender=gender)


def ordinal(number: int, gender="f", case="n") -> str:
    return num2words(number, lang="ru", to="ordinal", gender=gender, case=case)


def noun(number: int, one: str, few: str, many: str) -> str:
    if 11 <= number % 100 <= 14:
        return many
    return one if number % 10 == 1 else few if number % 10 in (2, 3, 4) else many


def numbers_to_words(text: str) -> str:
    def replace(match):
        value = match.group()
        if len(value) > 12 or (len(value) > 1 and value.startswith("0")):
            return " ".join(cardinal(int(d)) for d in value)
        return cardinal(int(value))
    # Add spaces also when a number touches a letter (305а, МДК02).
    result = re.sub(r"\d+", lambda m: " " + replace(m) + " ", text)
    # Prevent upstream text from being interpreted as Alice speech markup.
    result = re.sub(r"[<>+]", " ", result)
    result = re.sub(r"\s+([.,;:!?])", r"\1", result)
    return re.sub(r"\s+", " ", result).strip()


def group_to_speech(name: str) -> str:
    result = re.sub(r"[А-ЯЁ]{1,8}", lambda m: " ".join(LETTER_SOUNDS.get(c, c) for c in m.group()), name)
    return numbers_to_words(result.replace("-", " "))


def written_date(value: date) -> str:
    return f"{value.day} {MONTHS[value.month - 1]} {value.year} года"


def spoken_date(value: date) -> str:
    # Used after «на»: «на двадцать второе октября», not «на ... второго».
    return f"{ordinal(value.day, gender='n', case='a')} {MONTHS[value.month - 1]} {ordinal(value.year, gender='m', case='g')} года"
