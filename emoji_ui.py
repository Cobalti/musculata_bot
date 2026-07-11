"""
emoji_ui.py — отправка сообщений с кастомными эмодзи (в тексте и на кнопках)
через прямые HTTP-запросы к Telegram Bot API.

ПОЧЕМУ НЕ ЧЕРЕЗ pyTelegramBotAPI:
Bot API 9.4 (вышел в феврале 2026) добавил в InlineKeyboardButton/
KeyboardButton поля `style` и `icon_custom_emoji_id`. На момент написания
этого модуля pyTelegramBotAPI (даже последняя версия 4.24.0) эти поля
в своих классах ещё не поддерживает — библиотека не успела обновиться.
Чтобы не ждать её обновления, кнопки с эмодзи/цветом собираются как
обычные Python-словари в формате, который Telegram ожидает по API,
и отправляются напрямую через requests, в обход объектной модели telebot.

Обычный bot.send_message()/bot.send_photo() из main.py продолжают
работать как прежде для всего остального — этот модуль нужен только
там, где явно требуется icon_custom_emoji_id или style на кнопке,
либо кастомный эмодзи в тексте (хотя для текста это можно сделать
и через обычный bot.send_message с parse_mode="HTML" — см. пример ниже).

ТРЕБОВАНИЕ TELEGRAM: кастомные эмодзи в сообщениях/кнопках работают,
только если у ВЛАДЕЛЬЦА бота есть подписка Telegram Premium.
Если подписки нет — Telegram просто проигнорирует тег/поле без ошибки,
но эмодзи не покажется.
"""

import os
import logging
import requests

logger = logging.getLogger("emoji_ui")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message_with_emoji(chat_id: int, html_text: str, reply_markup: dict | None = None) -> dict:
    """
    Отправляет сообщение с HTML-разметкой, где кастомные эмодзи заданы
    через тег <tg-emoji emoji-id="...">заглушка</tg-emoji>.

    Пример text:
        '⭐ Добро пожаловать!' заменить на:
        '<tg-emoji emoji-id="5368324170671202286">⭐</tg-emoji> Добро пожаловать!'

    reply_markup — обычный python-словарь вида {"inline_keyboard": [[{...}]]},
    собранный через build_emoji_button() ниже. Можно передать None.
    """
    payload = {
        "chat_id": chat_id,
        "text": html_text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    response = requests.post(f"{API_BASE}/sendMessage", json=payload, timeout=10)
    data = response.json()
    if not data.get("ok"):
        logger.error("sendMessage с эмодзи не удался: %s", data)
    return data


def edit_message_with_emoji(chat_id: int, message_id: int, html_text: str,
                              reply_markup: dict | None = None) -> dict:
    """
    Аналог send_message_with_emoji, но редактирует существующее сообщение.
    Нужен для переходов между экранами (категории → страница каталога →
    карточка товара) без "мигания" — сообщение остаётся тем же, меняется
    только его содержимое и клавиатура с эмодзи-кнопками.
    """
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": html_text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    response = requests.post(f"{API_BASE}/editMessageText", json=payload, timeout=10)
    data = response.json()
    if not data.get("ok"):
        # "message is not modified" — валидная ситуация (например, повторное
        # нажатие той же кнопки); не логируем как ошибку, чтобы не шуметь.
        desc = data.get("description", "")
        if "not modified" not in desc:
            logger.warning("editMessageText не удался: %s", data)
    return data


def send_photo_with_emoji(chat_id: int, photo_file, caption_html: str,
                            reply_markup: dict | None = None) -> dict:
    """
    Отправка фото с caption, в котором есть кастомные эмодзи, + клавиатура
    с эмодзи-кнопками. Используется в карточке товара.

    photo_file — открытый файловый объект (open(path, "rb")) либо file_id
    (строка), уже загруженная в Telegram.
    """
    data_fields = {
        "chat_id": chat_id,
        "caption": caption_html,
        "parse_mode": "HTML",
    }
    if reply_markup:
        import json as _json
        data_fields["reply_markup"] = _json.dumps(reply_markup)

    if isinstance(photo_file, str):
        # уже file_id — отправляем как обычное поле
        data_fields["photo"] = photo_file
        response = requests.post(f"{API_BASE}/sendPhoto", data=data_fields, timeout=10)
    else:
        # файловый объект — грузим как multipart
        response = requests.post(
            f"{API_BASE}/sendPhoto",
            data=data_fields,
            files={"photo": photo_file},
            timeout=30,
        )

    result = response.json()
    if not result.get("ok"):
        logger.error("sendPhoto с эмодзи не удался: %s", result)
    return result


def build_emoji_button(text: str, callback_data: str | None = None, url: str | None = None,
                        style: str | None = None, icon_custom_emoji_id: str | None = None) -> dict:
    """
    Собирает один inline-кнопку-словарь с поддержкой style/icon_custom_emoji_id.

    style: "primary" (синяя) | "success" (зелёная) | "danger" (красная) | None (обычная)
    icon_custom_emoji_id: ID эмодзи, полученный через get_emoji_ids.py

    Ровно один из callback_data/url должен быть указан — как в обычной
    InlineKeyboardButton.
    """
    button = {"text": text}
    if callback_data:
        button["callback_data"] = callback_data
    if url:
        button["url"] = url
    if style:
        button["style"] = style
    if icon_custom_emoji_id:
        button["icon_custom_emoji_id"] = icon_custom_emoji_id
    return button


def build_emoji_keyboard(rows: list[list[dict]]) -> dict:
    """
    rows — список строк кнопок, каждая строка — список словарей от build_emoji_button.
    Пример:
        build_emoji_keyboard([
            [build_emoji_button("Оплатить", callback_data="pay", style="success",
                                 icon_custom_emoji_id="123")],
        ])
    """
    return {"inline_keyboard": rows}
