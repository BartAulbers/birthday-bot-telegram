import os
import time
import logging
import mysql.connector
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TOKEN = os.getenv("TELEGRAM_TOKEN")
TZ = ZoneInfo(os.getenv("TZ", "Europe/Amsterdam"))


def parse_birthday(text: str) -> tuple[str, str]:
    """Parse DD-MM-YYYY or DD-MM. Returns (db_value YYYY-MM-DD, display_str).
    Year 1900 is used as a sentinel when no year is provided."""
    text = text.strip()
    try:
        parsed = datetime.strptime(text, "%d-%m-%Y")
        return parsed.strftime("%Y-%m-%d"), text
    except ValueError:
        pass
    parsed = datetime.strptime(text, "%d-%m")  # defaults to year 1900
    return parsed.strftime("%Y-%m-%d"), text


def format_birthday(date_obj) -> str:
    """Display a birthday date. Omits year when stored as 1900 (unknown)."""
    if date_obj.year == 1900:
        return date_obj.strftime("%d-%m")
    return date_obj.strftime("%d-%m-%Y")


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
    """Ensures all required tables exist on startup, retrying until the DB is ready."""
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
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id VARCHAR(100) PRIMARY KEY,
                reminder_days INT NOT NULL DEFAULT 0,
                notification_time VARCHAR(5) NOT NULL DEFAULT '09:00'
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """
        )
        # Migrate existing deployments that may not have the notification_time column yet
        cursor.execute(
            "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS "
            "notification_time VARCHAR(5) NOT NULL DEFAULT '09:00';"
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
        "/add Name DD-MM-YYYY or DD-MM — save a birthday\n"
        "/bulkadd — save multiple birthdays at once\n"
        "/list — show all saved birthdays\n"
        "/remove Name — delete a birthday\n"
        "/setreminder <days> — get reminded N days before a birthday\n"
        "/settime HH:MM — set the time to receive daily reminders\n"
        "/reminder — show your current reminder settings"
    )


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    try:
        if len(context.args) < 2:
            raise ValueError("Missing arguments")
        name = " ".join(context.args[:-1])
        bday = context.args[-1]
        bday_db, bday_display = parse_birthday(bday)

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO birthdays (user_id, name, birthday) VALUES (%s, %s, %s)",
            (chat_id, name, bday_db),
        )
        conn.commit()
        cursor.close()
        conn.close()

        await update.message.reply_text(f"✅ Saved {name}'s birthday ({bday_display})!")
    except Exception as e:
        logging.warning("/add failed: %s", e)
        await update.message.reply_text("❌ Error. Use: /add Name DD-MM-YYYY or /add Name DD-MM")


async def cmd_bulkadd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accepts multiple birthdays, one per line after the command:
        /bulkadd
        Alice 1990-03-15
        Bob 1985-07-22
    """
    chat_id = str(update.effective_chat.id)
    lines = [l.strip() for l in update.message.text.split("\n")[1:] if l.strip()]

    if not lines:
        await update.message.reply_text(
            "Send one birthday per line after the command:\n"
                "/bulkadd\nAlice 15-03-1990\nBob 22-07"
        )
        return

    added, errors = [], []
    conn = get_db_connection()
    cursor = conn.cursor()

    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            errors.append(f"❌ '{line}' — use: Name DD-MM-YYYY or Name DD-MM")
            continue
        name = " ".join(parts[:-1])
        bday = parts[-1]
        try:
            bday_db, bday_display = parse_birthday(bday)
            cursor.execute(
                "INSERT INTO birthdays (user_id, name, birthday) VALUES (%s, %s, %s)",
                (chat_id, name, bday_db),
            )
            added.append(f"✅ {name} ({bday_display})")
        except ValueError:
            errors.append(f"❌ '{line}' — invalid date, use DD-MM-YYYY or DD-MM")

    conn.commit()
    cursor.close()
    conn.close()

    summary = "\n".join(added + errors)
    await update.message.reply_text(summary or "Nothing to add.")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT name, birthday FROM birthdays WHERE user_id = %s",
            (chat_id,),
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not rows:
            await update.message.reply_text("No birthdays saved yet. Use /add Name DD-MM-YYYY or /add Name DD-MM")
            return

        today = datetime.now(tz=TZ)
        today_mmdd = today.strftime("%m-%d")

        # Sort upcoming birthdays first (from today), then wrap to start of year
        rows.sort(key=lambda r: (
            r["birthday"].strftime("%m-%d") < today_mmdd,
            r["birthday"].strftime("%m-%d"),
        ))

        # Build list with a "── Vandaag ──" marker at today's position
        lines = []
        marker_placed = False
        for r in rows:
            mmdd = r["birthday"].strftime("%m-%d")
            if not marker_placed and mmdd >= today_mmdd:
                lines.append(f"── Vandaag ({today.strftime('%d-%m')}) ──")
                marker_placed = True
            lines.append(f"🎂 {r['name']}: {format_birthday(r['birthday'])}")
        if not marker_placed:
            lines.append(f"── Vandaag ({today.strftime('%d-%m')}) ──")

        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        logging.error("/list failed: %s", e)
        await update.message.reply_text("❌ Could not retrieve birthdays.")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    try:
        if not context.args:
            raise ValueError("Missing name")
        name = " ".join(context.args)

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


