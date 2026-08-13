"""
pack4_db.py — бесплатный ежемесячный пак №4 для участников Сообщества.

Из ТЗ по подпискам (см. subscription_tiers.COMMON_PERKS): "Бесплатный
пак №4 — уникальный, отдельно не продаётся, состав меняется каждый
месяц". Доступен только тем, у кого сейчас активно членство (любой
уровень, состав один и тот же для всех — разница только в праве
вообще его получить).

Админ обновляет состав раз в месяц командой /setpack4 прямо в боте
(см. main.py) — сознательно НЕ веб-панель, чтобы не плодить лишнюю
инфраструктуру ради одной простой задачи "раз в месяц вписать список".

ВАЖНО: бот не занимается физической отправкой — это делает человек.
Роль бота — честно фиксировать, кто имеет право и кто уже забрал пак
в этом месяце (claim), и уведомлять админа о каждом заборе, чтобы было
видно, кому физически готовить посылку.

СХЕМА:
    pack4_composition (
        month_key   -- PK, "2026-08" (год-месяц — так состав естественно
                       меняется раз в месяц и хранится история прошлых)
        items_json  -- JSON-список [{"name","brand","price"}, ...]
                       (price — розничная цена товара, просто для
                       информации подписчику, сам пак бесплатный)
        gift        -- опциональный текст доп. бонуса
        set_at      -- когда админ задал состав
    )
    pack4_claims (
        telegram_id + month_key -- составной PK, кто уже забрал пак
                                    в этом месяце (защита от повторного
                                    начисления)
        claimed_at
    )
"""

import sqlite3
import os
import json
import logging
from datetime import datetime, timezone
from contextlib import contextmanager

logger = logging.getLogger("pack4_db")

DB_PATH = os.path.join(os.path.dirname(__file__), "pack4.db")


def _init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pack4_composition (
                month_key  TEXT PRIMARY KEY,
                items_json TEXT NOT NULL,
                gift       TEXT,
                set_at     TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pack4_claims (
                telegram_id INTEGER NOT NULL,
                month_key   TEXT NOT NULL,
                claimed_at  TEXT NOT NULL,
                PRIMARY KEY (telegram_id, month_key)
            )
            """
        )


@contextmanager
def _connect():
    # WAL + busy_timeout — та же защита от конкурентного доступа, что и
    # в остальных базах проекта (см. subscriptions_db.py и т.д.).
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def current_month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def set_composition(items: list[dict], gift: str | None = None, month_key: str | None = None) -> None:
    """Задаёт состав пака на месяц (по умолчанию — текущий). Вызывается администратором."""
    month_key = month_key or current_month_key()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO pack4_composition (month_key, items_json, gift, set_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(month_key) DO UPDATE SET
                items_json = excluded.items_json,
                gift = excluded.gift,
                set_at = excluded.set_at
            """,
            (month_key, json.dumps(items, ensure_ascii=False), gift, datetime.now(timezone.utc).isoformat()),
        )
    logger.info("Состав пака №4 на %s обновлён: %s позиций", month_key, len(items))


def get_current_composition() -> dict | None:
    """Состав пака на ТЕКУЩИЙ месяц, либо None, если админ ещё не задал его."""
    month_key = current_month_key()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM pack4_composition WHERE month_key = ?", (month_key,)
        ).fetchone()
    if not row:
        return None
    return {
        "month_key": row["month_key"],
        "items": json.loads(row["items_json"]),
        "gift": row["gift"],
        "set_at": row["set_at"],
    }


def has_claimed(telegram_id: int) -> bool:
    """Забирал ли пользователь пак в ЭТОМ месяце."""
    month_key = current_month_key()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM pack4_claims WHERE telegram_id = ? AND month_key = ?",
            (telegram_id, month_key),
        ).fetchone()
    return row is not None


def claim(telegram_id: int) -> bool:
    """
    Отмечает, что пользователь забрал пак в этом месяце. Возвращает False,
    если уже забирал (защита от повторного получения) — тогда ничего
    не меняет и не пишет повторную запись.
    """
    if has_claimed(telegram_id):
        return False
    month_key = current_month_key()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO pack4_claims (telegram_id, month_key, claimed_at) VALUES (?, ?, ?)",
            (telegram_id, month_key, datetime.now(timezone.utc).isoformat()),
        )
    logger.info("Пак №4 забран: telegram_id=%s месяц=%s", telegram_id, month_key)
    return True


_init_db()
