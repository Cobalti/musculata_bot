import os

# Токен НИКОГДА не пишем прямо в коде.
# В PyCharm: Run -> Edit Configurations -> Environment variables -> BOT_TOKEN=твой_токен
# Либо создай файл .env (см. .env.example) и используй python-dotenv (уже подключено ниже).

from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError(
        "Не найден BOT_TOKEN. Создай файл .env рядом с main.py и впиши туда:\n"
        "BOT_TOKEN=твой_токен_от_BotFather"
    )

# Сайт магазина — куда ведём пользователя оформлять заказ.
# ВАЖНО: формат параметров (items/discount/client) пока условный,
# нужно подтвердить у технического специалиста MashinaBody, что сайт их реально принимает.
CHECKOUT_BASE_URL = "https://mashinabodystore.ru/checkout"

# Скидка за использование бота (пока фиксированная для всех, без реферальной логики).
DEFAULT_DISCOUNT_PERCENT = 10

# ---------- Безопасность / устойчивость / мониторинг ----------

# Твой личный Telegram user_id — сюда бот пришлёт уведомление, если
# что-то упадёт с ошибкой. Как узнать: напиши @userinfobot в Telegram,
# он пришлёт в ответ твой числовой ID.
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

# Логи — пишутся в файл с автоочисткой (ротацией), чтобы не расти
# бесконечно: максимум LOG_BACKUP_COUNT старых файлов по LOG_MAX_BYTES
# каждый, при превышении — самый старый удаляется автоматически.
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
LOG_FILE = os.path.join(LOG_DIR, "bot.log")
LOG_MAX_BYTES = 2 * 1024 * 1024  # 2 МБ на файл
LOG_BACKUP_COUNT = 5             # итого максимум ~12 МБ логов на диске

# Аналитика заходов/просмотров — отдельный CSV-файл, чтобы потом
# посчитать в Excel/Google Sheets или через pandas.
ANALYTICS_FILE = os.path.join(os.path.dirname(__file__), "analytics.csv")

# Rate limiting — защита от спама/флуда одним пользователем.
RATE_LIMIT_MAX_ACTIONS = 15      # не больше стольких действий
RATE_LIMIT_WINDOW_SECONDS = 10   # за столько секунд
