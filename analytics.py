"""
Пишет события пользователей в CSV — для подсчёта лидов (кто зашёл через
/start, кто какие категории/товары смотрел, кто дошёл до оформления).
Открывается любой программой для таблиц (Excel/Google Sheets) или через
pandas.read_csv().

Намеренно НЕ хранит ничего чувствительного — только telegram user_id,
публичный username (если есть у пользователя) и то, что он смотрел.
Никаких имён/телефонов/email тут нет и не должно появляться.
"""

import csv
import logging
import os
import threading
from datetime import datetime

from config import ANALYTICS_FILE

logger = logging.getLogger("analytics")
_lock = threading.Lock()
_HEADER = ["timestamp", "user_id", "username", "event", "details"]


def log_event(user_id: int, username: str, event: str, details: str = ""):
    is_new = not os.path.exists(ANALYTICS_FILE)
    try:
        with _lock:
            with open(ANALYTICS_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if is_new:
                    writer.writerow(_HEADER)
                writer.writerow(
                    [
                        datetime.now().isoformat(timespec="seconds"),
                        user_id,
                        username or "",
                        event,
                        details,
                    ]
                )
    except Exception:
        # Аналитика не должна ронять бота, даже если диск переполнен
        # или файл временно занят другим процессом.
        logger.exception("Не удалось записать событие аналитики")
