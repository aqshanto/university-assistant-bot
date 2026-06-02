import os
from threading import Thread
from http.server import SimpleHTTPRequestHandler, HTTPServer
import logging
import sqlite3
from datetime import datetime, timedelta, time as dtime
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

LOCAL_TZ = pytz.timezone('Asia/Dhaka')

# --- 🎯 আপনার ফিক্সড ক্লাস রুটিন (66_O) ---
# Format: ("Day", "HH:MM", "Room", "Class Name")
FIXED_ROUTINE = [
    # Saturday
    ("Saturday", "10:00", "KT-306", "Software Engineering"),
    ("Saturday", "11:30", "KT-318(A)", "Microprocessor and Microcontrollers"),
    ("Saturday", "14:30", "KT-221", "Financial and Managerial Accounting"),
    
    # Wednesday
    ("Wednesday", "11:30", "G1-022", "Computer Networks Lab (O2)"),
    ("Wednesday", "14:30", "KT-304", "Computer Networks"),
    ("Wednesday", "16:00", "KT-304", "Software Engineering"),
    
    # Thursday
    ("Thursday", "08:30", "G1-013", "Computer Networks Lab (O1)"),
    ("Thursday", "11:30", "KT-516", "Microprocessor and Microcontrollers"),
    ("Thursday", "14:30", "KT-518", "Computer Networks"),
    ("Thursday", "16:00", "KT-518", "Financial and Managerial Accounting"),
]

# --- DATABASE HELPER ---
def get_db():
    conn = sqlite3.connect('university_bot.db', detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row  
    return conn

def init_db():
    db = get_db()
    cursor = db.cursor()
    
    # Tasks Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            task_name TEXT NOT NULL,
            deadline TIMESTAMP NOT NULL,
            description TEXT DEFAULT NULL
        )
    ''')
    
    # Routines Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS routines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            day_of_week TEXT NOT NULL,
            class_name TEXT NOT NULL,
            start_time TEXT NOT NULL,
            room_no TEXT NOT NULL
        )
    ''')
    
    db.commit()
    cursor.close()
    db.close()

# --- JOB LOGIC (TASKS) ---
async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    await context.bot.send_message(chat_id=job.chat_id, text=job.data['message'], parse_mode="HTML")

def schedule_reminder_jobs(job_queue, chat_id, task_id, task_name, target_time, description=None):
    now = datetime.now(LOCAL_TZ)
    job_name = f"{chat_id}_{task_id}"

    desc_text = f"\n\n📝 <b>Notes:</b>\n<i>{description}</i>" if description else ""

    # ১ দিন আগের অ্যালার্ট
    time_1_day_before = target_time - timedelta(days=1)
    if time_1_day_before > now:
        job_queue.run_once(send_reminder, when=time_1_day_before, chat_id=chat_id, name=job_name,
                           data={'message': f"🔔 <b>Reminder:</b> 1 day left to complete '{task_name}'!{desc_text}"})

    # ১ ঘণ্টা আগের অ্যালার্ট
    time_1_hour_before = target_time - timedelta(hours=1)
    if time_1_hour_before > now:
        job_queue.run_once(send_reminder, when=time_1_hour_before, chat_id=chat_id, name=job_name,
                           data={'message': f"⏳ <b>Urgent:</b> Only 1 hour left for '{task_name}'!{desc_text}"})

    # ঠিক ডেডলাইনের সময় অ্যালার্ট
    if target_time > now:
        job_queue.run_once(send_reminder, when=target_time, chat_id=chat_id, name=job_name,
                           data={'message': f"🚨 <b>Time is up!</b> The deadline for '{task_name}' has been reached.{desc_text}"})

# --- JOB LOGIC (ROUTINES & MORNING SUMMARY) ---
async def check_class_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """প্রতি মিনিটে চেক করবে আগামী ১০ মিনিট পর কোনো ক্লাস আছে কি না"""
    now = datetime.now(LOCAL_TZ)
    current_day = now.strftime("%A")
    target_time = now + timedelta(minutes=10)
    target_time_str = target_time.strftime("%H:%M")

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT chat_id, class_name, room_no FROM routines WHERE day_of_week = ? AND start_time = ?",
        (current_day, target_time_str)
    )
    upcoming_classes = cursor.fetchall()
    cursor.close()
    db.close()

    for cls in upcoming_classes:
        message = (
            f"⏰ <b>ক্লাস অ্যালার্ট!</b>\n\n"
            f"📚 কোর্স: <b>{cls['class_name']}</b>\n"
            f"🚪 রুম নম্বর: <code>{cls['room_no']}</code>\n"
            f"⏳ ঠিক ১০ মিনিট পর আপনার ক্লাস শুরু হতে যাচ্ছে! জলদি রেডি হয়ে নিন।"
        )
        await context.bot.send_message(chat_id=cls['chat_id'], text=message, parse_mode="HTML")

