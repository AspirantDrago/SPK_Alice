"""Alice webhook. Production entry point: wsgi:app."""

import hashlib
import logging

from flask import Flask, jsonify, request

from src.config import Settings
from src.presentation import Answer, paginate, plain_answer, schedule_blocks
from src.speech import group_to_speech, spoken_date, written_date
from src.utils import Api, CacheExpired, GroupNeedsClarification, ScheduleOutdated, ScheduleUnavailable

CONTINUE = {"дальше", "далее", "продолжи", "продолжение", "ещё", "еще"}


def set_answer(res: dict, answer: Answer):
    res["response"].update(text=answer.text, tts=answer.tts)


def continuation(req: dict):
    state = req.get("state")
    session = state.get("session") if isinstance(state, dict) else None
    saved = session.get("schedule") if isinstance(session, dict) else None
    if not isinstance(saved, dict):
        return None
    page = saved.get("next_page")
    if (not isinstance(saved.get("group"), str) or len(saved["group"]) > 100
            or not isinstance(saved.get("date"), str)
            or not isinstance(saved.get("revision"), str)
            or type(page) is not int or not 0 <= page < 10000):
        return None
    return saved


def handle_dialog(req: dict, res: dict, api: Api):
    if req["session"]["new"]:
        set_answer(res, plain_answer("Здравствуйте! Назовите вашу группу."))
        res["response"]["buttons"] = []
        return
    utterance = req["request"]["original_utterance"].strip()
    saved = None
    if utterance.casefold().rstrip(".!?") in CONTINUE:
        saved = continuation(req)
        if saved is None:
            set_answer(res, plain_answer("Назовите группу, для которой нужно показать расписание."))
            return
        utterance = saved["group"]
    try:
        schedule = api.get_by_group(utterance)
    except GroupNeedsClarification as exc:
        choices = ", ".join(exc.candidates)
        spoken = ", ".join(group_to_speech(name) for name in exc.candidates)
        set_answer(res, Answer("Уточните группу. Вы имели в виду: " + choices + "?",
                               "Уточните группу. Вы имели в виду: " + spoken + "?"))
        res["response"]["buttons"] = [{"title": name, "hide": True} for name in exc.candidates]
        return
    except CacheExpired:
        set_answer(res, plain_answer("Сохранённое расписание устарело, а новое пока не удалось загрузить. Попробуйте позже."))
        return
    except ScheduleOutdated as exc:
        set_answer(res, Answer(
            f"На сайте пока только расписание на {written_date(exc.schedule_date)}. Актуальное расписание ещё не опубликовано.",
            f"На сайте пока только расписание на {spoken_date(exc.schedule_date)}. Актуальное расписание ещё не опубликовано.",
        ))
        return
    except ScheduleUnavailable:
        set_answer(res, plain_answer("Расписание пока загружается или источник временно недоступен. Попробуйте немного позже."))
        return
    if schedule is None:
        set_answer(res, plain_answer("Не удалось найти группу. Повторите её название, например по буквам и цифрам."))
        return
    pages = paginate(schedule_blocks(schedule))
    revision = hashlib.sha256(repr(pages).encode()).hexdigest()[:16]
    page = (saved["next_page"] if saved and saved["date"] == schedule.schedule_date.isoformat()
            and saved["revision"] == revision else 0)
    if page >= len(pages):
        page = 0
    set_answer(res, pages[page])
    if page + 1 < len(pages):
        res["session_state"] = {"schedule": {
            "group": schedule.name, "date": schedule.schedule_date.isoformat(),
            "revision": revision, "next_page": page + 1,
        }}
        res["response"]["buttons"] = [{"title": "Дальше", "hide": True}]


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
        return jsonify(status="ok")  # Liveness only, no source request.

    @app.post("/post")
    def webhook():
        payload = request.get_json(silent=True)
        if not valid_payload(payload):
            return jsonify(error="Invalid Alice request"), 400
        response = {
            "session": payload["session"], "version": payload["version"],
            "response": {"end_session": False}, "session_state": {},
        }
        handle_dialog(payload, response, app.extensions["schedule_api"])
        return jsonify(response)

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    api = app.extensions["schedule_api"]
    api.start_background_refresh()
    try:
        app.run(use_reloader=False)  # Production: Gunicorn starts the thread after fork.
    finally:
        api.stop_background_refresh()
