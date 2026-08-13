"""
order_access.py — автоматическая выдача и отзыв доступа в закрытый
канал и чат Сообщества по статусу членства.

ЕДИНАЯ ТОЧКА ПРАВДЫ: subscriptions_db.has_active_subscription() уже
учитывает и срок действия, и заморозку — сюда её логику не дублируем.
Вместо трёх отдельных мест (активация / истечение / разморозка) есть
одна функция reconcile_access(), которая сверяет "что должно быть"
с тем, что реально записано (access_granted), и приводит Telegram
в соответствие: выдаёт доступ там, где не хватает, забирает — где
просрочен или заморожен.

ТРЕБОВАНИЯ К БОТУ В TELEGRAM (настраивается руками, не кодом):
Бот должен быть администратором и в канале, и в чате Сообщества, с правами
минимум "приглашать участников" и "блокировать пользователей" — без
этого create_chat_invite_link/ban_chat_member/unban_chat_member просто
вернут ошибку доступа.

Если ORDER_CHANNEL_ID/ORDER_CHAT_ID не заданы в .env — весь модуль
тихо ничего не делает (это ожидаемо на этапе, пока канал/чат ещё не
заведены; не должно ронять остальной бот).
"""

import logging

from config import ORDER_CHANNEL_ID, ORDER_CHAT_ID
import subscriptions_db

logger = logging.getLogger("order_access")


def _configured() -> bool:
    return bool(ORDER_CHANNEL_ID and ORDER_CHAT_ID)


def grant_access(bot, telegram_id: int) -> bool:
    """
    Создаёт одноразовые пригласительные ссылки (member_limit=1 — чтобы
    ссылку нельзя было переслать и пустить постороннего) на канал и чат,
    присылает пользователю личным сообщением. Отмечает access_granted.

    Возвращает True при успехе. Если что-то пошло не так (бот не админ,
    канал ещё не настроен и т.п.) — логирует и возвращает False, не
    роняя вызывающий код.
    """
    if not _configured():
        logger.warning("ORDER_CHANNEL_ID/ORDER_CHAT_ID не заданы — выдача доступа пропущена")
        return False

    try:
        channel_link = bot.create_chat_invite_link(ORDER_CHANNEL_ID, member_limit=1).invite_link
        chat_link = bot.create_chat_invite_link(ORDER_CHAT_ID, member_limit=1).invite_link
    except Exception as e:
        logger.error("Не удалось создать пригласительные ссылки для telegram_id=%s: %s", telegram_id, e)
        return False

    try:
        bot.send_message(
            telegram_id,
            "🛡 <b>Доступ в сообщество открыт!</b>\n\n"
            f"Канал: {channel_link}\n"
            f"Общий чат: {chat_link}\n\n"
            "Ссылки одноразовые — переходи по ним сам, поделиться с кем-то не получится.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Не удалось отправить ссылки на канал/чат telegram_id=%s: %s", telegram_id, e)
        return False

    subscriptions_db.set_access_granted(telegram_id, True)
    logger.info("Доступ в канал/чат Сообщества выдан: telegram_id=%s", telegram_id)
    return True


def revoke_access(bot, telegram_id: int) -> bool:
    """
    Убирает пользователя из канала и чата — ban_chat_member сразу же
    unban_chat_member (only_if_banned=True), чтобы это было именно
    "исключить сейчас", а не перманентный бан: если членство потом
    возобновится, пользователь сможет зайти по новой ссылке.

    Не страшно, если пользователя там уже не было (например, сам вышел
    заранее) — Telegram в этом случае просто вернёт ошибку, которую
    тихо логируем и продолжаем считать доступ отозванным.
    """
    if not _configured():
        return False

    for chat_id in (ORDER_CHANNEL_ID, ORDER_CHAT_ID):
        try:
            bot.ban_chat_member(chat_id, telegram_id)
            bot.unban_chat_member(chat_id, telegram_id, only_if_banned=True)
        except Exception as e:
            # Частый и безобидный случай — пользователя там и так не было.
            logger.info(
                "Не удалось исключить telegram_id=%s из chat_id=%s (возможно, его там и не было): %s",
                telegram_id, chat_id, e,
            )

    subscriptions_db.set_access_granted(telegram_id, False)
    logger.info("Доступ в канал/чат Сообщества отозван: telegram_id=%s", telegram_id)
    return True


def reconcile_access(bot) -> None:
    """
    Сверяет всех участников сообщества с реальным статусом доступа и приводит
    Telegram в соответствие. Вызывается периодически (см. run.py) —
    покрывает все три случая разом: свежую активацию, истечение срока,
    заморозку и разморозку.
    """
    if not _configured():
        return

    mismatches = subscriptions_db.get_access_mismatches()
    if not mismatches:
        return

    logger.info("Сверка доступа в Сообщество: %s расхождений", len(mismatches))
    for item in mismatches:
        telegram_id = item["telegram_id"]
        try:
            if item["should_have_access"]:
                grant_access(bot, telegram_id)
            else:
                revoke_access(bot, telegram_id)
        except Exception:
            # Один упавший пользователь не должен прерывать сверку для
            # остальных — логируем и идём дальше.
            logger.exception("Ошибка при сверке доступа для telegram_id=%s", telegram_id)