async def send_morning_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """সকাল ৮টায় আজকের ক্লাস এবং টাস্কের লিস্ট একসাথে পাঠাবে"""
    now = datetime.now(LOCAL_TZ)
    current_day = now.strftime("%A")

    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999).replace(tzinfo=None)

    db = get_db()
    cursor = db.cursor()
    
    # যেসব গ্রুপে রুটিন বা টাস্ক আছে তাদের লিস্ট বের করা
    cursor.execute("SELECT chat_id FROM routines UNION SELECT chat_id FROM tasks")
    chats = [row['chat_id'] for row in cursor.fetchall()]

    for chat_id in chats:
        # আজকের ক্লাস খোঁজা
        cursor.execute(
            "SELECT class_name, start_time, room_no FROM routines WHERE chat_id = ? AND day_of_week = ? ORDER BY start_time ASC",
            (chat_id, current_day)
        )
        todays_classes = cursor.fetchall()
        
        # আজকের টাস্ক/অ্যাসাইনমেন্ট খোঁজা
        cursor.execute(
            "SELECT id, task_name, deadline FROM tasks WHERE chat_id = ? AND deadline >= ? AND deadline <= ? ORDER BY deadline ASC",
            (chat_id, start_of_day, end_of_day)
        )
        todays_tasks = cursor.fetchall()

        if not todays_classes and not todays_tasks:
            continue

        response = f"☀️ <b>শুভ সকাল! আজ {current_day} এর আপডেট:</b>\n\n"
        
        # ক্লাস সেকশন
        if todays_classes:
            response += "🎓 <b>আজকের ক্লাস রুটিন:</b>\n"
            for index, cls in enumerate(todays_classes, start=1):
                time_obj = datetime.strptime(cls['start_time'], "%H:%M")
                time_12h = time_obj.strftime("%I:%M %p")
                response += f"  {index}. 📘 <b>{cls['class_name']}</b>\n     ⏳ সময়: {time_12h} | 🚪 রুম: {cls['room_no']}\n"
            response += "\n"
        else:
            response += "🎓 <b>আজকের ক্লাস রুটিন:</b>\n  আজ আপনার কোনো ক্লাস নেই! 🎉\n\n"
            
        # টাস্ক সেকশন
        if todays_tasks:
            response += "📋 <b>আজকের অ্যাসাইনমেন্ট/টাস্ক:</b>\n"
            for index, task in enumerate(todays_tasks, start=1):
                deadline = task['deadline']
                time_f = deadline.strftime("%I:%M %p")
                response += f"  {index}. 📌 <b>{task['task_name']}</b> (ID: {task['id']})\n     ⏳ ডেডলাইন: {time_f}\n"
        else:
            response += "📋 <b>আজকের অ্যাসাইনমেন্ট/টাস্ক:</b>\n  আজ কোনো টাস্কের ডেডলাইন নেই! 🥳\n"

        await context.bot.send_message(chat_id=chat_id, text=response, parse_mode="HTML")

    cursor.close()
    db.close()

# --- STARTUP ROUTINE ---
async def post_init(application: Application) -> None:
    init_db() 

    # 1. Restore Task Reminders
    db = get_db()
    cursor = db.cursor()
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

    # 2. Schedule Routine Alerts (10 min warning)
    application.job_queue.run_repeating(check_class_alerts, interval=60, first=10)
    
    # 3. Schedule Morning Summary at 08:00 AM Daily
    morning_time = dtime(hour=8, minute=0, tzinfo=LOCAL_TZ)
    application.job_queue.run_daily(send_morning_summary, time=morning_time)
    
    # 4. Setup Telegram Native Bot Menu
    await application.bot.set_my_commands([
        BotCommand("start", "বটের মেইন মেনু ওপেন করুন"),
        BotCommand("schedule", "নতুন টাস্ক/অ্যাসাইনমেন্ট সেট করুন"),
        BotCommand("list", "পেন্ডিং টাস্কগুলো দেখুন"),
        BotCommand("list_routine", "পুরো সপ্তাহের ক্লাস রুটিন দেখুন"),
        BotCommand("load_routine", "ফিক্সড রুটিন ডেটাবেসে লোড করুন"),
        BotCommand("modify", "টাস্ক এডিট করুন"),
        BotCommand("delete", "টাস্ক ডিলিট করুন"),
        BotCommand("add_routine", "রুটিনে এক্সট্রা ক্লাস যোগ করুন"),
        BotCommand("delete_routine", "রুটিন থেকে ক্লাস ডিলিট করুন")
    ])
    
    print("Routine alerts, morning jobs, and bot menu scheduled successfully.")

