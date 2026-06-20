import os
import logging
import sqlite3
from datetime import datetime
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
# Переменные окружения читаются из настроек Bothost
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise ValueError("BOT_TOKEN не найден! Добавьте его в переменные окружения на Bothost.")

# Список ID мастеров и администраторов (вводим через запятую в одну строку)
MASTER_IDS = [int(x.strip()) for x in os.getenv('MASTER_IDS', '').split(',') if x.strip()]
ADMIN_IDS = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]

# Название бизнеса (можно задать через переменную или оставить по умолчанию)
BUSINESS_NAME = os.getenv('BUSINESS_NAME', 'БИЗНЕС')

# ==================== БАЗА ДАННЫХ (SQLite) ====================
DB_PATH = 'bot_database.db'

def init_db():
    """Создаёт таблицы, если их нет."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Таблица пользователей
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
    
    # Таблица логов
    cur.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            is_master BOOLEAN,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица для автоответчика
    cur.execute('''
        CREATE TABLE IF NOT EXISTS auto_response (
            id INTEGER PRIMARY KEY,
            keyword TEXT,
            response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Добавляем дефолтный автоответ, если таблица пуста
    cur.execute("SELECT COUNT(*) FROM auto_response")
    if cur.fetchone()[0] == 0:
        cur.execute('''
            INSERT INTO auto_response (keyword, response) 
            VALUES (?, ?)
        ''', ('цена', 'Стоимость маникюра начинается от 1500 ₽. Точная цена зависит от сложности и дизайна. Для записи напишите «хочу записаться», и я соединю вас с мастером.'))
    
    conn.commit()
    conn.close()

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def is_master(user_id: int) -> bool:
    """Проверяет, является ли пользователь мастером."""
    return user_id in MASTER_IDS

def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    return user_id in ADMIN_IDS

def save_user(user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    """Сохраняет пользователя в БД."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
        VALUES (?, ?, ?, ?)
    ''', (user_id, username, first_name, last_name))
    conn.commit()
    conn.close()

def log_message(user_id: int, text: str, is_master: bool = False):
    """Записывает сообщение в лог."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO logs (user_id, text, is_master)
        VALUES (?, ?, ?)
    ''', (user_id, text, is_master))
    conn.commit()
    conn.close()

