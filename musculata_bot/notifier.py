"""
Уведомление администратора (тебя) в Telegram, если в боте что-то сломалось.
Best-effort: если ADMIN_CHAT_ID не задан, либо сама отправка не удалась
(например, сеть недоступна) — молча логируем и не роняем бота из-за этого.
"""

import logging
from config import ADMIN_CHAT_ID

logger = logging.getLogger("notifier")


def notify_admin(bot, text: str):
    if not ADMIN_CHAT_ID:
        return
    try:
        # Telegram ограничивает длину сообщения ~4096 символами
        bot.send_message(ADMIN_CHAT_ID, text[:4000])
    except Exception:
        logger.exception("Не удалось отправить уведомление администратору")
