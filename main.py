import logging_setup  # noqa: F401  — настраивает логирование в файл при импорте, должен быть первым

import logging
import os
import telebot
from telebot import types

from config import BOT_TOKEN, ADMIN_CHAT_ID
from products import PRODUCTS_BY_ID, category_by_index
import keyboards
from keyboards import ALL_CATEGORIES
import cart
import state
import analytics
from legal import CONSENT_TEXT
from notifier import notify_admin
from errors import safe_handler
from checkout import price_breakdown
from integrations import create_order
import orders_db
import subscriptions_db
import subscription_tiers
import referrals_db
import consent_db
import health_check
import pack4_db
import payments
import order_access
import emoji_ui
import emoji_ids
import packs

logger = logging.getLogger("main")
bot = telebot.TeleBot(BOT_TOKEN)

# Пока у нас нет реальных фотографий товаров — используем общую
# картинку-заглушку. Когда будут настоящие фото, достаточно будет
# добавить в products.json поле "image" (путь к файлу) и слегка
# доработать handle_view_product, чтобы брать картинку из товара.
PLACEHOLDER_IMAGE_PATH = os.path.join(os.path.dirname(__file__), "media", "placeholder.png")

# Тексты всех кнопок постоянного меню — используется, чтобы отличить
# "пользователь печатает вопрос в поддержку" от "пользователь нажал
# другую кнопку меню" (см. handle_support_message ниже).
MENU_BUTTON_TEXTS = {
    keyboards.BTN_CATALOG,
    keyboards.BTN_ORDER_SUBSCRIPTION,
    keyboards.BTN_CART,
    keyboards.BTN_INVITE,
    keyboards.BTN_ORDERS,
    keyboards.BTN_SETTINGS,
}


# ---------- Вспомогательные функции ----------

def delete_user_message(message):
    """
    Удаляет сообщение, которое пользователь отправил нажатием на кнопку
    меню (текст кнопки прилетает боту как обычное входящее сообщение).
    Bot API разрешает ботам удалять входящие сообщения в личных чатах —
    это официально поддерживаемая возможность, не хак.
    """
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass


def show_content(chat_id, user_id, text, reply_markup=None, parse_mode=None):
    """
    Показывает "экран" (каталог/корзина/заглушка), удаляя предыдущий.

    reply_markup может быть либо обычным telebot-объектом
    (InlineKeyboardMarkup), либо словарём для эмодзи-кнопок из emoji_ui
    (форма {"inline_keyboard": [[{...}]]}). В случае словаря отправка идёт
    через прямой Bot API (emoji_ui), чтобы эмодзи на кнопках отобразились.

    ВАЖНО: эти сообщения НИКОГДА не несут ReplyKeyboardMarkup — поэтому
    постоянное меню внизу (отправленное один раз при первом /start)
    вообще не затрагивается ни при каких переходах между разделами.

    parse_mode: "HTML" нужен там, где в тексте используются кастомные
    эмодзи через <tg-emoji emoji-id="...">заглушка</tg-emoji>.
    Для эмодзи-словарей parse_mode всегда HTML (emoji_ui сам его ставит).
    """
    old_id = state.get_content(user_id)
    if old_id:
        try:
            bot.delete_message(chat_id, old_id)
        except Exception:
            pass

    if isinstance(reply_markup, dict):
        # эмодзи-словарь → отправляем через прямой API
        result = emoji_ui.send_message_with_emoji(chat_id, text, reply_markup=reply_markup)
        if result.get("ok"):
            state.set_content(user_id, result["result"]["message_id"])
        return result

    msg = bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
    state.set_content(user_id, msg.message_id)
    return msg


