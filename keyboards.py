"""
keyboards.py — все клавиатуры бота.

ВАЖНО про паттерн двойных клавиатур:

1) Reply-клавиатура (нижняя панель) — обычный telebot.types.ReplyKeyboardMarkup,
   потому что она отправляется один раз в /start и живёт постоянно.
   Кастомные эмодзи в reply-кнопках Telegram-клиенты пока не показывают
   даже с Bot API 9.4 — поэтому там оставлены обычные unicode-эмодзи.

2) Все inline-клавиатуры (под сообщениями) — теперь словари в формате Bot API
   ({"inline_keyboard": [[...]]}), собранные через emoji_ui.build_emoji_button().
   Каждая кнопка несёт icon_custom_emoji_id из набора MUSCULATA_Emoji, что
   отображается на клиентах пользователей с Telegram Premium.

Функции ниже, чьё имя оканчивается на _dict, возвращают именно словарь для
эмодзи-кнопок — их надо отправлять через emoji_ui.send_message_with_emoji /
edit_message_with_emoji / send_photo_with_emoji, а не через bot.send_message.
"""

from telebot import types
from products import get_page, total_pages, PRODUCTS_BY_ID, CATEGORIES, category_by_index
from cart import get_cart
import emoji_ids
import emoji_ui
import packs

# ---------- Тексты кнопок нижней панели (ReplyKeyboard) ----------

BTN_CATALOG           = "📜 Каталог"
BTN_INVITE            = "⚔️ Пригласить"
BTN_ORDERS            = "🗡️ История"
BTN_ORDER_SUBSCRIPTION = "🛡️ Орден"
BTN_SUPPORT           = "⚒️ Поддержка"
BTN_CART              = "🛒 Корзина"
BTN_SETTINGS          = "⚙️ Настройки"

ALL_CATEGORIES = -1


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


# ---------- Категории → страница каталога → карточка товара ----------

_CATEGORY_EMOJI = {
    "Протеин": emoji_ids.SWORD,
    "Гейнеры": emoji_ids.SHIELD,
    "Креатин": emoji_ids.DIAMOND,
    "Аминокислоты": emoji_ids.DROP,
    "L-карнитин": emoji_ids.DROP,
    "Витамины и минералы": emoji_ids.DIAMOND,
    "Жирные кислоты (Омега-3)": emoji_ids.DROP,
    "Предтренировочные комплексы": emoji_ids.SWORD,
    "Углеводы": emoji_ids.SHIELD,
}


def categories_keyboard_dict() -> dict:
    """Экран выбора категории — inline с эмодзи-иконками у каждой категории."""
    rows = []
    for idx, cat in enumerate(CATEGORIES):
        icon = _CATEGORY_EMOJI.get(cat, emoji_ids.SCROLL)
        rows.append([emoji_ui.build_emoji_button(
            cat, callback_data=f"cat:{idx}:0",
            icon_custom_emoji_id=icon,
        )])
    rows.append([emoji_ui.build_emoji_button(
        "Все товары", callback_data=f"cat:{ALL_CATEGORIES}:0",
        icon_custom_emoji_id=emoji_ids.SCROLL,
    )])
    return emoji_ui.build_emoji_keyboard(rows)


