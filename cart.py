"""
Простая корзина в оперативной памяти.
ВНИМАНИЕ: при перезапуске бота все корзины обнуляются.
Для продакшена (много пользователей, важно не терять корзину) нужно
перенести это в базу данных (SQLite/PostgreSQL) — это отдельный
следующий шаг, не входит в текущий MVP.

ВАЖНО: паки ("Военные Сундуки") сюда НЕ попадают — это тарифы подписки
Ордена, они оплачиваются напрямую на сайте (см. main.py,
handle_pack_subscribe), минуя корзину. Корзина работает только
с обычными товарами каталога.
"""

from products import PRODUCTS_BY_ID

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


def cart_total(user_id: int) -> int:
    cart = get_cart(user_id)
    total = 0
    for pid, qty in cart.items():
        product = PRODUCTS_BY_ID.get(pid)
        if product:
            total += product["price"] * qty
    return total


def cart_text(user_id: int) -> str:
    """
    HTML-текст корзины с кастомными эмодзи. Используется в обработчике
    'Корзина' в main.py — там отправляется через emoji_ui, так что
    HTML-теги здесь корректны.
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
        product = PRODUCTS_BY_ID.get(pid)
        if not product:
            continue
        lines.append(f"{_sword} {product['name']} — {qty} шт. × {product['price']} ₽ = <b>{product['price'] * qty} ₽</b>")
    lines.append(f"\n{_diamond} <b>Итого: {cart_total(user_id)} ₽</b>")
    return "\n".join(lines)
