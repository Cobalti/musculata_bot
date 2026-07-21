"""
subscriptions_db.py — статус подписки "Орден" по каждому пользователю.

ВАЖНО: подписка — это один из трёх УРОВНЕЙ Ордена (Оруженосец, Рыцарь,
Военачальник — см. subscription_tiers.py), а НЕ пак. Паки — обычные
товары каталога; подписка лишь даёт на них скидку 5/10/15%.
Подписка не кладётся в корзину — оплата идёт напрямую на сайте.

Хранится отдельно от orders_db.py, потому что подписка — это состояние
(активна/нет, до какой даты, какой тариф), а не разовое событие заказа.

СХЕМА:
    subscriptions (
        telegram_id    -- PK, кто подписан
        status         -- 'active' / 'inactive'
        tier_id        -- ID уровня (20001/20002/20003 из subscription_tiers.py)
        tier_name      -- имя уровня на момент активации (для истории —
                          если уровни переименуют, старая подписка не
                          "поедет" вслед за новым названием)
        site_order_id  -- order_id, которым сайт подтвердил оплату
        started_at     -- когда активирована
        expires_at     -- когда истекает (started_at + 365 дней)
    )

    pending_subscriptions (
        telegram_id    -- PK
        tier_id        -- какой уровень выбрал перед тем, как уйти платить
        requested_at   -- когда нажал "Оформить подписку"
    )
    Нужно потому, что вебхук payment-success от сайта (пока не согласовано
    окончательно с Фёдором) может не возвращать tier_id обратно — тогда
    activate_subscription() подстрахует себя, взяв последний "запрос на
    оплату" этого пользователя отсюда.
"""

import sqlite3
import os
import logging
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

logger = logging.getLogger("subscriptions_db")

DB_PATH = os.path.join(os.path.dirname(__file__), "subscriptions.db")
SUBSCRIPTION_DAYS = 365


def _init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                telegram_id   INTEGER PRIMARY KEY,
                status        TEXT NOT NULL DEFAULT 'active',
                tier_id       INTEGER,
                tier_name     TEXT,
                site_order_id INTEGER,
                started_at    TEXT NOT NULL,
                expires_at    TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_subscriptions (
                telegram_id  INTEGER PRIMARY KEY,
                tier_id      INTEGER NOT NULL,
                requested_at TEXT NOT NULL
            )
            """
        )


@contextmanager
def _connect():
    # timeout=10 + WAL + busy_timeout — защита от "database is locked" при
    # конкурентном доступе. Особенно важно, если на сервере на короткое
    # время оказываются запущены ДВА инстанса бота одновременно (например,
    # во время рестарта хостингом) — без этого одновременная запись из
    # двух процессов может уронить чтение/запись с ошибкой, и пользователь
    # получит тишину вместо ответа (см. errors.py — раньше такая ошибка
    # вообще не долетала до юзера).
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def has_active_subscription(telegram_id: int) -> bool:
    """Главная проверка — используется при показе состава пака и статуса Ордена."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT expires_at FROM subscriptions WHERE telegram_id = ? AND status = 'active'",
            (telegram_id,),
        ).fetchone()
    if not row:
        return False
    expires_at = datetime.fromisoformat(row["expires_at"])
    return expires_at > _now()


def get_subscription(telegram_id: int) -> dict | None:
    """Полная информация о подписке — для экрана 'Орден' (тариф, дата окончания)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
    return dict(row) if row else None


def record_pending_subscription(telegram_id: int, tier_id: int) -> None:
    """
    Запоминает, какой уровень пользователь выбрал перед уходом на оплату —
    вызывается сразу перед созданием запроса на сайт (см. main.py,
    handle_tier_subscribe). Один пользователь — одно ожидание одновременно.
    """
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO pending_subscriptions (telegram_id, tier_id, requested_at)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                tier_id = excluded.tier_id,
                requested_at = excluded.requested_at
            """,
            (telegram_id, tier_id, _now().isoformat()),
        )


def _pop_pending_tier_id(telegram_id: int) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT tier_id FROM pending_subscriptions WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        conn.execute("DELETE FROM pending_subscriptions WHERE telegram_id = ?", (telegram_id,))
    return row["tier_id"] if row else None


def activate_subscription(telegram_id: int, site_order_id: int | None = None,
                            tier_id: int | None = None) -> None:
    """
    Активирует подписку на SUBSCRIPTION_DAYS дней от текущего момента.
    Вызывается из вебхука payment-success с type="subscription".

    Если tier_id не передан явно сайтом — берём его из pending_subscriptions
    (см. record_pending_subscription): то, что пользователь выбрал перед
    уходом на оплату.
    """
    import subscription_tiers  # локальный импорт — избегаем циклических зависимостей

    if tier_id is None:
        tier_id = _pop_pending_tier_id(telegram_id)
    else:
        _pop_pending_tier_id(telegram_id)  # на всякий случай чистим "хвост" ожидания

    tier = subscription_tiers.get_tier(tier_id) if tier_id else None
    tier_name = tier["name"] if tier else "Неизвестный уровень"

    now = _now()
    expires = now + timedelta(days=SUBSCRIPTION_DAYS)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO subscriptions (telegram_id, status, tier_id, tier_name, site_order_id, started_at, expires_at)
            VALUES (?, 'active', ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                status = 'active',
                tier_id = excluded.tier_id,
                tier_name = excluded.tier_name,
                site_order_id = excluded.site_order_id,
                started_at = excluded.started_at,
                expires_at = excluded.expires_at
            """,
            (telegram_id, tier_id, tier_name, site_order_id, now.isoformat(), expires.isoformat()),
        )
    logger.info(
        "Подписка активирована: telegram_id=%s уровень=%s до %s",
        telegram_id, tier_name, expires.isoformat(),
    )


def get_active_tier_id(telegram_id: int) -> int | None:
    """ID активного уровня подписки, либо None. Нужен для расчёта скидки на паки."""
    if not has_active_subscription(telegram_id):
        return None
    sub = get_subscription(telegram_id)
    return sub["tier_id"] if sub else None


_init_db()