def edit_content(chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    """
    Редактирует существующее сообщение. Работает и с обычным telebot-объектом,
    и со словарём эмодзи-клавиатуры — сам роутит в нужный API.
    """
    if isinstance(reply_markup, dict):
        return emoji_ui.edit_message_with_emoji(chat_id, message_id, text, reply_markup=reply_markup)
    try:
        return bot.edit_message_text(
            text, chat_id, message_id,
            reply_markup=reply_markup, parse_mode=parse_mode,
        )
    except Exception as e:
        logger.warning("edit_content не смог отредактировать сообщение: %s", e)
        return None


def edit_or_resend(chat_id, user_id, message_id, text, reply_markup=None):
    """
    Пытается отредактировать сообщение (быстро, без "мигания" интерфейса).
    Если не получилось — например, Telegram вернул "message to edit not
    found" (сообщение удалено, слишком старое, или пользователь тапнул
    по устаревшей клавиатуре) — удаляет то, что осталось, и отправляет
    новое сообщение взамен, обновляя state.content на его message_id.

    Без этого запасного пути пользователь просто не видел бы обновления
    экрана вообще: колбэк отвечен, а редактирование молча проваливается.
    """
    result = emoji_ui.edit_message_with_emoji(chat_id, message_id, text, reply_markup=reply_markup)
    if result.get("ok"):
        return result

    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass
    sent = emoji_ui.send_message_with_emoji(chat_id, text, reply_markup=reply_markup)
    if sent.get("ok"):
        state.set_content(user_id, sent["result"]["message_id"])
    return sent


def _handle_referral_start_param(message):
    """
    Разбирает /start с параметром из ссылки t.me/<bot>?start=<referrer_id>
    (Telegram превращает такую ссылку в текст сообщения "/start 123456789").

    Реферальная связь фиксируется ТОЛЬКО для совсем новых пользователей —
    если у этого telegram_id уже есть согласие на ОПД (то есть он раньше
    пользовался ботом), это не "новый клиент", связь не создаём.
    """
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return
    param = parts[1].strip()
    if not param.isdigit():
        return

    referrer_id = int(param)
    invitee_id = message.from_user.id

    if consent_db.has_consent(invitee_id):
        return

    success, reason = referrals_db.register_referral(referrer_id, invitee_id)
    if not success:
        logger.info(
            "Реферальная связь не создана (referrer=%s invitee=%s): %s",
            referrer_id, invitee_id, reason,
        )
        return

    analytics.log_event(invitee_id, message.from_user.username, "referral_registered", f"referrer={referrer_id}")

    _sword = f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji>'
    try:
        bot.send_message(
            referrer_id,
            f"{_sword} <b>По твоей ссылке пришёл новый соратник!</b>\n\n"
            f"Он получит скидку {referrals_db.INVITEE_DISCOUNT_PERCENT}% на первую "
            f"оплату вступления в сообщество. Как только он вступит — это засчитается тебе.",
            parse_mode="HTML",
        )
    except Exception:
        logger.warning("Не удалось уведомить пригласившего user_id=%s", referrer_id)


# ---------- Старт: согласие на обработку ПД + меню создаётся один раз ----------

@bot.message_handler(commands=["start"])
@safe_handler(bot, require_consent=False)
def start_message(message):
    # /start в общем чате/канале Сообщества (где бот теперь администратор,
    # см. order_access.py) не должен запускать личный сценарий согласия
    # на ОПД и открытия меню — это всё имеет смысл только в личном диалоге.
    if getattr(message.chat, "type", "private") != "private":
        return

    user_id = message.from_user.id
    delete_user_message(message)
    state.clear_content(user_id)
    state.clear_awaiting_support(user_id)

    analytics.log_event(user_id, message.from_user.username, "start")

    _handle_referral_start_param(message)

    if not consent_db.has_consent(user_id):
        # Жёсткий вариант согласия: без нажатия кнопки "Принимаю" бот
        # дальше меню не показывает вообще (see errors.safe_handler —
        # он же блокирует и остальные разделы для этого пользователя).
        #
        # ВАЖНО: раньше повторный /start слал только текстовое напоминание
        # "нажми кнопку выше", полагаясь на то, что кнопка из первого
        # сообщения ещё видна и работает. На практике поймали реальный
        # случай, когда кнопка не дошла до пользователя вообще (см. фикс
        # в emoji_ui.py про несуществующее поле "style") — человек писал
        # /start пять раз подряд и просто утыкался в напоминание про
        # кнопку, которой физически не было. Теперь при КАЖДОМ /start без
        # согласия шлём заново полное сообщение с кнопкой — это чуть более
        # многословно в чате, зато гарантированно рабочий путь вперёд.
        result = emoji_ui.send_message_with_emoji(
            message.chat.id, CONSENT_TEXT,
            reply_markup=keyboards.consent_keyboard_dict(),
        )
        if result.get("ok"):
            state.set_consent_message(user_id, result["result"]["message_id"])
        return

    # Меню пересоздаём при КАЖДОМ /start — не только при первом обращении.
    # Раньше меню создавалось один раз и больше не трогалось, из расчёта,
    # что оно просто остаётся в чате навсегда. Но если пользователь сам
    # чистит историю чата на своём устройстве — бот никак не может об
    # этом узнать (Telegram не шлёт боту такое событие), и старое меню
    # визуально пропадает, а бот считает, что оно всё ещё есть, и не
    # присылает новое. Итог — пользователь остаётся с пустым чатом без
    # меню вообще. Поэтому теперь /start ВСЕГДА гарантированно показывает
    # свежее меню: если старое ещё живо — удаляем и заменяем; если его уже
    # нет (чат чистили) — попытка удаления просто тихо ни на что не влияет.
    old_menu_id = state.get_menu(user_id)
    if old_menu_id:
        try:
            bot.delete_message(message.chat.id, old_menu_id)
        except Exception:
            pass

    menu_msg = bot.send_message(
        message.chat.id,
        f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji> <b>Приветствую, соратник!</b>\n\n'
        f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji> Перед тобой снаряжение для покорения новых вершин силы.\n'
        f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji> Меню внизу — твой компас, оно всегда под рукой.',
        reply_markup=keyboards.main_menu(),
        parse_mode="HTML",
    )
    state.set_menu(user_id, menu_msg.message_id)


@bot.callback_query_handler(func=lambda c: c.data == "consent_accept")
@safe_handler(bot, require_consent=False)
def handle_consent_accept(call):
    """Пользователь нажал '✅ Принимаю' — фиксируем согласие и открываем меню."""
    user_id = call.from_user.id
    consent_db.give_consent(user_id)
    analytics.log_event(user_id, call.from_user.username, "consent_accepted")
    bot.answer_callback_query(call.id, "Принято ⚔")

    # Убираем кнопку из уже показанного уведомления и помечаем его принятым —
    # само уведомление остаётся в чате навсегда, как юридический текст.
    _news = f'<tg-emoji emoji-id="{emoji_ids.NEWS}">🗞</tg-emoji>'
    emoji_ui.edit_message_with_emoji(
        call.message.chat.id, call.message.message_id,
        CONSENT_TEXT + f"\n\n{_news} <b>Согласие получено. Спасибо!</b>",
        reply_markup={"inline_keyboard": []},
    )

    if not state.get_menu(user_id):
        menu_msg = bot.send_message(
            call.message.chat.id,
            f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji> <b>Приветствую, соратник!</b>\n\n'
            f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji> Перед тобой снаряжение для покорения новых вершин силы.\n'
            f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji> Меню внизу — твой компас, оно всегда под рукой.',
            reply_markup=keyboards.main_menu(),
            parse_mode="HTML",
        )
        state.set_menu(user_id, menu_msg.message_id)


# ---------- Каталог: сначала выбор категории ----------

@bot.message_handler(func=lambda m: m.text == keyboards.BTN_CATALOG)
@safe_handler(bot)
def handle_catalog_button(message):
    delete_user_message(message)
    state.clear_awaiting_support(message.from_user.id)
    show_content(
        message.chat.id,
        message.from_user.id,
        f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji> Выбери категорию снаряжения:',
        reply_markup=keyboards.categories_keyboard_dict(),
        parse_mode="HTML",
    )


@bot.callback_query_handler(func=lambda c: c.data == "catlist")
@safe_handler(bot)
def handle_catlist(call):
    bot.answer_callback_query(call.id)
    edit_content(
        call.message.chat.id,
        call.message.message_id,
        f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji> Выбери категорию снаряжения:',
        reply_markup=keyboards.categories_keyboard_dict(),
    )


# ---------- Военные Сундуки (паки) — в каталоге ----------
# Готовые наборы товаров со скидкой 15% против розницы. Живут в каталоге,
# подписка для покупки НЕ нужна — её может купить кто угодно. Присягнувшим
# участникам сообщества полагается дополнительная скидка 5/10/15% в зависимости от уровня
# (см. subscription_tiers.pack_discount_for).


def _pack_intro_text(tier_id) -> str:
    _shield = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
    _sword = f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji>'
    _diamond = f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'

    lines = [
        f"{_shield} <b>Военные Сундуки</b>\n",
        f"{_sword} Готовые наборы снаряжения — всё нужное в одном сундуке, "
        f"собрано под конкретный ранг воина.",
        f"{_diamond} Каждый сундук уже дешевле на <b>15%</b>, чем те же товары "
        f"поштучно.",
    ]

    discount = subscription_tiers.pack_discount_for(tier_id)
    if discount:
        tier = subscription_tiers.get_tier(tier_id)
        lines.append(
            f"\n{_diamond} <b>Твоё членство «{tier['name']}» даёт ещё "
            f"−{int(discount * 100)}%</b> — цены ниже уже с учётом этого."
        )
    else:
        lines.append(
            f"\n{_shield} <i>Участники сообщества получают на сундуки "
            f"дополнительную скидку до 15%.</i>"
        )

    lines.append("\nВыбери свой:")
    return "\n".join(lines)


@bot.callback_query_handler(func=lambda c: c.data == "packs_list")
@safe_handler(bot)
def handle_packs_list(call):
    """Список сундуков. Точка входа — кнопка внизу категорий каталога."""
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    tier_id = subscriptions_db.get_active_tier_id(user_id)
    analytics.log_event(user_id, call.from_user.username, "view_packs")

    text = _pack_intro_text(tier_id)
    kb = keyboards.packs_list_keyboard_dict(tier_id)
    result = emoji_ui.edit_message_with_emoji(
        call.message.chat.id, call.message.message_id, text, reply_markup=kb,
    )
    if not result.get("ok"):
        # пришли с фото-экрана (карточка товара) — edit не сработает
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        sent = emoji_ui.send_message_with_emoji(call.message.chat.id, text, reply_markup=kb)
        if sent.get("ok"):
            state.set_content(user_id, sent["result"]["message_id"])


def _pack_detail_text(pack: dict, tier_id) -> str:
    """Карточка сундука: состав, розница, цена, экономия, скидка за членство в сообществе."""
    _shield = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
    _sword = f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji>'
    _diamond = f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'
    _scroll = f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji>'

    final_price = packs.price_for(pack["id"], tier_id)
    discount = subscription_tiers.pack_discount_for(tier_id)

    lines = [
        f"{_shield} <b>Сундук «{pack['name']}»</b>",
        f"<i>{pack['tagline']}</i>",
        "",
        f"{_scroll} <b>Что внутри:</b>",
    ]
    for item in pack["items"]:
        lines.append(f"{_sword} {item['name']} <i>({item['brand']})</i> — {item['price']} ₽")

    lines += [
        "",
        f"Поштучно в рознице: <s>{pack['retail_total']} ₽</s>",
    ]

    if discount:
        tier = subscription_tiers.get_tier(tier_id)
        total_pct = packs.total_discount_percent(tier_id)
        lines += [
            f"{_diamond} <b>Твоя цена: {final_price} ₽</b> "
            f"<i>(−{total_pct}% — базовая скидка 15% + членство «{tier['name']}»)</i>",
            f"Экономия: <b>{pack['retail_total'] - final_price} ₽</b>",
        ]
    else:
        lines += [
            f"{_diamond} <b>Цена сундука: {final_price} ₽</b>",
            f"Экономия: <b>{pack['retail_total'] - final_price} ₽</b> (−{packs.total_discount_percent(None)}%)",
            "",
            f"{_shield} <i>Вступив в сообщество, этот сундук стоил бы "
            f"от {packs.price_for(pack['id'], subscription_tiers.TIERS[0]['id'])} ₽.</i>",
        ]

    if pack.get("gift"):
        lines.append(f"\n{_diamond} <b>Бонус:</b> {pack['gift']}")

    return "\n".join(lines)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pack:"))
@safe_handler(bot)
def handle_pack_detail(call):
    pack_id = int(call.data.split(":")[1])
    pack = packs.get_pack(pack_id)
    if not pack:
        bot.answer_callback_query(call.id, "Такого сундука нет")
        return

    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    tier_id = subscriptions_db.get_active_tier_id(user_id)
    analytics.log_event(user_id, call.from_user.username, "view_pack", pack["name"])

    edit_or_resend(
        call.message.chat.id, user_id, call.message.message_id,
        _pack_detail_text(pack, tier_id),
        reply_markup=keyboards.pack_detail_keyboard_dict(pack_id, user_id),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("pack_add:"))
@safe_handler(bot)
def handle_pack_add(call):
    """Кладёт сундук в корзину — доступно всем, членство в сообществе не требуется."""
    pack_id = int(call.data.split(":")[1])
    pack = packs.get_pack(pack_id)
    if not pack:
        bot.answer_callback_query(call.id, "Такого сундука нет")
        return

    user_id = call.from_user.id
    cart.add_item(user_id, pack_id, qty=1)
    analytics.log_event(user_id, call.from_user.username, "pack_added", pack["name"])
    bot.answer_callback_query(call.id, f"Сундук «{pack['name']}» в корзине ⚔")

    # обновляем клавиатуру, чтобы показать счётчик в корзине
    import requests as _rq
    _rq.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageReplyMarkup",
        json={
            "chat_id": call.message.chat.id,
            "message_id": call.message.message_id,
            "reply_markup": keyboards.pack_detail_keyboard_dict(pack_id, user_id),
        },
        timeout=10,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("cat:"))
