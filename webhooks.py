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


def _handle_referral_conversion(invitee_id: int):
    """
    Вызывается после успешной оплаты ПОДПИСКИ приглашённым пользователем.
    Засчитывает конверсию и уведомляет обоих. Ступени (1/3/6) и награды —
    из ТЗ по подпискам (п. 3.4): +1/+3/+6 месяцев подписки, на 3-й и
    6-й ступени ещё стикерпак/мерч-набор (это уже не автоматизируется —
    бот только упоминает в уведомлении, физическую отправку делает человек).
    """
    result = referrals_db.mark_converted(invitee_id)
    if not result:
        return

    _diamond = f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'
    _sword = f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji>'

    referrer_id = result["referrer_id"]
    count = result["converted_count"]

    text = (
        f"{_sword} <b>Твой соратник вступил в Орден!</b>\n\n"
        f"Всего по твоим приглашениям вступили: <b>{count}</b>."
    )

    if result["milestone_reached"]:
        reward = result["reward"] or {}
        extra_days = reward.get("extra_days", 0)
        merch_gift = reward.get("merch_gift")

        text += f"\n\n{_diamond} <b>Ты достиг ступени {result['milestone_reached']}!</b>"

        if extra_days:
            new_expires = subscriptions_db.extend_subscription(referrer_id, extra_days)
            if new_expires:
                months = extra_days // 30
                text += f"\nПодписка продлена на {months} мес. — теперь действует до {new_expires[:10]}."
            else:
                # У пригласившего сейчас нет активной подписки — продлевать
                # нечего (extend_subscription сам это проверяет и логирует).
                # Не обманываем человека сообщением о продлении, если оно
                # не произошло.
                text += "\nБонус на продление учтён, но у тебя сейчас нет активной подписки."

        if merch_gift:
            text += f"\n🎁 Тебе полагается: {merch_gift} — свяжемся с тобой отдельно."

    _notify(referrer_id, text, parse_mode="HTML")

    logger.info("Реферал конвертирован: invitee=%s referrer=%s всего=%s",
                 invitee_id, referrer_id, count)


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
        # tier_id — если сайт вернёт его в вебхуке. Если поля нет —
        # activate_subscription сам возьмёт уровень из pending_subscriptions
        # (то, что пользователь выбрал перед уходом на оплату).
        tier_id = data.get("tier_id")
        subscriptions_db.activate_subscription(telegram_id, site_order_id=order_id, tier_id=tier_id)

        sub = subscriptions_db.get_subscription(telegram_id)
        tier_name = sub.get("tier_name") if sub else "Орден"
        _notify(
            telegram_id,
            f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji> '
            f"<b>Добро пожаловать в Орден!</b>\n\n"
            f"Уровень «{tier_name}» активирован, оплата {total} ₽ получена.\n"
            f"Теперь тебе доступны все привилегии ранга — загляни в раздел "
            f"«Орден», чтобы посмотреть детали.",
            parse_mode="HTML",
        )

        # Реферальная конверсия засчитывается именно по ПОДПИСКЕ (по Excel
        # бонус приглашённому — скидка на первую годовую подписку).
        # mark_converted защищён от повторного вебхука.
        _handle_referral_conversion(telegram_id)

        logger.info("payment-success (подписка): telegram_id=%s order_id=%s tier=%s",
                     telegram_id, order_id, tier_name)
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

    logger.info("payment-success обработан: telegram_id=%s order_id=%s total=%s", telegram_id, order_id, total)
    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    """Простой чек, что веб-сервер жив — удобно для мониторинга на bothost/VPS."""
    return jsonify({"status": "ok"}), 200
