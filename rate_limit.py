"""
Простейший sliding-window rate limiter на пользователя. Защищает бота
от намеренного или случайного заспамливания командами/кнопками — без
этого один пользователь мог бы либо положить бота лавиной запросов,
либо (в будущем, если бот начнёт дёргать внешний API магазина) создать
лишнюю нагрузку на хостинг сайта.
"""

import time
from collections import defaultdict, deque

from config import RATE_LIMIT_MAX_ACTIONS, RATE_LIMIT_WINDOW_SECONDS

_history: dict[int, deque] = defaultdict(deque)


def is_rate_limited(user_id: int) -> bool:
    now = time.monotonic()
    q = _history[user_id]
    while q and now - q[0] > RATE_LIMIT_WINDOW_SECONDS:
        q.popleft()
    if len(q) >= RATE_LIMIT_MAX_ACTIONS:
        return True
    q.append(now)
    return False