@safe_handler(bot)
def handle_category_page(call):
    bot.answer_callback_query(call.id)
    _, cat_idx, page = call.data.split(":")
    cat_idx = int(cat_idx)
    page = int(page)

    category_name = category_by_index(cat_idx) if cat_idx != ALL_CATEGORIES else None
    _sc = f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji>'
    header = f"{_sc} Категория: {category_name}" if category_name else f"{_sc} Все товары:"

    if page == 0:
        analytics.log_event(
            call.from_user.id, call.from_user.username, "view_category", category_name or "Все товары"
        )

    # Сюда попадаем и с обычной пагинации (текстовое сообщение), и с
    # кнопки "К каталогу" внутри карточки товара (там сообщение с фото).
    # edit_message_text не умеет превращать фото-сообщение в текстовое,
    # поэтому пытаемся сначала edit — если не вышло, удаляем и отправляем
    # новое текстовое.
    kb = keyboards.catalog_page_keyboard_dict(page, call.from_user.id, cat_idx)
    result = emoji_ui.edit_message_with_emoji(
        call.message.chat.id, call.message.message_id, header, reply_markup=kb,
    )
    if not result.get("ok"):
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        sent = emoji_ui.send_message_with_emoji(call.message.chat.id, header, reply_markup=kb)
        if sent.get("ok"):
            state.set_content(call.from_user.id, sent["result"]["message_id"])


@bot.callback_query_handler(func=lambda c: c.data.startswith("view:"))
@safe_handler(bot)
def handle_view_product(call):
    _, pid, page, cat_idx = call.data.split(":")
    product = PRODUCTS_BY_ID.get(int(pid))
    if not product:
        bot.answer_callback_query(call.id, "Товар не найден")
        return

    bot.answer_callback_query(call.id)
    analytics.log_event(
        call.from_user.id,
        call.from_user.username,
        "view_product",
        f"{product['name']} ({product['price']} ₽)",
    )

    # У сообщений с фото и без фото в Telegram разный тип — простой
    # edit_message_text не может добавить картинку туда, где её не было.
    # Поэтому удаляем старое "экранное" сообщение и отправляем новое,
    # уже с фотографией. Это единственный корректный способ показать
    # изображение в карточке товара без "мигания" нижнего меню.
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

    caption = (
        f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji> <b>{product["name"]}</b>\n'
        f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji> Цена: <b>{product["price"]} ₽</b>'
    )
    kb = keyboards.product_card_keyboard_dict(
        product["id"], int(page), int(cat_idx), call.from_user.id
    )
    with open(PLACEHOLDER_IMAGE_PATH, "rb") as photo:
        result = emoji_ui.send_photo_with_emoji(
            call.message.chat.id, photo, caption_html=caption, reply_markup=kb,
        )
    if result.get("ok"):
        state.set_content(call.from_user.id, result["result"]["message_id"])


@bot.callback_query_handler(func=lambda c: c.data.startswith("add:"))
@safe_handler(bot)
def handle_add_to_cart(call):
    _, pid, page, cat_idx = call.data.split(":")
    user_id = call.from_user.id
    cart.add_item(user_id, int(pid), qty=1)

    product = PRODUCTS_BY_ID.get(int(pid))
    if product:
        analytics.log_event(user_id, call.from_user.username, "add_to_cart", product["name"])

    bot.answer_callback_query(call.id, "Добавлено ⚔")

    # Обновляем только клавиатуру под карточкой товара — через прямой
    # API, потому что клавиатура теперь эмодзи-словарь (не telebot-объект).
    import json as _json
    import requests as _rq
    _rq.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageReplyMarkup",
        json={
            "chat_id": call.message.chat.id,
            "message_id": call.message.message_id,
            "reply_markup": keyboards.product_card_keyboard_dict(int(pid), int(page), int(cat_idx), user_id),
        },
        timeout=10,
    )


# ---------- Корзина ----------

@bot.message_handler(func=lambda m: m.text == keyboards.BTN_CART)
@safe_handler(bot)
def handle_cart_button(message):
    delete_user_message(message)
    state.clear_awaiting_support(message.from_user.id)
    user_id = message.from_user.id
    show_content(
        message.chat.id,
        user_id,
        cart.cart_text(user_id),
        reply_markup=keyboards.cart_keyboard_dict(user_id),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("remove:"))
@safe_handler(bot)
def handle_remove_item(call):
    pid = int(call.data.split(":")[1])
    cart.remove_item(call.from_user.id, pid)
    bot.answer_callback_query(call.id, "Убрано из корзины")
    edit_or_resend(
        call.message.chat.id, call.from_user.id, call.message.message_id,
        cart.cart_text(call.from_user.id),
        reply_markup=keyboards.cart_keyboard_dict(call.from_user.id),
    )


@bot.callback_query_handler(func=lambda c: c.data == "checkout")
@safe_handler(bot)
def handle_checkout(call):
    """
    Оформление заказа через реальную интеграцию с сайтом (integrations.py).

    Раньше здесь была локальная генерация условной ссылки
    (build_checkout_url) — теперь бот действительно отправляет корзину
    на сайт Фёдора, получает order_id и готовую ссылку на оплату.

    ВАЖНО: пока Фёдор не пришлёт X_BOT_TOKEN, эта функция технически
    рабочая, но реальный запрос будет падать с ошибкой — это ожидаемо,
    интеграция не активна до получения токена (см. integrations.py).
    """
    user_id = call.from_user.id
    user_cart = cart.get_cart(user_id)

    if not user_cart:
        bot.answer_callback_query(call.id, "Корзина пуста")
        return

    bot.answer_callback_query(call.id)
    items_list = list(user_cart.keys())

    # Реферальная скидка 20% по Excel даётся на ПРИСЯГУ, а не на обычный
    # заказ — поэтому здесь всегда стандартный промокод бота. НО: по
    # документации сайта (wp_endpoints_api.pdf) обычный купон
    # ДОПОЛНИТЕЛЬНО К ПАКУ НЕ ПРИМЕНЯЕТСЯ — скидка на пак (15% база +
    # скидка уровня членства) считается сайтом отдельно и самостоятельно.
    # Если в корзине есть хоть один пак — промокод не отправляем вообще,
    # чтобы не запутать сайт конфликтующими скидками.
    has_pack_in_cart = any(packs.is_pack_id(item_id) for item_id in items_list)
    promo_code = None if has_pack_in_cart else "TELEGRAM10"

    response = create_order(
        telegram_id=user_id,
        items=items_list,
        promotions=promo_code,
    )

    if response.get("status") == "error" or not response.get("checkout_url"):
        analytics.log_event(user_id, call.from_user.username, "checkout_failed", "site error")
        _sword = f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji>'
        show_content(
            call.message.chat.id,
            user_id,
            f"{_sword} <b>Не удалось оформить заказ.</b>\n\n"
            "Сайт временно недоступен, либо интеграция ещё не настроена. "
            "Попробуй чуть позже — или напиши в поддержку "
            "(Настройки → Поддержка).",
            parse_mode="HTML",
        )
        # Колбэк уже подтверждён выше (до create_order) — здесь просто
        # завершаем обработку, повторно отвечать на тот же callback_query
        # нельзя (Telegram отклонит второй ответ).
        return

    order_id = response.get("order_id")
    checkout_url = response.get("checkout_url")
    missing_reported = response.get("missing_items_reported", False)

    # Сохраняем заказ в своей БД — это единственное место, где будет
    # жить история покупок пользователя (личного кабинета на сайте не будет).
    orders_db.create_order_record(
        telegram_id=user_id,
        site_order_id=order_id,
        checkout_url=checkout_url,
        items=items_list,
    )

    analytics.log_event(user_id, call.from_user.username, "checkout", f"Order #{order_id}")

    warning = (
        f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji> '
        "Некоторые товары оказались недоступны — счёт сформирован "
        "только на доступные позиции.\n\n"
        if missing_reported else ""
    )
    _diamond = f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'
    text = (
        f"{warning}{_diamond} <b>Заказ #{order_id} сформирован!</b>\n\n"
        "Нажми кнопку ниже, чтобы завершить оплату на сайте."
    )

    # Кнопка оплаты — зелёная (style="success"), с фирменным эмодзи 💎
    keyboard = emoji_ui.build_emoji_keyboard([[
        emoji_ui.build_emoji_button(
            "Перейти к оплате", url=checkout_url,
            style="success", icon_custom_emoji_id=emoji_ids.DIAMOND,
        )
    ]])
    result = emoji_ui.send_message_with_emoji(call.message.chat.id, text, reply_markup=keyboard)
    if result.get("ok"):
        state.set_content(user_id, result["result"]["message_id"])
    else:
        # Fallback на обычную кнопку, если Telegram не принял (например,
        # владелец бота ещё без Premium — тогда icon_custom_emoji_id
        # игнорируется, но само сообщение всё равно должно уйти).
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Перейти к оплате", url=checkout_url))
        show_content(call.message.chat.id, user_id, text, reply_markup=markup, parse_mode="HTML")


    # Корзину чистим сразу после успешного создания заказа на сайте —
    # дальнейший статус (оплачен/нет) отслеживается через вебхук
    # payment-success, который обновит запись в orders_db.
    cart.clear_cart(user_id)


