"""
subscriptions_db.py — статус подписки "Орден" по каждому пользователю.

Годовая подписка (365 дней) с поставками раз в 60 дней. Хранится
отдельно от orders_db.py, потому что это принципиально другая сущность:
заказ — разовое событие, подписка — состояние, которое либо активно,
либо нет, и имеет срок действия.

СХЕМА:
    subscriptions (
        telegram_id    -- PK, кто подписан
        status         -- 'active' / 'expired' / 'none' (none тут не
                          хранится физически — просто нет записи)
        site_order_id  -- order_id, которым сайт подтвердил оплату подписки
        started_at     -- когда активирована
        expires_at     -- когда истекает (started_at + 365 дней)
    )
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
                site_order_id INTEGER,
                started_at    TEXT NOT NULL,
                expires_at    TEXT NOT NULL
            )
            """
        )


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def has_active_subscription(telegram_id: int) -> bool:
    """Главная проверка — используется при попытке добавить пак в корзину."""
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
    """Полная информация о подписке — для экрана 'Орден' (статус, дата окончания)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
    return dict(row) if row else None


def activate_subscription(telegram_id: int, site_order_id: int | None = None) -> None:
    """
    Активирует/продлевает подписку на SUBSCRIPTION_DAYS дней от текущего
    момента. Вызывается из вебхука payment-success, когда подтверждена
    оплата именно подписки (см. main.py — webhooks.py должен уметь
    различать заказ и подписку, см. вопрос к Фёдору про это).
    """
    now = _now()
    expires = now + timedelta(days=SUBSCRIPTION_DAYS)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO subscriptions (telegram_id, status, site_order_id, started_at, expires_at)
            VALUES (?, 'active', ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                status = 'active',
                site_order_id = excluded.site_order_id,
                started_at = excluded.started_at,
                expires_at = excluded.expires_at
            """,
            (telegram_id, site_order_id, now.isoformat(), expires.isoformat()),
        )
    logger.info("Подписка активирована: telegram_id=%s до %s", telegram_id, expires.isoformat())


_init_db()
