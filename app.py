import multiprocessing
from logging.handlers import RotatingFileHandler
from multiprocessing import Process
from configparser import ConfigParser
from flask import Flask, request, Response
import requests
import os
import logging

from requests import HTTPError, Timeout, TooManyRedirects


"""__file__ выдает нам путь до файла,abspath редактирует путь под систему откуда запускается файл, dirname получаем \
путь до папки"""
root_path = os.path.dirname(os.path.abspath(__file__))

logger = logging.getLogger('faceid_transport')  # Лог, который при запуске скрипта будет создаваться в директории запуска
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(filename=os.path.join(root_path, 'faceid_transport.log'), maxBytes=20000, backupCount=2)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

config_path = os.path.join(root_path, 'faceid.cfg')  # Указываем путь к конфигу и парсим его
config = ConfigParser() 
config.read(config_path)

faceid_config_url = config.get('FACEID', 'url')  # Достаем из конфига url, port
faceid_config_port = config.get('FACEID', 'port')

number_of_attempts = 5

# http://192.168.10.98:8091/daycamprocessing?path_to_dir=/mnt/data/test_images/20210519_57&clientid=3

app = Flask(__name__)   # Создаем Flask приложение


@app.route('/daycamprocessing')  # Ждем запрос и формируем url из полученных параметров и данных конфига
def query():
    url = f'{faceid_config_url}:{faceid_config_port}/'
    path_to_dir = request.args.get('path_to_dir')
    clientid = request.args.get('clientid')
    url += f'daycamprocessing?path_to_dir={path_to_dir}&clientid={clientid}'
    logger.info(f'The request from the DLM came: {url}')

    manager = multiprocessing.Manager()  # Создаем словарь для общения между процессами
    check_dict = manager.dict()
    create_processes = Process(target=get_to_faceid, args=(number_of_attempts, url, check_dict))  # Создаем процесс,\
    # который будет отправлять гет запрос к FaceID и возвращать словарь со статусом ответа
    create_processes.daemon = True

    logger.info('Проверяем статус работы сервисов: ocr_lp, faceid, atlas_scheduler')
    # Запрашиваем статусы служб
    ocr_status = status_service('ocr_lp')
    faceid_status = status_service('faceid')
    scheduler_status = status_service('atlas_scheduler')

    logger.info('Если работают ocr_lp, atlas_scheduler - останавливаем; если faceid не работает - запускаем')
    if ocr_status:
        stop_ocr = command_to_service(number_of_attempts, 'ocr_lp', 'stop')
        if not stop_ocr:
            check_dict['status_code'] = 429  # Если команда не прошла отдаем DLM статус код 429
            return Response(status=check_dict['status_code'])

    if scheduler_status:
        stop_scheduler = command_to_service(number_of_attempts, 'atlas_scheduler', 'stop')
        if not stop_scheduler:
            check_dict['status_code'] = 429  # Если команда не прошла отдаем DLM статус код 429
            return Response(status=check_dict['status_code'])

    if not faceid_status:
        start_faceid = command_to_service(number_of_attempts, 'faceid', 'start')
        if not start_faceid:
            check_dict['status_code'] = 429  # Если команда не прошла отдаем DLM статус код 429
            return Response(status=check_dict['status_code'])

    create_processes.start()
    logger.info(f"Процесс отправки запроса к faceid с PID: {create_processes.pid} запущен.")

    while create_processes.is_alive():  # Ждём завершения процесса
        pass

    logger.info("Возвращаем службы в работу")
    restart_faceid = command_to_service(number_of_attempts, 'faceid', 'restart')
    start_ocr = command_to_service(number_of_attempts, 'ocr_lp', 'start')
    start_sheduler = command_to_service(number_of_attempts, 'atlas_scheduler', 'start')

    # Если какая-либо из команд не прошла отдаем DLM статус код 429
    if not restart_faceid or not start_ocr or not start_sheduler:
        check_dict['status_code'] = 429
        return Response(status=check_dict['status_code'])

    logger.info(f"Done. Status_code {check_dict['status_code']} was sent to DLM")
    return Response(status=check_dict['status_code'])


def status_service(name_service: str) -> bool:
    """
    Запрашивает статус службы и ожидает завершения
    :param name_service: имя службы
    :return: bool
    """
    exit_code = os.WEXITSTATUS(os.system(f'systemctl is-active --quiet {name_service}'))
    if exit_code == 0:
        logger.info(f'{name_service} status is active. Exit code: {exit_code}, message: {os.strerror(exit_code)}')
        return True
    else:
        logger.info(f'Error:Get status {name_service} failed, exit code is: {exit_code},\
         message: {os.strerror(exit_code)}')
    return False


def command_to_service(number_of_attempts: int, name_service: str, cmd: str) -> bool:
    """
    Функция отвечает за запуск, остановку и рестарт службы, после чего ожидает завершения и отдает контроль
    :param number_of_attempts: количество попыток
    :param name_service: имя службы
    :param cmd: команда на выполнение
    :return: bool
    """
    exit_code = os.WEXITSTATUS(os.system(f'systemctl {cmd} {name_service}'))
    for i in range(1, number_of_attempts + 1):
        if exit_code == 0:
            logger.info(f'{name_service} has been {cmd}ed. Exit code: {exit_code}, message: {os.strerror(exit_code)}')
            return True
        else:
            logger.error(f'Error:{name_service} has not been {cmd}ed, exit code: {exit_code},\
             message: {os.strerror(exit_code)}')
        logger.info(f'Количесвто попыток: {i}')
    return False


def get_to_faceid(number_of_attempts: int, url: str, check_dict: dict) -> dict:
    """
    Фунцкия описывает процесс отправки запроса к faceid
    :param number_of_attempts: количество попыток отправки запроса
    :param check_dict: словарь для общения между процессами
    :param url: url к faceid
    :return: check_dict
    """
    for i in range(1, number_of_attempts + 1):
        try:
            logger.info(f'Количесвто попыток отправки запроса к faceid: {i}')
            response = requests.get(url, timeout=9999999)
            check_dict['status_code'] = response.status_code
            logger.info(f'Ответ получен, статус ответа: {response.status_code}')
            return check_dict
        except ConnectionError as ex:
            logger.exception(ex)
        except HTTPError as ex:
            logger.exception(ex)
        except Timeout as ex:
            logger.exception(ex)
        except TooManyRedirects as ex:
            logger.exception(ex)
        except Exception as ex:
            logger.exception(ex)
    logger.info('Количество попыток отправки запроса исчерпано, запрос не отправлен,отдаю DLM`y status code 429')
    check_dict['status_code'] = 429
    return check_dict


if __name__ == '__main__':
    logger.info('faceid_transport service has been launched...')
    app.run(host='192.168.0.159', debug=True, port=8092)
