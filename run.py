"""
run.py — точка входа для продакшена (bothost/VPS).

main.py по-прежнему можно запускать отдельно для локальной разработки
без вебхуков (просто бот на polling). Но в проде нужны ОБА процесса
одновременно, живущие в одной программе, чтобы webhooks.py мог слать
сообщения через тот же экземпляр бота, что обрабатывает диалоги.

Схема:
    - main.py создаёт объект bot (TeleBot) и регистрирует обработчики
    - поток №1: bot.infinity_polling() — обычная работа бота
    - поток №2 (главный): Flask из webhooks.py — слушает вебхуки от сайта

На bothost в поле "Главный файл" нужно указать run.py вместо main.py.
"""

import threading
import logging
import os

import main as bot_main
import webhooks

logger = logging.getLogger("run")

WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "5000"))


def start_bot_polling():
    logger.info("Запуск Telegram-бота (polling)...")
    bot_main.bot.infinity_polling(skip_pending=True)


def main():
    # Передаём вебхукам тот же экземпляр бота, чтобы уведомления
    # (missing-items, payment-success) уходили через рабочий бот.
    webhooks.set_bot_instance(bot_main.bot)

    polling_thread = threading.Thread(target=start_bot_polling, daemon=True)
    polling_thread.start()

    logger.info("Запуск веб-сервера вебхуков на порту %s...", WEBHOOK_PORT)
    webhooks.app.run(host="0.0.0.0", port=WEBHOOK_PORT)


if __name__ == "__main__":
    main()
