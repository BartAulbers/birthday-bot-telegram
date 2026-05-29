# 🎂 Birthday Bot for Telegram

A self-hosted Telegram bot that stores birthdays in a MariaDB database and sends you a daily reminder at 09:00 when it's someone's birthday. Runs in Docker — designed for Synology NAS or any Docker host.

---

## Features

- `/add Name DD-MM-YYYY` — save a birthday
- `/bulkadd` — save multiple birthdays at once (one per line, `Name DD-MM-YYYY`)
- `/list` — view all saved birthdays (sorted by date)
- `/remove Name` — delete a birthday
- `/setreminder <days>` — get an advance reminder N days before each birthday
- `/reminder` — show your current advance reminder setting
- Daily reminder at 09:00 on the birthday itself (configurable timezone)

---

## Requirements

- Docker & Docker Compose
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/BartAulbers/birthday-bot-telegram.git
cd birthday-bot-telegram
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
TELEGRAM_TOKEN=your_telegram_bot_token_here

DB_ROOT_PASSWORD=strong_root_password
DB_NAME=birthday_db
DB_USER=bot_admin
DB_PASSWORD=strong_password

TZ=Europe/Amsterdam
```

### 3. Build and start

```bash
docker compose up -d --build
```

The bot will start polling Telegram automatically. The database is initialized on first run.

---

## Configuration

| Variable | Description | Default |
|---|---|---|
| `TELEGRAM_TOKEN` | Your bot token from BotFather | *(required)* |
| `DB_ROOT_PASSWORD` | MariaDB root password | `root_password_change_me` |
| `DB_NAME` | Database name | `birthday_db` |
| `DB_USER` | Database user | `bot_admin` |
| `DB_PASSWORD` | Database password | `change_me` |
| `TZ` | Timezone for daily reminders | `Europe/Amsterdam` |

---

## Architecture

```
docker-compose.yml
├── db      MariaDB 11.4 — stores birthdays
└── bot     Python 3.11 — polls Telegram, runs daily job
```

- **Polling mode** — no webhook, no HTTPS, no open ports needed
- **Bot waits for DB** — `depends_on: service_healthy` ensures MariaDB is ready before the bot starts
- **JobQueue** — built-in scheduler via `python-telegram-bot[job-queue]` fires reminders at 09:00 in your configured timezone

---

## Commands

| Command | Description |
|---|---|
| `/start` or `/help` | Show available commands |
| `/add Name DD-MM-YYYY` | Save a birthday |
| `/bulkadd` | Save multiple birthdays — send one `Name DD-MM-YYYY` per line after the command |
| `/list` | List all birthdays |
| `/remove Name` | Remove a birthday by name |
| `/setreminder <days>` | Get reminded N days before each birthday (e.g. `/setreminder 7`). Use `0` to disable. |
| `/reminder` | Show your current advance reminder setting |

---

## Data

MariaDB data is persisted in `./mysql_data/` (excluded from git).

---

## Updating

```bash
docker compose down
git pull
docker compose up -d --build
```
