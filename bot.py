import os
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ====================
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise ValueError("BOT_TOKEN не найден! Добавьте его в переменные окружения на Bothost.")

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
    # Дефолтный автоответ
    cur.execute("SELECT COUNT(*) FROM auto_response")
    if cur.fetchone()[0] == 0:
        cur.execute('''
            INSERT INTO auto_response (keyword, response) 
            VALUES (?, ?)
        ''', ('цена', '💅 Стоимость маникюра начинается от 1500 ₽. Точная цена зависит от сложности и дизайна. Для записи напишите «хочу записаться», и я соединю вас с мастером.'))
    conn.commit()
    conn.close()

def clean_old_logs(days=30):
    """Удаляет логи старше указанного количества дней."""
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

def save_user(user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
        VALUES (?, ?, ?, ?)
    ''', (user_id, username, first_name, last_name))
    conn.commit()
    conn.close()

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

# ==================== КРАСИВЫЕ КЛАВИАТУРЫ ====================
def get_client_keyboard():
    """Клавиатура для обычного клиента (красивая)."""
    keyboard = [
        [KeyboardButton("📋 Главное меню"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_master_keyboard():
    """Клавиатура для мастера (красивая)."""
    keyboard = [
        [KeyboardButton("📋 Активные диалоги"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_keyboard():
    """Клавиатура для администратора (красивая)."""
    keyboard = [
        [KeyboardButton("📋 Активные диалоги"), KeyboardButton("❓ Помощь")],
        [KeyboardButton("📜 Логи"), KeyboardButton("⚙️ Настроить автоответ")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_reply_buttons(client_id: int):
    """Инлайн-кнопки для мастера: Ответить, История, Закрыть (красивые)."""
    keyboard = [
        [
            InlineKeyboardButton("✏️ Ответить", callback_data=f"reply_{client_id}"),
            InlineKeyboardButton("📖 История", callback_data=f"history_{client_id}")
        ],
        [InlineKeyboardButton("❌ Закрыть диалог", callback_data=f"close_{client_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_client_info(user_id: int) -> str:
    """Возвращает строку с именем и юзернеймом клиента."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT first_name, username FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        name = row[0]
        if row[1]:
            name += f" (@{row[1]})"
        return name
    return f"ID: {user_id}"

# ==================== ОБРАБОТЧИКИ КОМАНД И ТЕКСТОВЫХ КНОПОК ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    welcome_text = (
        f"👋 Добрый день! Это виртуальный помощник студии маникюра «{BUSINESS_NAME}».\n\n"
        f"💬 Внизу экрана располагается меню – нажмите на подходящий для вас пункт.\n"
        f"Если у вас есть вопросы по ценам или записи, просто напишите их сюда – "
        f"я передам их мастеру."
    )
    
    if is_admin(user.id):
        await update.message.reply_text(welcome_text, reply_markup=get_admin_keyboard())
    elif is_master(user.id):
        await update.message.reply_text(welcome_text, reply_markup=get_master_keyboard())
    else:
        await update.message.reply_text(welcome_text, reply_markup=get_client_keyboard())
    
    log_message(user.id, "/start", is_master=False)

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
        await update.message.reply_text("📭 Активных диалогов нет.")
        return
    
    text = "📋 *Активные клиенты (писали за 7 дней):*\n\n"
    for idx, client_id in enumerate(clients, 1):
        name = get_client_info(client_id)
        text += f"{idx}. {name} (ID: `{client_id}`)\n"
    
    text += "\nЧтобы ответить, используйте кнопки в сообщениях."
    await update.message.reply_text(text, parse_mode='Markdown')

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

# ==================== ОБРАБОТЧИК ТЕКСТОВЫХ КНОПОК (reply-клавиатура) ====================
async def handle_text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на Reply-кнопки (меню)."""
    user = update.effective_user
    text = update.message.text
    
    if text == "📋 Главное меню" or text == "📋 Активные диалоги":
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
    else:
        # Если текст не совпадает с кнопками, обрабатываем как обычное сообщение
        await handle_message(update, context)

# ==================== ЗАДАНИЯ ДЛЯ ТАЙМЕРОВ (автоответ "заняты" и напоминания) ====================
async def send_busy_message(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет клиенту сообщение, что все мастера заняты (через 5 минут после первого сообщения)."""
    job = context.job
    client_id = job.data['client_id']
    # Проверяем, не ответил ли уже мастер (по флагу waiting_for_response)
    client_data = context.application.user_data.get(client_id, {})
    if client_data.get('waiting_for_response', False):
        try:
            await context.bot.send_message(
                chat_id=client_id,
                text="⏳ *Все мастера сейчас заняты, но мы увидели ваше сообщение.*\n"
                     "Ожидайте ответа, мы свяжемся с вами в ближайшее время.",
                parse_mode='Markdown'
            )
            # Логируем это автоматическое сообщение
            log_message(client_id, "[АВТО: мастера заняты]", is_master=False)
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение о занятости клиенту {client_id}: {e}")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет напоминание всем мастерам, что клиент ждёт ответа (каждые 30 минут)."""
    job = context.job
    client_id = job.data['client_id']
    client_data = context.application.user_data.get(client_id, {})
    
    # Если клиент уже получил ответ, ничего не делаем
    if not client_data.get('waiting_for_response', False):
        return
    
    # Получаем имя клиента и последнее сообщение
    name = get_client_info(client_id)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        SELECT text FROM logs
        WHERE user_id = ? AND is_master = 0
        ORDER BY timestamp DESC LIMIT 1
    ''', (client_id,))
    row = cur.fetchone()
    conn.close()
    last_msg = row[0] if row else "сообщение"
    
    reminder_text = (
        f"🔔 *Напоминание!*\n"
        f"Клиент {name} (ID: `{client_id}`) ждёт ответа уже больше 30 минут.\n"
        f"Последнее сообщение: {last_msg}\n\n"
        f"Нажмите «Ответить» в одном из предыдущих уведомлений."
    )
    
    # Отправляем всем мастерам
    for master_id in MASTER_IDS:
        try:
            await context.bot.send_message(
                chat_id=master_id,
                text=reminder_text,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Не удалось отправить напоминание мастеру {master_id}: {e}")
    
    # Перезапускаем задание через 30 минут, если клиент всё ещё ждёт
    if client_data.get('waiting_for_response', False):
        context.job_queue.run_once(
            send_reminder,
            when=timedelta(minutes=30),
            data={'client_id': client_id},
            name=f"reminder_{client_id}"
        )

# ==================== ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает все текстовые сообщения (и команды, и обычные)."""
    user = update.effective_user
    text = update.message.text
    
    if not text:
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
                await update.message.reply_text(f"✅ Ответ отправлен клиенту (ID: {client_id})")
                log_message(client_id, text, is_master=True)
                log_message(user.id, f"[Ответ клиенту {client_id}] {text}", is_master=True)
                
                # === ОТМЕНА ВСЕХ ЗАДАНИЙ ДЛЯ ЭТОГО КЛИЕНТА ===
                client_data = context.application.user_data.get(client_id)
                if client_data:
                    # Отменяем таймаут и напоминание
                    job_timeout = client_data.get('timeout_job')
                    if job_timeout:
                        job_timeout.schedule_removal()
                    job_reminder = client_data.get('reminder_job')
                    if job_reminder:
                        job_reminder.schedule_removal()
                    # Сбрасываем флаги
                    client_data['waiting_for_response'] = False
                    client_data['first_message_time'] = None
                    # Удаляем записи о заданиях, чтобы не висели
                    client_data.pop('timeout_job', None)
                    client_data.pop('reminder_job', None)
                
                # Очищаем активный диалог у мастера (чтобы не отправлять повторно)
                context.user_data.pop('active_client', None)
                
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка отправки: {e}")
        else:
            await update.message.reply_text(
                "ℹ️ Вы не в активном диалоге с клиентом.\n"
                "Чтобы ответить, нажмите «Ответить» под сообщением клиента."
            )
            log_message(user.id, text, is_master=True)
        return
    
    # === ОБЫЧНЫЙ КЛИЕНТ ===
    
    # 1. Проверяем автоответчик
    if not context.user_data.get('auto_triggered'):
        keyword, response = get_auto_response()
        if keyword and keyword.lower() in text.lower():
            await update.message.reply_text(response)
            context.user_data['auto_triggered'] = True
            log_message(user.id, f"[АВТООТВЕТ] {text}", is_master=False)
            return
    
    # 2. Логируем сообщение клиента
    log_message(user.id, text, is_master=False)
    
    # 3. Пересылаем всем мастерам
    username = f"@{user.username}" if user.username else f"ID:{user.id}"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    sender_info = f"{full_name} ({username})" if full_name else username
    
    forward_text = (
        f"📩 *Новое сообщение от клиента*\n"
        f"👤 {sender_info}\n"
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
        return  # Не запускаем таймеры, если не доставлено
    
    # === ЗАПУСК ТАЙМЕРОВ ДЛЯ КЛИЕНТА ===
    client_data = context.application.user_data.setdefault(user.id, {})
    
    # Проверяем, первое ли это сообщение (по отсутствию first_message_time)
    if 'first_message_time' not in client_data:
        client_data['first_message_time'] = datetime.now()
        # Запускаем таймер на 5 минут для уведомления о занятости
        job_timeout = context.job_queue.run_once(
            send_busy_message,
            when=timedelta(minutes=5),
            data={'client_id': user.id},
            name=f"timeout_{user.id}"
        )
        client_data['timeout_job'] = job_timeout
    else:
        # Если это не первое сообщение, таймаут не запускаем, но напоминание перезапустим
        # (удаляем старую job если есть)
        old_job = client_data.get('reminder_job')
        if old_job:
            old_job.schedule_removal()
    
    # Устанавливаем флаг ожидания ответа
    client_data['waiting_for_response'] = True
    
    # Запускаем / перезапускаем напоминание через 30 минут
    job_reminder = context.job_queue.run_once(
        send_reminder,
        when=timedelta(minutes=30),
        data={'client_id': user.id},
        name=f"reminder_{user.id}"
    )
    client_data['reminder_job'] = job_reminder

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
                f"Текущий диалог с ID: `{client_id}`",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                f"✏️ *Введите ваш ответ клиенту.*\n"
                f"Текущий диалог с ID: `{client_id}`",
                parse_mode='Markdown'
            )
    
    elif data.startswith('history_'):
        client_id = int(data.split('_')[1])
        history = get_user_history(client_id, limit=10)
        if not history:
            await query.edit_message_text(f"📭 История с клиентом (ID: {client_id}) пуста.")
            return
        
        text = f"📖 *История диалога с клиентом (ID: {client_id}):*\n\n"
        for msg_text, ts, is_master_flag in history:
            role = "👤 Клиент" if not is_master_flag else "🛠 Мастер"
            text += f"`{ts[:16]}` {role}: {msg_text}\n"
            if len(text) > 3800:
                text += "\n... (обрезано)"
                break
        
        keyboard = [[InlineKeyboardButton("✏️ Ответить", callback_data=f"reply_{client_id}")]]
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith('close_'):
        client_id = int(data.split('_')[1])
        if context.user_data.get('active_client') == client_id:
            context.user_data.pop('active_client', None)
        await query.edit_message_text(f"✅ Диалог с клиентом (ID: {client_id}) закрыт.")

# ==================== ГЛАВНАЯ ФУНКЦИЯ ====================
def main():
    init_db()
    logger.info("База данных инициализирована")
    
    # Очистка логов при старте
    clean_old_logs(30)
    
    application = Application.builder().token(TOKEN).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("active", active_command))
    application.add_handler(CommandHandler("logs", logs_command))
    application.add_handler(CommandHandler("set_auto", set_auto_command))
    
    # Обработчики
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_buttons))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Ежедневная очистка логов (запускаем в 3:00 ночи)
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_daily(
            clean_old_logs,
            time=datetime.strptime("03:00", "%H:%M").time(),
            days=30  # передаём параметр days=30
        )
        logger.info("Запланирована ежедневная очистка логов в 3:00")
    
    # Устанавливаем меню-команды
    commands = [
        BotCommand("start", "Начать работу"),
        BotCommand("help", "Помощь"),
        BotCommand("active", "Активные диалоги"),
        BotCommand("logs", "Логи (админ)"),
        BotCommand("set_auto", "Настроить автоответ (админ)"),
    ]
    application.bot.set_my_commands(commands)
    
    logger.info("Бот запущен в режиме Long Polling")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
