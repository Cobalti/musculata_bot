"""
subscriptions_db.py — статус подписки "Орден" по каждому пользователю.

ВАЖНО: Паки ("Базовый"/"Продвинутый"/"Премиум") — это И ЕСТЬ подписка,
а не отдельная фича поверх неё. Пользователь оформляет подписку на
конкретный тариф (один из трёх паков) на год, и раз в 2 месяца (6 раз
в год) ему по этому тарифу приходит доставка. Поэтому пак НЕ кладётся
в корзину — выбор пака сразу запускает оплату годовой подписки на сайте.

Хранится отдельно от orders_db.py, потому что подписка — это состояние
(активна/нет, до какой даты, какой тариф), а не разовое событие заказа.

СХЕМА:
    subscriptions (
        telegram_id    -- PK, кто подписан
        status         -- 'active' / 'inactive'
        pack_id        -- ID тарифа (10001/10002/10003 из packs.py)
        pack_name      -- имя тарифа на момент активации (для истории —
                          если тарифы переименуют, старая подписка не
                          "поедет" вслед за новым названием)
        site_order_id  -- order_id, которым сайт подтвердил оплату
        started_at     -- когда активирована
        expires_at     -- когда истекает (started_at + 365 дней)
    )

    pending_subscriptions (
        telegram_id    -- PK
        pack_id        -- какой тариф выбрал перед тем, как уйти платить
        requested_at   -- когда нажал "Оформить подписку"
    )
    Нужно потому, что вебхук payment-success от сайта (пока не согласовано
    окончательно с Фёдором) может не возвращать pack_id обратно — тогда
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
                pack_id       INTEGER,
                pack_name     TEXT,
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
                pack_id      INTEGER NOT NULL,
                requested_at TEXT NOT NULL
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


def record_pending_subscription(telegram_id: int, pack_id: int) -> None:
    """
    Запоминает, какой тариф пользователь выбрал перед уходом на оплату —
    вызывается сразу перед созданием запроса на сайт (см. main.py,
    handle_subscribe_pay). Один пользователь — одно ожидание одновременно.
    """
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO pending_subscriptions (telegram_id, pack_id, requested_at)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                pack_id = excluded.pack_id,
                requested_at = excluded.requested_at
            """,
            (telegram_id, pack_id, _now().isoformat()),
        )


def _pop_pending_pack_id(telegram_id: int) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT pack_id FROM pending_subscriptions WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        conn.execute("DELETE FROM pending_subscriptions WHERE telegram_id = ?", (telegram_id,))
    return row["pack_id"] if row else None


def activate_subscription(telegram_id: int, site_order_id: int | None = None,
                            pack_id: int | None = None) -> None:
    """
    Активирует подписку на SUBSCRIPTION_DAYS дней от текущего момента.
    Вызывается из вебхука payment-success с type="subscription".

    Если pack_id не передан явно сайтом — берём его из pending_subscriptions
    (см. record_pending_subscription): то, что пользователь выбрал перед
    уходом на оплату.
    """
    import packs  # локальный импорт, чтобы не плодить циклические зависимости на верхнем уровне

    if pack_id is None:
        pack_id = _pop_pending_pack_id(telegram_id)
    else:
        _pop_pending_pack_id(telegram_id)  # на всякий случай чистим "хвост" ожидания

    pack = packs.get_pack(pack_id) if pack_id else None
    pack_name = pack["name"] if pack else "Неизвестный тариф"

    now = _now()
    expires = now + timedelta(days=SUBSCRIPTION_DAYS)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO subscriptions (telegram_id, status, pack_id, pack_name, site_order_id, started_at, expires_at)
            VALUES (?, 'active', ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                status = 'active',
                pack_id = excluded.pack_id,
                pack_name = excluded.pack_name,
                site_order_id = excluded.site_order_id,
                started_at = excluded.started_at,
                expires_at = excluded.expires_at
            """,
            (telegram_id, pack_id, pack_name, site_order_id, now.isoformat(), expires.isoformat()),
        )
    logger.info(
        "Подписка активирована: telegram_id=%s тариф=%s до %s",
        telegram_id, pack_name, expires.isoformat(),
    )


_init_db()
