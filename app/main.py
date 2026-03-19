import os
import time
import logging
import requests
import mysql.connector
from flask import Flask, request
from datetime import datetime
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

# Load variables from .env file
load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# --- Configuration from Environment ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}/"
TZ = os.getenv("TZ", "Europe/Amsterdam")

if not TOKEN:
    logging.warning("TELEGRAM_TOKEN is not set. Telegram sendMessage calls will fail.")

def get_db_connection(retries=1, delay_seconds=1):
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
            logging.warning("DB connection failed (%s/%s): %s", attempt, retries, error)
            if attempt < retries:
                time.sleep(delay_seconds)
    raise last_error


def init_database():
    """Ensures required table exists when the container starts."""
    conn = get_db_connection(retries=20, delay_seconds=2)
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
        logging.info("Database schema check complete.")
    finally:
        conn.close()

# --- Bot Functions ---
def send_message(chat_id, text):
    url = f"{TELEGRAM_API}sendMessage"
    try:
        requests.post(url, json={'chat_id': chat_id, 'text': text}, timeout=10)
    except Exception as e:
        logging.error("Failed to send Telegram message: %s", e)

def check_birthdays():
    """Scheduled task to run every morning."""
    logging.info("Running birthday check at %s", datetime.now())
    today = datetime.now().strftime('%m-%d')
    
    try:
        conn = get_db_connection(retries=3, delay_seconds=2)
        cursor = conn.cursor(dictionary=True)
        
        # Query for birthdays matching today (ignoring the year)
        query = "SELECT user_id, name FROM birthdays WHERE DATE_FORMAT(birthday, '%%m-%%d') = %s"
        cursor.execute(query, (today,))
        results = cursor.fetchall()
        
        for row in results:
            send_message(row['user_id'], f"🎂 Reminder: It's {row['name']}'s birthday today!")
        
        cursor.close()
        conn.close()
    except Exception as e:
        logging.error("Database error during scheduled task: %s", e)

# --- Flask Webhook ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(silent=True) or {}
    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        # Command: /add Name YYYY-MM-DD
        if text.startswith("/add"):
            try:
                parts = text.split(" ")
                if len(parts) < 3:
                    raise ValueError("Missing parameters")
                name, bday = parts[1], parts[2]
                datetime.strptime(bday, "%Y-%m-%d")
                
                conn = get_db_connection(retries=3, delay_seconds=2)
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO birthdays (user_id, name, birthday) VALUES (%s, %s, %s)", 
                    (str(chat_id), name, bday)
                )
                conn.commit()
                cursor.close()
                conn.close()
                
                send_message(chat_id, f"✅ Saved {name}'s birthday!")
            except Exception as e:
                logging.warning("/add failed: %s", e)
                send_message(chat_id, "❌ Error. Use: /add Name YYYY-MM-DD")

    return "OK", 200

def start_scheduler():
    # Set timezone to align reminder time with Synology/NAS region.
    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.add_job(check_birthdays, 'cron', hour=9, minute=0)
    scheduler.start()
    logging.info("Scheduler started with timezone: %s", TZ)


def bootstrap():
    init_database()
    if os.getenv("ENABLE_SCHEDULER", "true").lower() == "true":
        start_scheduler()

if __name__ == '__main__':
    bootstrap()
    # On Synology/Docker, host must be 0.0.0.0
    app.run(host='0.0.0.0', port=8443)