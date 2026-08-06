"""
fetch_catalog.py — скачивает ПОЛНЫЙ каталог товаров с сайта Фёдора
и раскладывает по трём категориям через поле "role":
    role: null  -> обычный товар (в т.ч. развёрнутые варианты)
    role: "pack" -> Военный Сундук (пак)
    role: "tier" -> уровень присяги Ордена

ЗАПУСКАТЬ НА СЕРВЕРЕ (там есть доступ в интернет к mashinabodystore.ru,
в песочнице разработки — нет, egress туда запрещён).

Использование:
    cd /opt/musculata_bot
    source venv/bin/activate
    python3 fetch_catalog.py

Читает X_BOT_TOKEN из .env (через тот же механизм, что и сам бот).
Пишет три файла в текущую директорию:
    real_products.json  -- обычные товары, ГОТОВЫ к копированию
                            в products.json как есть
    real_packs.json      -- сырые данные о паках (role=pack) — нужно
                            руками свести с packs.py (там ещё и состав,
                            теглайны и т.д., не только id/цена)
    real_tiers.json       -- сырые данные об уровнях присяги (role=tier) —
                            НЕ подключаем в код, пока не пройдёт созвон
                            с Фёдором по присяге; просто сохраняем для
                            справки на будущее

ВАЖНО — ОТКРЫТЫЙ ВОПРОС К ФЁДОРУ:
В ответе API нет поля "категория" вообще. Наш каталог в боте построен
на категориях (Протеин, Креатин, Витамины и т.д.) — без этого поля
разложить 1714 товаров по смыслу автоматически невозможно. Пока этот
скрипт кладёт вообще ВСЕ обычные товары в одну категорию "Все товары" —
рабочий, но временный вариант. Нужно спросить Фёдора, можно ли получить
категории (либо отдельным полем в этом же эндпоинте, либо отдельным
запросом на список категорий WooCommerce).
"""

import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

X_BOT_TOKEN = os.environ.get("X_BOT_TOKEN", "")
PRODUCTS_ENDPOINT = os.environ.get(
    "SITE_PRODUCTS_ENDPOINT",
    "https://mashinabodystore.ru/wp-json/v2/integrations/musculata/products",
)

if not X_BOT_TOKEN:
    print("❌ X_BOT_TOKEN не задан в .env — нечем авторизоваться, прерываю.")
    sys.exit(1)


def fetch_all_pages() -> list[dict]:
    """Скачивает все страницы каталога, возвращает плоский список товаров-строк API как есть."""
    all_items = []
    page = 1
    while True:
        resp = requests.get(
            PRODUCTS_ENDPOINT,
            params={"page": page},
            headers={"X-Bot-Token": X_BOT_TOKEN},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        all_items.extend(data["items"])
        total_pages = data.get("total_pages", 1)
        print(f"  Страница {page}/{total_pages} — получено {len(data['items'])} позиций")
        if page >= total_pages:
            break
        page += 1
    return all_items


def expand_product(item: dict) -> list[dict]:
    """
    Разворачивает вариативный товар (type="variable") в отдельные позиции
    по вариантам (у каждого свой ID, своя цена, имя уже включает вариант
    прямо в строке — например "Pump-3G 375г - Fruit burst"). Простые
    товары возвращает как один элемент списка.
    """
    if item.get("variations"):
        return [
            {"id": v["id"], "name": v["name"], "price": v["price"], "stock_status": v["stock_status"],
             "orderable": v["orderable"]}
            for v in item["variations"]
        ]
    return [{
        "id": item["id"], "name": item["name"], "price": item["price"],
        "stock_status": item["stock_status"], "orderable": item["orderable"],
    }]


def main():
    print(f"Скачиваю каталог с {PRODUCTS_ENDPOINT} ...")
    raw_items = fetch_all_pages()
    print(f"\nВсего товаров (верхнего уровня, до разворота вариантов): {len(raw_items)}")

    products, packs, tiers = [], [], []

    for item in raw_items:
        role = item.get("role")
        if role == "pack":
            packs.append(item)
        elif role == "tier":
            tiers.append(item)
        else:
            for expanded in expand_product(item):
                if not expanded["orderable"] or expanded["stock_status"] == "outofstock":
                    continue  # не показываем в боте то, что нельзя купить
                products.append({
                    "id": expanded["id"],
                    "name": expanded["name"],
                    "price": int(float(expanded["price"])),
                    # ЖДЁМ ОТ ФЁДОРА: реальных категорий в API нет — пока
                    # всё в одну кучу, см. предупреждение в шапке файла.
                    "category": "Все товары",
                })

    with open("real_products.json", "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

    with open("real_packs.json", "w", encoding="utf-8") as f:
        json.dump(packs, f, ensure_ascii=False, indent=2)

    with open("real_tiers.json", "w", encoding="utf-8") as f:
        json.dump(tiers, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Готово:")
    print(f"   real_products.json — {len(products)} товаров (в наличии, доступны к заказу)")
    print(f"   real_packs.json    — {len(packs)} паков")
    print(f"   real_tiers.json    — {len(tiers)} уровней присяги (пока не подключаем в код)")
    print(f"\nПаки, которые нашлись:")
    for p in packs:
        print(f"   id={p['id']} | {p['name']} | {p['price']} ₽")
    print(f"\nУровни присяги, которые нашлись:")
    for t in tiers:
        print(f"   id={t['id']} | {t['name']} | {t['price']} ₽")


if __name__ == "__main__":
    main()
