"""
packs.py — «Военные Сундуки»: готовые наборы товаров со скидкой 15%
против розницы. Живут в КАТАЛОГЕ как обычные товары.

ВАЖНО: это ОБНОВЛЁННЫЕ версии трёх исходных паков, а НЕ дополнительные
наборы. Заказчик переименовал и обновил состав:
    Базовый     → Здоровье   (id 10001)
    Продвинутый → Качалка    (id 10002)
    Премиум     → Эксклюзив  (id 10003)
Старые названия и старые составы (Trec/Scitec/Optimum whey-наборы)
БОЛЬШЕ НЕ ДЕЙСТВУЮТ — их полностью заменяют данные ниже, взятые из
Paki4.xlsx (серая таблица A1:U16: «1. здоровье/базовый», «2. качалка/
продвинутый», «3. эксклюзивные товары»). Всего 3 пака, не 6.

СВЯЗЬ С ЧЛЕНСТВОМ (Сообщество):
Пак может купить кто угодно, членство НЕ требуется. Но участникам
сообщества полагается дополнительная скидка на паки поверх базовой цены:
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

# Базовая скидка на все паки — 15%, одинаковая независимо от членства.
# Скидка уровня членства (5/10/15%) СКЛАДЫВАЕТСЯ с этой базовой и
# применяется одним разом от розничной суммы — см. pack_as_cart_item().
BASE_DISCOUNT = 0.15

# Решение от 13.08.26: "Паки +400р к стоимости все" — плоская надбавка
# сверху, одинаковая для всех трёх паков и независимо от уровня скидки
# членства (то есть скидка считается от розницы как обычно, а потом
# сверху добавляются эти 400 ₽ — надбавка НЕ участвует в скидке).
PRICE_SURCHARGE = 400


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
        pack_id=PACK_ID_OFFSET + 2,
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
        pack_id=PACK_ID_OFFSET + 3,
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
    Представление пака для корзины.

    Скидки СКЛАДЫВАЮТСЯ и применяются ОДНИМ РАЗОМ от розничной суммы —
    подтверждено заказчиком на примере: пак "Здоровье" (розница 15 954 ₽)
    для уровня "Военачальник" (база 15% + уровень 15% = 30%):
        15 954 × (1 − 30%) = 11 168 ₽
    (а НЕ последовательно: 15954×0.85×0.85 — так было раньше, это неверно).

    tier_id — id активного уровня членства пользователя (None = без членства,
    тогда действует только базовая скидка BASE_DISCOUNT).
    """
    import subscription_tiers

    p = PACKS_BY_ID.get(pack_id)
    if not p:
        return {"id": pack_id, "name": "Неизвестный сундук", "price": 0}

    tier_discount = subscription_tiers.pack_discount_for(tier_id)
    total_discount = BASE_DISCOUNT + tier_discount
    price = round(p["retail_total"] * (1 - total_discount)) + PRICE_SURCHARGE
    return {"id": pack_id, "name": f"Сундук «{p['name']}»", "price": price}


def price_for(pack_id: int, tier_id: int | None = None) -> int:
    """Итоговая цена пака с учётом скидки членства (складываются, см. pack_as_cart_item)."""
    return pack_as_cart_item(pack_id, tier_id)["price"]


def total_discount_percent(tier_id: int | None = None) -> int:
    """Суммарный процент скидки (база + уровень) — для отображения в тексте."""
    import subscription_tiers
    tier_discount = subscription_tiers.pack_discount_for(tier_id)
    return int(round((BASE_DISCOUNT + tier_discount) * 100))
