"""
match_smeta_to_site.py — сопоставляет исходную смету заказчика (только
её товары должны продаваться через бота, по решению от 07.08) с реальным
каталогом сайта (real_products.json, полученным через fetch_catalog.py).

ЗАПУСКАТЬ НА СЕРВЕРЕ, в папке /opt/musculata_bot, где уже лежит
real_products.json (создан fetch_catalog.py).

Логика: для каждой позиции сметы ищем наиболее похожее по названию
совпадение среди всех товаров реального каталога (простое сравнение
строк, без интернета и внешних библиотек — только стандартная difflib).
Точных совпадений один-в-один быть не должно (названия у заказчика и
на сайте расходятся в мелочах — регистр, порядок слов, сокращения),
поэтому результат — это ЧЕРНОВИК для ручной проверки, а не готовый
финальный список.

Результат:
    matched_products.json   -- уверенные совпадения (готовы к проверке
                                и использованию как новый products.json)
    unmatched_smeta.json    -- позиции сметы, для которых НЕ нашлось
                                уверенного совпадения — нужно решать
                                руками (может, товара ещё нет на сайте,
                                может, просто нужно сопоставить вручную)

Использование:
    cd /opt/musculata_bot
    source venv/bin/activate
    python3 match_smeta_to_site.py
"""

import json
import difflib

# Порог уверенности совпадения (0.0-1.0). Ниже — считаем, что совпадения
# нет, лучше не угадать неправильно, чем протащить не тот товар.
CONFIDENCE_THRESHOLD = 0.55

# Смета заказчика — 46 позиций, "то, что мы выбирали" (решение от 07.08.26).
# Взято из SMETA_DLYa_BOTA43.xlsx.
SMETA_ITEMS = [
    {"name": "Applied Nutrition ISO-XP 850 Gr БАД", "category": "L-карнитин"},
    {"name": "Nature Foods ZMA+B6 100 caps", "category": "ZMA / Цинк"},
    {"name": "Nature Foods AAKG Powder 200g", "category": "Аргинин (AAKG)"},
    {"name": "Nature Foods Beta Alanine 90 caps", "category": "Бета-Аланин"},
    {"name": "Maxler Magnesium Glycinate Liquid 25 ml х14 БАД", "category": "Витамины и минералы"},
    {"name": "Universal Animal Flex (44 packs)", "category": "Витамины и минералы"},
    {"name": "Mutant GEAAR 14.8унции", "category": "Гейнеры"},
    {"name": "Nature Foods Gainer 5000g Ведро", "category": "Гейнеры"},
    {"name": "БАД Applied Nutrition Critical Mass Prof. 2.4kg (Клубника)", "category": "Гейнеры"},
    {"name": "БАД Applied Nutrition Critical Mass Prof. 2.4kg (Ваниль)", "category": "Гейнеры"},
    {"name": "БАД Applied Nutrition Critical Mass Prof. 2.4kg (Шоколад", "category": "Гейнеры"},
    {"name": "Reckful ® L-Glutamine 240g", "category": "Глутамин"},
    {"name": "NOW FLAX OIL ORGANIC 1000mg 90 SGELS", "category": "Жирные кислоты (Омега-3)"},
    {"name": "Nature Foods Creatine 200g (порошок)", "category": "Креатин"},
    {"name": "Nature Foods Creatine Hydrochloride 90 caps", "category": "Креатин"},
    {"name": "Optimum Nutrition Creatine 2500 mg 100 caps", "category": "Креатин"},
    {"name": "Optimum Nutrition Creatine 2500 mg 200 caps", "category": "Креатин"},
    {"name": "Maxler Krealkalyn 120 caps БАД", "category": "Креатин"},
    {"name": "Mutant L-Glutamine 300g", "category": "Креатин"},
    {"name": "БАД Trec Nutrition CREATINE 100% 300g", "category": "Креатин"},
    {"name": "Hell Labs Psychotic 210g (Сахарная вата)", "category": "Предтренировочные комплексы"},
    {"name": "БАД Trec Nutrition BOOGIEMAN 300g", "category": "Предтренировочные комплексы"},
    {"name": "БАД Scitec Nutrition Whey Protein Prof. 920g", "category": "Протеин"},
    {"name": "Dr.Hoffman Top Whey 908g", "category": "Протеин (сывороточный)"},
    {"name": "Scitec Nutrition 100% Whey Protein Prof. 1000g", "category": "Протеин (сывороточный)"},
    {"name": "Scitec Nutrition Whey Protein Prof. 2350g", "category": "Протеин (сывороточный)"},
    {"name": "Trec Nutrition WHEY 100 900g/700g клубника", "category": "Протеин (сывороточный)"},
    {"name": "Trec Nutrition WHEY 100 700g клубника", "category": "Протеин (сывороточный)"},
    {"name": "Trec Nutrition WHEY 100 900g шоколад", "category": "Протеин (сывороточный)"},
    {"name": "Trec Nutrition WHEY 100 900g ваниль", "category": "Протеин (сывороточный)"},
    {"name": "Trec Nutrition WHEY 100 900g печенье", "category": "Протеин (сывороточный)"},
    {"name": "Trec Nutrition WHEY 100 2275g клубника", "category": "Протеин (сывороточный)"},
    {"name": "Trec Nutrition WHEY 100 2275g шоколад", "category": "Протеин (сывороточный)"},
    {"name": "Trec Nutrition WHEY 100 2275g ваниль", "category": "Протеин (сывороточный)"},
    {"name": "Trec Nutrition WHEY 100 2275g печенье", "category": "Протеин (сывороточный)"},
    {"name": "Optimum Nutrition 100% Whey Gold standard 5lb", "category": "Протеин (сывороточный)"},
    {"name": "Mutant Whey 10lb", "category": "Протеин (сывороточный)"},
    {"name": "Mutant Whey 2lb", "category": "Протеин (сывороточный)"},
    {"name": "Mutant whey 5lb", "category": "Протеин (сывороточный)"},
    {"name": "БАД Trec Nutrition CASEIN 100 600g", "category": "Протеин(казеиновый)"},
    {"name": "Bounty protein Powder 875g", "category": "Сывороточный протеин"},
    {"name": "nature foods Amylopectin 1500g", "category": "Углеводы"},
    {"name": "Nature Foods Zinc Picolinate 60 caps", "category": "Цинк"},
    {"name": "Nature Foods Zinc Citrate+Vitamin С 100 caps", "category": "Цинк"},
    {"name": "Nature Foods Citrulline Malate 90 caps", "category": "Цитруллин"},
    {"name": "Nature Foods Citrulline Malate 200g (порошок)", "category": "Цитруллин"},
]


