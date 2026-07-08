"""
Простая корзина в оперативной памяти.
ВНИМАНИЕ: при перезапуске бота все корзины обнуляются.
Для продакшена (много пользователей, важно не терять корзину) нужно
перенести это в базу данных (SQLite/PostgreSQL) — это отдельный
следующий шаг, не входит в текущий MVP.
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
    cart = get_cart(user_id)
    if not cart:
        return "🛒 Корзина пуста, соратник."

    lines = ["🛒 Твоя корзина:\n"]
    for pid, qty in cart.items():
        product = PRODUCTS_BY_ID.get(pid)
        if not product:
            continue
        lines.append(f"• {product['name']} — {qty} шт. × {product['price']} ₽ = {product['price'] * qty} ₽")
    lines.append(f"\nИтого: {cart_total(user_id)} ₽")
    return "\n".join(lines)
