"""
referrals_db.py — реферальная система "Пригласить соратника".

ПРАВИЛА (зафиксированы по ТЗ):
  - У каждого пользователя своя ссылка: t.me/<bot_username>?start=<telegram_id>
  - Перешёл по ссылке — бот запоминает, кто пригласил (один раз, навсегда:
    если у человека уже есть пригласивший — повторно не перезаписываем).
  - Пригласить может максимум ТРЁХ человек — четвёртый переход по ссылке
    просто не создаёт реферальную связь (человек всё равно может
    пользоваться ботом как обычно).
  - Реферальная связь фиксируется ТОЛЬКО для совсем новых пользователей
    (ещё не проходивших согласие на ОПД) — иначе теряется смысл "привёл
    нового клиента".
  - При ПЕРВОМ ОПЛАЧЕННОМ заказе приглашённого:
      • пригласившему начисляется 500 ₽ бонуса (см. ВАЖНО ниже про то,
        что это пока только бот-side учёт);
      • статус связи меняется на 'converted' — второй раз бонус за этого
        же приглашённого не начисляется, даже если у него будет ещё заказ.
  - Скидка 10% приглашённому на первый заказ — реализована через промокод
    (см. main.py, REFERRAL_INVITEE_PROMO), применяется при оформлении,
    пока связь ещё 'pending'.

ВАЖНО — про 500 ₽ бонуса пригласившему:
Это ПОКА чисто бот-side цифра (таблица credits ниже) — бот знает и
показывает пользователю, что у него накоплено N рублей, но реально
СПИСАТЬ эту сумму с цены при оплате бот не может: оплата целиком идёт
через сайт (checkout_url от Фёдора), и только сайт решает финальную
цену. Чтобы бонус реально работал как скидка на будущий заказ, нужно
одно из:
  а) Фёдор заводит у себя баланс/кошелёк пользователя и наш бот сообщает
     ему через API "начислить N ₽ пользователю X";
  б) Фёдор даёт эндпоинт генерации одноразового купона на сумму N ₽,
     бот запрашивает купон, когда бонус начислен, и подставляет его код
     в поле promotions при следующем заказе.
Пока ни то ни другое не согласовано — это отдельный вопрос к Фёдору.
Функция get_balance() ниже уже готова показать эту сумму пользователю
("у тебя накоплено 500 ₽") — просто это накопление сейчас не с чем
состыковать на кассе.

СХЕМА:
    referrals (
        invitee_id     -- PK, кого пригласили (один пригласивший на человека)
        referrer_id    -- кто пригласил
        status         -- 'pending' / 'converted'
        invited_at
        converted_at
    )
    credits (
        id             -- PK автоинкремент
        referrer_id    -- кому начислено
        invitee_id     -- за кого начислено
        amount         -- сумма в рублях
        created_at
    )
"""

import sqlite3
import os
import logging
from datetime import datetime, timezone
from contextlib import contextmanager

logger = logging.getLogger("referrals_db")

DB_PATH = os.path.join(os.path.dirname(__file__), "referrals.db")
MAX_INVITES_PER_REFERRER = 3
REFERRAL_BONUS_RUB = 500


def _init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
                invitee_id   INTEGER PRIMARY KEY,
                referrer_id  INTEGER NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                invited_at   TEXT NOT NULL,
                converted_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS credits (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id   INTEGER NOT NULL,
                invitee_id    INTEGER NOT NULL,
                amount        INTEGER NOT NULL,
                created_at    TEXT NOT NULL
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def count_invites(referrer_id: int) -> int:
    """Сколько человек уже привязано к этому пригласившему (pending + converted)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM referrals WHERE referrer_id = ?", (referrer_id,)
        ).fetchone()
    return row["c"]


def can_invite_more(referrer_id: int) -> bool:
    return count_invites(referrer_id) < MAX_INVITES_PER_REFERRER


def get_referral(invitee_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM referrals WHERE invitee_id = ?", (invitee_id,)
        ).fetchone()
    return dict(row) if row else None


def register_referral(referrer_id: int, invitee_id: int) -> tuple[bool, str]:
    """
    Пытается зафиксировать связь "referrer_id пригласил invitee_id".

    Возвращает (успех: bool, причина: str). Причина нужна для логов/отладки,
    пользователю её показывать не обязательно дословно.

    Отказывает, если:
      - пытаются пригласить самого себя;
      - у invitee_id УЖЕ есть пригласивший (один пригласивший на человека,
        повторно не считаем — по ТЗ);
      - у referrer_id уже MAX_INVITES_PER_REFERRER приглашённых.
    """
    if referrer_id == invitee_id:
        return False, "self_referral"

    if get_referral(invitee_id) is not None:
        return False, "already_has_referrer"

    if not can_invite_more(referrer_id):
        return False, "referrer_limit_reached"

    with _connect() as conn:
        conn.execute(
            "INSERT INTO referrals (invitee_id, referrer_id, status, invited_at) VALUES (?, ?, 'pending', ?)",
            (invitee_id, referrer_id, _now()),
        )
    logger.info("Реферальная связь зафиксирована: referrer=%s invitee=%s", referrer_id, invitee_id)
    return True, "ok"


def mark_converted(invitee_id: int) -> int | None:
    """
    Вызывается при первом ОПЛАЧЕННОМ заказе приглашённого (см. webhooks.py,
    payment-success). Начисляет бонус пригласившему и переводит связь
    в статус 'converted' — повторно бонус за этого invitee не начислится.

    Возвращает telegram_id пригласившего, если бонус начислен, иначе None
    (нет связи, или она уже была converted раньше — защита от повторного
    начисления, если вебхук придёт дважды).
    """
    referral = get_referral(invitee_id)
    if not referral or referral["status"] != "pending":
        return None

    referrer_id = referral["referrer_id"]
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE referrals SET status = 'converted', converted_at = ? WHERE invitee_id = ?",
            (now, invitee_id),
        )
        conn.execute(
            "INSERT INTO credits (referrer_id, invitee_id, amount, created_at) VALUES (?, ?, ?, ?)",
            (referrer_id, invitee_id, REFERRAL_BONUS_RUB, now),
        )
    logger.info("Реферал конвертирован: referrer=%s invitee=%s +%s ₽", referrer_id, invitee_id, REFERRAL_BONUS_RUB)
    return referrer_id


def get_balance(referrer_id: int) -> int:
    """Сумма накопленных бонусов (в рублях) — см. примечание в шапке файла про интеграцию с сайтом."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM credits WHERE referrer_id = ?",
            (referrer_id,),
        ).fetchone()
    return row["total"]


_init_db()
