import os
import logging
import sqlite3
import threading
import asyncio
from datetime import datetime, timedelta
from functools import partial
from typing import Dict, Optional, List, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

MASTER_IDS = [int(x.strip()) for x in os.getenv('MASTER_IDS', '').split(',') if x.strip()]
ADMIN_IDS = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
BUSINESS_NAME = os.getenv('BUSINESS_NAME', 'БИЗНЕС')
DB_PATH = 'bot_database.db'

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            display_name TEXT,
            is_master BOOLEAN DEFAULT 0,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            is_master BOOLEAN,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS auto_response (
            id INTEGER PRIMARY KEY,
            keyword TEXT,
            response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    try:
        cur.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
    except sqlite3.OperationalError:
        pass

    cur.execute("SELECT COUNT(*) FROM auto_response")
    if cur.fetchone()[0] == 0:
        cur.execute('''
            INSERT INTO auto_response (keyword, response) 
            VALUES (?, ?)
        ''', ('цена', '💅 Стоимость маникюра начинается от 1500 ₽. Точная цена зависит от сложности и дизайна. Для записи напишите «хочу записаться», и я соединю вас с мастером.'))
    conn.commit()
    conn.close()

def clean_old_logs(days=30):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        DELETE FROM logs
        WHERE timestamp < datetime('now', '-' || ? || ' days')
    ''', (days,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    logger.info(f"Удалено старых логов: {deleted} записей")
    return deleted

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def is_master(user_id: int) -> bool:
    return user_id in MASTER_IDS

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def save_user(user_id: int, username: str = None, first_name: str = None, last_name: str = None, display_name: str = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, display_name)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, last_name, display_name))
    if display_name:
        cur.execute('''
            UPDATE users SET display_name = ? WHERE user_id = ? AND display_name IS NULL
        ''', (display_name, user_id))
    conn.commit()
    conn.close()

def get_user_display_name(user_id: int) -> str:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT display_name, first_name, username FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        if row[0]:
            return row[0]
        elif row[1]:
            return row[1]
        elif row[2]:
            return f"@{row[2]}"
    return f"ID:{user_id}"

def log_message(user_id: int, text: str, is_master: bool = False):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO logs (user_id, text, is_master)
        VALUES (?, ?, ?)
    ''', (user_id, text, is_master))
    conn.commit()
    conn.close()

