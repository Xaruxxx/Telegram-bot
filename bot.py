import asyncio
import os
import json
import aiohttp
from datetime import datetime
from telethon import TelegramClient, events
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────────
# НАСТРОЙКИ
# ──────────────────────────────────────────────────────────────

def _require_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise ValueError(f"❌ Переменная окружения '{key}' не задана в .env")
    return val

def _get_admin(specific: str, fallback: str) -> int:
    val = os.getenv(specific) or os.getenv(fallback)
    if not val:
        raise ValueError(f"❌ Не задан ни {specific}, ни {fallback} в .env")
    return int(val)

api_id         = int(_require_env('API_ID'))
api_hash       = _require_env('API_HASH')
GEMINI_API_KEY = _require_env('GEMINI_API_KEY')

# ──────────────────────────────────────────────────────────────
# НАСТРОЙКИ АДМИНОВ
# ──────────────────────────────────────────────────────────────
admin_configs = {
    'site':      _get_admin('ADMIN_ID_SITE',      'ADMIN_ID'),
    'design':    _get_admin('ADMIN_ID_DESIGN',    'ADMIN_ID'),
    'target':    _get_admin('ADMIN_ID_TARGET',    'ADMIN_ID'),
    'animation': _get_admin('ADMIN_ID_ANIMATION', 'ADMIN_ID'),
}

ADMIN_USER_ID = int(_require_env('ADMIN_ID'))  # только личный ID админа
ALL_ADMIN_IDS = set(admin_configs.values()) | {ADMIN_USER_ID}

# ID группы, куда бот отправляет уведомления об автоблоке
# Если не задан — уведомление идёт только личному ADMIN_USER_ID
NOTIFY_GROUP_ID = int(os.getenv('NOTIFY_GROUP_ID', '0')) or None

CATEGORY_NAMES = {
    'site':      'САЙТ',
    'design':    'ДИЗАЙН',
    'target':    'ТАРГЕТ',
    'animation': 'АНИМАЦИЯ',
}

print(f"👥 Админы: {admin_configs}")

# ──────────────────────────────────────────────────────────────
# ФАЙЛЫ
# ──────────────────────────────────────────────────────────────
BLOCKED_FILE     = 'blocked.json'
SESSION_FILE     = 'session'
REMINDER_MINUTES = 30

