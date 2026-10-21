"""Alice webhook. Production entry point: wsgi:app."""

import logging

from flask import Flask, jsonify, request

from src.api import Lession, ScheduleGroup
from src.config import Settings
from src.utils import Api, ScheduleUnavailable


def describe_lesson(lesson: Lession) -> str:
    text = lesson.title
    if lesson.cabinet:
        text += " в кабинете " + lesson.cabinet
    if lesson.korpus:
        text += " в корпусе " + lesson.korpus
    if lesson.teacher:
        text += f". Ведёт {lesson.teacher.rstrip('.')}" + "."
    return text


def format_schedule(schedule: ScheduleGroup) -> str:
    occupied = [i for i, pair in enumerate(schedule) if pair]
    if not occupied:
        return "Завтра отдыхаем"
    first, last = occupied[0], occupied[-1]
    answer = [f"Завтра у вас пар {len(occupied)}"]
    if first:
        answer.append(f"Учитесь с пары {first + 1}")
    answer.append("")
    for index in range(first, last + 1):
        pair = schedule[index]
        prefix = f"Пара {index + 1} - "
        if not pair:
            answer.append(prefix + "окно")
        elif len(pair) == 1:
            answer.append(prefix + describe_lesson(pair[0]))
        else:
            answer.append(prefix + "\n"+ "\n".join(
                f"Подгруппа {i} - {describe_lesson(lesson)}" for i, lesson in enumerate(pair, 1)
            ))
    return "\n".join(answer)


def handle_dialog(req: dict, res: dict, api: Api):
    if req["session"]["new"]:
        res["response"].update(text="Назовите вашу группу", buttons=[])
        return
    group = req["request"]["original_utterance"]
    try:
        schedule = api.get_by_group(group)
    except ScheduleUnavailable:
        res["response"]["text"] = "Расписание временно недоступно. Попробуйте немного позже."
        return
    if schedule is None:
        res["response"]["text"] = "Не удалось найти расписание. Повторите название вашей группы"
    else:
        res["response"]["text"] = format_schedule(schedule)


def valid_payload(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    session = payload.get("session")
    if not isinstance(session, dict) or not isinstance(session.get("new"), bool):
        return False
    if not isinstance(payload.get("version"), str):
        return False
    if session["new"]:
        return True
    utterance = payload.get("request")
    return (isinstance(utterance, dict)
            and isinstance(utterance.get("original_utterance"), str))


def create_app(settings: Settings | None = None, api: Api | None = None) -> Flask:
    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.extensions["schedule_api"] = api if api is not None else Api(settings)

    @app.get("/health")
    def health():
        # Liveness only: no network request and no guarantee that the source is up.
        return jsonify(status="ok")

    @app.post("/post")
    def webhook():
        payload = request.get_json(silent=True)
        if not valid_payload(payload):
            return jsonify(error="Invalid Alice request"), 400
        response = {
            "session": payload["session"],
            "version": payload["version"],
            "response": {"end_session": False},
        }
        handle_dialog(payload, response, app.extensions["schedule_api"])
        return jsonify(response)

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    create_app().run()  # Local development only. Use Gunicorn for deployment.
