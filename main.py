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


# ---------- Старт: согласие на обработку ПД + меню создаётся один раз ----------

@bot.message_handler(commands=["start"])
@safe_handler(bot, require_consent=False)
def start_message(message):
    user_id = message.from_user.id
    delete_user_message(message)
    state.clear_content(user_id)
    state.clear_awaiting_support(user_id)

    analytics.log_event(user_id, message.from_user.username, "start")

    if not state.has_given_consent(user_id):
        # Жёсткий вариант согласия: без нажатия кнопки "Принимаю" бот
        # дальше меню не показывает вообще (see errors.safe_handler —
        # он же блокирует и остальные разделы для этого пользователя).
        if not state.get_consent_message(user_id):
            result = emoji_ui.send_message_with_emoji(
                message.chat.id, CONSENT_TEXT,
                reply_markup=keyboards.consent_keyboard_dict(),
            )
            if result.get("ok"):
                state.set_consent_message(user_id, result["result"]["message_id"])
        else:
            bot.send_message(
                message.chat.id,
                "Чтобы продолжить, нажми «Принимаю» в сообщении с условиями выше ⬆️",
            )
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
    state.set_consent_given(user_id)
    analytics.log_event(user_id, call.from_user.username, "consent_accepted")

    # Убираем кнопку из уже показанного уведомления и помечаем его принятым —
    # само уведомление остаётся в чате навсегда, как юридический текст.
    _news = f'<tg-emoji emoji-id="{emoji_ids.NEWS}">🗞</tg-emoji>'
    emoji_ui.edit_message_with_emoji(
        call.message.chat.id, call.message.message_id,
        CONSENT_TEXT + f"\n\n{_news} <b>Согласие получено. Спасибо!</b>",
        reply_markup={"inline_keyboard": []},
    )
    bot.answer_callback_query(call.id, "Принято ⚔")

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
    edit_content(
        call.message.chat.id,
        call.message.message_id,
        f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji> Выбери категорию снаряжения:',
        reply_markup=keyboards.categories_keyboard_dict(),
    )
    bot.answer_callback_query(call.id)


# ---------- Военные Сундуки (паки) ----------
# Готовые наборы товаров со скидкой 15%. Данные лежат в packs.py, здесь
# только показ и добавление в корзину. Пак кладётся в корзину как единая
# позиция (виртуальный товар с id из диапазона PACK_ID_OFFSET+) —
# см. packs.pack_as_cart_item и cart._lookup.


def _pack_intro_text() -> str:
    """
    Общий заголовок над списком паков. Тональность — средневековая,
    в тон остальному интерфейсу (Орден, соратник, снаряжение).
    """
    _shield = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
    _sword  = f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji>'
    _diamond= f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'
    return (
        f"{_shield} <b>Военные Сундуки</b>\n\n"
        f"{_sword} Готовые наборы снаряжения для соратников любого ранга.\n"
        f"В каждом — только проверенные бренды из наших складов.\n"
        f"{_diamond} Забирая сундук целиком, ты экономишь <b>15%</b> "
        f"против розницы и получаешь позиции, которых нет в обычной "
        f"лавке.\n\n"
        f"Выбери свой ранг:"
    )


@bot.callback_query_handler(func=lambda c: c.data == "packs_list")
@safe_handler(bot)
def handle_packs_list(call):
    """Показывает список из трёх паков. Точка входа — кнопка внизу категорий."""
    analytics.log_event(call.from_user.id, call.from_user.username, "view_packs")
    kb = keyboards.packs_list_keyboard_dict()
    result = emoji_ui.edit_message_with_emoji(
        call.message.chat.id, call.message.message_id,
        _pack_intro_text(), reply_markup=kb,
    )
    if not result.get("ok"):
        # Если пришли сюда с фото-экрана (карточка товара) — edit не сработает
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        sent = emoji_ui.send_message_with_emoji(call.message.chat.id, _pack_intro_text(), reply_markup=kb)
        if sent.get("ok"):
            state.set_content(call.from_user.id, sent["result"]["message_id"])
    bot.answer_callback_query(call.id)


