from flask import Flask, request

import logging
import json

from src.utils import Api

app = Flask(__name__)
api = Api()
logging.basicConfig(level=logging.INFO)


@app.route('/post', methods=['POST'])
def main():
    logging.info('Request: %r', request.json)
    response = {
        'session': request.json['session'],
        'version': request.json['version'],
        'response': {
            'end_session': False
        }
    }
    handle_dialog(request.json, response)
    logging.info('Response: %r', request.json)
    return json.dumps(response)


def handle_dialog(req, res):
    if req['session']['new']:
        res['response']['text'] = 'Назовите вашу группу'
        res['response']['buttons'] = []
        return
    group = req['request']['original_utterance']
    schedule = api.get_by_group(group)
    if not schedule:
        res['response']['text'] = 'Не удалось найти расписание. Повторите название вашей группы'
        return
    if not any(schedule):
        res['response']['text'] = 'Завтра отдыхаем'
        return
    while schedule and schedule[-1] is None:
        schedule.pop()
    answer: list[str] = []
    answer.append('Завтра у вас пар {}'.format(
        sum(map(bool, schedule))
    ))
    skip_lessions = 0
    while schedule and schedule[0] is None:
        schedule.pop(0)
        skip_lessions += 1
    if skip_lessions:
        answer.append('Учитесь с пары {}'.format(skip_lessions))
    answer.append('')

    for n, pair in enumerate(schedule, 1):
        s = f'Пара {n}'
        if pair is None:
            s += f' - окно'
        elif len(pair) == 1:
            pair = pair[0]
            s += f' - {pair.title}'
            if pair.cabinet:
                s += ' в кабинете ' + pair.cabinet
            if pair.korpus:
                s += ' в корпусе ' + pair.korpus
            s += f'. Ведёт {pair.teacher}.'
        answer.append(s)

    res['response']['text'] = '\n'.join(answer)

if __name__ == '__main__':
    app.run()
