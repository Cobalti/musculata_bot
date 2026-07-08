from urllib.parse import urlencode
from config import CHECKOUT_BASE_URL, DEFAULT_DISCOUNT_PERCENT
from cart import get_cart, cart_total
from products import PRODUCTS_BY_ID


def price_breakdown(user_id: int, discount_percent: int = DEFAULT_DISCOUNT_PERCENT):
    """
    Возвращает (сумма_без_скидки, размер_скидки, итог_к_оплате).
    Скидка — за использование бота, сейчас фиксированная для всех (10%).
    """
    subtotal = cart_total(user_id)
    discount_amount = round(subtotal * discount_percent / 100)
    final_total = subtotal - discount_amount
    return subtotal, discount_amount, final_total


def build_checkout_url(user_id: int, discount_percent: int = DEFAULT_DISCOUNT_PERCENT) -> str | None:
    """
    Формирует ссылку на оформление заказа с товарами корзины.

    ВАЖНО: формат параметра items (product_id:qty,product_id:qty) —
    предположительный, по примеру из ТЗ. Нужно подтвердить у технического
    специалиста MashinaBody, что сайт реально ожидает такой формат
    (может понадобиться артикул товара вместо внутреннего id бота,
    другой разделитель, другое имя параметра для купона и т.д.)
    """
    cart = get_cart(user_id)
    if not cart:
        return None

    items_parts = []
    for pid, qty in cart.items():
        product = PRODUCTS_BY_ID.get(pid)
        if not product:
            continue
        # TODO: заменить product["id"] на реальный артикул WooCommerce,
        # когда будет получен формат от сайта.
        items_parts.append(f"{product['id']}:{qty}")

    params = {
        "items": ",".join(items_parts),
        "discount": discount_percent,
        "client": user_id,
    }
    return f"{CHECKOUT_BASE_URL}?{urlencode(params)}"
