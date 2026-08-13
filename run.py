"""
run.py — точка входа ТОЛЬКО для Telegram-поллинга (продакшен, VPS).

ВАЖНО: раньше этот файл ЕЩЁ И поднимал Flask (webhooks.py) в том же
процессе. Теперь веб-часть вынесена в wsgi.py и запускается отдельно
через gunicorn (см. musculata-webhooks.service) — это позволяет
gunicorn держать несколько worker-процессов для надёжности, не рискуя
плодить параллельные Telegram-поллинги (см. wsgi.py — там подробно
объяснено, почему это разделение принципиально).

Этот файл (run.py) — управляется systemd (musculata-bot.service) и
отвечает только за:
    - диагностику сервера при старте (health_check)
    - сам Telegram-поллинг с авто-восстановлением после сбоев

main.py по-прежнему можно запускать отдельно для локальной разработки
(просто бот на polling, без вебхуков вообще).
"""

import logging
import threading
import time

import main as bot_main
import order_access

logger = logging.getLogger("run")


def start_order_access_reconciliation(interval_seconds: int = 900):
    """
    Раз в interval_seconds (по умолчанию 15 минут) сверяет всех
    участников сообщества с реальным доступом в канал/чат Сообщества — выдаёт,
    где не хватает (например, разморозка), забирает, где просрочено
    или приостановлено. Активация членства выдаёт доступ сразу же отдельно
    (см. webhooks.py) — эта фоновая сверка подстраховывает на случай,
    если тот прямой вызов не сработал, плюс единственная точка, которая
    вообще обрабатывает истечение срока и заморозку/разморозку.
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
    # Реальная диагностика сервера (не слепое "Бот запущен") — пишет в
    # консоль/лог только при смене статуса. Ничего не шлёт в Telegram —
    # админ смотрит статус процесса в панели bothost/VPS. Плюс фоновая
    # проверка каждые 5 минут, чтобы заметить деградацию (например, диск
    # заполнился) уже во время работы, а не только в момент старта процесса.
    bot_main.run_startup_healthcheck()
    bot_main.health_check.start_periodic_check(bot_main.BOT_TOKEN)
    start_order_access_reconciliation()

    # Поллинг — в основном потоке (раньше был в фоновом, потому что
    # Flask занимал главный поток; теперь Flask здесь вообще нет, так что
    # можно просто держать поллинг как основной цикл процесса).
    start_bot_polling()


if __name__ == "__main__":
    main()

