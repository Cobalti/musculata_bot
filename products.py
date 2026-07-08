import json
import os

_PRODUCTS_PATH = os.path.join(os.path.dirname(__file__), "products.json")

with open(_PRODUCTS_PATH, "r", encoding="utf-8") as f:
    PRODUCTS = json.load(f)

PRODUCTS_BY_ID = {p["id"]: p for p in PRODUCTS}

# Список категорий — фиксированный порядок, вычисляется один раз при
# старте. Используется индекс категории в callback_data (а не сама строка),
# чтобы не упираться в лимит длины callback_data у Telegram (64 байта)
# на категориях с длинными названиями.
CATEGORIES = sorted({p["category"] for p in PRODUCTS})

PAGE_SIZE = 5


def category_by_index(idx: int):
    if 0 <= idx < len(CATEGORIES):
        return CATEGORIES[idx]
    return None


def _filtered(category: str | None):
    if category is None:
        return PRODUCTS
    return [p for p in PRODUCTS if p["category"] == category]


def get_page(page: int, category: str | None = None):
    """Возвращает срез товаров для страницы (нумерация страниц с 0),
    опционально отфильтрованных по категории."""
    items = _filtered(category)
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    return items[start:end]


def total_pages(category: str | None = None):
    items = _filtered(category)
    if not items:
        return 1
    return (len(items) - 1) // PAGE_SIZE + 1
