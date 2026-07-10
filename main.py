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


def show_content(chat_id, user_id, text, reply_markup=None):
    """
    Показывает "экран" (каталог/корзина/заглушка), удаляя предыдущий.
    ВАЖНО: эти сообщения НИКОГДА не несут ReplyKeyboardMarkup — поэтому
    постоянное меню внизу (отправленное один раз при первом /start)
    вообще не затрагивается ни при каких переходах между разделами.
    """
    old_id = state.get_content(user_id)
    if old_id:
        try:
            bot.delete_message(chat_id, old_id)
        except Exception:
            pass

    msg = bot.send_message(chat_id, text, reply_markup=reply_markup)
    state.set_content(user_id, msg.message_id)
    return msg


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
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ Принимаю", callback_data="consent_accept"))
            consent_msg = bot.send_message(message.chat.id, CONSENT_TEXT, reply_markup=markup)
            state.set_consent_message(user_id, consent_msg.message_id)
        else:
            bot.send_message(
                message.chat.id,
                "Чтобы продолжить, нажми «✅ Принимаю» в сообщении с условиями выше ⬆️",
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
        "⚔️ Приветствую, соратник!\n\n"
        "Перед тобой снаряжение для покорения новых вершин силы.\n"
        "Меню внизу — твой компас, оно всегда под рукой.",
        reply_markup=keyboards.main_menu(),
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
    bot.edit_message_text(
        CONSENT_TEXT + "\n\n✅ Согласие получено. Спасибо!",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=types.InlineKeyboardMarkup(),
    )
    bot.answer_callback_query(call.id, "Принято ✅")

    if not state.get_menu(user_id):
        menu_msg = bot.send_message(
            call.message.chat.id,
            "⚔️ Приветствую, соратник!\n\n"
            "Перед тобой снаряжение для покорения новых вершин силы.\n"
            "Меню внизу — твой компас, оно всегда под рукой.",
            reply_markup=keyboards.main_menu(),
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
        "📜 Выбери категорию снаряжения:",
        reply_markup=keyboards.categories_keyboard(),
    )


@bot.callback_query_handler(func=lambda c: c.data == "catlist")
@safe_handler(bot)
def handle_catlist(call):
    bot.edit_message_text(
        "📜 Выбери категорию снаряжения:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=keyboards.categories_keyboard(),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("cat:"))
@safe_handler(bot)
def handle_category_page(call):
    _, cat_idx, page = call.data.split(":")
    cat_idx = int(cat_idx)
    page = int(page)

    category_name = category_by_index(cat_idx) if cat_idx != ALL_CATEGORIES else None
    header = f"📜 Категория: {category_name}" if category_name else "📜 Все товары:"

    if page == 0:
        analytics.log_event(
            call.from_user.id, call.from_user.username, "view_category", category_name or "Все товары"
        )

    # Сюда попадаем и с обычной пагинации (текстовое сообщение), и с
    # кнопки "К каталогу" внутри карточки товара (там сообщение с фото).
    # edit_message_text не умеет превращать фото-сообщение в текстовое,
    # поэтому пытаемся сначала edit — если не вышло, удаляем и отправляем
    # новое текстовое.
    try:
        bot.edit_message_text(
            header,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=keyboards.catalog_page_keyboard(page, call.from_user.id, cat_idx),
        )
    except Exception:
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        msg = bot.send_message(
            call.message.chat.id,
            header,
            reply_markup=keyboards.catalog_page_keyboard(page, call.from_user.id, cat_idx),
        )
        state.set_content(call.from_user.id, msg.message_id)
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

    caption = f"⚔️ {product['name']}\n💰 Цена: {product['price']} ₽"
    with open(PLACEHOLDER_IMAGE_PATH, "rb") as photo:
        msg = bot.send_photo(
            call.message.chat.id,
            photo,
            caption=caption,
            reply_markup=keyboards.product_card_keyboard(
                product["id"], int(page), int(cat_idx), call.from_user.id
            ),
        )
    state.set_content(call.from_user.id, msg.message_id)
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

    # edit_message_reply_markup работает и на текстовых сообщениях, и на
    # сообщениях с фото — тут ничего менять не нужно.
    bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.message_id,
        reply_markup=keyboards.product_card_keyboard(int(pid), int(page), int(cat_idx), user_id),
    )
    bot.answer_callback_query(call.id, "Добавлено ✅")


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
        reply_markup=keyboards.cart_keyboard(user_id),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("remove:"))
