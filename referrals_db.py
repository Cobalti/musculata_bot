"""
referrals_db.py — реферальная система «Пригласить соратника».
Правила — из ТЗ по подпискам (п. 3.4, таблица «Реферальная система»,
подтверждает и уточняет более раннюю пустую колонку из подписки.xlsx).

ПРАВИЛА:
    Количество приглашённых | Бонус пригласившему        | Бонус приглашённому
             1              | +1 месяц к подписке         | Скидка 20% на первую годовую подписку
             3              | +3 месяца + стикерпак       | Скидка 20% на первую годовую подписку
             6              | +6 месяцев + мерч-набор     | Скидка 20% на первую годовую подписку

ЧТО РАБОТАЕТ СЕЙЧАС:
  - персональная ссылка t.me/musculataclub_bot?start=<telegram_id>;
  - переход по ссылке фиксирует связь «кто кого пригласил» (навсегда,
    один пригласивший на человека, повторно не перезаписывается);
  - самоприглашение отклоняется;
  - приглашённый получает скидку 20% на ПЕРВУЮ годовую подписку;
  - скидка сгорает после того, как подписка оплачена (статус converted) —
    второй раз та же связь не даёт скидку;
  - счётчик приглашённых у пригласившего растёт по факту КОНВЕРСИИ
    (оплаченной подписки приглашённого), а не по факту перехода
    по ссылке — иначе накрутить ступени было бы тривиально;
  - при достижении ступени 1 / 3 / 6 пригласивший получает уведомление,
    подписка автоматически продлевается на нужное число дней
    (см. subscriptions_db.extend_subscription), а стикерпак/мерч —
    текстовое напоминание админу (физическая отправка — вручную,
    бот сам ничего не производит и не доставляет).

Лимита на число приглашений НЕТ — ступени идут до 6, ограничение сверху
не заявлено (прошлое требование «максимум 3» отменено новой таблицей;
если лимит всё же нужен — вернуть проверку в register_referral).

СХЕМА:
    referrals (
        invitee_id    -- PK, кого пригласили
        referrer_id   -- кто пригласил
        status        -- 'pending' (перешёл, ещё не оплатил подписку)
                      -- 'converted' (оплатил первую подписку, ступень засчитана)
        invited_at
        converted_at
    )
    milestones_reached (
        referrer_id + milestone -- PK (составной), чтобы не уведомлять дважды
        reached_at
    )
"""

import sqlite3
import os
import logging
from datetime import datetime, timezone
from contextlib import contextmanager

logger = logging.getLogger("referrals_db")

DB_PATH = os.path.join(os.path.dirname(__file__), "referrals.db")

# Скидка приглашённому на первую годовую подписку — из Excel.
INVITEE_DISCOUNT_PERCENT = 20

