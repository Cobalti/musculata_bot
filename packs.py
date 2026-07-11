"""
packs.py — готовые "паки" (наборы товаров) со скидкой 15%.

Данные взяты один-в-один из Excel-файла заказчика (лист "Паки"):
БАЗОВЫЙ, ПРОДВИНУТЫЙ, ПРЕМИУМ.

ВАЖНО — как это едет в корзину:
Товары внутри пака могут ЕЩЁ НЕ БЫТЬ в products.json (сейчас в каталоге
45 товаров, а часть позиций пака — например, "Optimum Nutrition
Creatine 2500 mg 100 caps" — отсутствует). Поэтому пак кладётся в
корзину как один "виртуальный товар" (id = PACK_ID_OFFSET + N), а не
дробится на отдельные позиции. Cайту Фёдора в items уйдёт этот
единственный ID, а полный состав пака должен быть прописан у него
на стороне отдельно (согласовать с ним при финальной интеграции).

Если решите поменять формат передачи (например, слать не ID пака,
а список ID реальных товаров) — здесь достаточно поправить функцию
pack_as_cart_item(); всё остальное трогать не надо.
"""

# Отдельный диапазон ID, чтобы паки не пересекались с обычными товарами.
PACK_ID_OFFSET = 10000


def _pack(pack_id: int, name: str, tagline: str, items: list[tuple[str, str, int]],
           bundle_price: int) -> dict:
    """Собирает пак с автоматическим расчётом розничной суммы и экономии."""
    retail_total = sum(price for _, _, price in items)
    return {
        "id": pack_id,
        "name": name,
        "tagline": tagline,
        "items": [{"name": n, "brand": b, "price": p} for n, b, p in items],
        "retail_total": retail_total,
        "bundle_price": bundle_price,
        "savings": retail_total - bundle_price,
    }


PACKS = [
    _pack(
        pack_id=PACK_ID_OFFSET + 1,
        name="Базовый",
        tagline="Стартовый доспех для новобранца",
        items=[
            ("Trec Nutrition WHEY 100 900g (шоколад)", "Trec", 4512),
            ("Trec Nutrition CREATINE 100% 300g", "Trec", 2200),
            ("NOW FLAX OIL ORGANIC 1000mg 100 SGELS", "NOW Foods", 2703),
            ("Nature Foods ZMA+B6 100 caps", "Nature Foods", 2200),
        ],
        bundle_price=9873,
    ),
    _pack(
        pack_id=PACK_ID_OFFSET + 2,
        name="Продвинутый",
        tagline="Клинок бывалого воина",
        items=[
            ("Scitec Nutrition 100% Whey Protein Prof. 1000g", "Scitec", 5721),
            ("Optimum Nutrition Creatine 2500 mg 100 caps", "ON", 4200),
            ("Nature Foods PUMP 30 packs", "Nature Foods", 2278),
            ("Applied Nutrition Critical Mass 2.4kg (клубника)", "Applied Nutrition", 5200),
        ],
        bundle_price=14789,
    ),
    _pack(
        pack_id=PACK_ID_OFFSET + 3,
        name="Премиум",
        tagline="Легендарный арсенал магистра",
        items=[
            ("Optimum Nutrition 100% Whey Gold standard 5lb", "ON", 19600),
            ("Trec Nutrition CASEIN 100 600g", "Trec", 3966),
            ("Optimum Nutrition Creatine 2500 mg 200 caps", "ON", 5700),
            ("Nature Foods Citrulline Malate 200g (порошок)", "Nature Foods", 2200),
            ("Universal Animal Flex (44 packs)", "Universal", 7293),
        ],
        bundle_price=32945,
    ),
]

PACKS_BY_ID = {p["id"]: p for p in PACKS}


def is_pack_id(item_id: int) -> bool:
    return item_id in PACKS_BY_ID


def get_pack(pack_id: int) -> dict | None:
    return PACKS_BY_ID.get(pack_id)


def pack_as_cart_item(pack_id: int) -> dict:
    """
    Представление пака для корзины. Используется в cart.cart_text() —
    чтобы корзина знала, как показать пак (у него нет обычной карточки
    товара с ценой из products.json).
    """
    p = PACKS_BY_ID.get(pack_id)
    if not p:
        return {"id": pack_id, "name": "Неизвестный пак", "price": 0}
    return {"id": pack_id, "name": f"Пак «{p['name']}»", "price": p["bundle_price"]}
