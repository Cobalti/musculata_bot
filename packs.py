"""
packs.py — «Военные Сундуки»: готовые наборы товаров со скидкой 15%
против розницы. Живут в КАТАЛОГЕ как обычные товары.

Данные один-в-один из Excel заказчика:
БАЗОВЫЙ, ПРОДВИНУТЫЙ, ПРЕМИУМ (лист «Паки»),
ЗДОРОВЬЕ, КАЧАЛКА, ЭКСКЛЮЗИВ (Paki4.xlsx, серая таблица A1:U16 —
доп. наборы «здоровье/базовый», «качалка/продвинутый», «эксклюзивные
товары»). Итого 6 наборов.

СВЯЗЬ С ПОДПИСКОЙ (Орден):
Пак может купить кто угодно, подписка НЕ требуется. Но подписчикам
Ордена полагается дополнительная скидка на паки поверх базовой цены:
5% (Оруженосец) / 10% (Рыцарь) / 15% (Военачальник) —
см. subscription_tiers.pack_discount_for().

Пак кладётся в корзину как одна позиция (виртуальный товар с id из
диапазона PACK_ID_OFFSET), а не дробится на отдельные товары —
у Фёдора на стороне это будет «набор/бандл» (он обсуждает эту схему).
Часть товаров внутри паков может отсутствовать в products.json — это
нормально, состав пака ведётся здесь и на стороне сайта.
"""

# Отдельный диапазон ID — исторически использовался и как id корзины,
# сейчас просто уникальный идентификатор тарифа/пака.
PACK_ID_OFFSET = 10000


def _pack(pack_id: int, name: str, tagline: str, items: list[tuple[str, str, int]],
           bundle_price: int, gift: str | None = None) -> dict:
    """
    Собирает пак с автоматическим расчётом розничной суммы и экономии.
    gift — бонусный подарок к набору (например, таблетница, шейкер) —
    не входит в расчёт розницы/экономии, чисто информационная строка.
    """
    retail_total = sum(price for _, _, price in items)
    return {
        "id": pack_id,
        "name": name,
        "tagline": tagline,
        "items": [{"name": n, "brand": b, "price": p} for n, b, p in items],
        "retail_total": retail_total,
        "bundle_price": bundle_price,
        "savings": retail_total - bundle_price,
        "gift": gift,
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
    # ---- Новые наборы (добавлены из Paki4.xlsx, серая таблица A1:U16) ----
    _pack(
        pack_id=PACK_ID_OFFSET + 4,
        name="Здоровье",
        tagline="Забота о теле для долгой службы",
        items=[
            ("NOW Foods Omega 3", "NOW Foods", 2703),
            ("Maxler Magnesium Glycinate Liquid 25 ml х14", "Maxler", 2552),
            ("Maxler Daily Max/Women", "Maxler", 2100),
            ("Nature Foods GABA 500mg 90 caps", "Nature Foods", 2200),
            ("Nature Foods Zinc Picolinate 60 caps", "Nature Foods", 2200),
            ("Bounty Protein Powder", "Mars Inc.", 4199),
        ],
        bundle_price=13561,
        gift="Таблетница в подарок (~500 ₽)",
    ),
    _pack(
        pack_id=PACK_ID_OFFSET + 5,
        name="Качалка",
        tagline="Снаряжение для взятия зала штурмом",
        items=[
            ("Applied Nutrition Whey 2200g", "Applied Nutrition", 11000),
            ("Nature Foods Creatine 500g", "Nature Foods", 3590),
            ("Optimum Nutrition Opti-Women/Opti-Men 60 caps", "Optimum Nutrition", 3690),
            ("Trec Nutrition Citrulline 240 порошок (арбуз)", "Trec Nutrition", 2278),
            ("Nature Foods Multi PM", "Nature Foods", 2890),
            ("NOW Foods Super Omega 3/3D", "NOW Foods", 3590),
        ],
        bundle_price=22982,
        gift="Шейкер в подарок",
    ),
    _pack(
        pack_id=PACK_ID_OFFSET + 6,
        name="Эксклюзив",
        tagline="Редкие трофеи, которых нет в обычной оружейной",
        items=[
            ("Maxler/NOW Krealkalin", "Maxler", 5500),
            ("Mutant ZM8+ 90 caps", "Mutant", 3200),
            ("Maxler Marine Collagen Hyaluronic Acid Complex 60 softgels", "Maxler", 3200),
            ("Applied Nutrition ISO-XP 850 Gr", "Applied Nutrition", 6752),
            ("Trec Nutrition L-Carnitine 3000 1000 ml", "Trec Nutrition", 3600),
            ("Trec Nutrition Vitargo Electro Energy 1050g", "Trec Nutrition", 5500),
            ("Nature Foods Libidobooster Men's Formula 60 caps", "Nature Foods", 2400),
        ],
        bundle_price=25629,
    ),
]

PACKS_BY_ID = {p["id"]: p for p in PACKS}


def is_pack_id(item_id: int) -> bool:
    return item_id in PACKS_BY_ID


def get_pack(pack_id: int) -> dict | None:
    return PACKS_BY_ID.get(pack_id)


def pack_as_cart_item(pack_id: int, tier_id: int | None = None) -> dict:
    """
    Представление пака для корзины. Если у пользователя активна подписка
    Ордена — цена пересчитывается с учётом скидки уровня (5/10/15%).

    tier_id — id активного уровня подписки пользователя (None = без подписки).
    """
    import subscription_tiers

    p = PACKS_BY_ID.get(pack_id)
    if not p:
        return {"id": pack_id, "name": "Неизвестный сундук", "price": 0}

    discount = subscription_tiers.pack_discount_for(tier_id)
    price = round(p["bundle_price"] * (1 - discount))
    return {"id": pack_id, "name": f"Сундук «{p['name']}»", "price": price}


def price_for(pack_id: int, tier_id: int | None = None) -> int:
    """Итоговая цена пака с учётом скидки подписки."""
    return pack_as_cart_item(pack_id, tier_id)["price"]
