"""
Настраивает логирование при импорте. Импортировать этот модуль нужно
самым первым в main.py — до создания бота и любых других импортов,
которые могут что-то логировать.

Логи пишутся:
- в консоль (как и раньше, для удобства при разработке в PyCharm)
- в файл logs/bot.log — с автоматической ротацией, чтобы не расти
  бесконечно на диске: как только файл достигает LOG_MAX_BYTES,
  он переименовывается в bot.log.1, а самый старый (bot.log.5)
  удаляется автоматически. Итого на диске всегда максимум
  ~(LOG_BACKUP_COUNT + 1) * LOG_MAX_BYTES логов.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from config import LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT, LOG_DIR

os.makedirs(LOG_DIR, exist_ok=True)

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
)
_file_handler.setFormatter(_formatter)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(_file_handler)
root_logger.addHandler(_console_handler)

# pyTelegramBotAPI логирует штатное завершение polling'а (в том числе
# обычную остановку через Ctrl+C) на уровне ERROR — это не сигнал
# реальной проблемы, поэтому приглушаем именно этот логгер.
logging.getLogger("TeleBot").setLevel(logging.CRITICAL)