# --- COMMAND HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [
            InlineKeyboardButton("📅 List All Tasks", callback_data='list_tasks'),
            InlineKeyboardButton("📚 List Class Routine", callback_data='list_routine'),
        ],
        [
            InlineKeyboardButton("➕ Help & Commands", callback_data='help_schedule'),
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
        await update.message.reply_text("⚠️ <b>Usage:</b> <code>/schedule [Task] YYYY-MM-DD HH:MM</code>", parse_mode="HTML")
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
        cursor.execute("INSERT INTO tasks (chat_id, task_name, deadline, description) VALUES (?, ?, ?, ?)",
                       (chat_id, task_name, db_time, description))
        task_id = cursor.lastrowid
        db.commit()
        cursor.close()
        db.close()

        schedule_reminder_jobs(context.job_queue, chat_id, task_id, task_name, target_time, description)
        await update.message.reply_text(f"✅ Task <b>ID: {task_id}</b> scheduled successfully!", parse_mode="HTML")

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
        response += f"📌 <b>ID: {task['id']} | {task['task_name']}</b>\n     ↳ ⏳ <i>{date_f} at {time_f}</i>\n"
        if task['description']:
            response += f"     ↳ 📓 <i>{task['description'].replace(chr(10), ' | ')}</i>\n"
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
        await update.message.reply_text("⚠️ <b>Usage:</b> <code>/modify [Task_ID] [New Name] YYYY-MM-DD HH:MM</code>", parse_mode="HTML")
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
        cursor.execute("UPDATE tasks SET task_name=?, deadline=?, description=? WHERE id=?", (task_name, db_time, description, task_id))
        db.commit()
        cursor.close()
        db.close()

        for job in context.job_queue.get_jobs_by_name(f"{chat_id}_{task_id}"):
            job.schedule_removal()

        schedule_reminder_jobs(context.job_queue, chat_id, task_id, task_name, target_time, description)
        await update.message.reply_text(f"✅ Task <b>ID: {task_id}</b> updated successfully!", parse_mode="HTML")

    except ValueError:
        await update.message.reply_text("❌ Format error! Use `YYYY-MM-DD HH:MM` on the first line.")

# --- COMMAND HANDLERS (ROUTINE) ---
async def load_fixed_routine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Loads the hardcoded FIXED_ROUTINE into the database for this chat."""
    chat_id = update.effective_chat.id
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute("DELETE FROM routines WHERE chat_id = ?", (chat_id,))
    
    for day, time_str, room, class_name in FIXED_ROUTINE:
        cursor.execute(
            "INSERT INTO routines (chat_id, day_of_week, class_name, start_time, room_no) VALUES (?, ?, ?, ?, ?)",
            (chat_id, day, class_name, time_str, room)
        )
    
    db.commit()
    cursor.close()
    db.close()
    await update.message.reply_text("✅ আপনার ফিক্সড রুটিন সফলভাবে ডাটাবেসে সেভ হয়েছে! চেক করতে /list_routine দিন।")

async def add_routine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parts = context.args

    if len(parts) < 4:
        await update.message.reply_text(
            "⚠️ <b>ব্যবহারের নিয়ম:</b>\n/add_routine <code>[Day] [HH:MM] [Room] [Class Name]</code>", parse_mode="HTML"
        )
        return

    day = parts[0].capitalize()
    time_str = parts[1]
    room = parts[2]
    class_name = " ".join(parts[3:])
    valid_days = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    
    if day not in valid_days:
        await update.message.reply_text("❌ বারের নাম ইংরেজিতে সঠিকভাবে লিখুন (e.g., Sunday)")
        return
    try:
        datetime.strptime(time_str, "%H:%M")
    except ValueError:
        await update.message.reply_text("❌ সময়টি ২৪-ঘণ্টার ফরম্যাটে সঠিকভাবে লিখুন (e.g., 08:30 বা 14:15)")
        return

    db = get_db()
    cursor = db.cursor()
    cursor.execute("INSERT INTO routines (chat_id, day_of_week, class_name, start_time, room_no) VALUES (?, ?, ?, ?, ?)",
                   (chat_id, day, class_name, time_str, room))
    db.commit()
    cursor.close()
    db.close()
    await update.message.reply_text(f"✅ রুটিনে এক্সট্রা ক্লাস যুক্ত হয়েছে: <b>{class_name}</b>", parse_mode="HTML")

async def list_routine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute("SELECT id, day_of_week, class_name, start_time, room_no FROM routines WHERE chat_id = ? ORDER BY day_of_week, start_time", (chat_id,))
    routines = cursor.fetchall()
    cursor.close()
    db.close()

    if not routines:
        await context.bot.send_message(chat_id=chat_id, text="📭 <i>আপনার রুটিনে কোনো ক্লাস নেই। /load_routine কমান্ড দিন।</i>", parse_mode="HTML")
        return

    response = "📚 <b>আপনার সম্পূর্ণ ক্লাস রুটিন:</b>\n\n"
    for r in routines:
        time_12h = datetime.strptime(r['start_time'], "%H:%M").strftime("%I:%M %p")
        response += f"🔹 <b>ID: {r['id']}</b> | {r['day_of_week']} - {time_12h}\n"
        response += f"   📘 {r['class_name']} (রুম: {r['room_no']})\n\n"

    await context.bot.send_message(chat_id=chat_id, text=response, parse_mode="HTML")

async def delete_routine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parts = update.message.text.split()

    if len(parts) != 2:
        await update.message.reply_text("⚠️ <b>Usage:</b> /delete_routine <code>[Routine_ID]</code>", parse_mode="HTML")
        return
    try:
        routine_id = int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ Routine ID must be a number.")
        return

    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM routines WHERE id = ? AND chat_id = ?", (routine_id, chat_id))
    deleted_count = cursor.rowcount
    db.commit()
    cursor.close()
    db.close()

    if deleted_count == 0:
        await update.message.reply_text("❌ Class not found or already deleted.")
    else:
        await update.message.reply_text(f"🗑️ Routine <b>ID: {routine_id}</b> has been deleted successfully.", parse_mode="HTML")

async def button_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == 'list_tasks':
        await list_tasks(update, context)
    elif query.data == 'list_routine':
        await list_routine(update, context)
    elif query.data == 'help_schedule':
        await query.edit_message_text(
            text="📝 <b>Bot Commands:</b>\n\n"
                 "<b>📌 Tasks:</b>\n"
                 "/schedule <code>[Task] YYYY-MM-DD HH:MM</code>\n"
                 "/delete <code>[Task_ID]</code>\n"
                 "/modify <code>[Task_ID] [New Name] YYYY-MM-DD HH:MM</code>\n\n"
                 "<b>📌 Routine:</b>\n"
                 "/load_routine <i>(Load fixed routine)</i>\n"
                 "/list_routine <i>(Show all classes & IDs)</i>\n"
                 "/add_routine <code>[Day] [HH:MM] [Room] [Class Name]</code>\n"
                 "/delete_routine <code>[Routine_ID]</code>",
            parse_mode="HTML"
        )

# --- DUMMY SERVER FOR RENDER ---
def run_dummy_server():
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

    # Task Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("schedule", schedule_task))
    app.add_handler(CommandHandler("list", list_tasks))
    app.add_handler(CommandHandler("delete", delete_task))
    app.add_handler(CommandHandler("modify", modify_task))
    
    # Routine Handlers
    app.add_handler(CommandHandler("load_routine", load_fixed_routine))
    app.add_handler(CommandHandler("list_routine", list_routine))
    app.add_handler(CommandHandler("add_routine", add_routine))
    app.add_handler(CommandHandler("delete_routine", delete_routine))
    
    app.add_handler(CallbackQueryHandler(button_tap))

    print("Bot is running with SQLite integration and Morning Summary...")
    Thread(target=run_dummy_server, daemon=True).start()
    app.run_polling()

if __name__ == "__main__":
    main()