# Ступени — из ТЗ по подпискам (п. 3.4), таблица «Реферальная система»:
#   1 приглашённый → +1 месяц к подписке (автопродление)
#   3 приглашённых → +3 месяца + эксклюзивный стикерпак
#   6 приглашённых → +6 месяцев + фирменный мерч-набор (футболка/шейкер)
# extra_days начисляется автоматически (реально продлевает подписку —
# см. subscriptions_db.extend_subscription). merch_gift — просто текст
# для уведомления, физическую отправку мерча/стикеров делает человек
# (бот только напоминает администратору).
MILESTONE_REWARDS: dict[int, dict] = {
    1: {"extra_days": 30, "merch_gift": None},
    3: {"extra_days": 90, "merch_gift": "эксклюзивный стикерпак"},
    6: {"extra_days": 180, "merch_gift": "фирменный мерч-набор (футболка/шейкер)"},
}
MILESTONES = sorted(MILESTONE_REWARDS.keys())


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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ref_referrer ON referrals(referrer_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS milestones_reached (
                referrer_id INTEGER NOT NULL,
                milestone   INTEGER NOT NULL,
                reached_at  TEXT NOT NULL,
                PRIMARY KEY (referrer_id, milestone)
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


# ---------- Связи ----------

def get_referral(invitee_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM referrals WHERE invitee_id = ?", (invitee_id,)).fetchone()
    return dict(row) if row else None


def register_referral(referrer_id: int, invitee_id: int) -> tuple[bool, str]:
    """
    Фиксирует связь «referrer_id пригласил invitee_id».
    Возвращает (успех, причина) — причина для логов.

    Отказ, если: приглашает сам себя, либо у приглашённого уже есть
    пригласивший (один на человека, навсегда).
    """
    if referrer_id == invitee_id:
        return False, "self_referral"
    if get_referral(invitee_id) is not None:
        return False, "already_has_referrer"

    with _connect() as conn:
        conn.execute(
            "INSERT INTO referrals (invitee_id, referrer_id, status, invited_at) VALUES (?, ?, 'pending', ?)",
            (invitee_id, referrer_id, _now()),
        )
    logger.info("Реферальная связь: referrer=%s invitee=%s", referrer_id, invitee_id)
    return True, "ok"


# ---------- Скидка приглашённому ----------

def has_pending_invitee_discount(invitee_id: int) -> bool:
    """
    True, если этот пользователь пришёл по чьей-то ссылке и ещё НЕ оплатил
    свою первую подписку — значит, ему полагается скидка 20%.
    """
    ref = get_referral(invitee_id)
    return ref is not None and ref["status"] == "pending"


# ---------- Конверсия и ступени ----------

def count_converted(referrer_id: int) -> int:
    """Сколько приглашённых РЕАЛЬНО оплатили подписку (ступени считаются по этому числу)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM referrals WHERE referrer_id = ? AND status = 'converted'",
            (referrer_id,),
        ).fetchone()
    return row["c"]


def count_pending(referrer_id: int) -> int:
    """Сколько перешли по ссылке, но ещё не оплатили подписку."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM referrals WHERE referrer_id = ? AND status = 'pending'",
            (referrer_id,),
        ).fetchone()
    return row["c"]


def _mark_milestone(referrer_id: int, milestone: int) -> bool:
    """Отмечает ступень достигнутой. False, если она уже была отмечена раньше."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO milestones_reached (referrer_id, milestone, reached_at) VALUES (?, ?, ?)",
                (referrer_id, milestone, _now()),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def mark_converted(invitee_id: int) -> dict | None:
    """
    Вызывается, когда приглашённый оплатил свою ПЕРВУЮ подписку.

    Переводит связь в 'converted' (скидка 20% больше не действует) и
    проверяет, не достиг ли пригласивший новой ступени.

    Возвращает None, если связи нет или она уже была converted
    (защита от повторного вебхука). Иначе:
        {
          "referrer_id": int,
          "converted_count": int,        # сколько всего оплативших привёл
          "milestone_reached": int|None, # какая ступень взята прямо сейчас
          "reward": dict|None,           # {"extra_days": int, "merch_gift": str|None}
        }
    Продление подписки на extra_days НЕ делается здесь — эта функция
    только про учёт рефералов, реальное продление вызывает код,
    получивший этот результат (см. webhooks.py, _handle_referral_conversion,
    который дальше зовёт subscriptions_db.extend_subscription).
    """
    ref = get_referral(invitee_id)
    if not ref or ref["status"] != "pending":
        return None

    referrer_id = ref["referrer_id"]
    with _connect() as conn:
        conn.execute(
            "UPDATE referrals SET status = 'converted', converted_at = ? WHERE invitee_id = ?",
            (_now(), invitee_id),
        )

    converted_count = count_converted(referrer_id)

    milestone_reached = None
    if converted_count in MILESTONE_REWARDS and _mark_milestone(referrer_id, converted_count):
        milestone_reached = converted_count

    logger.info(
        "Реферал конвертирован: invitee=%s referrer=%s всего=%s ступень=%s",
        invitee_id, referrer_id, converted_count, milestone_reached,
    )
    return {
        "referrer_id": referrer_id,
        "converted_count": converted_count,
        "milestone_reached": milestone_reached,
        "reward": MILESTONE_REWARDS.get(milestone_reached) if milestone_reached else None,
    }


def next_milestone(converted_count: int) -> int | None:
    """Следующая непройденная ступень — для показа прогресса пользователю."""
    for m in MILESTONES:
        if converted_count < m:
            return m
    return None


_init_db()