# ──────────────────────────────────────────────────────────────
# БЛЭКЛИСТ
# ──────────────────────────────────────────────────────────────
def load_blocked() -> set:
    if os.path.exists(BLOCKED_FILE):
        with open(BLOCKED_FILE, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    return set()

def save_blocked(blocked: set):
    with open(BLOCKED_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(blocked), f, ensure_ascii=False, indent=2)

blocked_users: set = load_blocked()
print(f"🚫 Заблокировано пользователей: {len(blocked_users)}")

# ──────────────────────────────────────────────────────────────
# КЛЮЧЕВЫЕ СЛОВА
# ──────────────────────────────────────────────────────────────
def _parse_kw(env_key: str) -> list:
    raw = os.getenv(env_key, '')
    return [w.strip().lower() for w in raw.split(',') if w.strip()]

CATEGORY_KEYWORDS = {
    'site':      _parse_kw('KEYWORDS_SITE'),
    'design':    _parse_kw('KEYWORDS_DESIGN'),
    'target':    _parse_kw('KEYWORDS_TARGET'),
    'animation': _parse_kw('KEYWORDS_ANIMATION'),
}

KEYWORDS = [kw for kws in CATEGORY_KEYWORDS.values() for kw in kws]

print(
    f"📋 Загружено ключевых слов: {len(KEYWORDS)} "
    f"(сайт: {len(CATEGORY_KEYWORDS['site'])}, "
    f"дизайн: {len(CATEGORY_KEYWORDS['design'])}, "
    f"таргет: {len(CATEGORY_KEYWORDS['target'])}, "
    f"анимация: {len(CATEGORY_KEYWORDS['animation'])})"
)

# ──────────────────────────────────────────────────────────────
# СТОП-СЛОВА (продавцы / фрилансеры)
# ──────────────────────────────────────────────────────────────
STOP_WORDS = [
    # Русский
    'я делаю', 'я могу сделать', 'делаю сайты', 'делаю дизайн',
    'обращайтесь', 'пишите мне', 'мои работы', 'портфолио',
    'беру заказы', 'принимаю заказы', 'недорого сделаю',
    'качественно сделаю', 'я фрилансер', 'ищу клиентов',
    # Узбекский
    'man qilaman', 'men qilaman', 'qilib beraman', 'qilaman',
    'sayt qilaman', 'dizayn qilaman', 'logo qilaman',
    'murojaat qiling', 'yozing menga', 'ishlayman',
    'sifatli qilib', 'arzon qilib', 'mijoz qidiryapman',
    'buyurtma olaman', 'xizmat korsataman', "xizmat ko'rsataman",
]

# ──────────────────────────────────────────────────────────────
# ОЖИДАЮЩИЕ КЛИЕНТЫ (таймер) + ДЕДУПЛИКАЦИЯ
# ──────────────────────────────────────────────────────────────
pending_clients: dict = {}

# Дедупликация — время последней отправки лида по юзеру
sent_leads: dict = {}
DEDUP_HOURS = 24

# ──────────────────────────────────────────────────────────────
# АВТОБЛОК — счётчик сообщений с ключевыми словами
# ──────────────────────────────────────────────────────────────
# Структура: { sender_id: [datetime, datetime, ...] }
keyword_hits: dict = {}
AUTOBLOCK_LIMIT = 3        # сколько раз допустимо
AUTOBLOCK_WINDOW_HOURS = 24  # за какой период

def record_keyword_hit(sender_id: int) -> int:
    """Добавляет факт срабатывания ключевого слова и возвращает количество за окно."""
    now = datetime.now()
    hits = keyword_hits.get(sender_id, [])
    # Оставляем только попадания в окне
    hits = [t for t in hits if (now - t).total_seconds() / 3600 < AUTOBLOCK_WINDOW_HOURS]
    hits.append(now)
    keyword_hits[sender_id] = hits
    return len(hits)

def should_autoblock(sender_id: int) -> bool:
    """True если количество хитов превысило лимит."""
    hits = keyword_hits.get(sender_id, [])
    now = datetime.now()
    recent = [t for t in hits if (now - t).total_seconds() / 3600 < AUTOBLOCK_WINDOW_HOURS]
    return len(recent) >= AUTOBLOCK_LIMIT

# ──────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ──────────────────────────────────────────────────────────────
def has_keyword(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in KEYWORDS)

def get_matched_keyword(text: str) -> str:
    t = text.lower()
    for kw in KEYWORDS:
        if kw in t:
            return kw
    return '—'

def is_seller(text: str) -> bool:
    t = text.lower()
    return any(sw in t for sw in STOP_WORDS)

def get_category(text: str) -> str:
    t = text.lower()
    for cat, words in CATEGORY_KEYWORDS.items():
        if any(w in t for w in words):
            return cat
    return 'site'

def is_duplicate(sender_id: int) -> bool:
    last_sent = sent_leads.get(sender_id)
    if not last_sent:
        return False
    hours_passed = (datetime.now() - last_sent).total_seconds() / 3600
    return hours_passed < DEDUP_HOURS

def mark_sent(sender_id: int):
    sent_leads[sender_id] = datetime.now()

# ──────────────────────────────────────────────────────────────
# АВТОБЛОК — уведомление
# ──────────────────────────────────────────────────────────────
async def autoblock_user(client: TelegramClient, sender_id: int, sender_name: str, username: str):
    """Блокирует пользователя и отправляет уведомление."""
    blocked_users.add(sender_id)
    save_blocked(blocked_users)
    pending_clients.pop(sender_id, None)
    sent_leads.pop(sender_id, None)
    keyword_hits.pop(sender_id, None)

    msg = (
        f"🤖 **АВТОБЛОК СРАБОТАЛ**\n\n"
        f"👤 **Пользователь:** {sender_name}\n"
        f"📞 **Контакт:** {username}\n"
        f"🆔 **ID:** {sender_id}\n\n"
        f"⚠️ Превышен лимит: {AUTOBLOCK_LIMIT} сообщений с ключевыми словами "
        f"за {AUTOBLOCK_WINDOW_HOURS} часов.\n"
        f"🚫 Пользователь заблокирован автоматически.\n\n"
        f"Разблокировать: `!unblock {sender_id}`"
    )

    # Уведомление в группу (если задана) или личному админу
    notify_target = NOTIFY_GROUP_ID if NOTIFY_GROUP_ID else ADMIN_USER_ID
    try:
        entity = await client.get_entity(notify_target)
        await client.send_message(entity, msg, link_preview=False)
    except Exception as e:
        print(f"❌ Ошибка уведомления автоблока: {e}")

    print(f"🤖 Автоблок: {sender_name} ({sender_id})")

# ──────────────────────────────────────────────────────────────
# КОМАНДЫ АДМИНА — общая логика (личка и группа)
# ──────────────────────────────────────────────────────────────
async def handle_admin_command(client: TelegramClient, event, text: str):
    """
    Обрабатывает команды !block / !unblock / !blocked.
    Вызывается и из личных сообщений, и из группы.
    Возвращает True если команда была распознана.
    """
    cmd = text.lower()

    if cmd.startswith('!block'):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await event.reply("❌ Укажи ID или @username: `!block 123456789`")
            return True
        target = parts[1].strip().lstrip('@')
        try:
            if target.isdigit():
                target_id = int(target)
                display   = str(target_id)
            else:
                entity    = await client.get_entity(target)
                target_id = entity.id
                display   = f"@{target}"

            if target_id in blocked_users:
                await event.reply(f"ℹ️ {display} уже в блэклисте.")
                return True

            blocked_users.add(target_id)
            save_blocked(blocked_users)
            pending_clients.pop(target_id, None)
            sent_leads.pop(target_id, None)
            keyword_hits.pop(target_id, None)

            await event.reply(
                f"✅ **Заблокирован:** {display}\n"
                f"🚫 Всего в блэклисте: {len(blocked_users)}"
            )
            print(f"🚫 Заблокирован: {display} ({target_id})")
        except Exception as e:
            await event.reply(f"❌ Не удалось найти пользователя: {e}")
        return True

    if cmd.startswith('!unblock'):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await event.reply("❌ Укажи ID: `!unblock 123456789`")
            return True
        target = parts[1].strip().lstrip('@')
        try:
            target_id = (
                int(target) if target.isdigit()
                else (await client.get_entity(target)).id
            )
            if target_id not in blocked_users:
                await event.reply("ℹ️ Этого пользователя нет в блэклисте.")
                return True
            blocked_users.discard(target_id)
            save_blocked(blocked_users)
            await event.reply(
                f"✅ Разблокирован: {target_id}\n"
                f"🚫 В блэклисте: {len(blocked_users)}"
            )
            print(f"✅ Разблокирован: {target_id}")
        except Exception as e:
            await event.reply(f"❌ Ошибка: {e}")
        return True

    if cmd == '!blocked':
        if not blocked_users:
            await event.reply("📋 Блэклист пуст.")
        else:
            ids = '\n'.join(str(uid) for uid in sorted(blocked_users))
            await event.reply(f"🚫 **Заблокировано {len(blocked_users)}:**\n{ids}")
        return True

    return False  # команда не распознана

# ──────────────────────────────────────────────────────────────
# GEMINI — определяем, реальный ли заказ
# ──────────────────────────────────────────────────────────────
async def is_real_order(text: str) -> bool:
    prompt = (
        "Ты — строгий классификатор заказов. Язык сообщений: русский или узбекский.\n\n"
        "ЗАДАЧА: определить, является ли сообщение ЗАЯВКОЙ НА ПОКУПКУ услуги.\n\n"
        "Отвечай ТОЛЬКО одним словом — TRUE или FALSE. Никаких пояснений.\n\n"
        "TRUE только если человек:\n"
        "- ищет исполнителя / спрашивает 'кто делает?'\n"
        "- хочет заказать услугу / спрашивает цену\n"
        "- пишет 'нужен сайт', 'нужен дизайнер', 'kerak', 'qildirasizmi?'\n\n"
        "FALSE если:\n"
        "- человек САМ предлагает услугу (я делаю, qilaman, beraman)\n"
        "- реклама, портфолио, ссылки на свои работы\n"
        "- приветствие, флуд, вопросы не по теме\n"
        "- непонятный или слишком короткий текст\n"
        "- человек ищет работу (фрилансер, ищу заказы)\n\n"
        "СТРОГОЕ ПРАВИЛО: при малейшем сомнении — FALSE.\n\n"
        f"Сообщение: «{text}»\n\n"
        "Ответ (только TRUE или FALSE):"
    )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.0-flash-lite:generateContent?key=" + GEMINI_API_KEY
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 5,
            "topP": 1.0,
            "topK": 1,
        },
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=body, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()

                if "candidates" not in data:
                    error = data.get('error', {})
                    if error.get('code') == 429:
                        retry_delay = 45
                        print(f"⏳ Gemini 429 — ждём {retry_delay}с и пробуем снова...")
                        await asyncio.sleep(retry_delay)
                        return True  # fallback
                    print(f"⚠️ Gemini ошибка: {error}")
                    return True

                raw = (
                    data["candidates"][0]["content"]["parts"][0]["text"]
                    .strip()
                    .upper()
                )
                print(f"🤖 Gemini: '{raw}'")
                return raw == "TRUE"

    except asyncio.TimeoutError:
        print("⚠️ Gemini timeout — пропускаем по ключевым словам")
        return True
    except Exception as e:
        print(f"⚠️ Gemini исключение: {e}")
        return True

