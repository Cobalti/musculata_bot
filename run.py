"""
run.py — фоновые задачи бота (продакшен, VPS). Управляется systemd
(musculata-bot.service).

ВАЖНО — АРХИТЕКТУРА ИЗМЕНИЛАСЬ (было polling, стало webhook):
Раньше этот процесс держал bot.infinity_polling() — бот сам постоянно
спрашивал Telegram "есть что-то новое?". Это создавало целый класс
проблем: 409 Conflict при случайном дубле процессов (это ловили на
bothost), "query is too old" при нехватке потоков под нагрузкой
(несколько одновременных пользователей — и колбэки не успевали
обработаться вовремя).

Теперь Telegram сам стучится к нам по HTTPS (см. wsgi.py, маршрут
/telegram-webhook) — это обрабатывает тот же gunicorn с несколькими
воркерами, что уже принимает вебхуки сайта Фёдора. Никакого отдельного
пула потоков для этого больше не нужно, и сама категория проблем
("кто-то не успел обработать вовремя из-за общей очереди") исчезает.

Этот процесс (run.py) отвечает только за:
    - диагностику сервера при старте (health_check)
    - периодическую фоновую проверку раз в 5 минут
    - периодическую сверку доступа в канал/чат Сообщества
    - ОДНОКРАТНУЮ регистрацию вебхука в Telegram при старте

main.py по-прежнему можно запускать напрямую (python3 main.py) для
локальной разработки — тогда сработает polling-режим (см. main.py,
блок if __name__ == "__main__" — он не изменился, это удобно для
разработки без публичного HTTPS-адреса под рукой).
"""

import logging
import re
import threading
import time

import main as bot_main
import order_access
from config import PUBLIC_WEBHOOK_URL, TELEGRAM_WEBHOOK_SECRET

logger = logging.getLogger("run")


def register_telegram_webhook():
    """
    Регистрирует webhook в Telegram — вызывается ОДИН РАЗ при старте
    этого процесса (не в каждом gunicorn-воркере, чтобы не дёргать
    Telegram API лишний раз при каждом рестарте/масштабировании).

    Если PUBLIC_WEBHOOK_URL не задан — явно предупреждаем в логе и не
    падаем: бот просто не будет получать сообщения, пока это не
    исправят, но остальной процесс (health check, сверка доступа)
    продолжит работать.
    """
    if not PUBLIC_WEBHOOK_URL:
        logger.warning(
            "PUBLIC_WEBHOOK_URL не задан в .env — вебхук Telegram НЕ зарегистрирован, "
            "бот не будет получать сообщения! Впиши https://bot.mashinabodystore.ru "
            "(без слэша на конце) и перезапусти."
        )
        return

    # Telegram разрешает в secret_token только A-Z/a-z/0-9/подчёркивание/
    # дефис — если секрет сгенерирован через "openssl rand -base64"
    # (а не -hex), там могут оказаться "+", "/", "=", которые Telegram
    # отклонит с неочевидной ошибкой "unallowed characters". Проверяем
    # сами и даём понятную подсказку, а не просто падаем с traceback.
    if TELEGRAM_WEBHOOK_SECRET and not re.fullmatch(r"[A-Za-z0-9_-]+", TELEGRAM_WEBHOOK_SECRET):
        logger.error(
            "TELEGRAM_WEBHOOK_SECRET содержит недопустимые символы (Telegram разрешает "
            "только буквы/цифры/_/- ) — вебхук НЕ зарегистрирован. Перегенерируй командой "
            "'openssl rand -hex 32' (не -base64) и впиши заново в .env."
        )
        return

    webhook_url = f"{PUBLIC_WEBHOOK_URL}/telegram-webhook"
    try:
        bot_main.bot.remove_webhook()
        time.sleep(1)  # Telegram иногда просит небольшую паузу между remove и set
        bot_main.bot.set_webhook(
            url=webhook_url,
            secret_token=TELEGRAM_WEBHOOK_SECRET or None,
        )
        logger.info("Webhook Telegram зарегистрирован: %s", webhook_url)
    except Exception:
        logger.exception("Не удалось зарегистрировать webhook Telegram")


def start_order_access_reconciliation(interval_seconds: int = 900):
    """
    Раз в interval_seconds (по умолчанию 15 минут) сверяет всех
    участников сообщества с реальным доступом в канал/чат Сообщества —
    выдаёт, где не хватает (например, разморозка), забирает, где
    просрочено или приостановлено. Активация членства выдаёт доступ
    сразу же отдельно (см. main.py, handle_successful_payment) — эта
    фоновая сверка подстраховывает на случай, если тот прямой вызов не
    сработал, плюс единственная точка, которая вообще обрабатывает
    истечение срока и заморозку/разморозку.
    """
    def _loop():
        while True:
            time.sleep(interval_seconds)
            try:
                order_access.reconcile_access(bot_main.bot)
            except Exception:
                logger.exception("Ошибка в фоновой сверке доступа Сообщества")

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    logger.info("Фоновая сверка доступа Сообщества запущена (каждые %s сек.)", interval_seconds)


def main():
    # Реальная диагностика сервера (не слепое "Бот запущен") — пишет в
    # консоль/лог только при смене статуса. Плюс фоновая проверка каждые
    # 5 минут, чтобы заметить деградацию (например, диск заполнился) уже
    # во время работы, а не только в момент старта процесса.
    bot_main.run_startup_healthcheck()
    bot_main.health_check.start_periodic_check(bot_main.BOT_TOKEN)

    register_telegram_webhook()
    start_order_access_reconciliation()

    # Больше нет бесконечного polling-цикла как "сердца" процесса (сами
    # сообщения обрабатывает gunicorn/wsgi.py) — но процесс всё равно
    # должен продолжать жить, иначе systemd посчитает его завершённым и
    # остановит фоновые потоки (health check, сверка доступа). Просто
    # спим — реальная работа идёт в daemon-потоках выше.
    logger.info("Фоновый процесс запущен и работает (webhook-режим, без polling).")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
