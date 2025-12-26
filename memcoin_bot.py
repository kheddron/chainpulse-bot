import os
import time
import sqlite3
import requests
import schedule
from datetime import datetime, timedelta

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler

# =====================
# ENVIRONMENT VARIABLES
# =====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # used only for test messages
CMC_API_KEY = os.getenv("CMC_API_KEY")

if not TELEGRAM_BOT_TOKEN or not CMC_API_KEY:
    raise Exception("Missing environment variables")

# =====================
# TELEGRAM SETUP
# =====================
bot = Bot(token=TELEGRAM_BOT_TOKEN)
updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
dp = updater.dispatcher

# =====================
# DATABASE SETUP
# =====================
conn = sqlite3.connect("chainpulse.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS processed_coins (
    coin_id INTEGER PRIMARY KEY
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    chat_id TEXT PRIMARY KEY,
    max_age_hours INTEGER DEFAULT 3,
    require_telegram INTEGER DEFAULT 0,
    require_twitter INTEGER DEFAULT 0
)
""")

conn.commit()

# =====================
# HELPER FUNCTIONS
# =====================
def ensure_user(chat_id):
    cursor.execute(
        "INSERT OR IGNORE INTO users (chat_id) VALUES (?)",
        (chat_id,)
    )
    conn.commit()

def coin_already_processed(coin_id):
    cursor.execute(
        "SELECT 1 FROM processed_coins WHERE coin_id = ?",
        (coin_id,)
    )
    return cursor.fetchone() is not None

def mark_coin_processed(coin_id):
    cursor.execute(
        "INSERT OR IGNORE INTO processed_coins (coin_id) VALUES (?)",
        (coin_id,)
    )
    conn.commit()

def get_user_filters(chat_id):
    cursor.execute("""
        SELECT max_age_hours, require_telegram, require_twitter
        FROM users WHERE chat_id = ?
    """, (chat_id,))
    row = cursor.fetchone()

    if not row:
        return None

    return {
        "max_age_hours": row[0],
        "require_telegram": row[1],
        "require_twitter": row[2],
    }

# =====================
# TELEGRAM COMMANDS
# =====================
def start(update, context):
    chat_id = str(update.effective_chat.id)
    ensure_user(chat_id)

    update.message.reply_text(
        "👋 Welcome to *ChainPulse*\n\n"
        "I monitor newly listed crypto tokens and alert you early.\n\n"
        "Commands:\n"
        "/filters – customize alerts\n"
        "/status – bot status\n"
        "/help – info",
        parse_mode="Markdown"
    )

def help_command(update, context):
    update.message.reply_text(
        "ℹ️ *ChainPulse Help*\n\n"
        "• Automatic memecoin alerts\n"
        "• User-controlled filters\n"
        "• 24/7 monitoring\n\n"
        "Use /filters to customize alerts.",
        parse_mode="Markdown"
    )

def status(update, context):
    update.message.reply_text(
        "✅ ChainPulse is running\n"
        "🔍 Scanning new listings\n"
        "☁️ Deployed 24/7"
    )

def filters(update, context):
    chat_id = str(update.effective_chat.id)
    ensure_user(chat_id)
    args = context.args

    if len(args) == 0:
        update.message.reply_text(
            "⚙️ *Filters*\n\n"
            "/filters age <hours>\n"
            "/filters telegram on|off\n"
            "/filters twitter on|off\n\n"
            "Example:\n"
            "/filters age 1",
            parse_mode="Markdown"
        )
        return

    option = args[0]

    try:
        if option == "age" and len(args) == 2:
            hours = int(args[1])
            cursor.execute(
                "UPDATE users SET max_age_hours = ? WHERE chat_id = ?",
                (hours, chat_id)
            )

        elif option == "telegram" and len(args) == 2:
            value = 1 if args[1] == "on" else 0
            cursor.execute(
                "UPDATE users SET require_telegram = ? WHERE chat_id = ?",
                (value, chat_id)
            )

        elif option == "twitter" and len(args) == 2:
            value = 1 if args[1] == "on" else 0
            cursor.execute(
                "UPDATE users SET require_twitter = ? WHERE chat_id = ?",
                (value, chat_id)
            )

        else:
            update.message.reply_text("❌ Invalid filter command")
            return

        conn.commit()
        update.message.reply_text("✅ Filter updated")

    except Exception as e:
        update.message.reply_text("❌ Error updating filter")

# =====================
# REGISTER COMMANDS
# =====================
dp.add_handler(CommandHandler("start", start))
dp.add_handler(CommandHandler("help", help_command))
dp.add_handler(CommandHandler("filters", filters))
dp.add_handler(CommandHandler("status", status))

# =====================
# COIN SCANNER
# =====================
def get_new_coins():
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
    params = {"limit": 200, "sort": "date_added", "sort_dir": "desc"}

    try:
        data = requests.get(url, headers=headers, params=params).json()
        return data.get("data", [])
    except:
        return []

def extract_socials(coin):
    urls = coin.get("urls", {})
    return {
        "telegram": urls.get("telegram", [None])[0] if urls.get("telegram") else None,
        "twitter": urls.get("twitter", [None])[0] if urls.get("twitter") else None
    }

def send_alert(chat_id, coin, socials):
    buttons = []
    if socials["telegram"]:
        buttons.append([InlineKeyboardButton("Telegram", url=socials["telegram"])])
    if socials["twitter"]:
        buttons.append([InlineKeyboardButton("X", url=socials["twitter"])])

    markup = InlineKeyboardMarkup(buttons) if buttons else None

    text = (
        f"🚀 *New Token Detected*\n\n"
        f"Name: {coin['name']}\n"
        f"Symbol: {coin['symbol']}"
    )

    bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=markup,
        parse_mode="Markdown"
    )

def process_new_coins():
    coins = get_new_coins()
    now = datetime.utcnow()

    cursor.execute("SELECT chat_id FROM users")
    users = cursor.fetchall()

    for coin in coins:
        if coin_already_processed(coin["id"]):
            continue

        added = datetime.fromisoformat(coin["date_added"].replace("Z", ""))
        socials = extract_socials(coin)

        for (chat_id,) in users:
            filters = get_user_filters(chat_id)

            if now - added > timedelta(hours=filters["max_age_hours"]):
                continue
            if filters["require_telegram"] and not socials["telegram"]:
                continue
            if filters["require_twitter"] and not socials["twitter"]:
                continue

            send_alert(chat_id, coin, socials)

        mark_coin_processed(coin["id"])

# =====================
# START EVERYTHING
# =====================
schedule.every(10).minutes.do(process_new_coins)

updater.start_polling()
print("🤖 ChainPulse running 24/7")

while True:
    schedule.run_pending()
    time.sleep(1)