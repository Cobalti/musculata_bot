"""
Простая корзина в оперативной памяти.
ВНИМАНИЕ: при перезапуске бота все корзины обнуляются.
Для продакшена (много пользователей, важно не терять корзину) стоит
перенести это в PostgreSQL — база на bothost уже доступна, см. README.

В корзине могут лежать:
  - обычные товары каталога (products.json);
  - «Военные Сундуки» — паки (packs.py), id из диапазона PACK_ID_OFFSET.
Подписка Ордена в корзину НЕ кладётся — она оплачивается отдельно
на сайте (см. main.py, handle_tier_subscribe).

Цена паков зависит от уровня подписки пользователя (скидка 5/10/15%),
поэтому все функции подсчёта принимают user_id и сами узнают уровень.
"""

from products import PRODUCTS_BY_ID
import packs

# {user_id: {item_id: qty}}
_carts: dict[int, dict[int, int]] = {}


def get_cart(user_id: int) -> dict[int, int]:
    return _carts.setdefault(user_id, {})


def add_item(user_id: int, item_id: int, qty: int = 1):
    cart = get_cart(user_id)
    cart[item_id] = cart.get(item_id, 0) + qty


def remove_item(user_id: int, item_id: int):
    cart = get_cart(user_id)
    cart.pop(item_id, None)


def clear_cart(user_id: int):
    _carts[user_id] = {}


def cart_count(user_id: int) -> int:
    return sum(get_cart(user_id).values())


def _tier_of(user_id: int) -> int | None:
    """Активный уровень подписки — определяет скидку на паки."""
    import subscriptions_db
    return subscriptions_db.get_active_tier_id(user_id)


def lookup(item_id: int, tier_id: int | None = None) -> dict | None:
    """Единый поиск позиции: сначала обычные товары, потом паки."""
    if packs.is_pack_id(item_id):
        return packs.pack_as_cart_item(item_id, tier_id)
    return PRODUCTS_BY_ID.get(item_id)


def cart_total(user_id: int) -> int:
    tier_id = _tier_of(user_id)
    total = 0
    for item_id, qty in get_cart(user_id).items():
        item = lookup(item_id, tier_id)
        if item:
            total += item["price"] * qty
    return total


def cart_text(user_id: int) -> str:
    """HTML-текст корзины с кастомными эмодзи."""
    import emoji_ids as _e
    import subscription_tiers

    _sword = f'<tg-emoji emoji-id="{_e.SWORD}">⚔️</tg-emoji>'
    _shield = f'<tg-emoji emoji-id="{_e.SHIELD}">🛡</tg-emoji>'
    _diamond = f'<tg-emoji emoji-id="{_e.DIAMOND}">💎</tg-emoji>'

    cart = get_cart(user_id)
    if not cart:
        return (
            f"{_shield} <b>Оружейная пуста, соратник.</b>\n"
            "Загляни в каталог, чтобы собрать снаряжение."
        )

    tier_id = _tier_of(user_id)
    lines = [f"{_shield} <b>Твоя оружейная:</b>\n"]
    for item_id, qty in cart.items():
        item = lookup(item_id, tier_id)
        if not item:
            continue
        icon = _shield if packs.is_pack_id(item_id) else _sword
        lines.append(
            f"{icon} {item['name']} — {qty} шт. × {item['price']} ₽ = <b>{item['price'] * qty} ₽</b>"
        )

    # Если подписка даёт скидку на паки — честно показываем это в корзине,
    # иначе пользователь не поймёт, почему цена сундука ниже, чем в каталоге.
    has_pack = any(packs.is_pack_id(i) for i in cart)
    discount = subscription_tiers.pack_discount_for(tier_id)
    if has_pack and discount:
        tier = subscription_tiers.get_tier(tier_id)
        lines.append(
            f"\n{_diamond} <i>Скидка {int(discount * 100)}% на сундуки — "
            f"уровень «{tier['name']}»</i>"
        )

    lines.append(f"\n{_diamond} <b>Итого: {cart_total(user_id)} ₽</b>")
    return "\n".join(lines)