def catalog_page_keyboard_dict(page: int, user_id: int, cat_idx: int) -> dict:
    """
    Страница каталога: товары + пагинация + возврат.
    Каждый товар — с иконкой ⚔️ (или ✅ если уже в корзине).
    """
    category = category_by_index(cat_idx) if cat_idx != ALL_CATEGORIES else None
    cart_ids = get_cart(user_id)
    rows = []
    for product in get_page(page, category):
        in_cart = product["id"] in cart_ids
        # Знак наличия в корзине — обычным префиксом в тексте (эмодзи-иконка
        # у кнопки одна на кнопку, поэтому статус пишем словами/символом).
        prefix = "✓ " if in_cart else ""
        rows.append([emoji_ui.build_emoji_button(
            f"{prefix}{product['name']} — {product['price']} ₽",
            callback_data=f"view:{product['id']}:{page}:{cat_idx}",
            icon_custom_emoji_id=emoji_ids.SWORD,
        )])

    nav_row = []
    if page > 0:
        nav_row.append(emoji_ui.build_emoji_button(
            "Назад", callback_data=f"cat:{cat_idx}:{page - 1}",
            icon_custom_emoji_id=emoji_ids.SCROLL,
        ))
    if page < total_pages(category) - 1:
        nav_row.append(emoji_ui.build_emoji_button(
            "Далее", callback_data=f"cat:{cat_idx}:{page + 1}",
            icon_custom_emoji_id=emoji_ids.SCROLL,
        ))
    if nav_row:
        rows.append(nav_row)

    rows.append([emoji_ui.build_emoji_button(
        "К категориям", callback_data="catlist",
        icon_custom_emoji_id=emoji_ids.SCROLL,
    )])
    return emoji_ui.build_emoji_keyboard(rows)


def product_card_keyboard_dict(product_id: int, page: int, cat_idx: int, user_id: int) -> dict:
    """
    Кнопки под карточкой товара. Если товар уже в корзине — кнопка сама
    показывает количество вместо "Добавить в корзину" (нажатие повторно
    добавляет ещё одну штуку).
    """
    qty = get_cart(user_id).get(product_id, 0)

    if qty > 0:
        label = f"В корзине: {qty} шт. (нажми, чтобы добавить ещё)"
        icon = emoji_ids.DIAMOND
    else:
        label = "Взять в оружейную"
        icon = emoji_ids.SWORD

    rows = [
        [emoji_ui.build_emoji_button(
            label, callback_data=f"add:{product_id}:{page}:{cat_idx}",
            icon_custom_emoji_id=icon,
            style="success" if qty == 0 else None,
        )],
        [emoji_ui.build_emoji_button(
            "К каталогу", callback_data=f"cat:{cat_idx}:{page}",
            icon_custom_emoji_id=emoji_ids.SCROLL,
        )],
    ]
    return emoji_ui.build_emoji_keyboard(rows)


# ---------- Настройки ----------

def settings_keyboard_dict() -> dict:
    """Три пункта настроек — согласие, поддержка, выход."""
    return emoji_ui.build_emoji_keyboard([
        [emoji_ui.build_emoji_button(
            "Согласие на обработку ПД", callback_data="settings:consent",
            icon_custom_emoji_id=emoji_ids.NEWS,
        )],
        [emoji_ui.build_emoji_button(
            "Поддержка", callback_data="settings:support",
            icon_custom_emoji_id=emoji_ids.PENCIL,
        )],
        [emoji_ui.build_emoji_button(
            "Выход", callback_data="settings:exit",
            icon_custom_emoji_id=emoji_ids.SCROLL,
        )],
    ])


def settings_consent_back_keyboard_dict() -> dict:
    """Кнопка 'Назад' на экране просмотра ранее принятого согласия."""
    return emoji_ui.build_emoji_keyboard([[
        emoji_ui.build_emoji_button(
            "Назад", callback_data="settings:back",
            icon_custom_emoji_id=emoji_ids.SCROLL,
        )
    ]])


# ---------- Корзина ----------

def cart_keyboard_dict(user_id: int) -> dict:
    """
    Корзина: каждая позиция — своя кнопка с 'убрать',
    внизу — кнопка оформления. Умеет и обычные товары, и паки.
    """
    cart = get_cart(user_id)
    rows = []
    for pid, qty in cart.items():
        if packs.is_pack_id(pid):
            pack = packs.get_pack(pid)
            if not pack:
                continue
            label = f"Убрать сундук «{pack['name']}» ({qty} шт.)"
            icon = emoji_ids.SHIELD
        else:
            product = PRODUCTS_BY_ID.get(pid)
            if not product:
                continue
            label = f"Убрать {product['name']} ({qty} шт.)"
            icon = emoji_ids.SWORD
        rows.append([emoji_ui.build_emoji_button(
            label, callback_data=f"remove:{pid}",
            icon_custom_emoji_id=icon, style="danger",
        )])
    if cart:
        rows.append([emoji_ui.build_emoji_button(
            "Оформить заказ", callback_data="checkout",
            icon_custom_emoji_id=emoji_ids.DIAMOND, style="success",
        )])
    return emoji_ui.build_emoji_keyboard(rows)