# ---------- Пригласить соратника (реферальная система) ----------
# Правила из Excel заказчика: приглашённый получает скидку 20% на первую
# годовое вступление в сообщество; у пригласившего есть ступени 1 / 3 / 6 приглашённых.
# ⚠️ Что именно даётся пригласившему на каждой ступени — в Excel НЕ указано
# (колонка пустая), поэтому здесь показываем только прогресс. Как только
# заказчик определится — вписать в referrals_db.MILESTONE_REWARDS.

REFERRAL_LINK_BASE = "https://t.me/musculataclub_bot"


def referral_link(user_id: int) -> str:
    return f"{REFERRAL_LINK_BASE}?start={user_id}"


@bot.message_handler(func=lambda m: m.text == keyboards.BTN_INVITE)
@safe_handler(bot)
def handle_invite(message):
    delete_user_message(message)
    user_id = message.from_user.id
    state.clear_awaiting_support(user_id)

    _sword = f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji>'
    _diamond = f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'
    _shield = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
    _scroll = f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji>'

    converted = referrals_db.count_converted(user_id)
    pending = referrals_db.count_pending(user_id)
    link = referral_link(user_id)

    lines = [
        f"{_sword} <b>Пригласить соратника</b>\n",
        f"{_scroll} Отправь другу свою ссылку. Он получит "
        f"<b>скидку {referrals_db.INVITEE_DISCOUNT_PERCENT}%</b> на первую "
        f"годовое вступление в сообщество.",
        "",
        f"{_sword} <b>Твоя ссылка:</b>",
        f"<code>{link}</code>",
        "",
        f"{_diamond} <b>Твои соратники:</b>",
        f"{_shield} Вступили в сообщество: <b>{converted}</b>",
    ]
    if pending:
        lines.append(f"{_scroll} Перешли, но ещё не вступили: {pending}")

    # Прогресс до следующей ступени
    nxt = referrals_db.next_milestone(converted)
    if nxt:
        left = nxt - converted
        lines.append(f"\n{_diamond} До ступени <b>{nxt}</b> осталось: {left}")
    else:
        lines.append(f"\n{_diamond} <b>Все ступени пройдены!</b>")

    show_content(message.chat.id, user_id, "\n".join(lines), parse_mode="HTML")


STATUS_LABELS = {
    "pending":       f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji> Ожидает оплаты',
    "paid":          f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji> Оплачен',
    "missing_items": f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji> Часть товаров недоступна',
    "error":         f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji> Ошибка оформления',
}


@bot.message_handler(func=lambda m: m.text == keyboards.BTN_ORDERS)
@safe_handler(bot)
def handle_orders(message):
    """
    История заказов пользователя. Живёт только в нашей БД (orders_db),
    т.к. личного кабинета на сайте не будет — сайт не хранит для нас
    историю, только сам факт заказа + вебхуки о его статусе.
    """
    delete_user_message(message)
    state.clear_awaiting_support(message.from_user.id)
    user_id = message.from_user.id

    orders = orders_db.get_user_orders(user_id, limit=10)

    _ghost = f'<tg-emoji emoji-id="{emoji_ids.GHOST}">👻</tg-emoji>'
    _scroll = f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji>'

    if not orders:
        show_content(
            message.chat.id,
            user_id,
            f"{_ghost} <b>Твоя летопись пока пуста.</b>\n"
            f"Загляни в {_scroll} <b>Каталог</b> — впиши в неё первую битву.",
            parse_mode="HTML",
        )
        return

    lines = [f"{_ghost} <b>Хроники твоих походов:</b>\n"]
    for order in orders:
        status_label = STATUS_LABELS.get(order["status"], order["status"])
        order_id = order["site_order_id"] or "—"
        total = f"{order['total']} ₽" if order["total"] else "—"
        lines.append(f"<b>Заказ #{order_id}</b> · {status_label} · {total}")

    show_content(message.chat.id, user_id, "\n".join(lines), parse_mode="HTML")


# ---------- Сообщество: уровни членства ----------
# Три уровня из Excel заказчика: Оруженосец / Рыцарь / Военачальник.
# Годовая оплата, оплата идёт на сайте (не через корзину).
# Паки сюда НЕ входят — они в каталоге; членство в сообществе лишь даёт на них скидку.


def _order_menu_text(sub: dict | None, has_sub: bool) -> str:
    _shield = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
    _diamond = f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'
    _sword = f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji>'
    _scroll = f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji>'

    lines = [f"{_shield} <b>Сообщество</b>\n"]

    if has_sub and sub:
        expires = _format_date(sub.get("expires_at"))
        lines.append(
            f"{_diamond} <b>Ты в сообществе — уровень «{sub.get('tier_name')}»</b>"
            + (f", до {expires}" if expires else "")
        )
        lines.append("")

    lines += [
        f"{_sword} Годовое членство в сообществе. Что даёт любой уровень:",
        "",
    ]
    for perk in subscription_tiers.COMMON_PERKS:
        lines.append(f"{_scroll} {perk}")

    lines += [
        "",
        f"{_diamond} Чем выше ранг — тем больше контента, поддержки "
        f"и скидка на Военные Сундуки (до 15%).",
        "",
        "<b>Выбери уровень, чтобы посмотреть подробности:</b>",
    ]
    return "\n".join(lines)


def _format_date(iso_str) -> str | None:
    if not iso_str:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(iso_str).strftime("%d.%m.%Y")
    except Exception:
        return None


def _show_order_menu(chat_id: int, user_id: int, message_id: int | None = None):
    """Показ экрана Сообщества. Из reply-кнопки — новое сообщение, из inline — правка."""
    sub = subscriptions_db.get_subscription(user_id)
    has_sub = subscriptions_db.has_active_subscription(user_id)
    text = _order_menu_text(sub, has_sub)
    kb = keyboards.order_menu_keyboard_dict(has_sub)

    if message_id:
        result = emoji_ui.edit_message_with_emoji(chat_id, message_id, text, reply_markup=kb)
        if result.get("ok"):
            return
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass

    sent = emoji_ui.send_message_with_emoji(chat_id, text, reply_markup=kb)
    if sent.get("ok"):
        state.set_content(user_id, sent["result"]["message_id"])


@bot.message_handler(func=lambda m: m.text == keyboards.BTN_ORDER_SUBSCRIPTION)
@safe_handler(bot)
def handle_order_menu_button(message):
    delete_user_message(message)
    state.clear_awaiting_support(message.from_user.id)
    old_id = state.get_content(message.from_user.id)
    if old_id:
        try:
            bot.delete_message(message.chat.id, old_id)
        except Exception:
            pass
    _show_order_menu(message.chat.id, message.from_user.id)


@bot.callback_query_handler(func=lambda c: c.data == "order_menu")
@safe_handler(bot)
def handle_order_menu_callback(call):
    bot.answer_callback_query(call.id)
    _show_order_menu(call.message.chat.id, call.from_user.id, call.message.message_id)


