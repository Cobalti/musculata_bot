"""
integrations.py — связь бота с сайтом mashinabodystore.ru.

Реализует ровно то, что подтвердил Фёдор (техспециалист сайта) в
переписке от 09.07.26:
  - один эндпоинт создания заказа (см. create_order)
  - без личного кабинета/авторизации на сайте — сайт знает пользователя
    только по telegram_id в рамках заказа

Всё, что помечено TODO/ЖДЁМ ОТ ФЁДОРА — заглушки на случай, пока не
пришёл финальный ответ. Код рабочий и его не нужно переписывать с нуля,
когда ответы придут — только подставить значения.
"""

import requests
import logging
import os

logger = logging.getLogger("integrations")

# ЖДЁМ ОТ ФЁДОРА: подтверждение финального URL (в его сообщении помечено
# "может поменяться").
SITE_ORDER_ENDPOINT = os.environ.get(
    "SITE_ORDER_ENDPOINT",
    "https://mashinabodystore.ru/wp-json/v2/integrations/musculata",
)

# ЖДЁМ ОТ ФЁДОРА: "предоставлю когда будет готова интеграция".
X_BOT_TOKEN = os.environ.get("X_BOT_TOKEN", "")

REQUEST_TIMEOUT_SECONDS = 10


def create_order(telegram_id: int, items: list[int], promotions: str | None = None) -> dict:
    """
    Отправляет корзину на сайт, получает order_id и готовую ссылку на оплату.

    Args:
        telegram_id: ID пользователя в Telegram.
        items: список ID товаров в корзине.
            ЖДЁМ ОТ ФЁДОРА: подтверждение формата (сейчас предполагаем
            int; может оказаться, что нужны строковые ID из МойСклада —
            тогда меняется только тип здесь и в products.py, сам вызов
            не меняется).
        promotions: код промокода, например "TELEGRAM10". Необязателен.

    Returns:
        dict с ключами status, order_id, checkout_url, missing_items_reported.
        При сетевой ошибке возвращает status="error" и остальные поля пустые —
        вызывающий код (main.py) обязан явно это обработать (см. handle_checkout).
    """
    if not X_BOT_TOKEN:
        logger.error("X_BOT_TOKEN не задан — интеграция ещё не настроена Фёдором")
        return _error_response()

    payload = {"telegram_id": telegram_id, "items": items}
    if promotions:
        payload["promotions"] = promotions

    headers = {
        "Content-Type": "application/json",
        "X-Bot-Token": X_BOT_TOKEN,
    }

    try:
        response = requests.post(
            SITE_ORDER_ENDPOINT, json=payload, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()
        logger.info(
            "Заказ создан: telegram_id=%s order_id=%s missing_items=%s",
            telegram_id, data.get("order_id"), data.get("missing_items_reported"),
        )
        return data
    except requests.exceptions.RequestException as e:
        logger.error("Ошибка при создании заказа для telegram_id=%s: %s", telegram_id, e)
        return _error_response()
    except ValueError as e:
        # response.json() не смог распарсить ответ — сайт вернул не-JSON
        logger.error("Сайт вернул невалидный JSON для telegram_id=%s: %s", telegram_id, e)
        return _error_response()


def _error_response() -> dict:
    return {
        "status": "error",
        "order_id": None,
        "checkout_url": None,
        "missing_items_reported": False,
    }


# ЖДЁМ ОТ ФЁДОРА: отдельный (или тот же самый?) эндпоинт для оплаты
# подписки "Орден" — этого ещё нет в согласованной схеме, см. список
# вопросов. Пока используем ту же переменную, что и обычный заказ, как
# временную заглушку, чтобы код не падал — как только он даст точный
# адрес, здесь меняется одна строка.
SITE_SUBSCRIPTION_ENDPOINT = os.environ.get("SITE_SUBSCRIPTION_ENDPOINT", "")


def create_subscription_order(telegram_id: int, tier_id: int, promotions: str | None = None) -> dict:
    """
    Запрос на оплату годовой подписки Ордена на конкретный уровень
    (Оруженосец / Рыцарь / Военачальник — см. subscription_tiers.py).

    promotions — промокод, если есть. Сейчас используется REF20 (скидка 20%
    приглашённому на первую годовую подписку, по Excel заказчика).

    ⚠️ SITE_SUBSCRIPTION_ENDPOINT ещё не согласован с Фёдором — пока
    переменная пустая, функция сразу возвращает error, и handle_tier_subscribe
    показывает пользователю честное «оплата временно недоступна» вместо
    падения или зависания.
    """
    if not SITE_SUBSCRIPTION_ENDPOINT or not X_BOT_TOKEN:
        logger.error("Оплата подписки недоступна: эндпоинт или токен ещё не настроены Фёдором")
        return _error_response()

    payload = {
        "telegram_id": telegram_id,
        "product": "order_subscription",
        "tier_id": tier_id,
    }
    if promotions:
        payload["promotions"] = promotions

    headers = {"Content-Type": "application/json", "X-Bot-Token": X_BOT_TOKEN}

    try:
        response = requests.post(
            SITE_SUBSCRIPTION_ENDPOINT, json=payload, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()
        logger.info("Запрос на подписку создан: telegram_id=%s tier_id=%s order_id=%s",
                     telegram_id, tier_id, data.get("order_id"))
        return data
    except requests.exceptions.RequestException as e:
        logger.error("Ошибка при создании подписки для telegram_id=%s: %s", telegram_id, e)
        return _error_response()
    except ValueError as e:
        logger.error("Сайт вернул невалидный JSON для подписки telegram_id=%s: %s", telegram_id, e)
        return _error_response()
