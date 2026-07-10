"""
get_emoji_ids.py — одноразовый скрипт, чтобы вытащить custom_emoji_id
для каждого эмодзи из набора https://t.me/addemoji/MUSCULATA_Emoji

Запусти один раз локально (не на сервере), результат сохрани куда-нибудь —
дальше эти ID используются в коде бота напрямую, повторно запускать
скрипт не нужно.

Использование:
    BOT_TOKEN=твой_токен python3 get_emoji_ids.py
"""

import os
import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
STICKER_SET_NAME = "MUSCULATA_Emoji"  # short_name из ссылки t.me/addemoji/<short_name>

if not BOT_TOKEN:
    raise SystemExit("Укажи BOT_TOKEN: BOT_TOKEN=твой_токен python3 get_emoji_ids.py")

url = f"https://api.telegram.org/bot{BOT_TOKEN}/getStickerSet"
response = requests.get(url, params={"name": STICKER_SET_NAME}, timeout=10)
data = response.json()

if not data.get("ok"):
    print("Ошибка от Telegram API:", data)
    raise SystemExit(1)

stickers = data["result"]["stickers"]
print(f"Набор '{STICKER_SET_NAME}' содержит {len(stickers)} эмодзи:\n")

for sticker in stickers:
    emoji_id = sticker.get("custom_emoji_id")
    emoji_char = sticker.get("emoji")
    print(f"  {emoji_char}  ->  custom_emoji_id = \"{emoji_id}\"")

print("\nСкопируй нужные ID и вставь в emoji_ids.py (см. рядом).")