def _tier_detail_text(tier: dict, is_current: bool, invitee_discount: bool) -> str:
    """Карточка уровня — всё, что даёт этот ранг, человеческим языком."""
    _shield = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
    _diamond = f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'
    _sword = f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji>'
    _scroll = f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji>'
    _news = f'<tg-emoji emoji-id="{emoji_ids.NEWS}">🗞</tg-emoji>'
    _ghost = f'<tg-emoji emoji-id="{emoji_ids.GHOST}">👻</tg-emoji>'

    price = f"{tier['price_year']:,}".replace(",", " ")
    per_month = f"{round(tier['price_year'] / 12):,}".replace(",", " ")

    lines = [
        f"{_shield} <b>{tier['name']}</b>",
        f"<i>{tier['tagline']}</i>",
        "",
        f"{_diamond} <b>{price} ₽ / год</b>  <i>(~{per_month} ₽ в месяц)</i>",
    ]

    if is_current:
        lines.append(f"\n{_diamond} <i>Это твой текущий уровень.</i>")

    lines += ["", f"{_scroll} <b>Что входит:</b>"]
    lines.append(f"{_news} {tier['channel']}")
    lines.append(f"{_scroll} {tier['content']}")
    if tier["trainer"]:
        lines.append(f"{_sword} {tier['trainer']}")
    lines.append(f"{_ghost} {tier['merch']}")
    if tier["stickers"]:
        lines.append(f"{_ghost} {tier['stickers']}")
    lines.append(f"{_shield} Скидка <b>{int(tier['pack_discount'] * 100)}%</b> на Военные Сундуки")

    lines += ["", f"{_scroll} <b>Плюс для всех уровней:</b>"]
    for perk in subscription_tiers.COMMON_PERKS:
        lines.append(f"• {perk}")

    if invitee_discount and not is_current:
        lines += [
            "",
            f"{_diamond} <b>Тебя пригласил соратник — на первое вступление в сообщество "
            f"действует скидка {referrals_db.INVITEE_DISCOUNT_PERCENT}%!</b>",
        ]

    return "\n".join(lines)


@bot.callback_query_handler(func=lambda c: c.data.startswith("tier:"))
@safe_handler(bot)
def handle_tier_detail(call):
    tier_id = int(call.data.split(":")[1])
    tier = subscription_tiers.get_tier(tier_id)
    if not tier:
        bot.answer_callback_query(call.id, "Такого уровня нет")
        return

    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    has_sub = subscriptions_db.has_active_subscription(user_id)
    current_tier_id = subscriptions_db.get_active_tier_id(user_id)
    is_current = current_tier_id == tier_id
    invitee_discount = referrals_db.has_pending_invitee_discount(user_id)

    analytics.log_event(user_id, call.from_user.username, "view_tier", tier["name"])
    edit_or_resend(
        call.message.chat.id, user_id, call.message.message_id,
        _tier_detail_text(tier, is_current, invitee_discount),
        reply_markup=keyboards.tier_detail_keyboard_dict(tier_id, has_sub, is_current),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("tier_pay:"))
@safe_handler(bot)
def handle_tier_subscribe(call):
    """
    Вступление в сообщество на выбранный уровень. Решение от созвона
    13.08: оплата идёт ПРЯМО В TELEGRAM через ЮKassa (send_invoice),
    а не через сайт — в корзину членство не кладётся и checkout_url
    сайта здесь больше не участвует.
    """
    tier_id = int(call.data.split(":")[1])
    tier = subscription_tiers.get_tier(tier_id)
    if not tier:
        bot.answer_callback_query(call.id, "Такого уровня нет")
        return

    user_id = call.from_user.id
    if subscriptions_db.has_active_subscription(user_id):
        bot.answer_callback_query(call.id, "Ты уже в сообществе")
        return

    if not payments.is_configured():
        bot.answer_callback_query(call.id, "Пока недоступно")
        _shield = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
        show_content(
            call.message.chat.id, user_id,
            f"{_shield} <b>Вступление в сообщество временно недоступно.</b>\n\n"
            "Приём платежей ещё настраивается (ждём токен ЮKassa). "
            "Загляни позже или напиши в поддержку.",
            parse_mode="HTML",
        )
        return

    bot.answer_callback_query(call.id)

    # Запоминаем выбор — та же логика, что и раньше: если что-то пойдёт не
    # так между выставлением счёта и оплатой, у нас есть след, какой
    # уровень выбирал пользователь.
    subscriptions_db.record_pending_subscription(user_id, tier_id)

    # Скидка 20% приглашённому на первое годовое членство (из Excel).
    discount = referrals_db.INVITEE_DISCOUNT_PERCENT if referrals_db.has_pending_invitee_discount(user_id) else 0

    sent = payments.send_membership_invoice(
        bot, call.message.chat.id, user_id, tier, discount_percent=discount,
    )
    if not sent:
        analytics.log_event(user_id, call.from_user.username, "membership_invoice_failed", tier["name"])
        _shield = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
        show_content(
            call.message.chat.id, user_id,
            f"{_shield} <b>Не получилось выставить счёт.</b>\n\n"
            "Попробуй ещё раз чуть позже или напиши в поддержку.",
            parse_mode="HTML",
        )
        return

    analytics.log_event(user_id, call.from_user.username, "membership_invoice_sent", tier["name"])


@bot.pre_checkout_query_handler(func=lambda query: True)
def handle_pre_checkout_query(pre_checkout_query):
    """
    Telegram спрашивает "точно ли можно провести оплату" ПЕРЕД тем, как
    показать пользователю ввод карты — по правилам Telegram Payments
    ОБЯЗАТЕЛЬНО ответить в течение 10 секунд, иначе Telegram сам отменит
    операцию. Пока просто подтверждаем всегда — более сложная проверка
    (например, что счёт ещё актуален) может понадобиться позже.
    """
    try:
        bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
    except Exception:
        logger.exception("Не удалось ответить на pre_checkout_query")


@bot.message_handler(content_types=["successful_payment"])
@safe_handler(bot, require_consent=False)
def handle_successful_payment(message):
    """
    Оплата членства прошла — Telegram прислал обычное сообщение с полем
    successful_payment. Тут наконец активируем членство (раньше это
    делал вебхук с сайта, теперь оплата у нас, поэтому и активация тут).
    """
    payload = message.successful_payment.invoice_payload
    parsed = payments.parse_membership_payload(payload)
    if not parsed:
        logger.error("Не удалось разобрать payload успешного платежа: %r", payload)
        return

    telegram_id, tier_id = parsed
    subscriptions_db.record_pending_subscription(telegram_id, tier_id)
    subscriptions_db.activate_subscription(telegram_id, tier_id=tier_id)
    analytics.log_event(telegram_id, message.from_user.username, "membership_paid", str(tier_id))

    tier = subscription_tiers.get_tier(tier_id)
    tier_name = tier["name"] if tier else "Сообщество"

    _diamond = f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'
    bot.send_message(
        message.chat.id,
        f"{_diamond} <b>Добро пожаловать в сообщество!</b>\n\n"
        f"Уровень «{tier_name}» активирован. Загляни в раздел «Сообщество», "
        f"чтобы посмотреть детали.",
        parse_mode="HTML",
    )

    # Доступ в канал/чат — сразу, не дожидаясь фоновой сверки (см. run.py).
    order_access.grant_access(bot, telegram_id)

    # Реферальная конверсия. ВАЖНО: не переиспользуем webhooks._handle_
    # referral_conversion напрямую — тот модуль рассчитан на отдельный
    # процесс gunicorn/wsgi.py со своим лёгким notifier_bot (см. wsgi.py,
    # там объяснено, почему). Этот обработчик живёт в основном
    # процессе поллинга — используем свой bot и дублируем небольшую
    # логику уведомления, вместо того чтобы тянуть кросс-процессный
    # импорт, который не будет работать корректно.
    _notify_referral_conversion(telegram_id)


def _notify_referral_conversion(telegram_id: int):
    """
    Засчитывает реферальную конверсию и уведомляет пригласившего — аналог
    _handle_referral_conversion из webhooks.py, но использует основной
    bot (polling), не тот, что в вебхуках (см. комментарий выше).
    """
    result = referrals_db.mark_converted(telegram_id)
    if not result:
        return

    _diamond = f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'
    _sword = f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji>'

    referrer_id = result["referrer_id"]
    count = result["converted_count"]

    text = (
        f"{_sword} <b>Твой соратник вступил в сообщество!</b>\n\n"
        f"Всего по твоим приглашениям вступили: <b>{count}</b>."
    )
    if result["milestone_reached"]:
        reward = result["reward"] or {}
        extra_days = reward.get("extra_days", 0)
        merch_gift = reward.get("merch_gift")
        text += f"\n\n{_diamond} <b>Ты достиг ступени {result['milestone_reached']}!</b>"
        if extra_days:
            new_expires = subscriptions_db.extend_subscription(referrer_id, extra_days)
            if new_expires:
                months = extra_days // 30
                text += f"\nЧленство продлено на {months} мес. — теперь действует до {new_expires[:10]}."
        if merch_gift:
            text += f"\n🎁 Тебе полагается: {merch_gift} — свяжемся с тобой отдельно."

    try:
        bot.send_message(referrer_id, text, parse_mode="HTML")
    except Exception:
        logger.warning("Не удалось уведомить пригласившего telegram_id=%s о конверсии", referrer_id)

    logger.info(
        "Реферал конвертирован (оплата через Telegram Payments): invitee=%s referrer=%s всего=%s",
        telegram_id, referrer_id, count,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("tier_switch:"))
