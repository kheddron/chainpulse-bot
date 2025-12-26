import os
import requests
from datetime import datetime, timedelta
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
import schedule
import time
import sqlite3

# --- CONFIG ---
# Read keys from environment variables (no hardcoding)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CMC_API_KEY = os.getenv("CMC_API_KEY")
CHECK_INTERVAL_MINUTES = 10  # check every 10 minutes

# Check if keys are loaded
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not CMC_API_KEY:
    raise Exception("Missing environment variables. Please set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, and CMC_API_KEY")

bot = Bot(token=TELEGRAM_BOT_TOKEN)

# --- DATABASE SETUP ---
conn = sqlite3.connect('processed_coins.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS coins (
        id INTEGER PRIMARY KEY
    )
''')
conn.commit()

# --- FUNCTIONS ---

def coin_already_processed(coin_id):
    cursor.execute("SELECT id FROM coins WHERE id = ?", (coin_id,))
    return cursor.fetchone() is not None

def mark_coin_processed(coin_id):
    cursor.execute("INSERT INTO coins (id) VALUES (?)", (coin_id,))
    conn.commit()

def get_new_memecoins(hours=3):
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
    params = {"start": 1, "limit": 500, "sort": "date_added", "sort_dir": "desc"}

    try:
        response = requests.get(url, headers=headers, params=params).json()
        coins = response.get('data', [])
    except Exception as e:
        print(f"Error fetching coins: {e}")
        return []

    recent_coins = []
    now = datetime.utcnow()

    for coin in coins:
        added_time_str = coin.get('date_added')
        if not added_time_str:
            continue
        added_time = datetime.fromisoformat(added_time_str.replace('Z', ''))
        if now - added_time <= timedelta(hours=hours):
            recent_coins.append(coin)

    return recent_coins

def analyze_socials(coin):
    urls = coin.get('urls', {})
    socials = {}
    socials['telegram'] = urls.get('telegram', [None])[0] if urls.get('telegram') else None
    socials['twitter'] = urls.get('twitter', [None])[0] if urls.get('twitter') else None
    return socials

def send_telegram_message(coin, socials):
    name = coin.get('name')
    symbol = coin.get('symbol')

    telegram_link = socials.get('telegram')
    twitter_link = socials.get('twitter')

    keyboard = []
    if telegram_link:
        keyboard.append([InlineKeyboardButton("Telegram", url=telegram_link)])
    if twitter_link:
        keyboard.append([InlineKeyboardButton("X", url=twitter_link)])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    message = f"🚀 New Memecoin Detected!\n\nName: {name}\nSymbol: {symbol}"

    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, reply_markup=reply_markup)

def process_new_coins():
    new_coins = get_new_memecoins()

    for coin in new_coins:
        coin_id = coin['id']
        if coin_already_processed(coin_id):
            continue

        socials = analyze_socials(coin)
        send_telegram_message(coin, socials)
        mark_coin_processed(coin_id)

# --- SCHEDULER ---
schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(process_new_coins)

print("🤖 Memecoin bot is running 24/7...")
while True:
    schedule.run_pending()
    time.sleep(1)