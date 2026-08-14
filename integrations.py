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
import uuid

logger = logging.getLogger("integrations")

# Подтверждено Фёдором в wp_endpoints_api.pdf — этот URL актуален.
SITE_ORDER_ENDPOINT = os.environ.get(
    "SITE_ORDER_ENDPOINT",
    "https://mashinabodystore.ru/wp-json/v2/integrations/musculata",
)

# Каталог товаров — отдаётся постранично (50 на страницу), кэшируется
# у Фёдора на 5 минут. См. fetch_catalog.py — отдельный скрипт для
# первоначальной выгрузки и последующих обновлений каталога.
SITE_PRODUCTS_ENDPOINT = os.environ.get(
    "SITE_PRODUCTS_ENDPOINT",
    "https://mashinabodystore.ru/wp-json/v2/integrations/musculata/products",
)

X_BOT_TOKEN = os.environ.get("X_BOT_TOKEN", "")

# Было 10 сек — при нескольких одновременных пользователях один медленный
# ответ сайта мог надолго занять рабочий поток бота, и тогда у ДРУГИХ
# пользователей колбэки успевали "протухнуть" в очереди на свободный
# поток (не наша логика тормозила — сама очередь тормозила). 5 сек — с
# запасом достаточно для нормального ответа сайта, но не даёт одному
# медленному запросу забрать поток надолго.
REQUEST_TIMEOUT_SECONDS = 5

# Промокоды, которые сайт реально принимает для ОБЫЧНОГО заказа
# (не членства) — из wp_endpoints_api.pdf. REF20 сюда не входит —
# он только для членства, через отдельный эндпоинт.
ALLOWED_ORDER_PROMOTIONS = {"TELEGRAM10", "PROMO_10", "PROMO_15", "FREE_SHIPPING"}


def _new_request_id(prefix: str) -> str:
    """
    Уникальный ID запроса для идемпотентности — Фёдор подтвердил: если
    прислать повторно тот же request_id, сайт вернёт уже созданный заказ
    вместо дубликата. Критично на случай сетевых сбоев/ретраев с нашей
    стороны — без этого повторная попытка после таймаута могла бы
    создать два заказа на один и тот же товар.
    """
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def create_order(telegram_id: int, items: list[int], promotions: str | None = None) -> dict:
    """
    Отправляет корзину на сайт, получает order_id и готовую ссылку на оплату.

    Args:
        telegram_id: ID пользователя в Telegram.
        items: список реальных WooCommerce ID товаров в корзине (в том
            числе паков — по подтверждению Фёдора, пак передаётся как
            обычный товар с реальным ID, отдельного bundle_id не нужно).
        promotions: код промокода — должен входить в ALLOWED_ORDER_PROMOTIONS.
            НЕ передавать вместе с паком в корзине — по документации сайта
            обычный купон дополнительно к паку не применяется (скидка
            на пак считается отдельно самим сайтом).

    Returns:
        dict с ключами status, order_id, checkout_url, missing_items_reported.
        При сетевой ошибке возвращает status="error" и остальные поля пустые —
        вызывающий код (main.py) обязан явно это обработать (см. handle_checkout).
    """
    if not X_BOT_TOKEN:
        logger.error("X_BOT_TOKEN не задан — интеграция ещё не настроена Фёдором")
        return _error_response()

    payload = {
        "telegram_id": telegram_id,
        "items": items,
        "request_id": _new_request_id("order"),
    }
    if promotions:
        if promotions not in ALLOWED_ORDER_PROMOTIONS:
            logger.warning(
                "Промокод %r не входит в список разрешённых сайтом (%s) — отправляю как есть, "
                "сайт сам решит, но стоит проверить логику вызова",
                promotions, ALLOWED_ORDER_PROMOTIONS,
            )
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
            "Заказ создан: telegram_id=%s order_id=%s missing_items=%s request_id=%s",
            telegram_id, data.get("order_id"), data.get("missing_items_reported"), payload["request_id"],
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
# членства в Сообществе — этого ещё нет в согласованной схеме, см. список
# вопросов. Пока используем ту же переменную, что и обычный заказ, как
# временную заглушку, чтобы код не падал — как только он даст точный
# адрес, здесь меняется одна строка.
SITE_SUBSCRIPTION_ENDPOINT = os.environ.get("SITE_SUBSCRIPTION_ENDPOINT", "")


def create_subscription_order(telegram_id: int, tier_id: int, promotions: str | None = None) -> dict:
    """
    Запрос на годовое членство в Сообществе на конкретный уровень
    (Оруженосец / Рыцарь / Военачальник — см. subscription_tiers.py).

    promotions — промокод, если есть. Сейчас используется REF20 (скидка 20%
    приглашённому на первое годовое членство, по Excel заказчика).

    ⚠️ SITE_SUBSCRIPTION_ENDPOINT ещё не согласован с Фёдором — пока
    переменная пустая, функция сразу возвращает error, и handle_tier_subscribe
    показывает пользователю честное «оплата временно недоступна» вместо
    падения или зависания.
    """
    if not SITE_SUBSCRIPTION_ENDPOINT or not X_BOT_TOKEN:
        logger.error("Оформление членства недоступно: эндпоинт или токен ещё не настроены Фёдором")
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
        logger.info("Запрос на членство создан: telegram_id=%s tier_id=%s order_id=%s",
                     telegram_id, tier_id, data.get("order_id"))
        return data
    except requests.exceptions.RequestException as e:
        logger.error("Ошибка при создании членства для telegram_id=%s: %s", telegram_id, e)
        return _error_response()
    except ValueError as e:
        logger.error("Сайт вернул невалидный JSON для членства telegram_id=%s: %s", telegram_id, e)
        return _error_response()