@safe_handler(bot)
def handle_tier_switch(call):
    """
    Смена тарифа заявлена в Excel («в любой момент с пропорциональным
    перерасчётом»), но механика доплаты/возврата ещё не согласована
    с сайтом — поэтому пока честно сообщаем, а не делаем вид, что работает.
    """
    _shield = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
    bot.answer_callback_query(
        call.id,
        "Смена тарифа пока настраивается — напиши в поддержку, поможем вручную",
        show_alert=True,
    )


@bot.callback_query_handler(func=lambda c: c.data == "my_subscription")
@safe_handler(bot)
def handle_my_subscription(call):
    """Экран текущего членства — что активно, до какой даты, что даёт."""
    user_id = call.from_user.id
    sub = subscriptions_db.get_subscription(user_id)

    # ВАЖНО: has_active_subscription() возвращает False и во время
    # заморозки (доступ приостановлен по задумке) — но экран "Моя
    # членство" должен показываться и в этом случае, просто со статусом
    # "на паузе", а не говорить пользователю, что членства нет вообще.
    # Поэтому здесь проверяем именно срок действия, а не has_active_subscription.
    expires_at = None
    if sub and sub.get("expires_at"):
        try:
            from datetime import datetime, timezone
            expires_at = datetime.fromisoformat(sub["expires_at"])
        except Exception:
            expires_at = None

    if not sub or not expires_at or expires_at <= datetime.now(expires_at.tzinfo):
        bot.answer_callback_query(call.id, "Ты пока не в сообществе")
        return

    bot.answer_callback_query(call.id)
    tier = subscription_tiers.get_tier(sub.get("tier_id"))
    is_frozen = subscriptions_db.is_frozen(user_id)
    _diamond = f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'
    _shield = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
    _scroll = f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji>'
    _drop = f'<tg-emoji emoji-id="{emoji_ids.DROP}">💧</tg-emoji>'

    started = _format_date(sub.get("started_at"))
    expires = _format_date(sub.get("expires_at"))

    lines = [
        f"{_diamond} <b>Твоё членство в сообществе</b>\n",
        f"{_shield} Уровень: <b>{sub.get('tier_name')}</b>",
    ]

    if is_frozen:
        frozen_until = _format_date(sub.get("frozen_until"))
        lines.append(
            f"\n{_drop} <b>Присяга на паузе до {frozen_until}.</b>\n"
            f"Доступ приостановлен, но дата окончания сдвинута — оплаченное "
            f"время не потеряно."
        )

    if started:
        lines.append(f"{_scroll} Вступил: {started}")
    if expires:
        lines.append(f"{_scroll} Действует до: <b>{expires}</b>")

    if tier:
        lines += [
            "",
            f"{_diamond} Скидка на Военные Сундуки: <b>{int(tier['pack_discount'] * 100)}%</b>",
            f"{_scroll} {tier['content']}",
        ]
        if tier["trainer"]:
            lines.append(f"{_scroll} {tier['trainer']}")

    edit_or_resend(
        call.message.chat.id, user_id, call.message.message_id,
        "\n".join(lines),
        reply_markup=keyboards.my_subscription_keyboard_dict(is_frozen),
    )


@bot.callback_query_handler(func=lambda c: c.data == "freeze_prompt")
@safe_handler(bot)
def handle_freeze_prompt(call):
    """Показывает варианты срока заморозки (7/14/30 дней)."""
    user_id = call.from_user.id
    if not subscriptions_db.has_active_subscription(user_id):
        bot.answer_callback_query(call.id, "Ты пока не в сообществе")
        return
    if subscriptions_db.is_frozen(user_id):
        bot.answer_callback_query(call.id, "Присяга уже на паузе")
        return

    bot.answer_callback_query(call.id)
    _drop = f'<tg-emoji emoji-id="{emoji_ids.DROP}">💧</tg-emoji>'
    edit_or_resend(
        call.message.chat.id, user_id, call.message.message_id,
        f"{_drop} <b>На сколько дней приостановить участие в сообществе?</b>\n\n"
        "Доступ будет приостановлен на выбранный срок, а дата окончания "
        "членства сдвинется на столько же дней вперёд — оплаченное время "
        "не потеряется. Максимум 30 дней.",
        reply_markup=keyboards.freeze_options_keyboard_dict(),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("freeze_do:"))
@safe_handler(bot)
def handle_freeze_do(call):
    """Реально приостанавливает участие в сообществе на выбранный срок."""
    days = int(call.data.split(":")[1])
    user_id = call.from_user.id

    result = subscriptions_db.freeze_subscription(user_id, days)

    if not result.get("ok"):
        reason = result.get("reason")
        messages = {
            "invalid_days": "Некорректный срок",
            "no_subscription": "Ты пока не в сообществе",
            "already_frozen": "Присяга уже на паузе",
        }
        bot.answer_callback_query(call.id, messages.get(reason, "Не получилось"))
        return

    bot.answer_callback_query(call.id, "Присяга заморожена ❄")
    analytics.log_event(user_id, call.from_user.username, "subscription_frozen", f"{days} дней")

    _drop = f'<tg-emoji emoji-id="{emoji_ids.DROP}">💧</tg-emoji>'
    frozen_until = _format_date(result["frozen_until"])
    new_expires = _format_date(result["new_expires_at"])
    edit_or_resend(
        call.message.chat.id, user_id, call.message.message_id,
        f"{_drop} <b>Присяга заморожена на {days} дней.</b>\n\n"
        f"Доступ приостановлен до {frozen_until}, дата окончания членства "
        f"сдвинута на {new_expires}.",
        reply_markup=keyboards.my_subscription_keyboard_dict(is_frozen=True),
    )


# ---------- Пак №4: бесплатный ежемесячный набор для участников сообщества ----------
# Из ТЗ по подпискам: "уникальный, отдельно не продаётся, состав меняется
# каждый месяц". Бот НЕ занимается физической отправкой — только честно
# фиксирует, кто имеет право и кто уже забрал в этом месяце, и уведомляет
# админа о каждом заборе, чтобы было видно, кому готовить посылку.


def _pack4_text(comp: dict | None, already_claimed: bool) -> str:
    _diamond = f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'
    _shield = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
    _sword = f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji>'
    _scroll = f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji>'

    if not comp:
        return (
            f"{_shield} <b>Пак №4 — бесплатный подарок участникам сообщества</b>\n\n"
            f"{_scroll} Состав этого месяца ещё не объявлен — загляни чуть позже."
        )

    lines = [
        f"{_shield} <b>Пак №4 — бесплатный подарок участникам сообщества</b>\n",
        f"{_scroll} <b>Состав этого месяца:</b>",
    ]
    for item in comp["items"]:
        lines.append(f"{_sword} {item['name']} <i>({item['brand']})</i> — {item['price']} ₽")

    if comp.get("gift"):
        lines.append(f"\n{_diamond} <b>Плюс:</b> {comp['gift']}")

    if already_claimed:
        lines.append(f"\n{_diamond} <i>Ты уже забрал этот пак в этом месяце — увидимся в следующем!</i>")

    return "\n".join(lines)


@bot.callback_query_handler(func=lambda c: c.data == "pack4")
@safe_handler(bot)
def handle_pack4(call):
    """Экран пака №4 — доступен только участникам сообщества (и не во время паузы)."""
    user_id = call.from_user.id
    if not subscriptions_db.has_active_subscription(user_id):
        bot.answer_callback_query(call.id, "Нужно вступить в сообщество")
        return

    bot.answer_callback_query(call.id)
    comp = pack4_db.get_current_composition()
    already_claimed = pack4_db.has_claimed(user_id)

    edit_or_resend(
        call.message.chat.id, user_id, call.message.message_id,
        _pack4_text(comp, already_claimed),
        reply_markup=keyboards.pack4_keyboard_dict(has_composition=bool(comp), already_claimed=already_claimed),
    )


