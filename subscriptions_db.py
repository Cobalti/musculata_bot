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
        frozen_until   -- если подписка сейчас на паузе (ТЗ п. 3.5,
                          "заморозка до 30 дней") — до какой даты доступ
                          приостановлен. NULL, если заморозки нет/закончилась.
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
MAX_FREEZE_DAYS = 30  # ТЗ по подпискам, п. 3.5: "заморозка до 30 дней"


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
                expires_at    TEXT NOT NULL,
                frozen_until  TEXT
            )
            """
        )
        # Миграция для БД, созданных ДО появления заморозки — CREATE TABLE
        # IF NOT EXISTS не добавит новую колонку в уже существующую таблицу,
        # поэтому досоздаём её отдельно, если её ещё нет.
        try:
            conn.execute("ALTER TABLE subscriptions ADD COLUMN frozen_until TEXT")
        except sqlite3.OperationalError:
            pass  # колонка уже есть — база создана после этого изменения
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
    """
    Главная проверка — используется при показе состава пака и статуса Ордена.

    Во время заморозки (frozen_until в будущем) возвращает False — доступ
    приостановлен, хотя сама подписка (и дата её окончания) никуда не
    делась. Это соответствует ТЗ: "контент сохраняется, доступ
    приостанавливается".
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT expires_at, frozen_until FROM subscriptions WHERE telegram_id = ? AND status = 'active'",
            (telegram_id,),
        ).fetchone()
    if not row:
        return False
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at <= _now():
        return False
    if row["frozen_until"]:
        frozen_until = datetime.fromisoformat(row["frozen_until"])
        if frozen_until > _now():
            return False
    return True


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


def extend_subscription(telegram_id: int, extra_days: int) -> str | None:
    """
    Продлевает подписку на extra_days дней от текущей даты окончания.
    Используется для реферальных бонусов (ТЗ по подпискам, п. 3.4:
    +1/+3/+6 месяцев за 1/3/6 приглашённых) — см. referrals_db.mark_converted
    и webhooks.py, _handle_referral_conversion.

    Если у пользователя нет активной подписки — ничего не делает и
    возвращает None (бонус на продление можно получить, только уже
    имея подписку; сам факт достижения ступени всё равно фиксируется
    в referrals_db независимо от этого).

    Возвращает новую дату окончания (ISO-строка) при успехе, иначе None.
    """
    if not has_active_subscription(telegram_id):
        logger.warning(
            "Попытка продлить подписку telegram_id=%s на %s дней, но активной подписки нет — пропущено",
            telegram_id, extra_days,
        )
        return None

    sub = get_subscription(telegram_id)
    current_expires = datetime.fromisoformat(sub["expires_at"])
    new_expires = current_expires + timedelta(days=extra_days)

    with _connect() as conn:
        conn.execute(
            "UPDATE subscriptions SET expires_at = ? WHERE telegram_id = ?",
            (new_expires.isoformat(), telegram_id),
        )
    logger.info(
        "Подписка продлена: telegram_id=%s +%s дней, новая дата окончания %s",
        telegram_id, extra_days, new_expires.isoformat(),
    )
    return new_expires.isoformat()


def is_frozen(telegram_id: int) -> bool:
    """True, если подписка прямо сейчас стоит на паузе (заморожена)."""
    sub = get_subscription(telegram_id)
    if not sub or not sub.get("frozen_until"):
        return False
    return datetime.fromisoformat(sub["frozen_until"]) > _now()


def get_freeze_status(telegram_id: int) -> dict | None:
    """Если подписка сейчас заморожена — {"frozen_until": iso}, иначе None."""
    if not is_frozen(telegram_id):
        return None
    sub = get_subscription(telegram_id)
    return {"frozen_until": sub["frozen_until"]}


def freeze_subscription(telegram_id: int, days: int) -> dict:
    """
    Замораживает подписку на `days` дней (максимум MAX_FREEZE_DAYS — из
    ТЗ по подпискам, п. 3.5: "заморозка до 30 дней при травме, отпуске —
    контент сохраняется, доступ приостанавливается").

    На время заморозки has_active_subscription() возвращает False (доступ
    правда приостановлен), но дата окончания подписки СДВИГАЕТСЯ на то же
    число дней вперёд — оплаченное время не теряется, просто откладывается.
    Разморозка происходит автоматически, как только проходит frozen_until —
    отдельного действия/крона не требуется, has_active_subscription сам
    сравнивает с текущим временем при каждой проверке.

    Возвращает:
        {"ok": True, "frozen_until": iso, "new_expires_at": iso}
        {"ok": False, "reason": "invalid_days" | "no_subscription" | "already_frozen"}
    """
    if not (1 <= days <= MAX_FREEZE_DAYS):
        return {"ok": False, "reason": "invalid_days"}

    # Проверяем заморозку РАНЬШЕ активности подписки: во время заморозки
    # has_active_subscription() и так возвращает False (доступ приостановлен
    # по задумке) — если проверить в обратном порядке, "уже заморожена"
    # никогда не отличить от "подписки вообще нет".
    if is_frozen(telegram_id):
        return {"ok": False, "reason": "already_frozen"}

    if not has_active_subscription(telegram_id):
        return {"ok": False, "reason": "no_subscription"}

    sub = get_subscription(telegram_id)
    current_expires = datetime.fromisoformat(sub["expires_at"])
    now = _now()
    frozen_until = now + timedelta(days=days)
    new_expires = current_expires + timedelta(days=days)

    with _connect() as conn:
        conn.execute(
            "UPDATE subscriptions SET frozen_until = ?, expires_at = ? WHERE telegram_id = ?",
            (frozen_until.isoformat(), new_expires.isoformat(), telegram_id),
        )
    logger.info(
        "Подписка заморожена: telegram_id=%s на %s дней, до %s, новая дата окончания %s",
        telegram_id, days, frozen_until.isoformat(), new_expires.isoformat(),
    )
    return {"ok": True, "frozen_until": frozen_until.isoformat(), "new_expires_at": new_expires.isoformat()}


_init_db()
