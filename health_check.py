"""
health_check.py — настоящая проверка состояния сервера вместо слепого
"Бот запущен" сообщения при каждом старте.

ПРОБЛЕМА, КОТОРУЮ ЭТО РЕШАЕТ:
Раньше main.py при каждом запуске процесса слал админу в Telegram
"✅ Бот запущен...". Если сервер уходит в цикл падений/перезапусков
(crash loop, watchdog хостинга их периодически перезапускает), админ
получал этот "спам" раз за разом в личку, хотя реальной новой информации
там не было.

ВАЖНО: этот модуль НИЧЕГО не отправляет в Telegram. Админ и так видит
статус процесса (жив/упал/перезапускается) в панели bothost — дублировать
это личным сообщением от бота не нужно. Вместо этого модуль просто
ПЕЧАТАЕТ И ЛОГИРУЕТ результат проверки в консоль/лог-файл — то, что
и так собирает панель хостинга.

ЧТО ДЕЛАЕТ ЭТОТ МОДУЛЬ:
1. Проводит несколько конкретных проверок (не просто "процесс запустился"):
   - доступен ли Telegram API с этим токеном (get_me через прямой запрос);
   - достаточно ли свободного места на диске;
   - можно ли прочитать/записать в файлы баз данных (orders.db,
     subscriptions.db, consent.db).
2. Сравнивает результат с ПРЕДЫДУЩИМ сохранённым статусом (health_status.json),
   чтобы в логе тоже не дублировать одну и ту же запись при каждом
   безобидном перезапуске — пишет заметную строку только при смене статуса
   (ок→проблема, проблема→ок), а при "всё то же самое" — короткую тихую
   строку уровня DEBUG (видно, если понадобится, но не засоряет обычный лог).
3. Может запускаться не только при старте, но и периодически в фоне
   (start_periodic_check) — тогда деградация сервера, случившаяся уже
   ПОСЛЕ запуска (например, диск заполнился во время работы), тоже
   попадёт в лог без ручной проверки.
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


# ---------- Быстрая проверка "работает ли бот ПРЯМО СЕЙЧАС" (для пользователей) ----------
# Отдельно от полной диагностики выше (та шумит логами при старте/по расписанию).
# Эта функция вызывается ПЕРЕД КАЖДЫМ действием пользователя в боте
# (см. errors.safe_handler) — поэтому кэшируется, чтобы не дёргать сеть/диск/БД
# на каждое сообщение от каждого пользователя.
#
# Включает ВСЕ три проверки (Telegram API, диск, БД) — если есть проблемы
# со связью (потеря соединения, недоступность Telegram), пользователь
# должен узнать об этом настолько, насколько это в принципе возможно:
# если связь полностью отсутствует, сообщение и правда не дойдёт, но при
# частичной деградации (сеть моргает, отвечает через раз) следующая
# успешная попытка достучаться донесёт до юзера честное "бот сейчас не
# работает" вместо того, чтобы бот вслепую пытался выполнить действие.

CACHE_TTL_SECONDS = 30
_cache: dict = {"result": None, "checked_at": 0.0}


def is_operational(bot_token: str, force: bool = False) -> bool:
    """
    True, если бот прямо сейчас может нормально обслуживать пользователей
    (связь с Telegram, диск, БД в порядке). Используется в safe_handler —
    если False, пользователь получает сообщение "бот сейчас не работает"
    вместо попытки выполнить действие.
    """
    now = time.time()
    if force or _cache["result"] is None or (now - _cache["checked_at"] > CACHE_TTL_SECONDS):
        _cache["result"] = run_checks(bot_token)
        _cache["checked_at"] = now
    return _cache["result"]["ok"]


def get_problems() -> list[str]:
    """Список текущих проблем (для лога админу) — вызывать после is_operational(), кэш уже свежий."""
    if _cache["result"] is None:
        return []
    return _cache["result"]["problems"]


# ---------- Персистентный статус (чтобы не дублировать одну и ту же запись в логе) ----------

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

def check_and_log(bot_token: str) -> dict:
    """
    Прогоняет проверки и пишет результат в консоль/лог. Заметную строку
    (print + logger.warning/info) выводит только при смене статуса —
    ок→проблема, проблема→ок, или при самой первой проверке за всю
    историю. Если статус не изменился — тихая DEBUG-запись, не мешающая
    обычному логу, но доступная при необходимости.

    Ничего не отправляет в Telegram — админ смотрит статус процесса
    в панели bothost, дублировать это личным сообщением от бота не нужно.

    Возвращает результат текущей проверки (на случай если вызывающий код
    хочет использовать его сам).
    """
    result = run_checks(bot_token)
    last = _load_last_status()
    _save_status(result)

    if last is None:
        # первая проверка за всю историю — печатаем в любом случае,
        # это не спам, а разовая информация о первом запуске
        if result["ok"]:
            logger.info("Проверка сервера при запуске: всё в порядке")
            print("🛡 Проверка сервера при запуске: всё в порядке")
        else:
            logger.warning("Проверка сервера при запуске обнаружила проблемы: %s", result["problems"])
            print("⚠️ Проверка сервера при запуске обнаружила проблемы:")
        for detail in result["details"]:
            print(f"   • {detail}")

    elif last["ok"] and not result["ok"]:
        logger.warning("Сервер перешёл в состояние проблемы: %s", result["problems"])
        print("⚠️ Обнаружена проблема с сервером:")
        for p in result["problems"]:
            print(f"   • {p}")

    elif not last["ok"] and result["ok"]:
        logger.info("Проблема с сервером устранена, всё снова в порядке")
        print("✅ Проблема с сервером устранена, всё снова в порядке.")

    else:
        # статус не изменился — не спамим тем же самым, только тихая
        # DEBUG-запись на случай, если понадобится покопаться в логах
        logger.debug("Проверка сервера: статус не изменился (ok=%s)", result["ok"])

    return result


def start_periodic_check(bot_token: str, interval_seconds: int = 300):
    """
    Запускает фоновый поток, который прогоняет check_and_log каждые
    interval_seconds (по умолчанию 5 минут). Нужен, чтобы заметить
    деградацию (например, диск заполнился), случившуюся уже во время
    работы бота, а не только в момент старта процесса — попадёт в лог
    без ручной проверки.
    """
    def _loop():
        while True:
            time.sleep(interval_seconds)
            try:
                check_and_log(bot_token)
            except Exception:
                logger.exception("Ошибка в фоновой проверке состояния сервера")

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    logger.info("Фоновая проверка состояния сервера запущена (каждые %s сек.)", interval_seconds)
