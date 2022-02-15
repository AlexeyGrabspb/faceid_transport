import multiprocessing
from logging.handlers import RotatingFileHandler
from multiprocessing import Process
from configparser import ConfigParser
from flask import Flask, request, Response
import requests
import os
import subprocess
import time
import logging

from requests import HTTPError, Timeout, TooManyRedirects

root = logging.getLogger()
root.setLevel(logging.INFO)

logger = logging.getLogger('faceid_transport')
logger.setLevel(logging.INFO)
handler = RotatingFileHandler('faceid_transport.log', maxBytes=20000, backupCount=2)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)


app = Flask(__name__)   # Создаем Flask приложение
"""__file__ выдает нам путь до файла,abspath редактирует путь под систему откуда запускается файл, dirname получаем \
путь до папки"""
root_path = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(root_path, 'faceid.cfg') # Подкючаем конфиг к нашему приложению
config = ConfigParser() # Парсим конфиг faceid.cfg
config.read(config_path)

faceid_config_url = config.get('FACEID', 'url') # Достаем из конфига url, port
faceid_config_port = config.get('FACEID', 'port')

number_of_attempts = 5

# http://192.168.10.98:8091/daycamprocessing?path_to_dir=/mnt/data/test_images/20210519_57&clientid=3

@app.route('/daycamprocessing') # Ждем запрос и формируем url из полученных параметров
def query():
    url = f'{faceid_config_url}:{faceid_config_port}/'
    path_to_dir = request.args.get('path_to_dir')
    clientid = request.args.get('clientid')
    url += f'daycamprocessing?path_to_dir={path_to_dir}&clientid={clientid}'
    logger.debug(f'url is {url}')

    manager = multiprocessing.Manager()
    check_dict = manager.dict()
    create_processes = Process(target=get_to_faceid, args=(number_of_attempts, url, check_dict))  # Создаем процесс,\
    # который будет отправлять гет запрос к FaceID и возвращать статус ответа
    create_processes.daemon = True

    # Запускаем сабпроцессы в фоне, которые будет отдавать статус работы сервисов ocr_lp, faceid, atlas_scheduler
    ocr_status = status_service('ocr_lp')
    faceid_status = status_service('faceid')
    scheduler_status = status_service('atlas_schedule')

    # Если работают ocr_lp, atlas_scheduler - останавливаем; если faceid не работает - запускаем
    if ocr_status:
        stop_ocr = command_to_service(number_of_attempts, 'ocr_lp', 'stop')
        if not stop_ocr:
            check_dict['status_code'] = 429
            return Response(check_dict['status_code'])

    if scheduler_status:
        stop_scheduler = command_to_service(number_of_attempts, 'atlas_scheduler', 'stop')
        if not stop_scheduler:
            check_dict['status_code'] = 429
            return Response(check_dict['status_code'])

    if not faceid_status:
        start_faceid = command_to_service(number_of_attempts, 'faceid', 'start')
        if not start_faceid:
            check_dict['status_code'] = 429
            return Response(check_dict['status_code'])

    logger.info('Отправляем запрос к faceid')
    create_processes.start()

    while create_processes.is_alive():  # Ждём завершения процесса, который
        pass

    restart_faceid = command_to_service(number_of_attempts, 'faceid', 'restart')
    start_ocr = command_to_service(number_of_attempts, 'ocr_lp', 'start')
    start_sheduler = command_to_service(number_of_attempts, 'atlas_scheduler', 'start')

    if not restart_faceid or not start_ocr or not start_sheduler:
        check_dict['status_code'] = 429
        return Response(check_dict['status_code'])

    logger.info(f"status_code from faceid: {check_dict['status_code']}")
    return Response(check_dict['status_code'])

def status_service(name_service: str) -> bool:
    """
    Запрашивает статус службы и ожидает завершения
    :param name_service: имя службы
    :return: bool
    """
    exit_code = os.WEXITSTATUS(os.system(f'systemctl is-active --quiet {name_service}'))
    if exit_code == 0:
        logger.info(f'{name_service} status is active')
        return True
    else:
        logger.info(f'Error: exit code is: {exit_code}')
    return False

def command_to_service(number_of_attempts: int, name_service: str, cmd: str) -> bool:
    """
    Функция отвечает за запуск, остановку и рестарт службы, после чего ожидает завершения
    :param number_of_attempts: количество попыток
    :param name_service: имя службы
    :param cmd: команда на выполнение
    :return: bool
    """
    # subprocess.check_output отдает 0 если команда отработала и все остальное в любых других случаях
    exit_code = os.WEXITSTATUS(os.system(f'systemctl {cmd} {name_service}'))
    for i in range(1, number_of_attempts + 1):
        if exit_code == 0:
            logger.info(f'{name_service} has been {cmd}ed')
            return True
        else:
            logger.error(f'Error:{name_service} has not been {cmd}ed, exit code is: {exit_code}')
        logger.info(f'Количесвто попыток: {i}')
    return False

def get_to_faceid(number_of_attempts: int, url: str, check_dict: dict) -> dict:
    """
    Фунцкия описывает процесс запроса к faceid
    :param number_of_attempts: количество попыток отправки запроса
    :param check_dict: multiprocessing.Manager.dict()
    :param url: url к faceid
    :return: multiprocessing.Manager.dict()
    """
    for i in range(1, number_of_attempts + 1):
        try:
            logger.info(f'Отправляем запрос к faceid, количесвто попыток: {i}')
            response = requests.get(url, timeout=9999999)
            check_dict['status_code'] = response.status_code
            logger.info(f'Статус ответа: {response.status_code}')
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
    app.run(host='192.168.0.159', debug=True, port=8092)
