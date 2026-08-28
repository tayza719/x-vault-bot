import os
import logging
import sqlite3
import datetime
import requests
import telebot
from telebot import types
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes

# Environment Variables (Heroku Config Vars မှ ဖတ်ယူမည်)
BOT_TOKEN = os.getenv("BOT_TOKEN", "8683965691:AAEthMpBt_RJNY1NPNDPtH-hSnTcpWFU0L8")
ADMIN_ID = int(os.getenv("ADMIN_ID", 7613605178))
MNEMONIC = os.getenv("MASTER_MNEMONIC", "your twelve words seed phrase goes here")

DB_FILE = "store.db"
PRICE_USD = 1.0  # အကောင့် ၁ ကောင့်လျှင် ၁ ဒေါ်လာ

logging.basicConfig(level=logging.INFO)
bot = telebot.TeleBot(BOT_TOKEN)

# Database စတင်ဖွဲ့စည်းခြင်း
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT DEFAULT 'x',
            account_info TEXT UNIQUE,
            status TEXT DEFAULT 'available',
            buyer_id INTEGER,
            sold_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            category TEXT,
            qty INTEGER,
            coin TEXT,
            address TEXT,
            amount_coin REAL,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_stock_count(category):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM accounts WHERE category = ? AND status = 'available'", (category,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def add_accounts_to_db(category, acc_list):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    added = 0
    for acc in acc_list:
        try:
            cursor.execute("INSERT INTO accounts (category, account_info, status) VALUES (?, ?, 'available')", (category, acc))
            added += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return added

# --- HD WALLET SUB-ADDRESS GENERATOR ---
def generate_hd_address(coin: str, index: int) -> str:
    seed_bytes = Bip39SeedGenerator(MNEMONIC).Generate()
    
    if coin == "sol":
        bip_mst = Bip44.FromSeed(seed_bytes, Bip44Coins.SOLANA)
        return bip_mst.Purpose().Coin().Account(index).PublicKey().ToAddress()
    elif coin == "pol":
        bip_mst = Bip44.FromSeed(seed_bytes, Bip44Coins.POLYGON)
        return bip_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
    elif coin == "bnb":
        bip_mst = Bip44.FromSeed(seed_bytes, Bip44Coins.BINANCE_SMART_CHAIN)
        return bip_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
    elif coin == "trx":
        bip_mst = Bip44.FromSeed(seed_bytes, Bip44Coins.TRON)
        return bip_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
    return None

# --- COINGECKO CRYPTO PRICE CALCULATOR ---
def get_crypto_amount(usd_amount: float, coin: str) -> float:
    coin_ids = {
        "sol": "solana",
        "pol": "polygon-ecosystem-token",
        "bnb": "binancecoin",
        "trx": "tron"
    }
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_ids[coin]}&vs_currencies=usd"
        res = requests.get(url, timeout=10).json()
        price_in_usd = res[coin_ids[coin]]["usd"]
        return round(usd_amount / price_in_usd, 6)
    except Exception as e:
        logging.error(f"Price Error: {e}")
        return None

# --- BLOCKCHAIN EXPLORER BALANCE CHECKER ---
def check_blockchain_balance(address: str, coin: str) -> float:
    try:
        if coin == "sol":
            url = "https://api.mainnet-beta.solana.com"
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address]}
            res = requests.post(url, json=payload, timeout=10).json()
            return res['result']['value'] / 1e9
        elif coin == "pol":
            url = f"https://api.polygonscan.com/api?module=account&action=balance&address={address}"
            res = requests.get(url, timeout=10).json()
            return int(res['result']) / 1e18
        elif coin == "trx":
            url = f"https://api.trongrid.io/v1/accounts/{address}"
            res = requests.get(url, timeout=10).json()
            if res.get('data'):
                return res['data'][0]['balance'] / 1e6
            return 0.0
    except Exception as e:
        logging.error(f"Blockchain Check Error: {e}")
    return 0.0

