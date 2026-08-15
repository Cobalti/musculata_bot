"""
wsgi.py — точка входа для gunicorn. Обрабатывает ВСЁ входящее по HTTP:
    - вебхуки от сайта Фёдора (missing-items, payment-success)
    - вебхук от Telegram (входящие сообщения/кнопки — webhook-режим)

ПОЧЕМУ ТЕПЕРЬ БЕЗОПАСНО ИМПОРТИРОВАТЬ main.py ЦЕЛИКОМ (раньше было
специально нельзя — см. историю этого файла): раньше бот работал через
polling (bot.infinity_polling() в run.py), и если бы несколько
gunicorn-воркеров каждый по себе завели свой экземпляр TeleBot и начали
опрашивать Telegram — это дало бы 409 Conflict (несколько "конкурентов"
дерутся за один токен). Поэтому раньше здесь жил отдельный "тонкий" бот
только для исходящих уведомлений, без единого обработчика.

В webhook-режиме поллинга просто нет — Telegram сам присылает апдейт
по HTTP, gunicorn обрабатывает его любым свободным воркером, и
конфликтовать нечему (это обычный запрос-ответ, не долгоживущий опрос).
Поэтому теперь можно спокойно импортировать main.py целиком и
использовать ОДИН И ТОТ ЖЕ полный bot и для обработки входящих
сообщений, и для исходящих уведомлений из вебхуков сайта.

Запуск (см. musculata-webhooks.service):
    gunicorn --bind 127.0.0.1:5000 --workers 2 wsgi:app
"""

import logging
import logging_setup  # noqa: F401 — настраивает логирование в файл при импорте

from flask import request, jsonify
from telebot import types as telebot_types

import main as bot_main
import webhooks
from config import TELEGRAM_WEBHOOK_SECRET

logger = logging.getLogger("wsgi")

# Один и тот же полный bot (с зарегистрированными обработчиками) — и для
# входящих Telegram-апдейтов, и для исходящих уведомлений из вебхуков
# сайта. Раздельный "тонкий" notifier_bot больше не нужен (см. докстринг
# выше — в webhook-режиме риска конфликта, ради которого он был придуман,
# просто не существует).
webhooks.set_bot_instance(bot_main.bot)

app = webhooks.app


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    """
    Точка приёма апдейтов от Telegram. Регистрируется один раз через
    bot.set_webhook() (см. run.py, register_telegram_webhook) — Telegram
    сам стучится сюда при любом новом сообщении/нажатии кнопки.

    Защита — секретный заголовок X-Telegram-Bot-Api-Secret-Token,
    который Telegram обязуется присылать, если мы указали secret_token
    при регистрации вебхука (см. run.py). Без совпадения — отклоняем,
    чтобы никто посторонний не мог слать поддельные апдейты на этот URL.
    """
    if TELEGRAM_WEBHOOK_SECRET:
        incoming_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if incoming_secret != TELEGRAM_WEBHOOK_SECRET:
            logger.warning("telegram-webhook: неверный секрет в заголовке")
            return jsonify({"error": "invalid secret"}), 401

    try:
        raw = request.get_data().decode("utf-8")
        update = telebot_types.Update.de_json(raw)
        bot_main.bot.process_new_updates([update])
    except Exception:
        logger.exception("Ошибка обработки апдейта от Telegram")
        # Всё равно отвечаем 200 — иначе Telegram будет повторять один и
        # тот же битый апдейт бесконечно. Ошибка уже залогирована.

    return jsonify({"status": "ok"}), 200
