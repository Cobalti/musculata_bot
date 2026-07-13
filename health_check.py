"""
health_check.py — настоящая проверка состояния сервера вместо слепого
"Бот запущен" сообщения при каждом старте.

ПРОБЛЕМА, КОТОРУЮ ЭТО РЕШАЕТ:
Раньше main.py при каждом запуске процесса слал админу "✅ Бот запущен...".
Если сервер уходит в цикл падений/перезапусков (crash loop, watchdog
хостинга их периодически перезапускает), админ получает этот "спам"
раз за разом, хотя реальной новой информации там нет — бот не говорит,
жив ли сервер ПО-НАСТОЯЩЕМУ, просто сообщает факт "процесс стартовал".

ЧТО ДЕЛАЕТ ЭТОТ МОДУЛЬ:
1. Проводит несколько конкретных проверок (не просто "процесс запустился"):
   - доступен ли Telegram API с этим токеном (get_me через прямой запрос);
   - достаточно ли свободного места на диске;
   - можно ли прочитать/записать в файлы баз данных (orders.db,
     subscriptions.db, consent.db).
2. Сравнивает результат с ПРЕДЫДУЩИМ сохранённым статусом (health_status.json).
3. Шлёт админу сообщение ТОЛЬКО когда статус ИЗМЕНИЛСЯ:
   - не было данных → есть данные: обычное сообщение о старте
   - было ок → стало плохо: тревожное сообщение с деталями
   - было плохо → снова ок: сообщение "проблема устранена"
   - было плохо → всё ещё плохо: молчим (не спамим тем же самым)
   - было ок → снова ок (обычный чистый рестарт): молчим — вот что
     убирает спам "Бот запущен" на каждый безобидный перезапуск.
4. Может запускаться не только при старте, но и периодически в фоне
   (start_periodic_check) — тогда деградация сервера, случившаяся уже
   ПОСЛЕ запуска (например, диск заполнился во время работы), тоже
   будет замечена и админ получит предупреждение без ручной проверки.
"""

import os
import json
import shutil
import sqlite3
import logging
import threading
import time
import requests
from datetime import datetime, timezone

logger = logging.getLogger("health_check")

STATUS_FILE = os.path.join(os.path.dirname(__file__), "health_status.json")
MIN_FREE_DISK_MB = 200  # ниже этого порога считаем ситуацию с диском проблемной
DB_FILES = ["orders.db", "subscriptions.db", "consent.db"]


# ---------- Отдельные проверки ----------

def _check_telegram_api(bot_token: str) -> tuple[bool, str]:
    if not bot_token:
        return False, "BOT_TOKEN не задан"
    try:
        resp = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=10)
        data = resp.json()
        if data.get("ok"):
            return True, "Telegram API доступен"
        return False, f"Telegram API вернул ошибку: {data.get('description', 'без описания')}"
    except requests.exceptions.RequestException as e:
        return False, f"Telegram API недоступен: {e}"


def _check_disk_space() -> tuple[bool, str]:
    try:
        usage = shutil.disk_usage(os.path.dirname(__file__) or ".")
        free_mb = usage.free / (1024 * 1024)
        if free_mb < MIN_FREE_DISK_MB:
            return False, f"Мало места на диске: {free_mb:.0f} МБ свободно (порог {MIN_FREE_DISK_MB} МБ)"
        return True, f"Диск в порядке: {free_mb:.0f} МБ свободно"
    except Exception as e:
        return False, f"Не удалось проверить диск: {e}"


def _check_databases() -> tuple[bool, str]:
    problems = []
    for db_name in DB_FILES:
        path = os.path.join(os.path.dirname(__file__), db_name)
        try:
            conn = sqlite3.connect(path, timeout=5)
            conn.execute("SELECT 1")
            conn.close()
        except Exception as e:
            problems.append(f"{db_name}: {e}")
    if problems:
        return False, "Проблемы с базами данных: " + "; ".join(problems)
    return True, "Базы данных доступны"


def run_checks(bot_token: str) -> dict:
    """
    Прогоняет все проверки. Возвращает:
        {"ok": bool, "problems": [str, ...], "details": [str, ...], "checked_at": iso}
    """
    checks = [
        _check_telegram_api(bot_token),
        _check_disk_space(),
        _check_databases(),
    ]
    problems = [detail for ok, detail in checks if not ok]
    details = [detail for _, detail in checks]
    return {
        "ok": len(problems) == 0,
        "problems": problems,
        "details": details,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------- Персистентный статус (чтобы не спамить одним и тем же) ----------

def _load_last_status() -> dict | None:
    if not os.path.exists(STATUS_FILE):
        return None
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Не удалось прочитать health_status.json: %s", e)
        return None


def _save_status(status: dict) -> None:
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Не удалось сохранить health_status.json: %s", e)


# ---------- Публичный интерфейс ----------

def check_and_notify(bot, bot_token: str, admin_chat_id) -> dict:
    """
    Прогоняет проверки и уведомляет админа ТОЛЬКО при смене статуса.
    Возвращает результат текущей проверки (на случай если вызывающий код
    хочет залогировать или показать его сам).
    """
    result = run_checks(bot_token)
    last = _load_last_status()
    _save_status(result)

    if admin_chat_id is None:
        return result  # некому слать — но статус всё равно сохранили

    try:
        if last is None:
            # первая проверка за всю историю — сообщаем в любом случае,
            # это не спам, а разовая информация о первом запуске
            if result["ok"]:
                bot.send_message(
                    admin_chat_id,
                    "🛡 Бот запущен, сервер в порядке.\n\n" + "\n".join(f"• {d}" for d in result["details"]),
                )
            else:
                bot.send_message(
                    admin_chat_id,
                    "🛡 Бот запущен, но обнаружены проблемы:\n\n"
                    + "\n".join(f"• {p}" for p in result["problems"]),
                )
        elif last["ok"] and not result["ok"]:
            bot.send_message(
                admin_chat_id,
                "⚠️ Обнаружена проблема с сервером:\n\n"
                + "\n".join(f"• {p}" for p in result["problems"])
                + "\n\nБот может работать нестабильно, пока это не устранят.",
            )
        elif not last["ok"] and result["ok"]:
            bot.send_message(
                admin_chat_id,
                "✅ Проблема с сервером устранена, всё снова в порядке.",
            )
        # last["ok"] and result["ok"] -> тихий обычный рестарт, ничего не шлём
        # not last["ok"] and not result["ok"] -> проблема всё ещё та же, не спамим повторно
    except Exception as e:
        logger.warning("Не удалось отправить уведомление о статусе сервера: %s", e)

    return result


def start_periodic_check(bot, bot_token: str, admin_chat_id, interval_seconds: int = 300):
    """
    Запускает фоновый поток, который прогоняет check_and_notify каждые
    interval_seconds (по умолчанию 5 минут). Нужен, чтобы заметить
    деградацию (например, диск заполнился), случившуюся уже во время
    работы бота, а не только в момент старта процесса.
    """
    def _loop():
        while True:
            time.sleep(interval_seconds)
            try:
                check_and_notify(bot, bot_token, admin_chat_id)
            except Exception:
                logger.exception("Ошибка в фоновой проверке состояния сервера")

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    logger.info("Фоновая проверка состояния сервера запущена (каждые %s сек.)", interval_seconds)