def normalize(s: str) -> str:
    return s.lower().replace("ё", "е").strip()


def best_match(smeta_name: str, site_products: list[dict]) -> tuple[dict | None, float]:
    target = normalize(smeta_name)
    best_item, best_score = None, 0.0
    for product in site_products:
        score = difflib.SequenceMatcher(None, target, normalize(product["name"])).ratio()
        if score > best_score:
            best_item, best_score = product, score
    return best_item, best_score


def main():
    with open("real_products.json", encoding="utf-8") as f:
        site_products = json.load(f)

    matched = []
    unmatched = []

    for smeta_item in SMETA_ITEMS:
        product, score = best_match(smeta_item["name"], site_products)
        if product and score >= CONFIDENCE_THRESHOLD:
            matched.append({
                "id": product["id"],
                "name": product["name"],  # берём реальное название с сайта, не из сметы
                "price": product["price"],  # берём реальную актуальную цену с сайта
                "category": smeta_item["category"],  # категория — из сметы, на сайте её нет
                "_smeta_name": smeta_item["name"],  # для ручной сверки — как называлось у заказчика
                "_match_score": round(score, 2),
            })
        else:
            unmatched.append({
                "smeta_name": smeta_item["name"],
                "category": smeta_item["category"],
                "best_guess": product["name"] if product else None,
                "best_guess_score": round(score, 2) if product else 0,
            })

    with open("matched_products.json", "w", encoding="utf-8") as f:
        json.dump(matched, f, ensure_ascii=False, indent=2)

    with open("unmatched_smeta.json", "w", encoding="utf-8") as f:
        json.dump(unmatched, f, ensure_ascii=False, indent=2)

    print(f"Позиций в смете: {len(SMETA_ITEMS)}")
    print(f"✅ Уверенно сопоставлено: {len(matched)} -> matched_products.json")
    print(f"⚠️  Не сопоставлено: {len(unmatched)} -> unmatched_smeta.json")

    if unmatched:
        print("\nНе нашлось уверенного совпадения для:")
        for u in unmatched:
            guess = f" (похоже на «{u['best_guess']}», score={u['best_guess_score']})" if u["best_guess"] else " (вообще ничего похожего)"
            print(f"  • {u['smeta_name']}{guess}")

    print("\nВсе найденные совпадения (проверь глазами — score ближе к 1.0 = увереннее):")
    for m in sorted(matched, key=lambda x: x["_match_score"]):
        print(f"  [{m['_match_score']}] «{m['_smeta_name']}» -> id={m['id']} «{m['name']}» {m['price']}₽")


if __name__ == "__main__":
    main()
