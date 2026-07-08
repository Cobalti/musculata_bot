"""
Хранит id "якорных" сообщений бота для каждого пользователя:
- menu_message: то самое сообщение, что несёт постоянное меню внизу
  (ReplyKeyboardMarkup). Отправляется один раз и больше никогда не трогается —
  это и делает нижнее меню по-настоящему статичным.
- content_message: текущий "экран" (каталог/корзина/заглушка), который
  удаляется и пересоздаётся при переходах, не затрагивая меню.

Как и корзина — пока в памяти процесса, при перезапуске бота обнуляется.
Это не критично: просто /start заново создаст меню.
"""

_menu_message: dict[int, int] = {}
_content_message: dict[int, int] = {}


def get_menu(user_id: int):
    return _menu_message.get(user_id)


def set_menu(user_id: int, message_id: int):
    _menu_message[user_id] = message_id


def get_content(user_id: int):
    return _content_message.get(user_id)


def set_content(user_id: int, message_id: int):
    _content_message[user_id] = message_id


def clear_content(user_id: int):
    _content_message.pop(user_id, None)


# ---------- Поддержка: ожидание вопроса + связка "сообщение админу -> пользователь" ----------

_awaiting_support: set[int] = set()          # user_id, кто сейчас печатает вопрос в поддержку
_support_threads: dict[int, int] = {}        # message_id (в чате админа) -> user_id, кому отвечать


def set_awaiting_support(user_id: int):
    _awaiting_support.add(user_id)


def is_awaiting_support(user_id: int) -> bool:
    return user_id in _awaiting_support


def clear_awaiting_support(user_id: int):
    _awaiting_support.discard(user_id)


def set_support_thread(admin_message_id: int, user_id: int):
    _support_threads[admin_message_id] = user_id


def get_support_thread(admin_message_id: int):
    return _support_threads.get(admin_message_id)


# ---------- Согласие на обработку персональных данных ----------

_consent_given: set[int] = set()        # user_id, кто уже нажал "Принимаю"
_consent_message: dict[int, int] = {}   # user_id -> message_id сообщения с условиями


def set_consent_given(user_id: int):
    _consent_given.add(user_id)


def has_given_consent(user_id: int) -> bool:
    return user_id in _consent_given


def set_consent_message(user_id: int, message_id: int):
    _consent_message[user_id] = message_id


def get_consent_message(user_id: int):
    return _consent_message.get(user_id)
