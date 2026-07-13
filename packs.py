"""
packs.py — тарифы подписки "Орден" (Военные Сундуки), каждый со своим
фиксированным набором товаров и годовой ценой.

Данные взяты один-в-один из Excel-файла заказчика (лист "Паки"):
БАЗОВЫЙ, ПРОДВИНУТЫЙ, ПРЕМИУМ.

ВАЖНО: паки — это И ЕСТЬ подписка Ордена, а не товар для корзины.
Пользователь платит за годовую подписку на конкретный тариф; раз в
2 месяца (6 раз в год) ему по этому тарифу приходит доставка. Поэтому
тариф никогда не кладётся в cart.py — оплата идёт напрямую на сайт
(см. main.py, handle_pack_subscribe и integrations.create_subscription_order).

Товары внутри пака могут ЕЩЁ НЕ БЫТЬ в products.json (сейчас в каталоге
45 товаров, а часть позиций пака — например, "Optimum Nutrition
Creatine 2500 mg 100 caps" — отсутствует). Это ок, потому что пак
передаётся на сайт как единый "bundle_id" (см. integrations.py), а не
как список отдельных товаров, — состав должен быть также прописан
у Фёдора на его стороне (обсуждает в начале следующей недели).
"""

# Отдельный диапазон ID — исторически использовался и как id корзины,
# сейчас просто уникальный идентификатор тарифа/пака.
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