@bot.callback_query_handler(func=lambda c: c.data == "pack4_claim")
@safe_handler(bot)
def handle_pack4_claim(call):
    """Забрать пак №4 — раз в месяц, без повторов."""
    user_id = call.from_user.id
    if not subscriptions_db.has_active_subscription(user_id):
        bot.answer_callback_query(call.id, "Нужно вступить в сообщество")
        return

    comp = pack4_db.get_current_composition()
    if not comp:
        bot.answer_callback_query(call.id, "Состав ещё не объявлен")
        return

    claimed_now = pack4_db.claim(user_id)
    if not claimed_now:
        bot.answer_callback_query(call.id, "Уже забрал в этом месяце")
        return

    bot.answer_callback_query(call.id, "Пак забран ⚔")
    analytics.log_event(user_id, call.from_user.username, "pack4_claimed", comp["month_key"])

    # Бот не отправляет физически — просто честно сообщает админу, кому
    # готовить посылку, вместе с контактом, чтобы можно было связаться.
    if ADMIN_CHAT_ID:
        user = call.from_user
        full_name = " ".join(filter(None, [user.first_name, user.last_name])) or "—"
        username_part = f"@{user.username}" if user.username else "(без username)"
        try:
            bot.send_message(
                ADMIN_CHAT_ID,
                f"🎁 Пак №4 забран: {full_name} {username_part} (user_id: {user_id})\n"
                f"Месяц: {comp['month_key']}",
            )
        except Exception:
            logger.warning("Не удалось уведомить админа о заборе пака №4")

    edit_or_resend(
        call.message.chat.id, user_id, call.message.message_id,
        _pack4_text(comp, already_claimed=True),
        reply_markup=keyboards.pack4_keyboard_dict(has_composition=True, already_claimed=True),
    )


@bot.message_handler(commands=["setpack4"])
@safe_handler(bot, require_consent=False)
def handle_setpack4_command(message):
    """
    Только для администратора — запускает ввод состава пака №4 на текущий
    месяц. Простой текстовый формат вместо веб-панели: одна позиция на
    строку "Название | Бренд | Цена", последняя строка опционально
    "Подарок: текст".
    """
    delete_user_message(message)
    if not ADMIN_CHAT_ID or message.from_user.id != int(ADMIN_CHAT_ID):
        return  # тихо игнорируем — не администратор

    state.set_awaiting_pack4_setup(message.from_user.id)
    bot.send_message(
        message.chat.id,
        "🛡 Пришли состав пака №4 на этот месяц — по одной позиции на строку:\n\n"
        "<code>Название | Бренд | Цена</code>\n\n"
        "Последней строкой (не обязательно) можно добавить:\n"
        "<code>Подарок: текст бонуса</code>\n\n"
        "Пример:\n"
        "<code>NOW Foods Vitamin C 1000mg | NOW Foods | 890\n"
        "Trec Nutrition BCAA | Trec | 1200\n"
        "Подарок: стикер набора этого месяца</code>\n\n"
        "Чтобы отменить — /cancel_tech.",
        parse_mode="HTML",
    )


