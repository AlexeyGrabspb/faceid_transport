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

root = logging.getLogger()
root.setLevel(logging.DEBUG)

logger = logging.getLogger('faceid_transport')
logger.setLevel(logging.DEBUG)
handler = RotatingFileHandler('test/faceid_transport.log', maxBytes=20000, backupCount=2)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)


app = Flask(__name__)   # Создаем Flask приложение
"""__file__ выдает нам путь до файла,abspath редактирует путь под систему откуда запускается файл, dirname получаем \
путь до папки"""
root_path = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(root_path, 'test/faceid.cfg') # Подкючаем конфиг к нашему приложению
config = ConfigParser() # Парсим конфиг faceid.cfg
config.read(config_path)

faceid_config_url = config.get('FACEID', 'url') # Достаем из конфига url, port
faceid_port = config.get('FACEID', 'port')


# http://192.168.10.98:8091/daycamprocessing?path_to_dir=/mnt/data/test_images/20210519_57&clientid=3

@app.route('/daycamprocessing') # Ждем запрос и формируем url из полученных параметров
def query():
    url = f'{faceid_config_url}:{faceid_port}/'
    path_to_dir = request.args.get('path_to_dir')
    clientid = request.args.get('clientid')
    url += f'daycamprocessing?path_to_dir={path_to_dir}&clientid={clientid}'
    logger.debug(f'url is {url}')

    # Запускаем сабпроцессы в фоне, которые будет отдавать статус работы сервисов ocr_lp, faceid, atlas_scheduler
    ocr_status = subprocess.call(["systemctl", "is-active", "--quiet", "ocr_lp"])
    faceid_status = subprocess.call(["systemctl", "is-active", "--quiet", "faceid"])
    scheduler_status = subprocess.call(["systemctl", "is-active", "--quiet", "atlas_scheduler"])
    logger.debug(f'ocr_lp status is {ocr_status}, faceid status is {faceid_status}, scheduler status is {scheduler_status}')

    # Если работают ocr_lp, atlas_scheduler - останавливаем
    if ocr_status == 0:
        start_service('ocr_lp', 'stop')

    if scheduler_status == 0:
        start_service('atlas_scheduler', 'stop')

    if faceid_status != 0:
        logger.debug('Служба faceid неактивна, запускаем faceid')
        start_service('faceid', 'start')

    time.sleep(5)
    manager = multiprocessing.Manager()
    check_dict = manager.dict()
    create_processes = Process(target=start_faceid, args=(url, check_dict))  # Создаем процесс, который \
    # будет отправлять гет запрос к FaceID и возвращать статус ответа
    create_processes.daemon = True
    create_processes.start()

    while create_processes.is_alive():  # Ждём завершения процесса, который
        pass
    logger.debug('Запрос отправлен')

    start_service('faceid', 'restart')
    start_service('ocr_lp', 'start')
    start_service('atlas_scheduler', 'start')

    logger.debug(f"status_code from faceid: {check_dict['status_code']}")
    return Response(check_dict['status_code'])


def start_service(name_service: str, cmd: str) -> None:
    """
    Функция отвечает за операции со службами
    :param name_service: имя службы
    :param cmd: команда на выполнение
    :return: None
    """
    os.system(f'sudo service {name_service} {cmd}')
    logger.debug(f'{name_service} has been {cmd}ed')


def start_faceid(url: str, check_dict: dict) -> dict:
    """
    Фунцкия описывает процесс запроса к faceid
    :param check_dict: multiprocessing.Manager.dict()
    :param url: url к faceid
    :return: multiprocessing.Manager.dict()
    """
    logger.debug('Отправляем запрос')
    response = requests.get(url, timeout=9999999)
    check_dict['status_code'] = response.status_code
    return check_dict


if __name__ == '__main__':
    app.run(host='192.168.0.159', debug=True, port=8092)
