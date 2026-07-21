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
import time

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
    """
    ВАЖНО: infinity_polling() у pyTelegramBotAPI НЕ восстанавливается сам
    после необработанного исключения — если что-то пошло не так (сетевой
    сбой, 409 Conflict от Telegram и т.п.), поток просто умирает молча,
    и бот перестаёт отвечать на любые сообщения НАВСЕГДА, пока кто-то
    вручную не перезапустит процесс. Раньше именно так и было — отсюда
    "бот вообще не отвечает" после случайного сбоя.

    Оборачиваем в свой цикл с повторными попытками и растущей паузой —
    если сбой временный (например, 409 из-за краткого наложения старого
    и нового инстанса при рестарте хостингом), бот сам восстановится
    через несколько попыток, как только конфликт исчезнет.
    """
    attempt = 0
    while True:
        try:
            logger.info("Запуск Telegram-бота (polling)...")
            bot_main.bot.infinity_polling(skip_pending=True)
            # infinity_polling обычно не возвращается сам (крутится вечно).
            # Если всё же вернулся без исключения — тоже ненормально,
            # перезапускаем цикл, а не оставляем поток мёртвым.
            logger.warning("infinity_polling() завершился без исключения — перезапускаю.")
            attempt = 0
        except Exception as e:
            attempt += 1
            wait_seconds = min(60, 5 * attempt)  # растущая пауза, потолок 60 сек
            is_conflict = "409" in str(e) or "Conflict" in str(e)

            if is_conflict:
                logger.error(
                    "Polling упал с 409 Conflict — похоже, где-то ещё запущен "
                    "ВТОРОЙ инстанс бота с этим же токеном. Попытка №%s, "
                    "повтор через %s сек.",
                    attempt, wait_seconds,
                )
            else:
                logger.exception(
                    "Polling упал с ошибкой. Попытка №%s, повтор через %s сек.",
                    attempt, wait_seconds,
                )

            # Не спамим админа на каждую попытку — только на первую и потом
            # раз в 5 попыток, чтобы было видно, что проблема не разовая,
            # но не заваливало личку сообщениями каждые несколько секунд.
            if attempt == 1 or attempt % 5 == 0:
                try:
                    reason = (
                        "Похоже на конфликт с другим инстансом бота (409 Conflict) — "
                        "проверь, не запущена ли где-то ещё одна копия с этим же токеном."
                        if is_conflict else ""
                    )
                    bot_main.notify_admin(
                        bot_main.bot,
                        f"🔥 Бот перестал отвечать на сообщения (polling упал), "
                        f"попытка восстановления №{attempt}.\n{reason}\n"
                        f"{type(e).__name__}: {e}",
                    )
                except Exception:
                    logger.warning("Не удалось уведомить админа об упавшем polling")

            time.sleep(wait_seconds)


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