def get_auto_response() -> Tuple[Optional[str], Optional[str]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT keyword, response FROM auto_response ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    if row:
        return row[0], row[1]
    return None, None

def update_auto_response(keyword: str, response: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO auto_response (keyword, response)
        VALUES (?, ?)
    ''', (keyword, response))
    conn.commit()
    conn.close()

def get_user_history(user_id: int, limit: int = 10) -> List[Tuple[str, str, bool]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        SELECT text, timestamp, is_master FROM logs
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    ''', (user_id, limit))
    rows = cur.fetchall()
    conn.close()
    return rows[::-1]

def get_active_clients() -> List[int]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        SELECT DISTINCT user_id FROM logs
        WHERE is_master = 0
        AND timestamp > datetime('now', '-7 days')
        ORDER BY timestamp DESC
    ''')
    rows = cur.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_client_last_message(user_id: int) -> Optional[str]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        SELECT text FROM logs
        WHERE user_id = ? AND is_master = 0
        ORDER BY timestamp DESC LIMIT 1
    ''', (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

# ==================== КЛАВИАТУРЫ ====================
def get_client_keyboard():
    keyboard = [[KeyboardButton("❓ Помощь")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_master_keyboard():
    keyboard = [
        [KeyboardButton("📋 Активные диалоги"), KeyboardButton("❓ Помощь")],
        [KeyboardButton("🔄 Закончить диалог")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_keyboard():
    keyboard = [
        [KeyboardButton("📋 Активные диалоги"), KeyboardButton("❓ Помощь")],
        [KeyboardButton("📜 Логи"), KeyboardButton("⚙️ Настроить автоответ")],
        [KeyboardButton("🔄 Закончить диалог")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_reply_buttons(client_id: int):
    keyboard = [
        [
            InlineKeyboardButton("✏️ Ответить", callback_data=f"reply_{client_id}"),
            InlineKeyboardButton("📖 История", callback_data=f"history_{client_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_clients_list_keyboard(clients: List[int]):
    keyboard = []
    for client_id in clients:
        name = get_user_display_name(client_id)
        keyboard.append([InlineKeyboardButton(f"👤 {name}", callback_data=f"select_{client_id}")])
    return InlineKeyboardMarkup(keyboard)

# ==================== ТАЙМЕРЫ (на threading) ====================
# Храним таймеры в словаре: {client_id: {'timeout': timer, 'reminder': timer}}
# Чтобы отменять их при ответе мастера.

def schedule_busy_message(loop, bot, client_id, delay_seconds=300):
    """Запускает таймер на отправку сообщения о занятости через delay_seconds."""
    def wrapper():
        # Эта функция выполняется в отдельном потоке
        asyncio.run_coroutine_threadsafe(
            send_busy_message_coro(bot, client_id),
            loop
        )
    timer = threading.Timer(delay_seconds, wrapper)
    timer.daemon = True
    timer.start()
    return timer

async def send_busy_message_coro(bot, client_id):
    """Асинхронная функция отправки сообщения о занятости."""
    # Проверяем, всё ли ещё ждёт ответа – это должно быть проверено в вызывающей функции,
    # но для надёжности проверим здесь через user_data (но user_data не передаётся легко)
    # Можно сделать глобальный словарь или передавать context, но проще проверить через БД или флаг,
    # который мы будем хранить в глобальном словаре.
    # Используем глобальный словарь waiting_status
    if waiting_status.get(client_id, False):
        try:
            await bot.send_message(
                chat_id=client_id,
                text="⏳ *Все мастера сейчас заняты, но мы увидели ваше сообщение.*\n"
                     "Ожидайте ответа, мы свяжемся с вами в ближайшее время.",
                parse_mode='Markdown'
            )
            log_message(client_id, "[АВТО: мастера заняты]", is_master=False)
            logger.info(f"Сообщение о занятости отправлено клиенту {client_id}")
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение о занятости клиенту {client_id}: {e}")

def schedule_reminder(loop, bot, client_id, delay_seconds=1800):
    """Запускает таймер на отправку напоминания мастеру через delay_seconds."""
    def wrapper():
        asyncio.run_coroutine_threadsafe(
            send_reminder_coro(bot, client_id),
            loop
        )
    timer = threading.Timer(delay_seconds, wrapper)
    timer.daemon = True
    timer.start()
    return timer

async def send_reminder_coro(bot, client_id):
    """Асинхронная функция отправки напоминания мастерам."""
    if not waiting_status.get(client_id, False):
        return
    
    name = get_user_display_name(client_id)
    last_msg = get_client_last_message(client_id) or "сообщение"
    
    reminder_text = (
        f"🔔 *Напоминание!*\n"
        f"Клиент {name} (ID: `{client_id}`) ждёт ответа уже больше 30 минут.\n"
        f"Последнее сообщение: {last_msg}\n\n"
        f"Нажмите «Ответить» в одном из предыдущих уведомлений."
    )
    
    for master_id in MASTER_IDS:
        try:
            await bot.send_message(
                chat_id=master_id,
                text=reminder_text,
                parse_mode='Markdown'
            )
            logger.info(f"Напоминание отправлено мастеру {master_id}")
        except Exception as e:
            logger.error(f"Не удалось отправить напоминание мастеру {master_id}: {e}")
    
    # Если клиент всё ещё ждёт, запускаем следующее напоминание через 30 минут
    if waiting_status.get(client_id, False):
        timer = schedule_reminder(asyncio.get_event_loop(), bot, client_id, delay_seconds=1800)
        # Сохраняем таймер в глобальном словаре (если он ещё не отменён)
        timer_dict[client_id]['reminder'] = timer

# Глобальные словари для состояния таймеров
timer_dict = {}  # {client_id: {'timeout': timer, 'reminder': timer}}
waiting_status = {}  # {client_id: True/False}

def cancel_timers(client_id):
    """Отменяет все таймеры для клиента."""
    if client_id in timer_dict:
        if timer_dict[client_id].get('timeout'):
            timer_dict[client_id]['timeout'].cancel()
        if timer_dict[client_id].get('reminder'):
            timer_dict[client_id]['reminder'].cancel()
        del timer_dict[client_id]
    waiting_status[client_id] = False

# ==================== ОБРАБОТЧИКИ КОМАНД И ТЕКСТОВЫХ КНОПОК ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    display_name = get_user_display_name(user.id)
    if display_name.startswith("ID:"):
        await update.message.reply_text(
            f"👋 Добрый день! Это виртуальный помощник студии маникюра «{BUSINESS_NAME}».\n\n"
            "🙋‍♀️ Как мне к вам обращаться? Напишите ваше имя."
        )
        context.user_data['awaiting_name'] = True
        return
    
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = (
        f"👋 Добро пожаловать, {get_user_display_name(user.id)}!\n\n"
        f"💬 Просто напишите свой вопрос, и я передам его мастеру.\n"
        f"Если вы спросите про цену, я сразу дам ответ."
    )
    if is_admin(user.id):
        await update.message.reply_text(welcome_text, reply_markup=get_admin_keyboard())
    elif is_master(user.id):
        await update.message.reply_text(welcome_text, reply_markup=get_master_keyboard())
    else:
        await update.message.reply_text(welcome_text, reply_markup=get_client_keyboard())
    log_message(user.id, "/start", is_master=False)

async def handle_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Пожалуйста, напишите ваше имя (не оставляйте пустым).")
        return
    save_user(user.id, user.username, user.first_name, user.last_name, display_name=name)
    context.user_data.pop('awaiting_name', None)
    await update.message.reply_text(f"✅ Отлично, {name}! Теперь я знаю, как к вам обращаться.")
    await show_main_menu(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    help_text = (
        "📖 *Справка по боту*\n\n"
        "• Просто напишите свой вопрос, и я передам его мастеру.\n"
        "• Если вы спросите про *цену*, я сразу дам ответ.\n"
        "• Мастера увидят ваше сообщение и смогут ответить.\n\n"
        "Для связи с мастером просто пишите сюда."
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')
    log_message(user.id, "/help", is_master=False)

async def active_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_master(user.id) and not is_admin(user.id):
        await update.message.reply_text("⛔ Эта команда только для мастеров.")
        return
    
    clients = get_active_clients()
    if not clients:
        await update.message.reply_text("📭 Активных клиентов нет.")
        return
    
    text = "📋 *Активные клиенты (писали за 7 дней):*\n\n"
    for idx, client_id in enumerate(clients, 1):
        name = get_user_display_name(client_id)
        last_msg = get_client_last_message(client_id)
        if last_msg and len(last_msg) > 30:
            last_msg = last_msg[:27] + "..."
        text += f"{idx}. {name} (ID: `{client_id}`)\n"
        if last_msg:
            text += f"   Последнее: {last_msg}\n"
    
    text += "\nВыберите клиента, нажав на кнопку ниже."
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_clients_list_keyboard(clients))

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Эта команда только для администраторов.")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        SELECT user_id, text, is_master, timestamp FROM logs
        ORDER BY timestamp DESC
        LIMIT 50
    ''')
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        await update.message.reply_text("📭 Логов пока нет.")
        return
    
    text = "📜 *Последние 50 логов:*\n\n"
    for user_id, msg_text, is_master_flag, ts in rows[::-1]:
        role = "👤 Клиент" if not is_master_flag else "🛠 Мастер"
        if len(msg_text) > 50:
            msg_text = msg_text[:47] + "..."
        text += f"`{ts}` {role} (ID:{user_id}): {msg_text}\n"
        if len(text) > 3800:
            text += "\n... (обрезано)"
            break
    await update.message.reply_text(text, parse_mode='Markdown')

async def set_auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Эта команда только для администраторов.")
        return
    
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ Использование: `/set_auto ключевое_слово текст ответа`\n"
            "Пример: `/set_auto цена Стоимость услуги 5000 ₽`",
            parse_mode='Markdown'
        )
        return
    
    keyword = context.args[0]
    response = ' '.join(context.args[1:])
    update_auto_response(keyword, response)
    await update.message.reply_text(f"✅ Автоответчик обновлён!\n\n🔑 Ключевое слово: `{keyword}`\n💬 Ответ: {response}", parse_mode='Markdown')

async def stop_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_master(user.id) and not is_admin(user.id):
        await update.message.reply_text("⛔ Эта команда только для мастеров.")
        return
    
    if 'active_client' in context.user_data:
        client_id = context.user_data.pop('active_client')
        # Отменяем таймеры для этого клиента (если они ещё висят)
        cancel_timers(client_id)
        await update.message.reply_text(f"✅ Диалог с клиентом (ID: {client_id}) завершён.")
    else:
        await update.message.reply_text("ℹ️ У вас нет активного диалога.")

# ==================== ОБРАБОТЧИК ТЕКСТОВЫХ КНОПОК ====================
async def handle_text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    
    if text == "📋 Активные диалоги":
        await active_command(update, context)
    elif text == "❓ Помощь":
        await help_command(update, context)
    elif text == "📜 Логи" and is_admin(user.id):
        await logs_command(update, context)
    elif text == "⚙️ Настроить автоответ" and is_admin(user.id):
        await update.message.reply_text(
            "Введите команду в формате:\n`/set_auto ключевое_слово текст ответа`",
            parse_mode='Markdown'
        )
    elif text == "🔄 Закончить диалог" and (is_master(user.id) or is_admin(user.id)):
        await stop_dialog(update, context)
    else:
        await handle_message(update, context)

# ==================== ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    
    if not text:
        return
    
    # Если клиент ещё не представился
    if context.user_data.get('awaiting_name'):
        await handle_name_input(update, context)
        return
    
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    # Если сообщение от мастера
    if is_master(user.id) or is_admin(user.id):
        client_id = context.user_data.get('active_client')
        if client_id:
            try:
                await context.bot.send_message(
                    chat_id=client_id,
                    text=f"🛠 *Мастер:* {text}",
                    parse_mode='Markdown'
                )
                await update.message.reply_text(f"✅ Ответ отправлен клиенту {get_user_display_name(client_id)} (ID: {client_id})")
                log_message(client_id, text, is_master=True)
                log_message(user.id, f"[Ответ клиенту {client_id}] {text}", is_master=True)
                
                # Отменяем таймеры для этого клиента
                cancel_timers(client_id)
                
                await update.message.reply_text(
                    f"💬 Вы всё ещё в диалоге с {get_user_display_name(client_id)}.\n"
                    f"Чтобы переключиться на другого клиента, используйте «📋 Активные диалоги»."
                )
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка отправки: {e}")
        else:
            await update.message.reply_text(
                "ℹ️ У вас нет активного диалога.\n"
                "Нажмите «Ответить» под сообщением клиента или выберите клиента в «📋 Активные диалоги»."
            )
            log_message(user.id, text, is_master=True)
        return
    
    # === ОБЫЧНЫЙ КЛИЕНТ ===
    
    # 1. Автоответчик
    if not context.user_data.get('auto_triggered'):
        keyword, response = get_auto_response()
        if keyword and keyword.lower() in text.lower():
            await update.message.reply_text(response)
            context.user_data['auto_triggered'] = True
            log_message(user.id, f"[АВТООТВЕТ] {text}", is_master=False)
            return
    
    # 2. Логируем
    log_message(user.id, text, is_master=False)
    
    # 3. Пересылаем мастерам
    display_name = get_user_display_name(user.id)
    forward_text = (
        f"📩 *Новое сообщение от клиента*\n"
        f"👤 {display_name}\n"
        f"🆔 `{user.id}`\n\n"
        f"📝 {text}"
    )
    reply_markup = get_reply_buttons(user.id)
    
    sent = False
    for master_id in MASTER_IDS:
        try:
            await context.bot.send_message(
                chat_id=master_id,
                text=forward_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            sent = True
        except Exception as e:
            logger.error(f"Не удалось отправить мастеру {master_id}: {e}")
    
    if sent:
        await update.message.reply_text("✅ Ваше сообщение отправлено мастеру. Ожидайте ответа.")
    else:
        await update.message.reply_text("⚠️ К сожалению, не удалось доставить сообщение мастерам. Попробуйте позже.")
        return
    
    # === ЗАПУСК ТАЙМЕРОВ (через threading) ===
    client_id = user.id
    loop = asyncio.get_event_loop()
    
    # Устанавливаем флаг ожидания
    waiting_status[client_id] = True
    
    # Таймаут на 5 минут только для первого сообщения
    if 'first_message_time' not in context.user_data:
        context.user_data['first_message_time'] = datetime.now()
        # Создаём таймер на 5 минут
        timer = schedule_busy_message(loop, context.bot, client_id, delay_seconds=300)
        timer_dict.setdefault(client_id, {})['timeout'] = timer
        logger.info(f"Таймаут запущен для клиента {client_id} на 5 минут")
    else:
        # Если это не первое сообщение, отменяем старую напоминалку (если есть)
        if client_id in timer_dict and timer_dict[client_id].get('reminder'):
            timer_dict[client_id]['reminder'].cancel()
            logger.info(f"Старая напоминалка отменена для клиента {client_id}")
    
    # Запускаем напоминание через 30 минут
    reminder_timer = schedule_reminder(loop, context.bot, client_id, delay_seconds=1800)
    timer_dict.setdefault(client_id, {})['reminder'] = reminder_timer
    logger.info(f"Напоминание запущено для клиента {client_id} через 30 минут")

# ==================== ОБРАБОТЧИК ИНЛАЙН-КНОПОК ====================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    data = query.data
    
    if data.startswith('reply_'):
        client_id = int(data.split('_')[1])
        context.user_data['active_client'] = client_id
        
        history = get_user_history(client_id, limit=5)
        if history:
            hist_text = "📖 *Последние сообщения:*\n\n"
            for msg_text, ts, is_master_flag in history:
                role = "👤 Клиент" if not is_master_flag else "🛠 Мастер"
                hist_text += f"`{ts[:16]}` {role}: {msg_text[:50]}\n"
            await query.edit_message_text(
                f"{hist_text}\n\n✏️ *Введите ваш ответ клиенту.*\n"
                f"Текущий диалог с: {get_user_display_name(client_id)} (ID: `{client_id}`)",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                f"✏️ *Введите ваш ответ клиенту.*\n"
                f"Текущий диалог с: {get_user_display_name(client_id)} (ID: `{client_id}`)",
                parse_mode='Markdown'
            )
    
    elif data.startswith('history_'):
        client_id = int(data.split('_')[1])
        history = get_user_history(client_id, limit=10)
        if not history:
            await query.edit_message_text(f"📭 История с клиентом {get_user_display_name(client_id)} пуста.")
            return
        
        text = f"📖 *История диалога с {get_user_display_name(client_id)} (ID: {client_id}):*\n\n"
        for msg_text, ts, is_master_flag in history:
            role = "👤 Клиент" if not is_master_flag else "🛠 Мастер"
            text += f"`{ts[:16]}` {role}: {msg_text}\n"
            if len(text) > 3800:
                text += "\n... (обрезано)"
                break
        
        keyboard = [[InlineKeyboardButton("✏️ Ответить", callback_data=f"reply_{client_id}")]]
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith('select_'):
        client_id = int(data.split('_')[1])
        context.user_data['active_client'] = client_id
        await query.edit_message_text(
            f"✅ Вы выбрали клиента: {get_user_display_name(client_id)} (ID: `{client_id}`)\n\n"
            f"Теперь все ваши сообщения будут отправляться этому клиенту.\n"
            f"Чтобы переключиться, используйте «📋 Активные диалоги».",
            parse_mode='Markdown'
        )

# ==================== ГЛАВНАЯ ФУНКЦИЯ ====================
def main():
    init_db()
    logger.info("База данных инициализирована")
    
    clean_old_logs(30)
    
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("active", active_command))
    application.add_handler(CommandHandler("logs", logs_command))
    application.add_handler(CommandHandler("set_auto", set_auto_command))
    application.add_handler(CommandHandler("stop_dialog", stop_dialog))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_buttons))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Ежедневная очистка логов (можно оставить через threading, но это не критично)
    # Просто запустим при старте, а дальше раз в сутки через threading.Timer – но это не обязательно, оставим только при старте.
    
    commands = [
        BotCommand("start", "Начать работу"),
        BotCommand("help", "Помощь"),
        BotCommand("active", "Активные диалоги (мастер)"),
        BotCommand("logs", "Логи (админ)"),
        BotCommand("set_auto", "Настроить автоответ (админ)"),
        BotCommand("stop_dialog", "Завершить диалог (мастер)"),
    ]
    application.bot.set_my_commands(commands)
    
    logger.info("Бот запущен в режиме Long Polling")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
