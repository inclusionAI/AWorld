import os
import json
import logging
from logging.handlers import RotatingFileHandler

from aworld.utils.diagnostic_tools.configs import settings


def get_logger(name: str, log_file: str, level: int | str = logging.INFO):
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    formatter = logging.Formatter(log_format)
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=settings.diagnostic_log_file_max_bytes,
        backupCount=settings.diagnostic_log_file_backup_count,
        encoding='utf-8')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
    return logger


def convert_dict_2_str(data: dict) -> str:
    if not isinstance(data, dict):
        return str(data) if not data else ""
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        logging.warning(f'{e}')
        return f'{repr(e)}'