def get_auto_response() -> Tuple[Optional[str], Optional[str]]:
    """Возвращает (ключевое_слово, текст_ответа) из БД."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT keyword, response FROM auto_response ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    if row:
        return row[0], row[1]
    return None, None

def update_auto_response(keyword: str, response: str):
    """Обновляет настройки автоответчика (вставляем новую запись)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO auto_response (keyword, response)
        VALUES (?, ?)
    ''', (keyword, response))
    conn.commit()
    conn.close()

def get_user_history(user_id: int, limit: int = 10) -> List[Tuple[str, str, bool]]:
    """
    Возвращает историю переписки с пользователем.
    Возвращает список кортежей: (текст, время, is_master)
    """
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
    return rows[::-1]  # Переворачиваем, чтобы шли от старых к новым

def get_active_clients() -> List[int]:
    """Возвращает список ID клиентов, которые писали боту за последние 7 дней."""
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

# ==================== КЛАВИАТУРЫ ====================
def get_client_keyboard():
    """Клавиатура для обычного клиента."""
    keyboard = [
        [KeyboardButton("/start"), KeyboardButton("/help")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_master_keyboard():
    """Клавиатура для мастера."""
    keyboard = [
        [KeyboardButton("/active"), KeyboardButton("/help")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_keyboard():
    """Клавиатура для администратора."""
    keyboard = [
        [KeyboardButton("/active"), KeyboardButton("/help")],
        [KeyboardButton("/logs"), KeyboardButton("/set_auto")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_reply_buttons(client_id: int):
    """Инлайн-кнопки для мастера: Ответить, Закрыть, История."""
    keyboard = [
        [
            InlineKeyboardButton("✏️ Ответить", callback_data=f"reply_{client_id}"),
            InlineKeyboardButton("📖 История", callback_data=f"history_{client_id}")
        ],
        [InlineKeyboardButton("❌ Закрыть диалог", callback_data=f"close_{client_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    user = update.effective_user
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    welcome_text = (
        f"👋 Добрый день! Это виртуальный помощник студии маникюра «{BUSINESS_NAME}».\n\n"
        f"Внизу экрана располагается меню – нажмите на подходящий для вас пункт.\n"
        f"Если у вас есть вопросы по ценам или записи, просто напишите их сюда – "
        f"я передам их мастеру."
    )
    
    # Отправляем приветствие с клавиатурой
    if is_admin(user.id):
        await update.message.reply_text(welcome_text, reply_markup=get_admin_keyboard())
    elif is_master(user.id):
        await update.message.reply_text(welcome_text, reply_markup=get_master_keyboard())
    else:
        await update.message.reply_text(welcome_text, reply_markup=get_client_keyboard())
    
    log_message(user.id, "/start", is_master=False)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help."""
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
    """Обработчик команды /active — список активных клиентов."""
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
        # Пытаемся получить имя из БД
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT first_name, username FROM users WHERE user_id = ?", (client_id,))
        row = cur.fetchone()
        conn.close()
        name = row[0] if row and row[0] else f"ID:{client_id}"
        if row and row[1]:
            name += f" (@{row[1]})"
        text += f"{idx}. {name} (ID: `{client_id}`)\n"
    
    text += "\nЧтобы ответить, используйте кнопки в сообщениях."
    await update.message.reply_text(text, parse_mode='Markdown')

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /logs — выгрузка логов (только для админов)."""
    user = update.effective_user
    
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Эта команда только для администраторов.")
        return
    
    # Получаем последние 50 логов
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
        # Обрезаем длинные сообщения
        if len(msg_text) > 50:
            msg_text = msg_text[:47] + "..."
        text += f"`{ts}` {role} (ID:{user_id}): {msg_text}\n"
        if len(text) > 3800:  # Telegram лимит ~4096
            text += "\n... (обрезано)"
            break
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def set_auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /set_auto <ключевое_слово> <текст_ответа>."""
    user = update.effective_user
    
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Эта команда только для администраторов.")
        return
    
    # Проверяем, что передан аргумент
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

# ==================== ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает все текстовые сообщения."""
    user = update.effective_user
    text = update.message.text
    
    if not text:
        return
    
    # Сохраняем пользователя
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    # Проверяем, является ли отправитель мастером
    if is_master(user.id) or is_admin(user.id):
        # Это сообщение от мастера — нужно отправить клиенту, если есть активный диалог
        client_id = context.user_data.get('active_client')
        if client_id:
            # Отправляем клиенту
            try:
                await context.bot.send_message(
                    chat_id=client_id,
                    text=f"🛠 *Мастер:* {text}",
                    parse_mode='Markdown'
                )
                await update.message.reply_text(f"✅ Ответ отправлен клиенту (ID: {client_id})")
                log_message(client_id, text, is_master=True)
                log_message(user.id, f"[Ответ клиенту {client_id}] {text}", is_master=True)
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка отправки: {e}")
        else:
            # Мастер не в активном диалоге — просто логируем
            await update.message.reply_text(
                "ℹ️ Вы не в активном диалоге с клиентом.\n"
                "Чтобы ответить, нажмите «Ответить» под сообщением клиента."
            )
            log_message(user.id, text, is_master=True)
        return
    
    # === ОБЫЧНЫЙ КЛИЕНТ ===
    
    # 1. Проверяем автоответчик (только если ещё не срабатывал)
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
    
    # Кнопки для мастера
    reply_markup = get_reply_buttons(user.id)
    
    # Отправляем каждому мастеру
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

# ==================== ОБРАБОТЧИК ИНЛАЙН-КНОПОК ====================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на инлайн-кнопки."""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    data = query.data
    
    if data.startswith('reply_'):
        # Кнопка "Ответить"
        client_id = int(data.split('_')[1])
        context.user_data['active_client'] = client_id
        
        # Показываем историю диалога
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
        # Кнопка "История"
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
        
        # Возвращаем кнопку "Ответить"
        keyboard = [[InlineKeyboardButton("✏️ Ответить", callback_data=f"reply_{client_id}")]]
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith('close_'):
        # Кнопка "Закрыть диалог"
        client_id = int(data.split('_')[1])
        if context.user_data.get('active_client') == client_id:
            context.user_data.pop('active_client', None)
        await query.edit_message_text(f"✅ Диалог с клиентом (ID: {client_id}) закрыт.")

# ==================== ГЛАВНАЯ ФУНКЦИЯ ====================
def main():
    """Запуск бота."""
    # Инициализируем БД
    init_db()
    logger.info("База данных инициализирована")
    
    # Создаём приложение
    application = Application.builder().token(TOKEN).build()
    
    # Регистрируем команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("active", active_command))
    application.add_handler(CommandHandler("logs", logs_command))
    application.add_handler(CommandHandler("set_auto", set_auto_command))
    
    # Регистрируем обработчики сообщений и кнопок
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Устанавливаем меню-команды для бота (появятся при вводе "/" в Telegram)
    commands = [
        BotCommand("start", "Начать работу"),
        BotCommand("help", "Помощь"),
        BotCommand("active", "Активные диалоги (мастер)"),
        BotCommand("logs", "Логи (админ)"),
        BotCommand("set_auto", "Настроить автоответ (админ)"),
    ]
    application.bot.set_my_commands(commands)
    
    # Запускаем в режиме Long Polling (идеально для Bothost)
    logger.info("Бот запущен в режиме Long Polling")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
