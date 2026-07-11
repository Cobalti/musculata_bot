"""
Простая корзина в оперативной памяти.
ВНИМАНИЕ: при перезапуске бота все корзины обнуляются.
Для продакшена (много пользователей, важно не терять корзину) нужно
перенести это в базу данных (SQLite/PostgreSQL) — это отдельный
следующий шаг, не входит в текущий MVP.
"""

from products import PRODUCTS_BY_ID
import packs

# {user_id: {product_id: qty}}
_carts: dict[int, dict[int, int]] = {}


def get_cart(user_id: int) -> dict[int, int]:
    return _carts.setdefault(user_id, {})


def add_item(user_id: int, product_id: int, qty: int = 1):
    cart = get_cart(user_id)
    cart[product_id] = cart.get(product_id, 0) + qty


def remove_item(user_id: int, product_id: int):
    cart = get_cart(user_id)
    if product_id in cart:
        del cart[product_id]


def clear_cart(user_id: int):
    _carts[user_id] = {}


def cart_count(user_id: int) -> int:
    return sum(get_cart(user_id).values())


def _lookup(item_id: int) -> dict | None:
    """
    Универсальный поиск позиции — сначала среди обычных товаров,
    потом среди паков. Так cart.py становится независим от того,
    что именно лежит в корзине.
    """
    if packs.is_pack_id(item_id):
        return packs.pack_as_cart_item(item_id)
    return PRODUCTS_BY_ID.get(item_id)


def cart_total(user_id: int) -> int:
    cart = get_cart(user_id)
    total = 0
    for pid, qty in cart.items():
        item = _lookup(pid)
        if item:
            total += item["price"] * qty
    return total


def cart_text(user_id: int) -> str:
    """
    HTML-текст корзины с кастомными эмодзи (⚔️ у товаров, 🛡 у сундуков).
    Используется в обработчике 'Корзина' в main.py — там отправляется с parse_mode
    и через emoji_ui, так что HTML-теги здесь корректны.
    """
    import emoji_ids as _e
    _sword = f'<tg-emoji emoji-id="{_e.SWORD}">⚔️</tg-emoji>'
    _shield = f'<tg-emoji emoji-id="{_e.SHIELD}">🛡</tg-emoji>'
    _diamond = f'<tg-emoji emoji-id="{_e.DIAMOND}">💎</tg-emoji>'

    cart = get_cart(user_id)
    if not cart:
        return f"{_shield} <b>Оружейная пуста, соратник.</b>\nЗагляни в каталог, чтобы собрать снаряжение."

    lines = [f"{_shield} <b>Твоя оружейная:</b>\n"]
    for pid, qty in cart.items():
        item = _lookup(pid)
        if not item:
            continue
        icon = _shield if packs.is_pack_id(pid) else _sword
        lines.append(f"{icon} {item['name']} — {qty} шт. × {item['price']} ₽ = <b>{item['price'] * qty} ₽</b>")
    lines.append(f"\n{_diamond} <b>Итого: {cart_total(user_id)} ₽</b>")
    return "\n".join(lines)
