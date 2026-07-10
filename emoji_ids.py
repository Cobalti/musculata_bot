"""
emoji_ids.py — ID кастомных эмодзи из набора MUSCULATA_Emoji
(https://t.me/addemoji/MUSCULATA_Emoji).

Как заполнить:
    1. Запусти: BOT_TOKEN=твой_токен python3 get_emoji_ids.py
    2. Скопируй нужные custom_emoji_id из вывода сюда, дав им понятные имена
    3. Используй эти константы в emoji_ui.send_message_with_emoji() /
       emoji_ui.build_emoji_button()

Пока не запущен get_emoji_ids.py — здесь заглушки (None), код с ними
не упадёт, просто эмодзи не покажется, пока не подставишь реальный ID.
"""

STAR = None       # custom_emoji_id для ⭐ из набора MUSCULATA_Emoji
LOGO = None       # custom_emoji_id для фирменного лого/значка
CHECK = None      # custom_emoji_id для ✅ в фирменном стиле
# Добавляй новые константы по мере надобности — по одной на каждый
# эмодзи из набора, который реально используется в боте.