# ---------- Consent (согласие при первом /start) ----------

def consent_keyboard_dict() -> dict:
    """Единственная кнопка 'Принимаю' в первом сообщении с оферой."""
    return emoji_ui.build_emoji_keyboard([[
        emoji_ui.build_emoji_button(
            "Принимаю", callback_data="consent_accept",
            icon_custom_emoji_id=emoji_ids.NEWS, style="success",
        )
    ]])


# ---------- Орден: статус подписки + вход в Военные Сундуки ----------

def order_menu_keyboard_dict(has_subscription: bool) -> dict:
    """
    Главный экран Ордена: кнопка входа в Военные Сундуки (доступна всегда —
    состав посмотреть можно без подписки) и кнопка оплаты/статуса подписки.
    """
    rows = [
        [emoji_ui.build_emoji_button(
            "Военные Сундуки", callback_data="packs_list",
            icon_custom_emoji_id=emoji_ids.SHIELD,
        )],
    ]
    if has_subscription:
        rows.append([emoji_ui.build_emoji_button(
            "Подписка активна", callback_data="noop",
            icon_custom_emoji_id=emoji_ids.DIAMOND, style="success",
        )])
    else:
        rows.append([emoji_ui.build_emoji_button(
            "Оплатить подписку", callback_data="subscribe_pay",
            icon_custom_emoji_id=emoji_ids.DIAMOND, style="success",
        )])
    return emoji_ui.build_emoji_keyboard(rows)


# ---------- Военные Сундуки (паки) ----------

def packs_list_keyboard_dict() -> dict:
    """Три сундука на выбор + возврат в Орден."""
    rows = []
    for pack in packs.PACKS:
        rows.append([emoji_ui.build_emoji_button(
            f"{pack['name']} — {pack['bundle_price']} ₽",
            callback_data=f"pack:{pack['id']}",
            icon_custom_emoji_id=emoji_ids.SHIELD,
        )])
    rows.append([emoji_ui.build_emoji_button(
        "В Орден", callback_data="order_menu",
        icon_custom_emoji_id=emoji_ids.SCROLL,
    )])
    return emoji_ui.build_emoji_keyboard(rows)


def pack_detail_keyboard_dict(pack_id: int, has_subscription: bool) -> dict:
    """
    Под карточкой сундука. Состав виден всем — но добавить в корзину
    может только подписчик Ордена. Без подписки вместо кнопки добавления
    показывается кнопка перехода к оплате подписки.
    """
    if has_subscription:
        action_row = [emoji_ui.build_emoji_button(
            "Взять сундук в поход", callback_data=f"pack_add:{pack_id}",
            icon_custom_emoji_id=emoji_ids.DIAMOND, style="success",
        )]
    else:
        action_row = [emoji_ui.build_emoji_button(
            "Нужна подписка Ордена", callback_data="subscribe_pay",
            icon_custom_emoji_id=emoji_ids.SHIELD, style="primary",
        )]
    return emoji_ui.build_emoji_keyboard([
        action_row,
        [emoji_ui.build_emoji_button(
            "Отмена", callback_data="packs_list",
            icon_custom_emoji_id=emoji_ids.SCROLL,
        )],
    ])


# ---------- Совместимость со старым кодом ----------
def categories_keyboard() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=1)
    for idx, cat in enumerate(CATEGORIES):
        markup.add(types.InlineKeyboardButton(cat, callback_data=f"cat:{idx}:0"))
    markup.add(types.InlineKeyboardButton("📋 Все товары", callback_data=f"cat:{ALL_CATEGORIES}:0"))
    return markup