# ──────────────────────────────────────────────────────────────
# НАПОМИНАНИЕ (фоновая задача)
# ──────────────────────────────────────────────────────────────
async def reminder_loop(client: TelegramClient):
    while True:
        await asyncio.sleep(60)
        now = datetime.now()
        for uid, data in list(pending_clients.items()):
            if data['notified']:
                continue
            mins = (now - data['time']).seconds // 60
            if mins >= REMINDER_MINUTES:
                try:
                    admin_id = admin_configs[data['category']]
                    entity   = await client.get_entity(admin_id)
                    await client.send_message(
                        entity,
                        f"⏰ **ВНИМАНИЕ! Клиент ждёт {mins} минут!**\n\n"
                        f"👤 **Клиент:** {data['name']} ({data['username']})\n"
                        f"🌐 **Категория:** {CATEGORY_NAMES.get(data['category'], data['category'])}\n"
                        f"📝 **Сообщение:**\n{data['text']}\n\n"
                        f"⚡ Ответьте как можно скорее!",
                    )
                    pending_clients[uid]['notified'] = True
                    print(f"⏰ Напоминание — {data['name']}")
                except Exception as e:
                    print(f"❌ Ошибка напоминания: {e}")

# ──────────────────────────────────────────────────────────────
# ГЛАВНАЯ ФУНКЦИЯ
# ──────────────────────────────────────────────────────────────
async def main():
    client = TelegramClient(SESSION_FILE, api_id, api_hash)

    # ──────────────────────────────────────────────────────────
    # ХЕНДЛЕР 1 — личные сообщения (команды + клиенты)
    # ──────────────────────────────────────────────────────────
    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def handler_private(event):
        if not event.message.text:
            return

        sender_id = event.sender_id
        text = event.message.text.strip()

        # ── КОМАНДЫ — только для личного ADMIN_USER_ID ─────────
        if sender_id == ADMIN_USER_ID:
            await handle_admin_command(client, event, text)
            return

        # ── БЛОК КЛИЕНТОВ (не-админы) ─────────────────────────
        if sender_id in blocked_users:
            print(f"🚫 Заблокирован (личка): {sender_id}")
            return

        if not has_keyword(text):
            print(f"⏭️ Нет ключевых слов (личка): {text[:50]}...")
            return

        if is_seller(text):
            print(f"🚫 Продавец (личка): {text[:50]}...")
            return

        # Счётчик автоблока
        hit_count = record_keyword_hit(sender_id)
        if should_autoblock(sender_id):
            try:
                sender      = await event.get_sender()
                sender_name = getattr(sender, 'first_name', 'Пользователь')
                if getattr(sender, 'last_name', None):
                    sender_name += f" {sender.last_name}"
                username = (
                    f"@{sender.username}"
                    if getattr(sender, 'username', None)
                    else f"ID: {sender.id}"
                )
            except Exception:
                sender_name, username = str(sender_id), str(sender_id)
            await autoblock_user(client, sender_id, sender_name, username)
            return

        # Дедупликация
        if is_duplicate(sender_id):
            mins = int((datetime.now() - sent_leads[sender_id]).total_seconds() / 60)
            print(f"⏭️ Дубль (личка) — {sender_id} уже отправлен {mins} мин назад")
            return

        if not await is_real_order(text):
            print(f"🗑️ Не заказ (личка): {text[:50]}...")
            return

        category = get_category(text)

        try:
            sender      = await event.get_sender()
            sender_name = getattr(sender, 'first_name', 'Клиент')
            if getattr(sender, 'last_name', None):
                sender_name += f" {sender.last_name}"

            username     = (
                f"@{sender.username}"
                if getattr(sender, 'username', None)
                else f"ID: {sender.id}"
            )
            profile_link = f"tg://user?id={sender.id}"
            message_link = (
                f"https://t.me/{sender.username}"
                if getattr(sender, 'username', None)
                else profile_link
            )

            pending_clients[sender.id] = {
                'time':     datetime.now(),
                'name':     sender_name,
                'username': username,
                'text':     text,
                'category': category,
                'notified': False,
            }

            admin_id = admin_configs[category]
            entity   = await client.get_entity(admin_id)
            await client.send_message(
                entity,
                f"🔔 **НАЙДЕН НОВЫЙ КЛИЕНТ!**\n\n"
                f"🌐 **КАТЕГОРИЯ: {CATEGORY_NAMES.get(category, category)}**\n\n"
                f"👤 **Клиент:** {sender_name}\n"
                f"🆔 **ID клиента:** {sender.id}\n"
                f"📞 **Контакт:** {username}\n"
                f"💬 **Канал/Чат:** Личное сообщение\n"
                f"🏷️ **Тип:** Личное сообщение\n"
                f"🎯 **Ключевое слово:** {get_matched_keyword(text)}\n\n"
                f"📝 **Сообщение:**\n{text}\n\n"
                f"👉 [Открыть сообщение]({message_link})\n\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"✅ [Открыть профиль]({profile_link})\n"
                f"🚫 Заблокировать: `!block {sender.id}`\n\n"
                f"⏳ Таймер запущен — {REMINDER_MINUTES} мин",
                link_preview=False,
            )
            mark_sent(sender.id)
            print(f"✅ Личка [{category}] — {sender_name} ({username})")

        except Exception as e:
            print(f"❌ Ошибка (личка): {e}")

    # ──────────────────────────────────────────────────────────
    # ХЕНДЛЕР 2 — снятие таймера при ответе
    # ──────────────────────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, func=lambda e: e.is_private))
    async def handler_outgoing(event):
        try:
            chat = await event.get_chat()
            if chat.id in pending_clients:
                data = pending_clients.pop(chat.id)
                print(f"✅ Таймер снят — {data['name']}")
        except Exception as e:
            print(f"❌ Ошибка таймера: {e}")

    # ──────────────────────────────────────────────────────────
    # ХЕНДЛЕР 3 — КОМАНДЫ В ГРУППЕ (только от ALL_ADMIN_IDS)
    # ──────────────────────────────────────────────────────────
    @client.on(events.NewMessage(
        incoming=True,
        func=lambda e: not e.is_private and e.message.text and e.message.text.startswith('!')
    ))
    async def handler_group_commands(event):
        sender_id = event.sender_id
        if sender_id not in ALL_ADMIN_IDS:
            return  # только админы могут использовать команды в группе

        text = event.message.text.strip()
        recognized = await handle_admin_command(client, event, text)
        if not recognized:
            pass  # неизвестная команда — молчим

    # ──────────────────────────────────────────────────────────
    # ХЕНДЛЕР 4 — РАДАР (группы и каналы)
    # ──────────────────────────────────────────────────────────
    @client.on(events.NewMessage(incoming=True, func=lambda e: not e.is_private))
    async def handler_radar(event):
        if not event.message.text:
            return

        text = event.message.text
        if len(text.strip()) < 5:
            return

        # Не обрабатываем команды (они уже в handler_group_commands)
        if text.strip().startswith('!'):
            return

        sender_id = event.sender_id

        # Игнорируем самих себя и всех админов в радаре
        if sender_id in ALL_ADMIN_IDS:
            return

        if sender_id in blocked_users:
            print(f"🚫 Заблокирован (группа): {sender_id}")
            return

        if not has_keyword(text):
            return

        if is_seller(text):
            print(f"🚫 Продавец (группа): {text[:50]}...")
            return

        # Счётчик автоблока
        hit_count = record_keyword_hit(sender_id)
        print(f"📊 Хиты {sender_id}: {hit_count}/{AUTOBLOCK_LIMIT}")

        if should_autoblock(sender_id):
            try:
                sender      = await event.get_sender()
                sender_name = getattr(sender, 'first_name', 'Пользователь')
                if getattr(sender, 'last_name', None):
                    sender_name += f" {sender.last_name}"
                username = (
                    f"@{sender.username}"
                    if getattr(sender, 'username', None)
                    else f"ID: {sender.id}"
                )
            except Exception:
                sender_name, username = str(sender_id), str(sender_id)
            await autoblock_user(client, sender_id, sender_name, username)
            return

        # Дедупликация
        if is_duplicate(sender_id):
            mins = int((datetime.now() - sent_leads[sender_id]).total_seconds() / 60)
            print(f"⏭️ Дубль (группа) — {sender_id} уже отправлен {mins} мин назад")
            return

        print(f"🤖 Отправляем в Gemini: {text[:60]}...")
        if not await is_real_order(text):
            print("🗑️ Gemini: FALSE — пропуск")
            return

        try:
            sender      = await event.get_sender()
            sender_name = getattr(sender, 'first_name', 'Пользователь')
            if getattr(sender, 'last_name', None):
                sender_name += f" {sender.last_name}"

            username     = (
                f"@{sender.username}"
                if getattr(sender, 'username', None)
                else f"ID: {sender.id}"
            )
            chat         = await event.get_chat()
            group_name   = getattr(chat, 'title', 'Неизвестная группа')
            category     = get_category(text)
            profile_link = f"tg://user?id={sender.id}"
            message_link = (
                f"https://t.me/{sender.username}"
                if getattr(sender, 'username', None)
                else profile_link
            )

            admin_id = admin_configs[category]
            print(f"📤 Отправляю админу/группе: {admin_id} (категория: {category})")
            entity   = await client.get_entity(admin_id)
            await client.send_message(
                entity,
                f"🔔 **НАЙДЕН НОВЫЙ КЛИЕНТ!**\n\n"
                f"🌐 **КАТЕГОРИЯ: {CATEGORY_NAMES.get(category, category)}**\n\n"
                f"👤 **Клиент:** {sender_name}\n"
                f"🆔 **ID клиента:** {sender.id}\n"
                f"📞 **Контакт:** {username}\n"
                f"💬 **Источник:** {group_name}\n"
                f"🏷️ **Тип:** Группа/Канал\n"
                f"🎯 **Ключевое слово:** {get_matched_keyword(text)}\n\n"
                f"📝 **Сообщение:**\n{text}\n\n"
                f"👉 [Открыть профиль]({message_link})\n\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"✅ [Профиль]({profile_link})\n"
                f"🚫 Заблокировать: `!block {sender.id}`",
                link_preview=False,
            )
            mark_sent(sender.id)
            print(f"✅ Радар [{category}] — {sender_name} в {group_name}")

        except Exception as e:
            print(f"❌ Ошибка радара: {e}")

    # ──────────────────────────────────────────────────────────
    # ЗАПУСК — используем готовый session.session
    # ──────────────────────────────────────────────────────────
    await client.connect()

    if not await client.is_user_authorized():
        raise RuntimeError(
            "❌ Сессия недействительна или истекла. "
            "Запусти бота локально один раз, чтобы пересоздать session.session"
        )

    print("✅ Авторизован через session.session")

    asyncio.create_task(reminder_loop(client))
    print(
        "🚀 Бот запущен "
        "(RU + UZ + Радар + Таймер + Блокировка + Дедупликация + "
        f"Автоблок [{AUTOBLOCK_LIMIT} хитов за {AUTOBLOCK_WINDOW_HOURS}ч] + "
        "Команды в группе)"
    )

    await client.run_until_disconnected()


if __name__ == '__main__':
    while True:
        try:
            asyncio.run(main())
        except Exception as e:
            print(f"💥 Бот упал: {e}")
            print("🔄 Перезапуск через 5 секунд...")
            import time
            time.sleep(5)
