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
import subscription_tiers

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
    "Протеин (сывороточный)": emoji_ids.SWORD,
    "Протеин(казеиновый)": emoji_ids.SWORD,
    "Гейнеры": emoji_ids.SHIELD,
    "Креатин": emoji_ids.DIAMOND,
    "Цинк": emoji_ids.DROP,
    "L-карнитин": emoji_ids.DROP,
    "Витамины и минералы": emoji_ids.DIAMOND,
    "Жирные кислоты (Омега-3)": emoji_ids.DROP,
    "Предтренировочные комплексы": emoji_ids.POTION_RED,
    "Углеводы": emoji_ids.SHIELD,
    "Аргинин (AAKG)": emoji_ids.POTION_BLUE,
    "Бета-Аланин": emoji_ids.POTION_ORANGE,
    "Глутамин": emoji_ids.POTION_GREEN,
    "Цитруллин": emoji_ids.POTION_PURPLE,
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
    # Военные Сундуки — готовые наборы. Живут в каталоге как обычные
    # товары (подписка не нужна), внизу списка категорий.
    rows.append([emoji_ui.build_emoji_button(
        "Военные Сундуки", callback_data="packs_list",
        icon_custom_emoji_id=emoji_ids.SHIELD,
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
        # Цену намеренно не показываем здесь — она появляется только на
        # карточке товара после нажатия (там же фото и полное описание).
        prefix = "✓ " if in_cart else ""
        rows.append([emoji_ui.build_emoji_button(
            f"{prefix}{product['name']}",
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
    """
    Экран просмотра ранее принятого согласия: кнопка отзыва (ведёт на
    подтверждение — отзыв это серьёзное действие, блокирующее весь бот)
    и кнопка назад.
    """
    return emoji_ui.build_emoji_keyboard([
        [emoji_ui.build_emoji_button(
            "Отозвать согласие", callback_data="settings:revoke_confirm",
            icon_custom_emoji_id=emoji_ids.SHIELD, style="danger",
        )],
        [emoji_ui.build_emoji_button(
            "Назад", callback_data="settings:back",
            icon_custom_emoji_id=emoji_ids.SCROLL,
        )],
    ])


def settings_revoke_confirm_keyboard_dict() -> dict:
    """Подтверждение отзыва — отдельный шаг, чтобы не отозвать случайным тапом."""
    return emoji_ui.build_emoji_keyboard([
        [emoji_ui.build_emoji_button(
            "Да, отозвать", callback_data="settings:revoke_do",
            icon_custom_emoji_id=emoji_ids.SHIELD, style="danger",
        )],
        [emoji_ui.build_emoji_button(
            "Отмена", callback_data="settings:consent",
            icon_custom_emoji_id=emoji_ids.SCROLL,
        )],
    ])


# ---------- Корзина ----------

def cart_keyboard_dict(user_id: int) -> dict:
    """Корзина: каждая позиция — своя кнопка с 'убрать', внизу — кнопка оформления."""
    cart = get_cart(user_id)
    rows = []
    for pid, qty in cart.items():
        if packs.is_pack_id(pid):
            pack = packs.get_pack(pid)
            if not pack:
                continue
            label, icon = f"Убрать сундук «{pack['name']}» ({qty} шт.)", emoji_ids.SHIELD
        else:
            product = PRODUCTS_BY_ID.get(pid)
            if not product:
                continue
            label, icon = f"Убрать {product['name']} ({qty} шт.)", emoji_ids.SWORD
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


# ---------- Орден: уровни подписки ----------

def order_menu_keyboard_dict(has_subscription: bool) -> dict:
    """
    Главный экран Ордена. Показывает три уровня подписки; если подписка
    уже активна — уровни всё равно видны (можно посмотреть, что даёт
    следующий), но помечено, какой активен.
    """
    # Три "сундука" из набора эмодзи подписаны автором именно как базовый/
    # средний/премиум набор ОРДЕНА — по порядку совпадают с TIERS
    # (Оруженосец/Рыцарь/Военачальник), поэтому у каждого уровня своя иконка
    # вместо одного и того же SHIELD на все три.
    tier_icons = [emoji_ids.BOX_BASIC, emoji_ids.BOX_MEDIUM, emoji_ids.BOX_PREMIUM]

    rows = []
    for idx, tier in enumerate(subscription_tiers.TIERS):
        rows.append([emoji_ui.build_emoji_button(
            f"{tier['name']} — {tier['price_year']:,} ₽/год".replace(",", " "),
            callback_data=f"tier:{tier['id']}",
            icon_custom_emoji_id=tier_icons[idx] if idx < len(tier_icons) else emoji_ids.SHIELD,
        )])
    if has_subscription:
        rows.append([emoji_ui.build_emoji_button(
            "Моя подписка", callback_data="my_subscription",
            icon_custom_emoji_id=emoji_ids.DIAMOND, style="success",
        )])
    return emoji_ui.build_emoji_keyboard(rows)


def tier_detail_keyboard_dict(tier_id: int, has_subscription: bool, is_current: bool) -> dict:
    """
    Под карточкой уровня. Оформить можно, только если подписки ещё нет.
    Если уже подписан на этот же уровень — статус-кнопка. Если подписан
    на другой — смена тарифа заявлена в Excel, но механика перерасчёта
    ещё не согласована с сайтом, поэтому пока просто сообщаем об этом.
    """
    tier = subscription_tiers.get_tier(tier_id)
    price = f"{tier['price_year']:,}".replace(",", " ") if tier else "?"

    if is_current:
        action = emoji_ui.build_emoji_button(
            "Твой текущий уровень", callback_data="noop",
            icon_custom_emoji_id=emoji_ids.DIAMOND, style="success",
        )
    elif has_subscription:
        action = emoji_ui.build_emoji_button(
            "Сменить тариф", callback_data=f"tier_switch:{tier_id}",
            icon_custom_emoji_id=emoji_ids.SWORD, style="primary",
        )
    else:
        action = emoji_ui.build_emoji_button(
            f"Вступить в Орден — {price} ₽/год", callback_data=f"tier_pay:{tier_id}",
            icon_custom_emoji_id=emoji_ids.DIAMOND, style="success",
        )

    return emoji_ui.build_emoji_keyboard([
        [action],
        [emoji_ui.build_emoji_button(
            "К уровням", callback_data="order_menu",
            icon_custom_emoji_id=emoji_ids.SCROLL,
        )],
    ])


def my_subscription_keyboard_dict() -> dict:
    return emoji_ui.build_emoji_keyboard([[
        emoji_ui.build_emoji_button(
            "К уровням", callback_data="order_menu",
            icon_custom_emoji_id=emoji_ids.SCROLL,
        )
    ]])


# ---------- Военные Сундуки (паки) — в каталоге ----------

def packs_list_keyboard_dict(tier_id: int | None = None) -> dict:
    """Три сундука (Здоровье/Качалка/Эксклюзив) + возврат к категориям."""
    # Капля (здоровье/добавки), кинжал (тренировки), редкий изумруд-акцент
    # (эксклюзив). Специально не те же иконки, что у уровней Ордена (BOX_*),
    # чтобы паки и подписку не путать визуально.
    pack_icons = [emoji_ids.DROP, emoji_ids.DAGGER, emoji_ids.EMERALD]

    rows = []
    for idx, pack in enumerate(packs.PACKS):
        price = packs.price_for(pack["id"], tier_id)
        rows.append([emoji_ui.build_emoji_button(
            f"{pack['name']} — {price:,} ₽".replace(",", " "),
            callback_data=f"pack:{pack['id']}",
            icon_custom_emoji_id=pack_icons[idx] if idx < len(pack_icons) else emoji_ids.SHIELD,
        )])
    rows.append([emoji_ui.build_emoji_button(
        "К категориям", callback_data="catlist",
        icon_custom_emoji_id=emoji_ids.SCROLL,
    )])
    return emoji_ui.build_emoji_keyboard(rows)


def pack_detail_keyboard_dict(pack_id: int, user_id: int) -> dict:
    """Под карточкой сундука: добавить в корзину (доступно всем) и назад."""
    qty = get_cart(user_id).get(pack_id, 0)
    if qty > 0:
        label = f"В корзине: {qty} шт. (нажми, чтобы добавить ещё)"
        icon, style = emoji_ids.DIAMOND, None
    else:
        label = "Взять сундук в поход"
        icon, style = emoji_ids.SHIELD, "success"

    return emoji_ui.build_emoji_keyboard([
        [emoji_ui.build_emoji_button(
            label, callback_data=f"pack_add:{pack_id}",
            icon_custom_emoji_id=icon, style=style,
        )],
        [emoji_ui.build_emoji_button(
            "К сундукам", callback_data="packs_list",
            icon_custom_emoji_id=emoji_ids.SCROLL,
        )],
    ])


# ---------- Совместимость со старым кодом ----------
def categories_keyboard() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=1)
    for idx, cat in enumerate(CATEGORIES):
        markup.add(types.InlineKeyboardButton(cat, callback_data=f"cat:{idx}:0"))
    markup.add(types.InlineKeyboardButton("📋 Все товары", callback_data=f"cat:{ALL_CATEGORIES}:0"))
    markup.add(types.InlineKeyboardButton("🛡 Военные Сундуки", callback_data="packs_list"))
    return markup