def _parse_pack4_input(text: str) -> tuple[list[dict], str | None, list[str]]:
    """
    Разбирает текст админа в список позиций + опциональный подарок.
    Возвращает (items, gift, errors) — errors непустой, если что-то
    не распозналось (тогда состав НЕ сохраняем, просим исправить).
    """
    items = []
    gift = None
    errors = []

    for line_no, raw_line in enumerate(text.strip().split("\n"), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("подарок:"):
            gift = line.split(":", 1)[1].strip()
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3:
            errors.append(f"Строка {line_no}: ожидал 'Название | Бренд | Цена', получил «{line}»")
            continue
        name, brand, price_str = parts
        try:
            price = int(price_str)
        except ValueError:
            errors.append(f"Строка {line_no}: цена «{price_str}» не похожа на число")
            continue
        if not name or not brand:
            errors.append(f"Строка {line_no}: пустое название или бренд")
            continue
        items.append({"name": name, "brand": brand, "price": price})

    if not items and not errors:
        errors.append("Не нашёл ни одной позиции — пусто.")

    return items, gift, errors


@bot.message_handler(
    func=lambda m: state.is_awaiting_pack4_setup(m.from_user.id) and m.text not in MENU_BUTTON_TEXTS,
    content_types=["text"],
)
@safe_handler(bot, require_consent=False)
def handle_pack4_setup_input(message):
    """Парсит и сохраняет присланный админом состав пака №4."""
    user_id = message.from_user.id
    state.clear_awaiting_pack4_setup(user_id)
    delete_user_message(message)

    items, gift, errors = _parse_pack4_input(message.text)

    if errors:
        error_text = "\n".join(f"• {e}" for e in errors)
        bot.send_message(
            message.chat.id,
            f"⚠️ Не смог разобрать состав:\n\n{error_text}\n\n"
            f"Попробуй ещё раз командой /setpack4.",
        )
        return

    pack4_db.set_composition(items, gift=gift)
    analytics.log_event(user_id, message.from_user.username, "pack4_composition_set", f"{len(items)} позиций")

    lines_preview = "\n".join(f"• {i['name']} ({i['brand']}) — {i['price']} ₽" for i in items)
    gift_line = f"\nПодарок: {gift}" if gift else ""
    bot.send_message(
        message.chat.id,
        f"✅ Состав пака №4 на {pack4_db.current_month_key()} сохранён:\n\n{lines_preview}{gift_line}",
    )


@bot.callback_query_handler(func=lambda c: c.data == "noop")
@safe_handler(bot)
def handle_noop(call):
    """Кнопка-статус без действия."""
    bot.answer_callback_query(call.id)


# ---------- Настройки (пока тестовый раздел: согласие на ПД + поддержка) ----------

@bot.message_handler(func=lambda m: m.text == keyboards.BTN_SETTINGS)
@safe_handler(bot)
def handle_settings_button(message):
    delete_user_message(message)
    state.clear_awaiting_support(message.from_user.id)
    show_content(
        message.chat.id,
        message.from_user.id,
        f'<tg-emoji emoji-id="{emoji_ids.NEWS}">🗞</tg-emoji> <b>Настройки:</b>',
        reply_markup=keyboards.settings_keyboard_dict(),
    )


@bot.callback_query_handler(func=lambda c: c.data == "settings:consent")
@safe_handler(bot)
def handle_settings_consent(call):
    """
    Показывает текст согласия, которое пользователь уже принял — на случай
    споров ("покажи, на что я соглашался") это должно быть доступно
    в любой момент, а не только в момент самого первого /start.
    """
    bot.answer_callback_query(call.id)
    edit_or_resend(
        call.message.chat.id, call.from_user.id, call.message.message_id, CONSENT_TEXT,
        reply_markup=keyboards.settings_consent_back_keyboard_dict(),
    )


@bot.callback_query_handler(func=lambda c: c.data == "settings:revoke_confirm")
@safe_handler(bot)
def handle_settings_revoke_confirm(call):
    """Промежуточный шаг — не отзываем по одному тапу, сначала подтверждение."""
    bot.answer_callback_query(call.id)
    _shield = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
    edit_or_resend(
        call.message.chat.id, call.from_user.id, call.message.message_id,
        f"{_shield} <b>Точно отозвать согласие?</b>\n\n"
        "После этого бот перестанет отвечать на любые действия, пока ты "
        "не примешь условия заново через /start.",
        reply_markup=keyboards.settings_revoke_confirm_keyboard_dict(),
    )


@bot.callback_query_handler(func=lambda c: c.data == "settings:revoke_do")
@safe_handler(bot)
def handle_settings_revoke_do(call):
    """
    Реально отзывает согласие. С этого момента safe_handler блокирует
    ВСЕ разделы бота для этого пользователя, кроме /start — там ему
    заново предложат принять условия (см. consent_db.has_consent и
    errors.safe_handler).
    """
    user_id = call.from_user.id
    consent_db.revoke_consent(user_id)
    # Чтобы следующий /start прислал СВЕЖЕЕ сообщение с кнопкой "Принимаю",
    # а не сослался на старое (там кнопка уже убрана после первого принятия).
    state.clear_consent_message(user_id)
    analytics.log_event(user_id, call.from_user.username, "consent_revoked")
    bot.answer_callback_query(call.id, "Согласие отозвано")

    _shield = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
    edit_or_resend(
        call.message.chat.id, user_id, call.message.message_id,
        f"{_shield} <b>Согласие отозвано.</b>\n\n"
        "Бот больше не будет отвечать на действия. Чтобы продолжить "
        "пользоваться сервисом — напиши /start и прими условия заново.",
        reply_markup={"inline_keyboard": []},
    )


@bot.callback_query_handler(func=lambda c: c.data == "settings:back")
@safe_handler(bot)
def handle_settings_back(call):
    bot.answer_callback_query(call.id)
    edit_or_resend(
        call.message.chat.id, call.from_user.id, call.message.message_id,
        f'<tg-emoji emoji-id="{emoji_ids.NEWS}">🗞</tg-emoji> <b>Настройки:</b>',
        reply_markup=keyboards.settings_keyboard_dict(),
    )


@bot.callback_query_handler(func=lambda c: c.data == "settings:support")
@safe_handler(bot)
def handle_settings_support(call):
    bot.answer_callback_query(call.id)
    state.set_awaiting_support(call.from_user.id)
    _pencil = f'<tg-emoji emoji-id="{emoji_ids.PENCIL}">📝</tg-emoji>'
    edit_or_resend(
        call.message.chat.id, call.from_user.id, call.message.message_id,
        f"{_pencil} Опиши свой вопрос ОДНИМ сообщением — я передам его администратору "
        f"напрямую, вместе с твоим Telegram-ником.\n\n"
        f"Чтобы отменить отправку — введи команду /cancel_tech.",
    )


@bot.message_handler(commands=["cancel_tech"])
@safe_handler(bot)
def handle_cancel_tech(message):
    """
    Отмена ожидающего вопроса в поддержку. Работает только если пользователь
    уже нажимал 'Поддержка' и сейчас должен был писать вопрос — в остальных
    случаях просто говорит, что отменять нечего.
    """
    user_id = message.from_user.id
    delete_user_message(message)

    if state.is_awaiting_support(user_id):
        state.clear_awaiting_support(user_id)
        show_content(
            message.chat.id,
            user_id,
            "❎ Отправка вопроса в поддержку отменена.",
        )
    elif state.is_awaiting_pack4_setup(user_id):
        state.clear_awaiting_pack4_setup(user_id)
        bot.send_message(message.chat.id, "❎ Ввод состава пака №4 отменён.")
    else:
        show_content(
            message.chat.id,
            user_id,
            "Нет активной отправки в поддержку — отменять нечего.",
        )


@bot.callback_query_handler(func=lambda c: c.data == "settings:exit")
@safe_handler(bot)
def handle_settings_exit(call):
    """
    Выход из настроек — просто удаляем сообщение с настройками, так же
    как это делают другие "экраны" при переходе. Клавиатура внизу
    (основное меню) не затрагивается, потому что она живёт в отдельном
    сообщении.
    """
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    state.clear_content(call.from_user.id)


# ---------- Поддержка: полноценная двусторонняя связь с администратором ----------
# Поддержка вызывается ТОЛЬКО из раздела Настройки (см. handle_settings_support ниже),
# чтобы не дублировать одну и ту же кнопку в двух местах интерфейса.


@bot.message_handler(
    func=lambda m: state.is_awaiting_support(m.from_user.id) and m.text not in MENU_BUTTON_TEXTS,
    content_types=["text"],
)
@safe_handler(bot)
def handle_support_message(message):
    """
    Пересылает вопрос пользователя администратору. Администратор отвечает
    ОБЫЧНЫМ Reply на пересланное сообщение в Telegram — ответ автоматически
    уходит нужному пользователю (см. handle_admin_reply ниже).
    """
    user_id = message.from_user.id
    state.clear_awaiting_support(user_id)
    delete_user_message(message)

    user = message.from_user
    full_name = " ".join(filter(None, [user.first_name, user.last_name])) or "—"
    username_part = f"@{user.username}" if user.username else "(без username)"

    admin_text = (
        f"🆘 Новый вопрос в поддержку\n\n"
        f"От: {full_name} {username_part}\n"
        f"user_id: {user_id}\n\n"
        f"Сообщение:\n{message.text}\n\n"
        f"Чтобы ответить — сделай Reply прямо на это сообщение, текст ответа "
        f"уйдёт пользователю автоматически."
    )

    sent_ok = False
    if ADMIN_CHAT_ID:
        try:
            admin_msg = bot.send_message(ADMIN_CHAT_ID, admin_text)
            state.set_support_thread(admin_msg.message_id, user_id)
            sent_ok = True
        except Exception:
            logger.exception("Не удалось переслать вопрос в поддержку администратору")

    analytics.log_event(user_id, user.username, "support_question", message.text[:200])

    if sent_ok:
        show_content(
            message.chat.id,
            user_id,
            f'<tg-emoji emoji-id="{emoji_ids.PENCIL}">📝</tg-emoji> <b>Вопрос отправлен глашатаю.</b>\n'
            "Как только ответят — весть придёт сюда же.",
            parse_mode="HTML",
        )
    else:
        show_content(
            message.chat.id,
            user_id,
            f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji> Не удалось передать вопрос глашатаю (техническая накладка). '
            "Попробуй ещё раз чуть позже через раздел «Поддержка».",
            parse_mode="HTML",
        )


@bot.message_handler(
    func=lambda m: ADMIN_CHAT_ID is not None and str(m.chat.id) == str(ADMIN_CHAT_ID) and m.reply_to_message is not None,
    content_types=["text"],
)
@safe_handler(bot, require_consent=False)
def handle_admin_reply(message):
    """
    Ловит Reply администратора на пересланный вопрос поддержки и
    автоматически отправляет текст ответа нужному пользователю.
    Обычные (не-Reply) сообщения администратора этот обработчик не трогает.
    """
    target_user_id = state.get_support_thread(message.reply_to_message.message_id)
    if not target_user_id:
        return  # это Reply не на вопрос поддержки — игнорируем

    try:
        bot.send_message(
            target_user_id,
            f'<tg-emoji emoji-id="{emoji_ids.PENCIL}">📝</tg-emoji> <b>Весть от глашатая:</b>\n\n{message.text}',
            parse_mode="HTML",
        )
        bot.reply_to(message, "✅ Ответ отправлен пользователю.")
        analytics.log_event(target_user_id, None, "support_reply_sent", message.text[:200])
    except Exception as e:
        bot.reply_to(message, f"⚠️ Не удалось отправить ответ пользователю: {e}")
        logger.exception("Не удалось доставить ответ поддержки пользователю %s", target_user_id)


# ---------- Логирование произвольных текстовых сообщений ----------
# Этот обработчик срабатывает ПОСЛЕДНИМ — только для текстов, которые
# не подхвачены никаким другим обработчиком (не /start, не кнопка меню,
# не вопрос в поддержку, не /cancel_tech). То есть если пользователь
# просто пишет боту какой-то произвольный текст ("привет", "а как это
# работает", ну или что-то нехорошее) — оно попадает сюда.
# Записываем это в analytics.csv, чтобы потом можно было посмотреть,
# что и в каких контекстах пишут пользователи. По вашим правам — это ваш
# бот и ваши клиенты, но текст согласия про такое логирование стоит
# обновить (см. напоминание в конце сообщения).

@bot.message_handler(content_types=["text"])
@safe_handler(bot)
def handle_free_text(message):
    """Ловит произвольный текст, не попавший в другие обработчики."""
    user_id = message.from_user.id
    text = message.text or ""
    analytics.log_event(user_id, message.from_user.username, "free_text", text[:500])
    delete_user_message(message)
    show_content(
        message.chat.id,
        user_id,
        "Не понял команду 🤔 Воспользуйся меню внизу.",
    )


def run_startup_healthcheck():
    """
    Настоящая диагностика сервера вместо слепого "Бот запущен" на каждый
    рестарт. Пишет результат в консоль/лог (не в Telegram — админ смотрит
    статус процесса в панели bothost) только при смене статуса — это
    убирает спам в логах при чистых безобидных перезапусках.

    Вызывается и из main.py (запуск для разработки), и из run.py
    (продакшен-точка входа) — чтобы диагностика работала в обоих случаях.
    Работает независимо от ADMIN_CHAT_ID — это чисто диагностика сервера,
    а не уведомление конкретного человека.
    """
    health_check.check_and_log(BOT_TOKEN)


if __name__ == "__main__":
    # Регистрируем /start в системном списке команд Telegram (иконка "/"
    # рядом с полем ввода). Это некритично — если таймаут или ошибка —
    # бот всё равно запустится и будет работать, просто команда "/start"
    # просто не будет видна в выпадающем списке помощников. Логирование
    # ошибки достаточно.
    try:
        bot.set_my_commands([types.BotCommand("start", "Запустить бота / открыть меню")])
    except Exception as e:
        logger.warning("Не удалось зарегистрировать /start в Telegram: %s", e)
        print(f"⚠️ Не удалось зарегистрировать /start: {e}")
        print("   Бот всё равно запустится, просто команда не будет видна в '/' меню.")

    run_startup_healthcheck()
    health_check.start_periodic_check(BOT_TOKEN)

    if not ADMIN_CHAT_ID:
        print(
            "ℹ️ ADMIN_CHAT_ID не задан в .env — уведомления об ошибках и пересылка "
            "вопросов из поддержки работать не будут (это не связано с проверкой "
            "состояния сервера выше)."
        )

    print("Бот запущен... (для остановки нажми Ctrl+C)")
    logger.info("Бот запущен")

    try:
        bot.infinity_polling()
    except KeyboardInterrupt:
        print("Бот остановлен вручную.")
        logger.info("Бот остановлен вручную (Ctrl+C)")
    except Exception as e:
        logger.exception("Бот упал с необработанным исключением")
        notify_admin(bot, f"🔥 Бот полностью упал и остановился:\n{type(e).__name__}: {e}")
        raise
