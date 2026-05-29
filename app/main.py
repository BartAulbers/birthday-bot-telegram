import os
import time
import logging
import mysql.connector
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TOKEN = os.getenv("TELEGRAM_TOKEN")
TZ = ZoneInfo(os.getenv("TZ", "Europe/Amsterdam"))


def get_db_connection(retries=5, delay_seconds=2):
    """Connects to MariaDB/MySQL with retries to survive slow container startup."""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return mysql.connector.connect(
                host=os.getenv("DB_HOST", "db"),
                port=int(os.getenv("DB_PORT", "3306")),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database=os.getenv("DB_NAME", "birthday_db"),
                connection_timeout=10,
                autocommit=False,
            )
        except Exception as error:
            last_error = error
            logging.warning("DB connection attempt %s/%s failed: %s", attempt, retries, error)
            if attempt < retries:
                time.sleep(delay_seconds)
    raise last_error


def init_database():
    """Ensures the required table exists on startup, retrying until the DB is ready."""
    conn = get_db_connection(retries=20, delay_seconds=3)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS birthdays (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(100) NOT NULL,
                name VARCHAR(255) NOT NULL,
                birthday DATE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """
        )
        conn.commit()
        cursor.close()
        logging.info("Database schema ready.")
    finally:
        conn.close()


# --- Command handlers ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎂 Birthday Bot\n\n"
        "/add Name YYYY-MM-DD — save a birthday\n"
        "/list — show all saved birthdays\n"
        "/remove Name — delete a birthday"
    )


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    try:
        if len(context.args) < 2:
            raise ValueError("Missing arguments")
        name, bday = context.args[0], context.args[1]
        datetime.strptime(bday, "%Y-%m-%d")

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO birthdays (user_id, name, birthday) VALUES (%s, %s, %s)",
            (chat_id, name, bday),
        )
        conn.commit()
        cursor.close()
        conn.close()

        await update.message.reply_text(f"✅ Saved {name}'s birthday ({bday})!")
    except Exception as e:
        logging.warning("/add failed: %s", e)
        await update.message.reply_text("❌ Error. Use: /add Name YYYY-MM-DD")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT name, birthday FROM birthdays WHERE user_id = %s "
            "ORDER BY DATE_FORMAT(birthday, '%%m-%%d')",
            (chat_id,),
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not rows:
            await update.message.reply_text("No birthdays saved yet. Use /add Name YYYY-MM-DD")
            return

        lines = [f"🎂 {r['name']}: {r['birthday'].strftime('%d %b %Y')}" for r in rows]
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        logging.error("/list failed: %s", e)
        await update.message.reply_text("❌ Could not retrieve birthdays.")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    try:
        if not context.args:
            raise ValueError("Missing name")
        name = context.args[0]

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM birthdays WHERE user_id = %s AND name = %s",
            (chat_id, name),
        )
        affected = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()

        if affected:
            await update.message.reply_text(f"🗑️ Removed {name}'s birthday.")
        else:
            await update.message.reply_text(f"❌ No birthday found for '{name}'.")
    except Exception as e:
        logging.error("/remove failed: %s", e)
        await update.message.reply_text("❌ Error. Use: /remove Name")


# --- Scheduled job ---

async def check_birthdays(context: ContextTypes.DEFAULT_TYPE):
    """Runs daily at 09:00 (local timezone) to send birthday reminders."""
    today = datetime.now(tz=TZ).strftime("%m-%d")
    logging.info("Running birthday check for %s", today)
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT user_id, name FROM birthdays WHERE DATE_FORMAT(birthday, '%%m-%%d') = %s",
            (today,),
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        for row in rows:
            await context.bot.send_message(
                chat_id=row["user_id"],
                text=f"🎂 Reminder: It's {row['name']}'s birthday today!",
            )
    except Exception as e:
        logging.error("Birthday check failed: %s", e)


# --- Entry point ---

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not set in the environment.")

    init_database()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_start))
    application.add_handler(CommandHandler("add", cmd_add))
    application.add_handler(CommandHandler("list", cmd_list))
    application.add_handler(CommandHandler("remove", cmd_remove))

    # Schedule daily birthday check at 09:00 local time
    application.job_queue.run_daily(
        check_birthdays,
        time=datetime.now(tz=TZ).replace(hour=9, minute=0, second=0, microsecond=0).timetz(),
    )

    logging.info("Bot started in polling mode (timezone: %s)", TZ)
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()