from telebot import types
from products import get_page, total_pages, PRODUCTS_BY_ID, CATEGORIES, category_by_index
from cart import get_cart

# Тексты кнопок главного меню — вынесены в константы,
# чтобы не разъезжались при сравнении в обработчиках.
BTN_CATALOG = "📜 Каталог"
BTN_INVITE = "⚔️ Пригласить"
BTN_ORDERS = "🗡️ История"
BTN_ORDER_SUBSCRIPTION = "🛡️ Орден"
BTN_SUPPORT = "⚒️ Поддержка"
BTN_CART = "🛒 Корзина"
BTN_SETTINGS = "⚙️ Настройки"

ALL_CATEGORIES = -1  # спец-значение "показать все товары без фильтра"


def main_menu() -> types.ReplyKeyboardMarkup:
    """
    Главное меню — 2 строки по 3 кнопки. Полностью статично: создаётся
    РОВНО ОДИН РАЗ за всю историю общения с пользователем (см. main.py,
    start_message) и больше никогда не пересоздаётся и не удаляется —
    поэтому оно физически не может "мигать" или съезжать при нажатии
    любых других кнопок.
    """
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(
        types.KeyboardButton(BTN_CATALOG),
        types.KeyboardButton(BTN_ORDER_SUBSCRIPTION),
        types.KeyboardButton(BTN_CART),
    )
    markup.row(
        types.KeyboardButton(BTN_INVITE),
        types.KeyboardButton(BTN_ORDERS),
        types.KeyboardButton(BTN_SETTINGS),
    )
    return markup


def categories_keyboard() -> types.InlineKeyboardMarkup:
    """Экран выбора категории — первое, что видит пользователь в каталоге."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    for idx, cat in enumerate(CATEGORIES):
        markup.add(types.InlineKeyboardButton(cat, callback_data=f"cat:{idx}:0"))
    markup.add(types.InlineKeyboardButton("📋 Все товары", callback_data=f"cat:{ALL_CATEGORIES}:0"))
    return markup


def catalog_page_keyboard(page: int, user_id: int, cat_idx: int) -> types.InlineKeyboardMarkup:
    """
    Инлайн-клавиатура: товары страницы (в рамках выбранной категории,
    либо все, если cat_idx == ALL_CATEGORIES) + кнопки листания + возврат
    к выбору категории.
    """
    category = category_by_index(cat_idx) if cat_idx != ALL_CATEGORIES else None
    markup = types.InlineKeyboardMarkup(row_width=1)
    cart_ids = get_cart(user_id)
    for product in get_page(page, category):
        mark = "✅ " if product["id"] in cart_ids else ""
        markup.add(
            types.InlineKeyboardButton(
                f"{mark}{product['name']} — {product['price']} ₽",
                callback_data=f"view:{product['id']}:{page}:{cat_idx}",
            )
        )

    nav_row = []
    if page > 0:
        nav_row.append(types.InlineKeyboardButton("◀️ Назад", callback_data=f"cat:{cat_idx}:{page - 1}"))
    if page < total_pages(category) - 1:
        nav_row.append(types.InlineKeyboardButton("Далее ▶️", callback_data=f"cat:{cat_idx}:{page + 1}"))
    if nav_row:
        markup.row(*nav_row)

    markup.add(types.InlineKeyboardButton("◀️ К категориям", callback_data="catlist"))
    return markup


def product_card_keyboard(product_id: int, page: int, cat_idx: int, user_id: int) -> types.InlineKeyboardMarkup:
    """
    Кнопки под карточкой товара. Если товар уже в корзине — кнопка
    сама показывает галочку и количество вместо "Добавить в корзину"
    (нажатие на неё повторно добавляет ещё одну штуку).
    """
    markup = types.InlineKeyboardMarkup(row_width=1)
    qty = get_cart(user_id).get(product_id, 0)

    if qty > 0:
        label = f"✅ В корзине: {qty} шт. (тапни, чтобы добавить ещё)"
    else:
        label = "➕ Добавить в корзину"

    markup.add(types.InlineKeyboardButton(label, callback_data=f"add:{product_id}:{page}:{cat_idx}"))
    markup.add(types.InlineKeyboardButton("◀️ К каталогу", callback_data=f"cat:{cat_idx}:{page}"))
    return markup


def settings_keyboard() -> types.InlineKeyboardMarkup:
    """Раздел настроек — три пункта: согласие, поддержка, выход в главный экран."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("📄 Согласие на обработку ПД", callback_data="settings:consent"))
    markup.add(types.InlineKeyboardButton("⚒️ Поддержка", callback_data="settings:support"))
    markup.add(types.InlineKeyboardButton("◀️ Выход", callback_data="settings:exit"))
    return markup


def cart_keyboard(user_id: int) -> types.InlineKeyboardMarkup:
    """Товары в корзине — в том же прозрачном стиле кнопок, с возможностью убрать."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    cart = get_cart(user_id)
    for pid, qty in cart.items():
        product = PRODUCTS_BY_ID.get(pid)
        if not product:
            continue
        markup.add(
            types.InlineKeyboardButton(
                f"❌ {product['name']} ({qty} шт.)",
                callback_data=f"remove:{pid}",
            )
        )
    if cart:
        markup.add(types.InlineKeyboardButton("💳 Оформить заказ", callback_data="checkout"))
    return markup
