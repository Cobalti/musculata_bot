"""
orders_db.py — собственное хранилище заказов бота.

ПОЧЕМУ ЭТО НУЖНО:
Фёдор (техспециалист MashinaBodyStore) подтвердил: личного кабинета
и авторизации на сайте не будет. Сайт знает пользователя только по
telegram_id в рамках конкретного заказа — истории заказов на сайте
у юзера не будет. Значит, раздел "Мои заказы" внутри бота должен
опираться на СОБСТВЕННУЮ базу, а не на данные с сайта.

Здесь используется SQLite — файл базы лежит рядом с ботом
(orders.db). Для продакшена с более высокой нагрузкой это несложно
перенести на PostgreSQL (сама схема таблицы не изменится, поменяется
только слой подключения — psycopg2/asyncpg вместо sqlite3).

СХЕМА:
    orders (
        id            -- внутренний ID записи (наш)
        telegram_id   -- кто оформил
        site_order_id -- order_id, который вернул сайт Фёдора
        status        -- pending / paid / missing_items / error
        total         -- сумма заказа (заполняется при оплате)
        checkout_url  -- ссылка на оплату, которую мы показывали юзеру
        items_json    -- снапшот корзины на момент заказа (для истории)
        created_at    -- когда создан
        paid_at        -- когда пришёл вебхук payment-success
    )
"""

import sqlite3
import json
import os
import logging
from datetime import datetime, timezone
from contextlib import contextmanager

logger = logging.getLogger("orders_db")

DB_PATH = os.path.join(os.path.dirname(__file__), "orders.db")


def _init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id   INTEGER NOT NULL,
                site_order_id INTEGER,
                status        TEXT NOT NULL DEFAULT 'pending',
                total         TEXT,
                checkout_url  TEXT,
                items_json    TEXT,
                created_at    TEXT NOT NULL,
                paid_at       TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_telegram_id ON orders(telegram_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_site_order_id ON orders(site_order_id)"
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_order_record(telegram_id: int, site_order_id: int | None,
                          checkout_url: str | None, items: list[int],
                          status: str = "pending") -> int:
    """
    Сохраняет заказ сразу после ответа от сайта (create_order в integrations.py).
    Возвращает внутренний id записи.
    """
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders (telegram_id, site_order_id, status, checkout_url, items_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (telegram_id, site_order_id, status, checkout_url, json.dumps(items), _now()),
        )
        return cur.lastrowid


def mark_order_paid(site_order_id: int, telegram_id: int, total: str) -> bool:
    """
    Вызывается из вебхука payment-success. Находит заказ по site_order_id
    (и на всякий случай сверяет telegram_id) и помечает его оплаченным.

    Возвращает True, если запись найдена и обновлена, False — если нет
    совпадения (тогда стоит залогировать это как аномалию).
    """
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE orders
            SET status = 'paid', total = ?, paid_at = ?
            WHERE site_order_id = ? AND telegram_id = ?
            """,
            (total, _now(), site_order_id, telegram_id),
        )
        if cur.rowcount == 0:
            logger.warning(
                "mark_order_paid: не найден заказ site_order_id=%s telegram_id=%s",
                site_order_id, telegram_id,
            )
            return False
        return True


def mark_order_missing_items(site_order_id: int | None, telegram_id: int, missing_items: list[int]) -> None:
    """
    Вызывается из вебхука missing-items. site_order_id тут может быть
    неизвестен (Фёдор в примере его не передаёт — только telegram_id и
    missing_items), поэтому ищем последний pending-заказ этого юзера.
    """
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT id FROM orders
            WHERE telegram_id = ? AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
            """,
            (telegram_id,),
        )
        row = cur.fetchone()
        if not row:
            logger.warning("mark_order_missing_items: нет pending-заказа для telegram_id=%s", telegram_id)
            return
        conn.execute(
            "UPDATE orders SET status = 'missing_items' WHERE id = ?",
            (row["id"],),
        )


def get_user_orders(telegram_id: int, limit: int = 10) -> list[dict]:
    """Последние заказы юзера — для раздела 'Мои заказы' в боте."""
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT * FROM orders
            WHERE telegram_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (telegram_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]


# Инициализация при импорте модуля — таблица создаётся один раз, если её ещё нет.
_init_db()
