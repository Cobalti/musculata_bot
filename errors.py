"""
safe_handler — оборачивает каждый обработчик сообщений/колбэков так,
чтобы:
  1) исключение внутри одного обработчика не роняло весь процесс бота
     и не зависало молча — оно логируется и уходит уведомлением админу;
  2) один пользователь не мог заспамить бота — работает rate limiting;
  3) ни один раздел бота не был доступен, пока пользователь не нажал
     "✅ Принимаю" под уведомлением о персональных данных (жёсткий вариант
     согласия — не просто информирование, а реальный gate);
  4) если сервер РЕАЛЬНО не в порядке (диск забит, БД недоступна —
     см. health_check.is_operational) — пользователь получает понятное
     сообщение о технических работах ДО того, как попытается что-то
     сделать, вместо тихого сбоя или необъяснимой ошибки. Админ отдельно
     ничего не получает лично — он смотрит статус процесса в панели
     bothost, дублировать это личным сообщением не нужно.

Использование (декоратор применяется НИЖЕ telebot-декоратора, то есть
ближе к самой функции):

    @bot.message_handler(commands=["start"])
    @safe_handler(bot, require_consent=False)   # /start доступен всегда
    def start_message(message):
        ...

    @bot.message_handler(func=lambda m: m.text == keyboards.BTN_CATALOG)
    @safe_handler(bot)                           # по умолчанию требует согласия
    def handle_catalog_button(message):
        ...
"""

import functools
import logging

import consent_db
import health_check
from config import BOT_TOKEN
from notifier import notify_admin
from rate_limit import is_rate_limited

logger = logging.getLogger("handlers")


def _chat_id_of(update):
    """Достаёт chat_id и из Message, и из CallbackQuery."""
    chat = getattr(update, "chat", None)
    if chat:
        return chat.id
    msg = getattr(update, "message", None)
    if msg:
        return msg.chat.id
    return None


def _reply(bot, update, text: str):
    """Отправляет текст в тот же чат, откуда пришло сообщение/колбэк."""
    chat_id = _chat_id_of(update)
    if chat_id is not None:
        try:
            bot.send_message(chat_id, text)
        except Exception:
            logger.exception("Не удалось отправить сообщение пользователю")

    # Если это нажатие инлайн-кнопки — нужно закрыть "часики" в интерфейсе
    if hasattr(update, "id") and hasattr(update, "data"):
        try:
            bot.answer_callback_query(update.id)
        except Exception:
            pass


def _remind_consent(bot, update):
    _reply(
        bot, update,
        "Чтобы пользоваться ботом, сначала нажми «Принимаю» под "
        "условиями обработки персональных данных выше ⬆️",
    )


def _notify_technical_issue(bot, update):
    _reply(
        bot, update,
        "🛠 Бот сейчас не работает — проблемы с сервером или связью. "
        "Пожалуйста, попробуй снова через несколько минут.",
    )


def safe_handler(bot, require_consent: bool = True):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(update, *args, **kwargs):
            user = getattr(update, "from_user", None)
            user_id = user.id if user else None

            # Проверяем РЕАЛЬНОЕ состояние сервера раньше всего остального —
            # если нет связи с Telegram, диск забит или БД недоступна, нет
            # смысла даже проверять согласие или rate limit. Проверка
            # дешёвая (кэш на 30 секунд, см. health_check.py) — сеть
            # дёргается не на каждое сообщение, а раз в CACHE_TTL_SECONDS.
            if not health_check.is_operational(BOT_TOKEN):
                logger.warning(
                    "Действие отклонено — проблемы с сервером/связью: %s (user_id=%s)",
                    health_check.get_problems(), user_id,
                )
                _notify_technical_issue(bot, update)
                return

            if user_id is not None and is_rate_limited(user_id):
                logger.warning("Rate limit сработал для user_id=%s", user_id)
                return

            if require_consent and user_id is not None and not consent_db.has_consent(user_id):
                _remind_consent(bot, update)
                return

            try:
                return func(update, *args, **kwargs)
            except Exception as e:
                logger.exception("Ошибка в обработчике %s", func.__name__)
                notify_admin(
                    bot,
                    f"⚠️ Ошибка в обработчике `{func.__name__}`\n"
                    f"user_id: {user_id}\n"
                    f"{type(e).__name__}: {e}",
                )

        return wrapper

    return decorator