# --- BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🇲🇲 မြန်မာစာ", callback_data="lang_mm"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
    )
    bot.send_message(message.chat.id, "🌐 **ကျေးဇူးပြု၍ ဘာသာစကား ရွေးချယ်ပါ**", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    data = call.data

    if data.startswith("lang_"):
        x_stock = get_stock_count('x')
        outlook_stock = get_stock_count('outlook')
        
        welcome_text = f"🛒 **Vault Store**\n\n🔹 X Stock: `{x_stock}`\n🔹 Outlook Stock: `{outlook_stock}`\n\nဝယ်ယူလိုသော အမျိုးအစားကို ရွေးချယ်ပါ -"
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("𝕏 X Accounts", callback_data="cat_x"),
            types.InlineKeyboardButton("📧 Outlook Accounts", callback_data="cat_outlook")
        )
        bot.edit_message_text(welcome_text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data in ["cat_x", "cat_outlook"]:
        category = data.split("_")[1]
        stock_qty = get_stock_count(category)
        if stock_qty == 0:
            bot.answer_callback_query(call.id, "Stock ကုန်နေပါသည်", show_alert=True)
            return
        
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("1 acc", callback_data=f"qty_{category}_1"),
            types.InlineKeyboardButton("2 accs", callback_data=f"qty_{category}_2")
        )
        markup.add(
            types.InlineKeyboardButton("3 accs", callback_data=f"qty_{category}_3"),
            types.InlineKeyboardButton("5 accs", callback_data=f"qty_{category}_5")
        )
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="lang_mm"))
        bot.edit_message_text(f"🔢 ဝယ်ယူလိုသော **{category.upper()}** အကောင့် အရေအတွက်ကို ရွေးချယ်ပါ (Stock: {stock_qty}):", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("qty_"):
        parts = data.split("_")
        category = parts[1]
        qty = int(parts[2])
        
        stock_qty = get_stock_count(category)
        if qty > stock_qty:
            bot.answer_callback_query(call.id, f"Stock မလောက်ပါ။ (လက်ကျန်: {stock_qty})", show_alert=True)
            return

        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("Solana (SOL)", callback_data=f"pay_{category}_{qty}_sol"),
            types.InlineKeyboardButton("Polygon (POL)", callback_data=f"pay_{category}_{qty}_pol")
        )
        markup.add(
            types.InlineKeyboardButton("BNB Chain (BNB)", callback_data=f"pay_{category}_{qty}_bnb"),
            types.InlineKeyboardButton("TRON (TRX)", callback_data=f"pay_{category}_{qty}_trx")
        )
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"cat_{category}"))
        bot.edit_message_text("💳 **ငွေပေးချေလိုသော Native Coin ကို ရွေးချယ်ပါ -**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("pay_"):
        _, category, qty, coin = data.split("_")
        qty = int(qty)
        usd_total = round(qty * PRICE_USD, 2)
        
        coin_amount = get_crypto_amount(usd_total, coin)
        if not coin_amount:
            bot.answer_callback_query(call.id, "Crypto ဈေးနှုန်း ဖတ်ယူ၍ မရပါ။ ခဏစောင့်ပေးပါ။", show_alert=True)
            return

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO orders (user_id, category, qty, coin, created_at) VALUES (?, ?, ?, ?, ?)",
                       (call.from_user.id, category, qty, coin, datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()

        address = generate_hd_address(coin, order_id)
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE orders SET address = ?, amount_coin = ? WHERE order_id = ?", (address, coin_amount, order_id))
        conn.commit()
        conn.close()

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔄 Check Payment (ငွေလွှဲစစ်မည်)", callback_data=f"check_{order_id}"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"qty_{category}_{qty}"))

        msg = f"💳 **Direct Native Crypto Payment**\n\n" \
              f"**Order ID:** `{order_id}`\n" \
              f"**Coin:** `{coin.upper()}`\n" \
              f"**လွှဲရမည့် ပမာဏ:** `{coin_amount} {coin.upper()}`\n" \
              f"**ငွေလက်ခံမည့် Address:**\n`{address}`\n\n" \
              f"⚠️ *အထက်ပါ Address သို့ တိကျစွာ လွှဲပေးပါ။ ငွေလွှဲပြီးပါက 'Check Payment' ခလုတ်ကို နှိပ်ပါ။*"
        
        bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("check_"):
        order_id = int(data.split("_")[1])
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, category, qty, coin, address, amount_coin, status FROM orders WHERE order_id = ?", (order_id,))
        order = cursor.fetchone()
        
        if not order:
            bot.answer_callback_query(call.id, "Order မရှိတော့ပါ။", show_alert=True)
            conn.close()
            return
            
        user_id, category, qty, coin, address, amount_coin, status = order
        
        if status == 'completed':
            bot.answer_callback_query(call.id, "ဒီ Order အတွက် အကောင့် ထုတ်ပေးပြီးပါပြီ။", show_alert=True)
            conn.close()
            return

        current_balance = check_blockchain_balance(address, coin)
        
        if current_balance >= (amount_coin * 0.98):  # 2% Tolerance
            cursor.execute("SELECT id, account_info FROM accounts WHERE category = ? AND status = 'available' LIMIT ?", (category, qty))
            rows = cursor.fetchall()
            
            if len(rows) >= qty:
                account_ids = [r[0] for r in rows]
                accounts_info = [r[1] for r in rows]
                
                placeholders = ','.join(['?'] * len(account_ids))
                cursor.execute(f"UPDATE accounts SET status = 'sold', buyer_id = ?, sold_at = ? WHERE id IN ({placeholders})",
                               [user_id, datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")] + account_ids)
                cursor.execute("UPDATE orders SET status = 'completed' WHERE order_id = ?", (order_id,))
                conn.commit()
                
                acc_text = "\n".join(accounts_info)
                bot.send_message(user_id, f"✅ **ငွေပေးချေမှု အောင်မြင်ပါသည်!**\n\nဝယ်ယူလိုက်သော အကောင့်များ:\n\n`{acc_text}`", parse_mode="Markdown")
                bot.answer_callback_query(call.id, "ငွေပေးချေမှု အောင်မြင်ပါသည်။", show_alert=False)
            else:
                bot.answer_callback_query(call.id, "Stock မလုံလောက်ပါ။ Admin ကို ဆက်သွယ်ပါ။", show_alert=True)
        else:
            bot.answer_callback_query(call.id, f"ငွေမရောက်သေးပါ။ (ရောက်ရှိမှု: {current_balance} / {amount_coin} {coin.upper()})", show_alert=True)
        
        conn.close()

@bot.message_handler(commands=['addacc'])
def add_acc(message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/addacc", "").strip()
    parts = text.split(" ", 1)
    if len(parts) < 2 or parts[0].lower() not in ['x', 'outlook']:
        bot.reply_to(message, "⚠️ ပုံစံအမှန်: `/addacc x user|pass|link` သို့မဟုတ် `/addacc outlook email|pass`")
        return
    
    category = parts[0].lower()
    acc_lines = [line.strip() for line in parts[1].split("\n") if line.strip()]
    added = add_accounts_to_db(category, acc_lines)
    bot.reply_to(message, f"✅ **{category.upper()}** Stock အသစ် `{added}` ကောင့် ထည့်ပြီးပါပြီ။")

if __name__ == "__main__":
    print("Bot is running as worker...")
    bot.infinity_polling(skip_pending=True)
