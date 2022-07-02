import logging
import os
import sys
import time
from http import HTTPStatus
from logging import StreamHandler
from logging.handlers import RotatingFileHandler

import requests
import telegram
from dotenv import load_dotenv
from telegram.ext import CommandHandler, Updater

from exceptions import (APIResponseStatusCodeException,
                        CheckResponseException)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, 'telegram_bot.log')
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5000000, backupCount=5)
stream_handler = StreamHandler(sys.stdout)
logger.addHandler(file_handler)
logger.addHandler(stream_handler)
formatter = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s'
)
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)


load_dotenv()


PRACTICUM_TOKEN = os.getenv('PRACTICUM_TOKEN')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

RETRY_TIME = 600
ENDPOINT = 'https://practicum.yandex.ru/api/user_api/homework_statuses/'
HEADERS = {'Authorization': f'OAuth {PRACTICUM_TOKEN}'}


HOMEWORK_STATUSES = {
    'approved': 'Работа проверена: ревьюеру всё понравилось. Ура!',
    'reviewing': 'Работа взята на проверку ревьюером.',
    'rejected': 'Работа проверена: у ревьюера есть замечания.'
}


def send_message(bot, message):
    """
    Отправляет сообщение в Telegram чат, определяемый TELEGRAM_CHAT_ID.
    Принимает на вход два параметра:
    экземпляр класса Bot и строку с текстом сообщения
    """
    try:
        result = bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logger.info(f'Бот отправил сообщение в Telegram: {message}')
    except telegram.error.TelegramError as telegram_error:
        error_msg = f'Cбой при отправке сообщения в Telegram: {telegram_error}'
        logger.error(error_msg)
        raise telegram.error.TelegramError(error_msg)
    return result


def wake_up(update, context):
    """Приветствуем пользователя."""
    chat = update.effective_chat
    name = update.message.chat.first_name
    context.bot.send_message(
        chat_id=chat.id,
        text=(
            f'Привет, {name}. Я помогу тебе '
            f'проверить статус домашней работы'
        )
    )


def get_api_answer(current_timestamp):
    """
    Делает запрос к эндпоинту API-сервиса.
    В качестве параметра функция получает временную метку.
    В случае успешного запроса должна вернуть ответ API,
    преобразовав его из формата JSON к типам данных Python.
    """
    timestamp = current_timestamp or int(time.time())
    params = {'from_date': timestamp}
    logger.info('Отправляю запрос к ENDPOINT')
    response = requests.get(ENDPOINT, headers=HEADERS, params=params)
    if response.status_code == HTTPStatus.OK:
        logger.info('Ответ от ENDPOINT получен')
        try:
            return response.json()
        except AttributeError:
            error_msg = 'В ответ на запрос API выдал не JSON'
            logger.error(error_msg)
            raise AttributeError(error_msg)
    else:
        error_msg = (f'Ошибка соединения с сервером '
                     f'(ответ{response.status_code}).')
        logger.error(error_msg)
        raise ConnectionError(error_msg)


def check_response(response):
    """
    Проверяет ответ API на корректность.
    В качестве параметра функция получает ответ API,
    приведенный к типам данных Python.
    Если ответ API соответствует ожиданиям,
    то функция должна вернуть список домашних работ
    (он может быть и пустым),
    доступный в ответе API по ключу 'homeworks'
    """
    if response is None:
        error_msg = (f'В ответ на запрос к API '
                     f'получен: {type(response)}')
        logger.error(error_msg)
        raise CheckResponseException(error_msg)
    if not isinstance(response, dict):
        error_msg = ('В ответ на запрос API прислал не словарь')
        logger.error(error_msg)
        raise TypeError(error_msg)
    if 'homeworks' not in response:
        error_msg = ('Отсутствует ключ homeworks в ответе API')
        logger.error(error_msg)
        raise KeyError(error_msg)
    if not response['homeworks']:
        return {}
    homeworks_list = response.get('homeworks')
    if not isinstance(homeworks_list, list):
        error_msg = ('homeworks в ответе API не соответствует типу list')
        logger.error(error_msg)
        raise TypeError(error_msg)
    return homeworks_list


def parse_status(homework):
    """
    Извлекает из информации о конкретной домашней работе статус этой работы.
    В качестве параметра функция получает
    только один элемент из списка домашних работ.
    В случае успеха, функция возвращает
    подготовленную для отправки в Telegram строку,
    содержащую один из вердиктов словаря HOMEWORK_STATUSES
    """
    homework_name = homework.get('homework_name')
    if homework_name is None:
        msg = ('Ошибка обращения по ключу homework_name')
        raise KeyError(msg)
    homework_status = homework.get('status')
    if homework_status is None:
        raise KeyError(f'Ошибка в homework_status: {homework_status}')
    if homework_status not in HOMEWORK_STATUSES:
        error_msg = (f'Неизвестный статус домашней работы: '
                     f'{homework_status}')
        logger.error(error_msg)
        raise ValueError(error_msg)
    verdict = HOMEWORK_STATUSES[homework_status]

    return f'Изменился статус проверки работы "{homework_name}". {verdict}'


def check_tokens():
    """Проверяет доступность переменных окружения.
    Если отсутствует хотя бы одна переменная
    окружения — функция возвращает False,
    иначе — True
    """
    logger.info('Проверяю доступность переменных окружения')

    return all([PRACTICUM_TOKEN, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID])


def main():
    """Основная логика работы бота."""
    if not check_tokens():
        tokens = {
            'PRACTICUM_TOKEN': PRACTICUM_TOKEN,
            'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
            'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID
        }
        for token_name, token in tokens.items():
            if not token:
                logger.critical(
                    f'Отсутствует обязательная переменная '
                    f'окружения: \'{token_name}\'. '
                    f'Программа принудительно остановлена.')
        sys.exit('Отсутствуют обязательные переменные окружения:')

    updater = Updater(TELEGRAM_TOKEN)
    updater.dispatcher.add_handler(CommandHandler('start', wake_up))
    updater.start_polling()
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    current_timestamp = int(time.time())

    status_old = ''

    while True:
        try:
            response = get_api_answer(current_timestamp)
            homework = check_response(response)
            status = parse_status(homework[0])

            if status_old != status:
                send_message(bot, status)
                status_old = status
            current_timestamp = response.get('current_date')

        except APIResponseStatusCodeException as error:
            error_message = f'Ошибка при запросе к API: {error}'
            status_old = error_message
            logging.error(error_message)
            send_message(bot, error_message)
        except telegram.error.TelegramError as telegram_error:
            error_message = f'Ошибка в работе Telegramm: {telegram_error}'
            logging.error(error_message)
        finally:
            time.sleep(RETRY_TIME)


if __name__ == '__main__':
    main()