def _pack_detail_text(pack: dict) -> str:
    """
    Карточка конкретного пака: тэглайн, состав с розничными ценами,
    итог по рознице, цена набора со скидкой, экономия.
    """
    _shield  = f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji>'
    _sword   = f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji>'
    _diamond = f'<tg-emoji emoji-id="{emoji_ids.DIAMOND}">💎</tg-emoji>'
    _scroll  = f'<tg-emoji emoji-id="{emoji_ids.SCROLL}">📜</tg-emoji>'

    lines = [
        f"{_shield} <b>Сундук «{pack['name']}»</b>",
        f"<i>{pack['tagline']}</i>",
        "",
        f"{_scroll} <b>Что внутри:</b>",
    ]
    for item in pack["items"]:
        lines.append(
            f"{_sword} {item['name']} <i>({item['brand']})</i> — {item['price']} ₽"
        )
    lines.extend([
        "",
        f"Розница поштучно: <s>{pack['retail_total']} ₽</s>",
        f"{_diamond} <b>Цена сундука: {pack['bundle_price']} ₽</b>",
        f"Экономия: <b>{pack['savings']} ₽</b> (−15%)",
    ])
    return "\n".join(lines)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pack:"))
@safe_handler(bot)
def handle_pack_detail(call):
    """Показывает состав выбранного пака + кнопки 'Добавить'/'Отмена'."""
    pack_id = int(call.data.split(":")[1])
    pack = packs.get_pack(pack_id)
    if not pack:
        bot.answer_callback_query(call.id, "Такого сундука нет")
        return

    analytics.log_event(call.from_user.id, call.from_user.username, "view_pack", pack["name"])
    emoji_ui.edit_message_with_emoji(
        call.message.chat.id, call.message.message_id,
        _pack_detail_text(pack),
        reply_markup=keyboards.pack_detail_keyboard_dict(pack_id),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pack_add:"))
@safe_handler(bot)
def handle_pack_add(call):
    """Кладёт пак в корзину как единую позицию."""
    pack_id = int(call.data.split(":")[1])
    pack = packs.get_pack(pack_id)
    if not pack:
        bot.answer_callback_query(call.id, "Такого сундука нет")
        return

    cart.add_item(call.from_user.id, pack_id, qty=1)
    analytics.log_event(call.from_user.id, call.from_user.username, "pack_added", pack["name"])
    bot.answer_callback_query(call.id, f"Сундук «{pack['name']}» в корзине ✅")


@bot.callback_query_handler(func=lambda c: c.data.startswith("cat:"))
@safe_handler(bot)
def handle_category_page(call):
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
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("view:"))
@safe_handler(bot)
def handle_view_product(call):
    _, pid, page, cat_idx = call.data.split(":")
    product = PRODUCTS_BY_ID.get(int(pid))
    if not product:
        bot.answer_callback_query(call.id, "Товар не найден")
        return

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
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("add:"))
@safe_handler(bot)
def handle_add_to_cart(call):
    _, pid, page, cat_idx = call.data.split(":")
    user_id = call.from_user.id
    cart.add_item(user_id, int(pid), qty=1)

    product = PRODUCTS_BY_ID.get(int(pid))
    if product:
        analytics.log_event(user_id, call.from_user.username, "add_to_cart", product["name"])

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
    bot.answer_callback_query(call.id, "Добавлено ⚔")


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
    emoji_ui.edit_message_with_emoji(
        call.message.chat.id, call.message.message_id,
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

    items_list = list(user_cart.keys())

    response = create_order(
        telegram_id=user_id,
        items=items_list,
        promotions="TELEGRAM10",
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
        bot.answer_callback_query(call.id, "Ошибка сервера")
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

    bot.answer_callback_query(call.id)

    # Корзину чистим сразу после успешного создания заказа на сайте —
    # дальнейший статус (оплачен/нет) отслеживается через вебхук
    # payment-success, который обновит запись в orders_db.
    cart.clear_cart(user_id)


# ---------- Заглушки для будущих разделов ----------

@bot.message_handler(func=lambda m: m.text == keyboards.BTN_INVITE)
@safe_handler(bot)
def handle_invite_stub(message):
    delete_user_message(message)
    state.clear_awaiting_support(message.from_user.id)
    show_content(
        message.chat.id,
        message.from_user.id,
        f'<tg-emoji emoji-id="{emoji_ids.SWORD}">⚔️</tg-emoji> Реферальная система в разработке. '
        "Скоро здесь появится твоя личная ссылка для приглашения соратников "
        "и бонусы за каждого приведённого воина.",
        parse_mode="HTML",
    )


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


@bot.message_handler(func=lambda m: m.text == keyboards.BTN_ORDER_SUBSCRIPTION)
@safe_handler(bot)
def handle_order_subscription_stub(message):
    delete_user_message(message)
    state.clear_awaiting_support(message.from_user.id)
    show_content(
        message.chat.id,
        message.from_user.id,
        f'<tg-emoji emoji-id="{emoji_ids.SHIELD}">🛡</tg-emoji> Вступление в Орден '
        "(годовая подписка с поставками раз в 60 дней) "
        "требует подключения приёма платежей — раздел в разработке.",
        parse_mode="HTML",
    )


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
    emoji_ui.edit_message_with_emoji(
        call.message.chat.id, call.message.message_id, CONSENT_TEXT,
        reply_markup=keyboards.settings_consent_back_keyboard_dict(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "settings:back")
@safe_handler(bot)
def handle_settings_back(call):
    emoji_ui.edit_message_with_emoji(
        call.message.chat.id, call.message.message_id,
        f'<tg-emoji emoji-id="{emoji_ids.NEWS}">🗞</tg-emoji> <b>Настройки:</b>',
        reply_markup=keyboards.settings_keyboard_dict(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "settings:support")
@safe_handler(bot)
def handle_settings_support(call):
    state.set_awaiting_support(call.from_user.id)
    _pencil = f'<tg-emoji emoji-id="{emoji_ids.PENCIL}">📝</tg-emoji>'
    emoji_ui.edit_message_with_emoji(
        call.message.chat.id, call.message.message_id,
        f"{_pencil} Опиши свой вопрос ОДНИМ сообщением — я передам его администратору "
        f"напрямую, вместе с твоим Telegram-ником.\n\n"
        f"Чтобы отменить отправку — введи команду /cancel_tech.",
    )
    bot.answer_callback_query(call.id)


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
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    state.clear_content(call.from_user.id)
    bot.answer_callback_query(call.id)


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

    # Самопроверка уведомлений: сразу видно в консоли, реально ли долетит
    # сообщение до администратора, а не только после первой настоящей ошибки.
    if ADMIN_CHAT_ID:
        try:
            bot.send_message(
                ADMIN_CHAT_ID,
                "✅ Бот запущен. Если ты видишь это сообщение — уведомления об ошибках "
                "и пересылка вопросов из поддержки настроены правильно.",
            )
            print(f"✅ Тестовое сообщение отправлено на ADMIN_CHAT_ID={ADMIN_CHAT_ID}")
        except Exception as e:
            print(f"⚠️ НЕ удалось отправить сообщение на ADMIN_CHAT_ID={ADMIN_CHAT_ID}: {e}")
            print(
                "   Частые причины:\n"
                "   1) с этого Telegram-аккаунта ни разу не нажимали /start именно этому "
                "боту — Telegram не разрешает ботам писать первыми тем, кто с ними ещё "
                "не взаимодействовал;\n"
                "   2) ADMIN_CHAT_ID указан неверно — нужен числовой user_id "
                "(его даёт @userinfobot), а не @username и не номер телефона."
            )
    else:
        print(
            "ℹ️ ADMIN_CHAT_ID не задан в .env — уведомления об ошибках и пересылка "
            "вопросов в поддержку работать не будут."
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
