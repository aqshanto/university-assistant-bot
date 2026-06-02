import os
from threading import Thread
from http.server import SimpleHTTPRequestHandler, HTTPServer
import logging
import sqlite3
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

LOCAL_TZ = pytz.timezone('Asia/Dhaka')

# --- DATABASE HELPER ---
def get_db():
    """Establishes a connection to the local SQLite database."""
    # PARSE_DECLTYPES automatically converts timestamps to Python datetime objects
    conn = sqlite3.connect('university_bot.db', detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row  # Allows us to access columns by name (like a dictionary)
    return conn

def init_db():
    """Creates the database table automatically if it doesn't exist."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            task_name TEXT NOT NULL,
            deadline TIMESTAMP NOT NULL,
            description TEXT DEFAULT NULL
        )
    ''')
    db.commit()
    cursor.close()
    db.close()

# --- JOB LOGIC ---
async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    await context.bot.send_message(chat_id=job.chat_id, text=job.data['message'], parse_mode="HTML")

def schedule_reminder_jobs(job_queue, chat_id, task_id, task_name, target_time, description=None):
    now = datetime.now(LOCAL_TZ)
    job_name = f"{chat_id}_{task_id}"

    desc_text = f"\n\n📝 <b>Notes:</b>\n<i>{description}</i>" if description else ""

    time_1_day_before = target_time - timedelta(days=1)
    if time_1_day_before > now:
        job_queue.run_once(send_reminder, when=time_1_day_before, chat_id=chat_id, name=job_name,
                           data={'message': f"🔔 <b>Reminder:</b> 1 day left to complete '{task_name}'!{desc_text}"})

    time_1_hour_before = target_time - timedelta(hours=1)
    if time_1_hour_before > now:
        job_queue.run_once(send_reminder, when=time_1_hour_before, chat_id=chat_id, name=job_name,
                           data={'message': f"⏳ <b>Urgent:</b> Only 1 hour left for '{task_name}'!{desc_text}"})

    if target_time > now:
        job_queue.run_once(send_reminder, when=target_time, chat_id=chat_id, name=job_name,
                           data={'message': f"🚨 <b>Time is up!</b> The deadline for '{task_name}' has been reached.{desc_text}"})

# --- STARTUP ROUTINE ---
async def post_init(application: Application) -> None:
    init_db() # Ensure the database exists when the bot starts

    db = get_db()
    cursor = db.cursor()

    # We compare with the current naive time
    now_naive = datetime.now(LOCAL_TZ).replace(tzinfo=None)
    cursor.execute("SELECT id, chat_id, task_name, deadline, description FROM tasks WHERE deadline > ?", (now_naive,))
    pending_tasks = cursor.fetchall()

    for task in pending_tasks:
        target_time = LOCAL_TZ.localize(task['deadline'])
        schedule_reminder_jobs(
            application.job_queue,
            task['chat_id'],
            task['id'],
            task['task_name'],
            target_time,
            task['description']
        )

    print(f"Restored {len(pending_tasks)} upcoming reminders from the database.")
    cursor.close()
    db.close()

# --- COMMAND HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [
            InlineKeyboardButton("📅 List All Tasks", callback_data='list_tasks'),
            InlineKeyboardButton("➕ Help & Commands", callback_data='help_schedule'),
        ],
        [
            InlineKeyboardButton("🌐 Varsity Class Routine", url="https://routine.zohirrayhan.me/"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 <b>Welcome to the University Assistant Bot!</b>\n\n"
        "I help track research deadlines and assignment routines. "
        "Use the buttons below to interact with me:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def schedule_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    raw_text = update.message.text

    lines = raw_text.split('\n', 1)
    command_line = lines[0]
    description = lines[1].strip() if len(lines) > 1 and lines[1].strip() else None

    parts = command_line.split()

    if len(parts) < 4:
        await update.message.reply_text(
            "⚠️ <b>Usage:</b> <code>/schedule [Task Name] YYYY-MM-DD HH:MM</code>\n"
            "<i>(Optional: Press Shift+Enter to add notes on the next line)</i>",
            parse_mode="HTML"
        )
        return

    time_str, date_str = parts[-1], parts[-2]
    task_name = " ".join(parts[1:-2])

    try:
        target_time = LOCAL_TZ.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        if target_time < datetime.now(LOCAL_TZ):
            await update.message.reply_text("❌ You cannot schedule a task in the past!")
            return

        db = get_db()
        cursor = db.cursor()
        db_time = target_time.replace(tzinfo=None)

        # SQLite uses ? instead of %s
        cursor.execute("INSERT INTO tasks (chat_id, task_name, deadline, description) VALUES (?, ?, ?, ?)",
                       (chat_id, task_name, db_time, description))

        task_id = cursor.lastrowid
        db.commit()
        cursor.close()
        db.close()

        schedule_reminder_jobs(context.job_queue, chat_id, task_id, task_name, target_time, description)

        await update.message.reply_text(f"✅ Task <b>ID: {task_id}</b> ('{task_name}') scheduled successfully!", parse_mode="HTML")

    except ValueError:
        await update.message.reply_text("❌ Format error! Use `YYYY-MM-DD HH:MM` on the first line.")

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    db = get_db()
    cursor = db.cursor()

    now_naive = datetime.now(LOCAL_TZ).replace(tzinfo=None)
    cursor.execute("SELECT id, task_name, deadline, description FROM tasks WHERE chat_id = ? AND deadline > ? ORDER BY deadline ASC", (chat_id, now_naive))
    active_tasks = cursor.fetchall()
    cursor.close()
    db.close()

    if not active_tasks:
        await context.bot.send_message(chat_id=chat_id, text="📭 <i>No pending tasks scheduled right now.</i>", parse_mode="HTML")
        return

    response = "📋 <b>Active Deadlines</b>\n\n"
    for index, task in enumerate(active_tasks, start=1):
        deadline = task['deadline']
        date_f = deadline.strftime("%b %d, %Y")
        time_f = deadline.strftime("%I:%M %p")

        response += f"📌 <b>ID: {task['id']} | {task['task_name']}</b>\n"
        response += f"     ↳ ⏳ <i>{date_f} at {time_f}</i>\n"

        if task['description']:
            clean_desc = task['description'].replace('\n', ' | ')
            response += f"     ↳ 📓 <i>{clean_desc}</i>\n"

        response += "\n"

    await context.bot.send_message(chat_id=chat_id, text=response, parse_mode="HTML")

async def delete_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parts = update.message.text.split()

    if len(parts) != 2:
        await update.message.reply_text("⚠️ <b>Usage:</b> <code>/delete [Task_ID]</code>", parse_mode="HTML")
        return

    try:
        task_id = int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ Task ID must be a number.")
        return

    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM tasks WHERE id = ? AND chat_id = ?", (task_id, chat_id))
    deleted_count = cursor.rowcount
    db.commit()
    cursor.close()
    db.close()

    if deleted_count == 0:
        await update.message.reply_text("❌ Task not found or already deleted.")
        return

    current_jobs = context.job_queue.get_jobs_by_name(f"{chat_id}_{task_id}")
    for job in current_jobs:
        job.schedule_removal()

    await update.message.reply_text(f"🗑️ Task <b>ID: {task_id}</b> has been deleted successfully.", parse_mode="HTML")

async def modify_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    raw_text = update.message.text

    lines = raw_text.split('\n', 1)
    command_line = lines[0]
    description = lines[1].strip() if len(lines) > 1 and lines[1].strip() else None

    parts = command_line.split()

    if len(parts) < 5:
        await update.message.reply_text(
            "⚠️ <b>Usage:</b> <code>/modify [Task_ID] [New Name] YYYY-MM-DD HH:MM</code>\n"
            "<i>(Optional: Press Shift+Enter to add new notes on the next line)</i>",
            parse_mode="HTML"
        )
        return

    try:
        task_id = int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ Task ID must be a number.")
        return

    time_str, date_str = parts[-1], parts[-2]
    task_name = " ".join(parts[2:-2])

    try:
        target_time = LOCAL_TZ.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        if target_time < datetime.now(LOCAL_TZ):
            await update.message.reply_text("❌ You cannot schedule a task in the past!")
            return

        db = get_db()
        cursor = db.cursor()

        cursor.execute("SELECT id FROM tasks WHERE id = ? AND chat_id = ?", (task_id, chat_id))
        if not cursor.fetchone():
            await update.message.reply_text("❌ Task not found.")
            cursor.close()
            db.close()
            return

        db_time = target_time.replace(tzinfo=None)
        cursor.execute("UPDATE tasks SET task_name=?, deadline=?, description=? WHERE id=?",
                       (task_name, db_time, description, task_id))
        db.commit()
        cursor.close()
        db.close()

        current_jobs = context.job_queue.get_jobs_by_name(f"{chat_id}_{task_id}")
        for job in current_jobs:
            job.schedule_removal()

        schedule_reminder_jobs(context.job_queue, chat_id, task_id, task_name, target_time, description)

        await update.message.reply_text(f"✅ Task <b>ID: {task_id}</b> updated successfully!", parse_mode="HTML")

    except ValueError:
        await update.message.reply_text("❌ Format error! Use `YYYY-MM-DD HH:MM` on the first line.")

async def button_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == 'list_tasks':
        await list_tasks(update, context)

    elif query.data == 'help_schedule':
        await query.edit_message_text(
            text="📝 <b>Bot Commands:</b>\n\n"
                 "<b>1. Schedule:</b>\n<code>/schedule [Task Name] YYYY-MM-DD HH:MM</code>\n"
                 "<i>(Optional: Use Shift+Enter to add notes on a new line)</i>\n\n"
                 "<b>2. Delete:</b>\n<code>/delete [Task_ID]</code>\n\n"
                 "<b>3. Modify:</b>\n<code>/modify [Task_ID] [New Name] YYYY-MM-DD HH:MM</code>",
            parse_mode="HTML"
        )
def run_dummy_server():
    """Runs a dummy HTTP server to satisfy Render's Web Service port requirement."""
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"Dummy web server running on port {port}...")
    server.serve_forever()

def main() -> None:
    TOKEN = "8626960850:AAGhjWkDpS3NbVHSnDJmLulqX-j4vPuYY1E"

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("schedule", schedule_task))
    app.add_handler(CommandHandler("list", list_tasks))
    app.add_handler(CommandHandler("delete", delete_task))
    app.add_handler(CommandHandler("modify", modify_task))
    app.add_handler(CallbackQueryHandler(button_tap))

    print("Bot is running with SQLite integration...")
    Thread(target=run_dummy_server, daemon=True).start()
    app.run_polling()

if __name__ == "__main__":
    main()