async def cmd_setreminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set how many days in advance to receive a birthday reminder (0 = disabled)."""
    chat_id = str(update.effective_chat.id)
    try:
        if not context.args:
            raise ValueError("Missing argument")
        days = int(context.args[0])
        if days < 0:
            raise ValueError("Days must be 0 or positive")

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO user_settings (user_id, reminder_days) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE reminder_days = %s",
            (chat_id, days, days),
        )
        conn.commit()
        cursor.close()
        conn.close()

        if days == 0:
            await update.message.reply_text("⏰ Advance reminders disabled.")
        else:
            await update.message.reply_text(
                f"⏰ You'll be reminded {days} day{'s' if days != 1 else ''} before each birthday."
            )
    except Exception as e:
        logging.warning("/setreminder failed: %s", e)
        await update.message.reply_text("❌ Error. Use: /setreminder <days>  (e.g. /setreminder 7)")


async def cmd_settime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the time of day to receive birthday reminders."""
    chat_id = str(update.effective_chat.id)
    try:
        if not context.args:
            raise ValueError("Missing time")
        time_str = context.args[0]
        datetime.strptime(time_str, "%H:%M")  # validate format

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO user_settings (user_id, notification_time) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE notification_time = %s",
            (chat_id, time_str, time_str),
        )
        conn.commit()
        cursor.close()
        conn.close()

        await update.message.reply_text(f"🕘 Reminder time set to {time_str}.")
    except Exception as e:
        logging.warning("/settime failed: %s", e)
        await update.message.reply_text("❌ Error. Use: /settime HH:MM  (e.g. /settime 08:30)")


async def cmd_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current reminder settings."""
    chat_id = str(update.effective_chat.id)
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT reminder_days, notification_time FROM user_settings WHERE user_id = %s",
            (chat_id,),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        days = row["reminder_days"] if row else 0
        notif_time = row["notification_time"] if row else "09:00"

        advance = (
            f"advance reminder: {days} day{'s' if days != 1 else ''} before"
            if days > 0 else "no advance reminder"
        )
        await update.message.reply_text(
            f"⏰ Reminder time: {notif_time}\n"
            f"📅 {advance}\n\n"
            f"Change with /settime HH:MM or /setreminder <days>"
        )
    except Exception as e:
        logging.error("/reminder failed: %s", e)
        await update.message.reply_text("❌ Could not retrieve reminder settings.")


# --- Scheduled job ---

async def check_birthdays(context: ContextTypes.DEFAULT_TYPE):
    """Runs every minute. Sends reminders to users whose configured notification time matches now."""
    now = datetime.now(tz=TZ)
    current_time = now.strftime("%H:%M")
    today_mmdd = now.strftime("%m-%d")

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Get all users with at least one birthday, with their settings (defaults: 09:00, 0 days)
        cursor.execute(
            "SELECT DISTINCT b.user_id, "
            "COALESCE(s.notification_time, '09:00') AS notification_time, "
            "COALESCE(s.reminder_days, 0) AS reminder_days "
            "FROM birthdays b LEFT JOIN user_settings s ON b.user_id = s.user_id"
        )
        users = [u for u in cursor.fetchall() if u["notification_time"] == current_time]

        for user in users:
            user_id = user["user_id"]
            logging.info("Running birthday check for user %s at %s", user_id, current_time)

            # On-the-day reminders
            cursor.execute(
                "SELECT name FROM birthdays WHERE user_id = %s "
                "AND DATE_FORMAT(birthday, '%%m-%%d') = %s",
                (user_id, today_mmdd),
            )
            for row in cursor.fetchall():
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🎂 Het is vandaag de verjaardag van {row['name']}!",
                )

            # Advance reminders
            if user["reminder_days"] > 0:
                advance_date = now + timedelta(days=user["reminder_days"])
                cursor.execute(
                    "SELECT name FROM birthdays WHERE user_id = %s "
                    "AND DATE_FORMAT(birthday, '%%m-%%d') = %s",
                    (user_id, advance_date.strftime("%m-%d")),
                )
                days = user["reminder_days"]
                for row in cursor.fetchall():
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"⏰ Reminder: de verjaardag van {row['name']} is over "
                            f"{days} dag{'en' if days != 1 else ''} ({advance_date.strftime('%d-%m')})!"
                        ),
                    )

        cursor.close()
        conn.close()
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
    application.add_handler(CommandHandler("bulkadd", cmd_bulkadd))
    application.add_handler(CommandHandler("list", cmd_list))
    application.add_handler(CommandHandler("remove", cmd_remove))
    application.add_handler(CommandHandler("setreminder", cmd_setreminder))
    application.add_handler(CommandHandler("settime", cmd_settime))
    application.add_handler(CommandHandler("reminder", cmd_reminder))

    # Check every minute; only notifies users whose configured time matches current HH:MM
    application.job_queue.run_repeating(check_birthdays, interval=60, first=10)

    logging.info("Bot started in polling mode (timezone: %s)", TZ)
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()