@safe_handler(bot)
def handle_remove_item(call):
    pid = int(call.data.split(":")[1])
    cart.remove_item(call.from_user.id, pid)
    bot.answer_callback_query(call.id, "Убрано из корзины")
    bot.edit_message_text(
        cart.cart_text(call.from_user.id),
        call.message.chat.id,
        call.message.message_id,
        reply_markup=keyboards.cart_keyboard(call.from_user.id),
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
        show_content(
            call.message.chat.id,
            user_id,
            "❌ Не получилось оформить заказ — сайт временно недоступен "
            "или интеграция ещё не настроена. Попробуй чуть позже, либо "
            "напиши в поддержку (⚙️ Настройки → Поддержка).",
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
        "⚠️ Некоторые товары оказались недоступны — счёт сформирован "
        "только на доступные позиции.\n\n"
        if missing_reported else ""
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💳 Перейти к оплате", url=checkout_url))

    show_content(
        call.message.chat.id,
        user_id,
        f"{warning}✅ Заказ #{order_id} сформирован!\n\n"
        f"Нажми кнопку ниже, чтобы завершить оплату на сайте.",
        reply_markup=markup,
    )
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
        "⚔️ Реферальная система в разработке. Скоро здесь появится твоя личная ссылка "
        "для приглашения соратников и бонусы за каждого приведённого воина.",
    )


STATUS_LABELS = {
    "pending": "⏳ Ожидает оплаты",
    "paid": "✅ Оплачен",
    "missing_items": "⚠️ Часть товаров недоступна",
    "error": "❌ Ошибка оформления",
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

    if not orders:
        show_content(
            message.chat.id,
            user_id,
            "🗡️ У тебя пока нет заказов. Загляни в 📜 Каталог, чтобы выбрать что-нибудь!",
        )
        return

    lines = ["🗡️ Твои последние заказы:\n"]
    for order in orders:
        status_label = STATUS_LABELS.get(order["status"], order["status"])
        order_id = order["site_order_id"] or "—"
        total = f"{order['total']} ₽" if order["total"] else "—"
        lines.append(f"Заказ #{order_id} · {status_label} · {total}")

    show_content(message.chat.id, user_id, "\n".join(lines))


@bot.message_handler(func=lambda m: m.text == keyboards.BTN_ORDER_SUBSCRIPTION)
@safe_handler(bot)
def handle_order_subscription_stub(message):
    delete_user_message(message)
    state.clear_awaiting_support(message.from_user.id)
    show_content(
        message.chat.id,
        message.from_user.id,
        "🛡️ Вступление в Орден (годовая подписка с поставками раз в 60 дней) "
        "требует подключения приёма платежей — раздел в разработке.",
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
        "⚙️ Настройки:",
        reply_markup=keyboards.settings_keyboard(),
    )


@bot.callback_query_handler(func=lambda c: c.data == "settings:consent")
@safe_handler(bot)
def handle_settings_consent(call):
    """
    Показывает текст согласия, которое пользователь уже принял — на случай
    споров ("покажи, на что я соглашался") это должно быть доступно
    в любой момент, а не только в момент самого первого /start.
    """
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="settings:back"))
    bot.edit_message_text(
        CONSENT_TEXT,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "settings:back")
@safe_handler(bot)
def handle_settings_back(call):
    bot.edit_message_text(
        "⚙️ Настройки:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=keyboards.settings_keyboard(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "settings:support")
@safe_handler(bot)
def handle_settings_support(call):
    state.set_awaiting_support(call.from_user.id)
    bot.edit_message_text(
        "⚒️ Опиши свой вопрос ОДНИМ сообщением — я передам его администратору "
        "напрямую, вместе с твоим Telegram-ником.\n\n"
        "Чтобы отменить отправку — введи команду /cancel_tech.",
        call.message.chat.id,
        call.message.message_id,
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
            "✅ Вопрос отправлен администратору. Как только ответят — пришлём ответ сюда же.",
        )
    else:
        show_content(
            message.chat.id,
            user_id,
            "⚠️ Не получилось отправить вопрос администратору (техническая накладка). "
            "Попробуй ещё раз чуть позже через раздел «Поддержка».",
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
        bot.send_message(target_user_id, f"💬 Ответ поддержки:\n\n{message.text}")
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
