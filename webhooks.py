"""
webhooks.py — принимает вебхуки от сайта mashinabodystore.ru.

Реализует ровно 2 эндпоинта, которые подтвердил Фёдор:
    POST /webhook/missing-items
    POST /webhook/payment-success

Оба защищены заголовком X-WP-Secret — сайт присылает его в каждом
запросе, мы сверяем со значением из .env (X_WP_SECRET). Пока Фёдор не
даст этот секрет, сравнение всегда будет проваливаться — это ожидаемо
и безопасно (лучше отклонять все запросы, чем принимать неавторизованные).

ВАЖНО (архитектура запуска):
Этот файл — отдельное Flask-приложение. Чтобы оно могло слать сообщения
через того же бота, что работает в main.py (polling), оба запускаются
в одном процессе: бот — в отдельном потоке, Flask — в основном.
Смотри run.py — это единая точка входа для продакшена.

ЖДЁМ ОТ ФЁДОРА:
  - X_WP_SECRET (значение секрета)
  - подтверждение: это просто строка для сравнения, или HMAC-подпись
    тела запроса? Пока реализовано как прямое сравнение строк.
"""

from flask import Flask, request, jsonify
import logging
import os
import hmac

import orders_db
import subscriptions_db
import referrals_db
import emoji_ids

logger = logging.getLogger("webhooks")

app = Flask(__name__)

# ЖДЁМ ОТ ФЁДОРА.
X_WP_SECRET = os.environ.get("X_WP_SECRET", "")

# Устанавливается снаружи (из run.py) — экземпляр TeleBot, чтобы вебхуки
# могли слать сообщения пользователям. Если None — вебхуки настроены
# для отладки без реального бота (сообщения просто логируются).
bot = None


def set_bot_instance(bot_instance):
    global bot
    bot = bot_instance


def _check_secret(req) -> bool:
    if not X_WP_SECRET:
        logger.warning("X_WP_SECRET не задан — все вебхуки будут отклонены до настройки")
        return False
    incoming = req.headers.get("X-WP-Secret", "")
    return hmac.compare_digest(incoming, X_WP_SECRET)


def _notify(telegram_id: int, text: str, parse_mode: str | None = None):
    """Отправляет сообщение пользователю, если бот подключён; иначе логирует."""
    if bot is None:
        logger.info("[DRY-RUN] Сообщение для %s: %s", telegram_id, text)
        return
    try:
        bot.send_message(telegram_id, text, parse_mode=parse_mode)
    except Exception as e:
        logger.error("Не удалось отправить сообщение telegram_id=%s: %s", telegram_id, e)


@app.route("/webhook/missing-items", methods=["POST"])
def missing_items():
    if not _check_secret(request):
        return jsonify({"error": "invalid secret"}), 401

    data = request.get_json(silent=True) or {}
    telegram_id = data.get("telegram_id")
    missing = data.get("missing_items", [])

    if not telegram_id or not missing:
        logger.warning("missing-items: некорректное тело запроса: %s", data)
        return jsonify({"error": "bad payload"}), 400

    ids_str = ", ".join(str(i) for i in missing)
    text = (
        f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji> '
        f"К сожалению, некоторых товаров (ID {ids_str}) сейчас нет в наличии.\n"
        f"Мы сформировали счёт на доступные позиции."
    )

    orders_db.mark_order_missing_items(site_order_id=None, telegram_id=telegram_id, missing_items=missing)
    _notify(telegram_id, text, parse_mode="HTML")

    logger.info("missing-items обработан: telegram_id=%s items=%s", telegram_id, missing)
    return jsonify({"status": "ok"}), 200


@app.route("/webhook/payment-success", methods=["POST"])
def payment_success():
    """
    ВАЖНО (пока не согласовано с Фёдором окончательно): чтобы отличить
    оплату обычного заказа от оплаты подписки Ордена, ожидаем опциональное
    поле "type" в теле запроса: "subscription" — активируем подписку,
    иначе (или поле отсутствует) — считаем это обычным заказом, как раньше.
    Если Фёдор пришлёт другой контракт различения — здесь меняется
    только эта развилка.
    """
    if not _check_secret(request):
        return jsonify({"error": "invalid secret"}), 401

    data = request.get_json(silent=True) or {}
    telegram_id = data.get("telegram_id")
    order_id = data.get("order_id")
    total = data.get("total")
    payment_type = data.get("type", "order")

    if not all([telegram_id, order_id, total]):
        logger.warning("payment-success: некорректное тело запроса: %s", data)
        return jsonify({"error": "bad payload"}), 400

    if payment_type == "subscription":
        # bundle_id — если сайт вернёт его в вебхуке (согласно предложению
        # Фёдора завести понятие "набор/бандл"). Если поля нет —
        # activate_subscription сам возьмёт тариф из pending_subscriptions
        # (то, что пользователь выбрал перед уходом на оплату).
        bundle_id = data.get("bundle_id")
        subscriptions_db.activate_subscription(telegram_id, site_order_id=order_id, pack_id=bundle_id)
        text = (
            f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji> '
            f"Оплата подписки Ордена на сумму {total} ₽ успешно получена! "
            f"Добро пожаловать в Орден — теперь тебе доступны Военные Сундуки."
        )
        _notify(telegram_id, text, parse_mode="HTML")
        logger.info("payment-success (подписка) обработан: telegram_id=%s order_id=%s", telegram_id, order_id)
        return jsonify({"status": "ok"}), 200

    updated = orders_db.mark_order_paid(site_order_id=order_id, telegram_id=telegram_id, total=total)
    if not updated:
        # Не нашли исходный заказ в своей БД — не блокируем уведомление
        # пользователю (деньги уже списаны, ему нужно подтверждение),
        # но логируем аномалию для разбора.
        logger.warning(
            "payment-success: заказ order_id=%s telegram_id=%s не найден в orders_db",
            order_id, telegram_id,
        )

    text = (
        f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji> '
        f"Оплата заказа #{order_id} на сумму {total} ₽ успешно получена! Спасибо за покупку."
    )
    _notify(telegram_id, text, parse_mode="HTML")

    # Реферальная система: если это ПЕРВЫЙ оплаченный заказ приглашённого
    # пользователя — начисляем бонус пригласившему и уведомляем обоих.
    # mark_converted сам защищён от повторного начисления (см. referrals_db.py),
    # поэтому безопасно вызывать на каждый payment-success без доп. проверок.
    referrer_id = referrals_db.mark_converted(telegram_id)
    if referrer_id:
        _diamond = f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'
        _sword = f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji>'
        _notify(
            referrer_id,
            f"{_diamond} <b>Твой соратник оплатил первый заказ!</b>\n\n"
            f"Тебе начислено {referrals_db.REFERRAL_BONUS_RUB} ₽ бонуса.",
            parse_mode="HTML",
        )
        _notify(
            telegram_id,
            f"{_sword} Скидка за приглашение применена — спасибо, что присоединился "
            f"по ссылке соратника!",
            parse_mode="HTML",
        )
        logger.info(
            "Реферал конвертирован через payment-success: invitee=%s referrer=%s order_id=%s",
            telegram_id, referrer_id, order_id,
        )

    logger.info("payment-success обработан: telegram_id=%s order_id=%s total=%s", telegram_id, order_id, total)
    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    """Простой чек, что веб-сервер жив — удобно для мониторинга на bothost/VPS."""
    return jsonify({"status": "ok"}), 200
