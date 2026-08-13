"""
payments.py — приём оплаты за членство в Сообществе напрямую в Telegram,
через ЮKassa (Telegram Payments API), а не через сайт.

РЕШЕНИЕ ОТ СОЗВОНА 13.08: оплата членства — на нашей стороне (счёт
Тиграна/Ильи в ЮKassa), а не через checkout_url сайта, как обычные
заказы. Раньше это было наоборот (main.py дёргал create_subscription_order
на сайт) — теперь платёж полностью внутри Telegram: бот показывает
пользователю окно оплаты (send_invoice), Telegram сам обрабатывает
ввод карты, деньги идут на подключённый провайдер (ЮKassa).

КАК ЭТО РАБОТАЕТ ТЕХНИЧЕСКИ (стандартный Telegram Payments flow):
    1. Бот вызывает bot.send_invoice(...) — пользователь видит кнопку
       "Оплатить X ₽" прямо в чате.
    2. Пользователь нажимает — Telegram присылает боту pre_checkout_query
       (проверка "всё ли ещё актуально, разрешить ли оплату"). Бот ОБЯЗАН
       ответить в течение 10 секунд (см. main.py, handle_pre_checkout_query).
    3. Если бот подтвердил — Telegram показывает пользователю ввод карты,
       списывает деньги через ЮKassa.
    4. После успешной оплаты боту приходит обычное сообщение с полем
       message.successful_payment — вот тут (main.py,
       handle_successful_payment) мы наконец активируем членство.

ЧТО НУЖНО, ЧТОБЫ ЭТО ЗАРАБОТАЛО (ждём от Тиграна):
    1. Провайдер-токен ЮKassa для Telegram-бота — получается в личном
       кабинете ЮKassa (нужен статус ИП/самозанятого), подключается
       через @BotFather -> выбрать бота -> Bot Settings -> Payments ->
       ЮKassa. BotFather выдаёт токен вида "381764678:TEST:XXXXX"
       (тестовый) или "381764678:LIVE:XXXXX" (боевой).
    2. Токен вписывается в .env как YOOKASSA_PROVIDER_TOKEN — до этого
       момента вся эта функциональность просто отвечает пользователю
       "оплата временно недоступна", ничего не падает.

ЕЩЁ НЕ РЕШЕНО (см. вопрос Фёдору про скидку на паки после созвона):
    Как сайт узнаёт о статусе членства для скидки на паки — обсуждается
    отдельно (рандомный код или прямой вебхук от нас к нему). Как только
    решится — здесь же, в handle_successful_payment (main.py), нужно
    будет добавить вызов, который сообщает об этом сайту.
"""

import logging
import os

from telebot.types import LabeledPrice

logger = logging.getLogger("payments")

YOOKASSA_PROVIDER_TOKEN = os.environ.get("YOOKASSA_PROVIDER_TOKEN", "")

# Telegram Payments требует передавать сумму в МИНИМАЛЬНЫХ единицах
# валюты — для рублей это копейки (1 ₽ = 100).
CURRENCY = "RUB"


def is_configured() -> bool:
    """True, если токен провайдера уже вписан в .env — можно принимать платежи."""
    return bool(YOOKASSA_PROVIDER_TOKEN)


def build_membership_payload(telegram_id: int, tier_id: int) -> str:
    """
    invoice_payload — произвольная строка, которую МЫ САМИ придумываем при
    создании счёта, а Telegram потом возвращает её нам же в
    successful_payment. Используем её, чтобы понять, за что именно
    заплатили, не полагаясь на отдельное хранение состояния.

    Формат: "membership:<telegram_id>:<tier_id>" — простой и однозначно
    парсится обратно (см. parse_membership_payload).
    """
    return f"membership:{telegram_id}:{tier_id}"


def parse_membership_payload(payload: str) -> tuple[int, int] | None:
    """Разбирает payload обратно в (telegram_id, tier_id). None, если формат не совпал."""
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "membership":
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def send_membership_invoice(bot, chat_id: int, telegram_id: int, tier: dict, discount_percent: int = 0) -> bool:
    """
    Показывает пользователю счёт на оплату членства через Telegram Payments.

    tier — словарь уровня из subscription_tiers.py (name, price_year и т.д.).
    discount_percent — скидка для приглашённых по REF20 (0, если нет).

    Возвращает True, если счёт успешно отправлен. Если провайдер ещё не
    настроен (нет токена) — ничего не отправляет, возвращает False,
    вызывающий код (main.py) сам покажет пользователю понятное сообщение.
    """
    if not is_configured():
        logger.warning("Оплата членства недоступна: YOOKASSA_PROVIDER_TOKEN не задан")
        return False

    price = tier["price_year"]
    if discount_percent:
        price = round(price * (1 - discount_percent / 100))

    # Telegram ожидает сумму в копейках.
    amount_kopecks = price * 100

    payload = build_membership_payload(telegram_id, tier["id"])

    try:
        bot.send_invoice(
            chat_id,
            title=f"Членство в Сообществе — {tier['name']}",
            description=f"Годовое членство, уровень «{tier['name']}». "
                        f"{tier['tagline']}.",
            invoice_payload=payload,
            provider_token=YOOKASSA_PROVIDER_TOKEN,
            currency=CURRENCY,
            prices=[LabeledPrice(label=f"Членство «{tier['name']}» на год", amount=amount_kopecks)],
            start_parameter=f"membership-{tier['id']}",
        )
        logger.info("Счёт на членство отправлен: telegram_id=%s tier_id=%s сумма=%s ₽",
                     telegram_id, tier["id"], price)
        return True
    except Exception as e:
        logger.error("Не удалось отправить счёт на членство telegram_id=%s: %s", telegram_id, e)
        return False
