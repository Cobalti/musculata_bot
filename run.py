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

# Многие хостинги (в том числе bothost) назначают порт динамически через
# свою переменную окружения PORT — если домен привязан к прокси, который
# форвардит именно на этот порт, а не на наш WEBHOOK_PORT, приложение
# внутри контейнера будет слушать не тот порт, куда стучится прокси, и
# снаружи всё будет выглядеть как 404, хотя Flask внутри стартовал нормально.
# Проверяем сначала платформенный PORT, потом свой WEBHOOK_PORT, и только
# затем дефолт 5000.
WEBHOOK_PORT = int(os.environ.get("PORT") or os.environ.get("WEBHOOK_PORT", "5000"))


def start_bot_polling():
    logger.info("Запуск Telegram-бота (polling)...")
    bot_main.bot.infinity_polling(skip_pending=True)


def main():
    # Передаём вебхукам тот же экземпляр бота, чтобы уведомления
    # (missing-items, payment-success) уходили через рабочий бот.
    webhooks.set_bot_instance(bot_main.bot)

    # Реальная диагностика сервера (не слепое "Бот запущен") — пишет в
    # консоль/лог только при смене статуса. Ничего не шлёт в Telegram —
    # админ смотрит статус процесса в панели bothost. Плюс фоновая проверка
    # каждые 5 минут, чтобы заметить деградацию (например, диск заполнился)
    # уже во время работы, а не только в момент старта процесса.
    bot_main.run_startup_healthcheck()
    bot_main.health_check.start_periodic_check(bot_main.BOT_TOKEN)

    polling_thread = threading.Thread(target=start_bot_polling, daemon=True)
    polling_thread.start()

    port_source = "PORT" if os.environ.get("PORT") else ("WEBHOOK_PORT" if os.environ.get("WEBHOOK_PORT") else "дефолт")
    logger.info(
        "Запуск веб-сервера вебхуков на порту %s (источник: %s)...",
        WEBHOOK_PORT, port_source,
    )
    webhooks.app.run(host="0.0.0.0", port=WEBHOOK_PORT)


if __name__ == "__main__":
    main()
