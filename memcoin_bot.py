# ======================================================
# ChainPulse — Full Intelligence & Whale Bot (Solana)
# ======================================================

import os
import time
import sqlite3
import threading
import requests
import schedule
from datetime import datetime

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Updater, CommandHandler, CallbackQueryHandler, CallbackContext
)

# ======================================================
# ENV VARIABLES
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SOLSCAN_API_KEY = os.getenv("SOLSCAN_API_KEY")  # optional

DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/search?q=solana"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
SOLSCAN_WHALE_URL = "https://api.solscan.io/account/transactions"

# ======================================================
# DATABASE
# ======================================================

conn = sqlite3.connect("chainpulse.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS alerted_pairs (
    pair_address TEXT PRIMARY KEY
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS filters (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS prices (
    pair_address TEXT PRIMARY KEY,
    last_price REAL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS whale_wallets (
    address TEXT PRIMARY KEY
)
""")

conn.commit()

# ======================================================
# DEFAULT FILTERS
# ======================================================

DEFAULT_FILTERS = {
    "min_liquidity": 20000,
    "min_volume": 50000,
    "min_score": 55,
    "price_change_pct": 15,
    "auto_scan": 0,
    "scan_interval": 10
}

for k, v in DEFAULT_FILTERS.items():
    cursor.execute(
        "INSERT OR IGNORE INTO filters VALUES (?,?)",
        (k, str(v))
    )

conn.commit()

# ======================================================
# FILTER HELPERS
# ======================================================

def get_filter(key):
    cursor.execute("SELECT value FROM filters WHERE key=?", (key,))
    return cursor.fetchone()[0]

def set_filter(key, value):
    cursor.execute(
        "UPDATE filters SET value=? WHERE key=?",
        (str(value), key)
    )
    conn.commit()

# ======================================================
# SCORING
# ======================================================

def score_token(pair):
    score = 0
    liq = pair.get("liquidity", {}).get("usd", 0)
    vol = pair.get("volume", {}).get("h24", 0)

    if liq >= 50000: score += 30
    elif liq >= 20000: score += 20

    if vol >= 100000: score += 30
    elif vol >= 50000: score += 20

    socials = pair.get("info", {}).get("socials", [])
    if any(s["type"] == "telegram" for s in socials): score += 10
    if any(s["type"] == "twitter" for s in socials): score += 10

    return min(score, 100)

# ======================================================
# FILTER ENGINE
# ======================================================

def passes_filters(pair):
    liq = pair.get("liquidity", {}).get("usd", 0)
    vol = pair.get("volume", {}).get("h24", 0)
    score = score_token(pair)

    if liq < float(get_filter("min_liquidity")): return False
    if vol < float(get_filter("min_volume")): return False
    if score < float(get_filter("min_score")): return False

    return True

# ======================================================
# PRICE CHANGE ALERTS
# ======================================================

def price_change_alert(pair):
    pair_addr = pair["pairAddress"]
    price = float(pair["priceUsd"])

    cursor.execute(
        "SELECT last_price FROM prices WHERE pair_address=?",
        (pair_addr,)
    )
    row = cursor.fetchone()

    if not row:
        cursor.execute(
            "INSERT INTO prices VALUES (?,?)",
            (pair_addr, price)
        )
        conn.commit()
        return None

    last_price = row[0]
    pct = ((price - last_price) / last_price) * 100

    if abs(pct) >= float(get_filter("price_change_pct")):
        cursor.execute(
            "UPDATE prices SET last_price=? WHERE pair_address=?",
            (price, pair_addr)
        )
        conn.commit()
        return pct

    return None

# ======================================================
# SCANNER
# ======================================================

def scan(update: Update, context: CallbackContext):
    r = requests.get(DEXSCREENER_URL, timeout=15)
    pairs = r.json().get("pairs", [])
    sent = 0

    for pair in pairs:
        addr = pair.get("pairAddress")
        if not addr:
            continue

        cursor.execute(
            "SELECT 1 FROM alerted_pairs WHERE pair_address=?",
            (addr,)
        )
        if cursor.fetchone():
            continue

        if not passes_filters(pair):
            continue

        pct = price_change_alert(pair)
        score = score_token(pair)

        base = pair["baseToken"]
        msg = f"""
🚀 *New Solana Token*

🪙 {base['name']} ({base['symbol']})
💧 Liquidity: ${pair['liquidity']['usd']:,.0f}
📊 Volume 24h: ${pair['volume']['h24']:,.0f}
🧠 Score: {score}/100
"""

        if pct:
            msg += f"\n📈 *Price Change:* {pct:.2f}%"

        update.message.reply_text(msg, parse_mode="Markdown")

        cursor.execute(
            "INSERT INTO alerted_pairs VALUES (?)",
            (addr,)
        )
        conn.commit()
        sent += 1

    update.message.reply_text(f"✅ Scan complete — {sent} alerts.")

# ======================================================
# SOL PRICE
# ======================================================

def sol_price(update, context):
    r = requests.get(
        COINGECKO_URL,
        params={"ids": "solana", "vs_currencies": "usd"}
    )
    price = r.json()["solana"]["usd"]
    update.message.reply_text(f"💰 SOL Price: ${price:,.2f}")

# ======================================================
# WHALE TRACKING
# ======================================================

def add_whale(update, context):
    if not context.args:
        update.message.reply_text("Usage: /add_whale WALLET_ADDRESS")
        return

    addr = context.args[0]
    cursor.execute(
        "INSERT OR IGNORE INTO whale_wallets VALUES (?)",
        (addr,)
    )
    conn.commit()
    update.message.reply_text("🐋 Whale wallet added.")

def check_whales():
    if not SOLSCAN_API_KEY:
        return

    cursor.execute("SELECT address FROM whale_wallets")
    wallets = cursor.fetchall()

    headers = {"token": SOLSCAN_API_KEY}

    for (addr,) in wallets:
        r = requests.get(
            SOLSCAN_WHALE_URL,
            params={"account": addr, "limit": 1},
            headers=headers
        )
        if r.status_code == 200:
            tx = r.json()["data"][0]
            print("🐋 Whale activity:", addr, tx["txHash"])

# ======================================================
# AUTO ALERTS
# ======================================================

def auto_on(update, context):
    set_filter("auto_scan", 1)
    update.message.reply_text("✅ Auto alerts enabled")

def auto_off(update, context):
    set_filter("auto_scan", 0)
    update.message.reply_text("⛔ Auto alerts disabled")

def auto_loop():
    if int(get_filter("auto_scan")):
        dummy = type("obj", (), {})()
        dummy.message = type("obj", (), {"reply_text": print})
        scan(dummy, None)
        check_whales()

schedule.every(10).minutes.do(auto_loop)
threading.Thread(
    target=lambda: [schedule.run_pending() or time.sleep(1)],
    daemon=True
).start()

# ======================================================
# BOT SETUP
# ======================================================

updater = Updater(BOT_TOKEN, use_context=True)
dp = updater.dispatcher

dp.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("🤖 ChainPulse online")))
dp.add_handler(CommandHandler("scan", scan))
dp.add_handler(CommandHandler("sol", sol_price))
dp.add_handler(CommandHandler("auto_on", auto_on))
dp.add_handler(CommandHandler("auto_off", auto_off))
dp.add_handler(CommandHandler("add_whale", add_whale))

print("🚀 ChainPulse running")
updater.start_polling()
updater.idle()