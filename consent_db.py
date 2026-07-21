"""
consent_db.py — согласие пользователя на обработку персональных данных.

Раньше жило только в памяти процесса (state.py, множество _consent_given)
и обнулялось при каждом перезапуске бота — пользователю приходилось
заново нажимать "Принимаю". Теперь, когда бот стоит на постоянном
сервере, согласие хранится в SQLite и переживает перезапуски.

Также поддерживает ОТЗЫВ согласия (раздел Настройки): если пользователь
отзывает согласие, бот должен полностью перестать отвечать ему на любые
действия, кроме повторного прохождения /start и нажатия "Принимаю" —
это уже обеспечивает errors.safe_handler(require_consent=True), который
теперь проверяет именно эту базу, а не state.py.

СХЕМА:
    consent (
        telegram_id  -- PK
        status       -- 'accepted' / 'revoked'
        accepted_at  -- когда впервые принял (или принял повторно после отзыва)
        revoked_at   -- когда в последний раз отозвал (NULL, если не отзывал)
    )
"""

import sqlite3
import os
import logging
from datetime import datetime, timezone
from contextlib import contextmanager

logger = logging.getLogger("consent_db")

DB_PATH = os.path.join(os.path.dirname(__file__), "consent.db")


def _init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS consent (
                telegram_id  INTEGER PRIMARY KEY,
                status       TEXT NOT NULL,
                accepted_at  TEXT,
                revoked_at   TEXT
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def has_consent(telegram_id: int) -> bool:
    """Главная проверка — используется в errors.safe_handler на каждый запрос."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM consent WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    return bool(row) and row["status"] == "accepted"


def give_consent(telegram_id: int) -> None:
    """Вызывается при нажатии 'Принимаю' — как при первом согласии, так и повторно после отзыва."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO consent (telegram_id, status, accepted_at, revoked_at)
            VALUES (?, 'accepted', ?, NULL)
            ON CONFLICT(telegram_id) DO UPDATE SET
                status = 'accepted',
                accepted_at = excluded.accepted_at
            """,
            (telegram_id, _now()),
        )
    logger.info("Согласие на ОПД дано: telegram_id=%s", telegram_id)


def revoke_consent(telegram_id: int) -> None:
    """Вызывается из Настроек — 'Отозвать согласие'. После этого бот блокирует пользователя."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE consent SET status = 'revoked', revoked_at = ?
            WHERE telegram_id = ?
            """,
            (_now(), telegram_id),
        )
    logger.info("Согласие на ОПД отозвано: telegram_id=%s", telegram_id)


_init_db()
