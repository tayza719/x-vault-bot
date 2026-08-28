# main.py
import os
import logging
import sqlite3
import datetime
import requests
import telebot
from telebot import types
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "8683965691:AAEthMpBt_RJNY1NPNDPtH-hSnTcpWFU0L8")
ADMIN_ID = int(os.getenv("ADMIN_ID", 7613605178))
MNEMONIC = os.getenv("MASTER_MNEMONIC", "your twelve words seed phrase goes here")

DB_FILE = "store.db"

PRICES = {
    "x": 0.15,
    "outlook": 0.10
}

logging.basicConfig(level=logging.INFO)
bot = telebot.TeleBot(BOT_TOKEN)

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

# Admin ထံ အလိုအလျောက် Database Auto Backup ပို့ပေးမည့် Function
def auto_backup_to_admin(reason="Auto Backup"):
    try:
        with open(DB_FILE, 'rb') as f:
            bot.send_document(ADMIN_ID, f, caption=f"📦 **DB Backup System** ({reason})")
    except Exception as e:
        logging.error(f"Auto Backup Failed: {e}")

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
            # INSERT OR REPLACE ပြောင်းလဲထားသဖြင့် ရောင်းပြီးသား/စမ်းထားသည့် အကောင့်များ ပြန်ထည့်ပါက တန်းဝင်မည်ဖြစ်ပါသည်
            cursor.execute("INSERT OR REPLACE INTO accounts (category, account_info, status) VALUES (?, ?, 'available')", (category, acc))
            added += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    if added > 0:
        auto_backup_to_admin(f"Added {added} {category.upper()} Stock")
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

# --- BLOCKCHAIN RPC/EXPLORER BALANCE CHECKER ---
def check_blockchain_balance(address: str, coin: str) -> float:
    try:
        if coin == "sol":
            url = "https://api.mainnet-beta.solana.com"
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address]}
            res = requests.post(url, json=payload, timeout=10).json()
            return res['result']['value'] / 1e9

        elif coin == "pol":
            url = "https://polygon-bor-rpc.publicnode.com"
            payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [address, "latest"]}
            res = requests.post(url, json=payload, timeout=10).json()
            hex_bal = res.get('result', '0x0')
            return int(hex_bal, 16) / 1e18

        elif coin == "bnb":
            url = "https://bsc-rpc.publicnode.com"
            payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [address, "latest"]}
            res = requests.post(url, json=payload, timeout=10).json()
            hex_bal = res.get('result', '0x0')
            return int(hex_bal, 16) / 1e18

        elif coin == "trx":
            url = f"https://api.trongrid.io/v1/accounts/{address}"
            res = requests.get(url, timeout=10).json()
            if res.get('data'):
                return res['data'][0]['balance'] / 1e6
            return 0.0
    except Exception as e:
        logging.error(f"Blockchain Check Error ({coin}): {e}")
    return 0.0

# --- USER HANDLERS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🇲🇲 မြန်မာစာ", callback_data="lang_mm"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
    )
    bot.send_message(message.chat.id, "🌐 **Please select your language / ကျေးဇူးပြု၍ ဘာသာစကား ရွေးချယ်ပါ**", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    data = call.data

    if data.startswith("lang_"):
        lang = data.split("_")[1]
        x_stock = get_stock_count('x')
        outlook_stock = get_stock_count('outlook')
        
        if lang == "mm":
            welcome_text = f"🛒 **Alpha Vault Store မှ ကြိုဆိုပါတယ်**\n\n🔹 X Stock: `{x_stock}` (Price: ${PRICES['x']})\n🔹 Outlook Stock: `{outlook_stock}` (Price: ${PRICES['outlook']})\n\nဝယ်ယူလိုသော အမျိုးအစားကို ရွေးချယ်ပါ -"
        else:
            welcome_text = f"🛒 **Welcome to Alpha Vault Store**\n\n🔹 X Stock: `{x_stock}` (Price: ${PRICES['x']})\n🔹 Outlook Stock: `{outlook_stock}` (Price: ${PRICES['outlook']})\n\nPlease select category to buy -"

        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("𝕏 X Accounts", callback_data=f"cat_x_{lang}"),
            types.InlineKeyboardButton("📧 Outlook Accounts", callback_data=f"cat_outlook_{lang}")
        )
        bot.edit_message_text(welcome_text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("cat_"):
        parts = data.split("_")
        category = parts[1]
        lang = parts[2] if len(parts) > 2 else "mm"
        
        stock_qty = get_stock_count(category)
        unit_price = PRICES[category]
        
        if stock_qty < 2:
            alert_msg = "Stock မလုံလောက်ပါ။ (အနည်းဆုံး 2 ကောင့် လိုအပ်ပါသည်)" if lang == "mm" else "Out of stock! (Min 2 accs required)"
            bot.answer_callback_query(call.id, alert_msg, show_alert=True)
            return
        
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(f"2 accs (${round(2*unit_price, 2)})", callback_data=f"qty_{category}_2_{lang}"),
            types.InlineKeyboardButton(f"4 accs (${round(4*unit_price, 2)})", callback_data=f"qty_{category}_4_{lang}")
        )
        markup.add(
            types.InlineKeyboardButton(f"6 accs (${round(6*unit_price, 2)})", callback_data=f"qty_{category}_6_{lang}"),
            types.InlineKeyboardButton(f"8 accs (${round(8*unit_price, 2)})", callback_data=f"qty_{category}_8_{lang}")
        )
        markup.add(
            types.InlineKeyboardButton(f"10 accs (${round(10*unit_price, 2)})", callback_data=f"qty_{category}_10_{lang}"),
            types.InlineKeyboardButton(f"15 accs (${round(15*unit_price, 2)})", callback_data=f"qty_{category}_15_{lang}")
        )
        markup.add(
            types.InlineKeyboardButton(f"20 accs (${round(20*unit_price, 2)})", callback_data=f"qty_{category}_20_{lang}")
        )
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"lang_{lang}"))
        
        title = f"🔢 ဝယ်ယူလိုသော **{category.upper()}** အရေအတွက်ကို ရွေးချယ်ပါ -" if lang == "mm" else f"🔢 Select quantity for **{category.upper()}** -"
        bot.edit_message_text(
            f"{title}\n*(1 acc = ${unit_price} | Stock: {stock_qty})*",
            call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown"
        )

    elif data.startswith("qty_"):
        parts = data.split("_")
        category = parts[1]
        qty = int(parts[2])
        lang = parts[3] if len(parts) > 3 else "mm"
        
        stock_qty = get_stock_count(category)
        if qty > stock_qty:
            alert_msg = f"Stock မလောက်ပါ။ (လက်ကျန်: {stock_qty})" if lang == "mm" else f"Insufficient stock! (Available: {stock_qty})"
            bot.answer_callback_query(call.id, alert_msg, show_alert=True)
            return

        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("Solana (SOL)", callback_data=f"pay_{category}_{qty}_sol_{lang}"),
            types.InlineKeyboardButton("Polygon (POL)", callback_data=f"pay_{category}_{qty}_pol_{lang}")
        )
        markup.add(
            types.InlineKeyboardButton("BNB Chain (BNB)", callback_data=f"pay_{category}_{qty}_bnb_{lang}"),
            types.InlineKeyboardButton("TRON (TRX)", callback_data=f"pay_{category}_{qty}_trx_{lang}")
        )
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"cat_{category}_{lang}"))
        
        pay_title = "💳 **ငွေပေးချေလိုသော Native Coin ကို ရွေးချယ်ပါ -**" if lang == "mm" else "💳 **Select Native Coin for Payment -**"
        bot.edit_message_text(pay_title, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("pay_"):
        parts = data.split("_")
        category = parts[1]
        qty = int(parts[2])
        coin = parts[3]
        lang = parts[4] if len(parts) > 4 else "mm"
        
        usd_total = round(qty * PRICES[category], 2)
        coin_amount = get_crypto_amount(usd_total, coin)
        
        if not coin_amount:
            alert_msg = "Crypto ဈေးနှုန်း ဖတ်ယူ၍ မရပါ။ ခဏစောင့်ပေးပါ။" if lang == "mm" else "Failed to fetch crypto price. Please wait."
            bot.answer_callback_query(call.id, alert_msg, show_alert=True)
            return

        created_time = datetime.datetime.utcnow()
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO orders (user_id, category, qty, coin, created_at) VALUES (?, ?, ?, ?, ?)",
                       (call.from_user.id, category, qty, coin, created_time.strftime("%Y-%m-%d %H:%M:%S")))
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
        btn_text = "🔄 Check Payment (ငွေလွှဲစစ်မည်)" if lang == "mm" else "🔄 Check Payment"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"check_{order_id}_{lang}"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"qty_{category}_{qty}_{lang}"))

        if lang == "mm":
            msg = f"💳 **Direct Native Crypto Payment**\n\n" \
                  f"🆔 **Order ID:** `{order_id}`\n" \
                  f"🪙 **Coin:** `{coin.upper()}`\n" \
                  f"💲 **Total Value:** `${usd_total} USD`\n\n" \
                  f"⚠️ **EXACT AMOUNT TO SEND (တိကျစွာ လွှဲရမည့် ပမာဏ):**\n" \
                  f"👉 `{coin_amount}` **{coin.upper()}** 👈\n\n" \
                  f"📍 **DEPOSIT ADDRESS (ငွေလက်ခံမည့် Address):**\n`{address}`\n\n" \
                  f"⏳ **ငွေလွှဲရန် ကြာချိန်:** `15 မိနစ်`\n" \
                  f"🚨 *Network Fee မနှုတ်ဘဲ အထက်ပါ `{coin_amount}` {coin.upper()} တိကျစွာ ရောက်ရှိရန် လွှဲပေးပါ။ ငွေလွှဲပြီးပါက 'Check Payment' ခလုတ်ကို နှိပ်ပါ။*"
        else:
            msg = f"💳 **Direct Native Crypto Payment**\n\n" \
                  f"🆔 **Order ID:** `{order_id}`\n" \
                  f"🪙 **Coin:** `{coin.upper()}`\n" \
                  f"💲 **Total Value:** `${usd_total} USD`\n\n" \
                  f"⚠️ **EXACT AMOUNT TO SEND:**\n" \
                  f"👉 `{coin_amount}` **{coin.upper()}** 👈\n\n" \
                  f"📍 **DEPOSIT ADDRESS:**\n`{address}`\n\n" \
                  f"⏳ **Payment Time Limit:** `15 Minutes`\n" \
                  f"🚨 *Please ensure exact `{coin_amount}` {coin.upper()} reaches the address (excluding network fees). Click 'Check Payment' after sending.*"
        
        bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("check_"):
        parts = data.split("_")
        order_id = int(parts[1])
        lang = parts[2] if len(parts) > 2 else "mm"
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, category, qty, coin, address, amount_coin, status, created_at FROM orders WHERE order_id = ?", (order_id,))
        order = cursor.fetchone()
        
        if not order:
            alert_msg = "Order မရှိတော့ပါ။" if lang == "mm" else "Order not found."
            bot.answer_callback_query(call.id, alert_msg, show_alert=True)
            conn.close()
            return
            
        user_id, category, qty, coin, address, amount_coin, status, created_at_str = order
        
        if status == 'completed':
            alert_msg = "ဒီ Order အတွက် အကောင့် ထုတ်ပေးပြီးပါပြီ။" if lang == "mm" else "Account already delivered for this order."
            bot.answer_callback_query(call.id, alert_msg, show_alert=True)
            conn.close()
            return

        if status == 'expired':
            alert_msg = "အချိန် ၁၅ မိနစ် ကျော်သွားသဖြင့် Order ပယ်ဖျက်ပြီးပါပြီ။" if lang == "mm" else "Order expired after 15 minutes limit."
            bot.answer_callback_query(call.id, alert_msg, show_alert=True)
            conn.close()
            return

        created_time = datetime.datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
        time_diff = (datetime.datetime.utcnow() - created_time).total_seconds() / 60.0
        
        if time_diff > 15:
            cursor.execute("UPDATE orders SET status = 'expired' WHERE order_id = ?", (order_id,))
            conn.commit()
            conn.close()
            alert_msg = "⏳ ၁၅ မိနစ် ကျော်သွားသဖြင့် Order သက်တမ်း ကုန်သွားပါပြီ။ အသစ် ပြန်ဝယ်ယူပါ။" if lang == "mm" else "⏳ Order expired (15 mins time limit exceeded). Please order again."
            bot.answer_callback_query(call.id, alert_msg, show_alert=True)
            return

        current_balance = check_blockchain_balance(address, coin)
        
        if current_balance >= (amount_coin * 0.98):
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
                
                success_msg = f"🎉 **Payment Successful / ငွေပေးချေမှု အောင်မြင်ပါသည်!**\n\n" \
                              f"📦 **Your Accounts / ဝယ်ယူလိုက်သော အကောင့်များ:**\n" \
                              f"`{acc_text}`\n\n" \
                              f"💡 **Code ယူနည်း / How to get OTP Code:**\n" \
                              f"🇲🇲 အကောင့်ဝင်စဉ် OTP Code တောင်းပါက စာကြောင်းထဲတွင်ပါသော Link ကို Browser တွင် ဖွင့်၍ Code ယူပါ။\n" \
                              f"🇬🇧 If prompted for OTP code, open the included link in your browser to get the code.\n\n" \
                              f"━━━━━━━━━━━━━━━━━━━\n" \
                              f"📌 **Note / သတိပြုရန်:**\n" \
                              f"🇲🇲 ကျေးဇူးတင်ပါသည်။ အကောင့်ရရှိပြီးပါက Mail Password ကို ချက်ချင်း ပြောင်းလဲပေးပါ။\n" \
                              f"🇬🇧 Thank you for your purchase! Please change your email password immediately."
                
                bot.send_message(user_id, success_msg, parse_mode="Markdown")
                bot.answer_callback_query(call.id, "Success!", show_alert=False)
                
                # Admin Noti
                admin_noti = f"🔔 **[NEW PURCHASE ALERT]**\n\n" \
                             f"👤 **Buyer User ID:** `{user_id}`\n" \
                             f"🆔 **Order ID:** `#{order_id}`\n" \
                             f"📦 **Category:** `{category.upper()}` ({qty} accs)\n" \
                             f"🪙 **Amount Received:** `{current_balance} {coin.upper()}`\n" \
                             f"📍 **Address:** `{address}`"
                try:
                    bot.send_message(ADMIN_ID, admin_noti, parse_mode="Markdown")
                except Exception as e:
                    logging.error(f"Failed to send Admin Noti: {e}")

                auto_backup_to_admin(f"User {user_id} bought {qty} {category.upper()} (Order #{order_id})")
            else:
                alert_msg = "Stock မလုံလောက်ပါ။ Admin ကို ဆက်သွယ်ပါ။" if lang == "mm" else "Insufficient stock! Please contact Admin."
                bot.answer_callback_query(call.id, alert_msg, show_alert=True)
        else:
            alert_msg = f"ငွေမရောက်သေးပါ။ (ရောက်ရှိမှု: {current_balance} / {amount_coin} {coin.upper()})" if lang == "mm" else f"Payment not detected yet. ({current_balance} / {amount_coin} {coin.upper()})"
            bot.answer_callback_query(call.id, alert_msg, show_alert=True)
        
        conn.close()

# --- ADMIN COMMAND HANDLERS ---
@bot.message_handler(commands=['addacc'])
def add_acc(message):
    if message.from_user.id != ADMIN_ID: return
    raw_text = message.text.replace("/addacc", "").strip()
    if not raw_text:
        bot.reply_to(message, "⚠️ ပုံစံအမှန်: `/addacc x user|pass|link` သို့မဟုတ် `/addacc outlook email|pass|link`", parse_mode="Markdown")
        return

    parts = raw_text.split(maxsplit=1)
    category = parts[0].lower()

    if category not in ['x', 'outlook'] or len(parts) < 2:
        bot.reply_to(message, "⚠️ ကျေးဇူးပြု၍ Category အမျိုးအစား (x သို့မဟုတ် outlook) ပါဝင်အောင် ထည့်ပေးပါ!\n\nဥပမာ: `/addacc x user|pass|link`", parse_mode="Markdown")
        return

    acc_data = parts[1].strip()
    acc_lines = [line.strip() for line in acc_data.split("\n") if line.strip()]
    
    added = add_accounts_to_db(category, acc_lines)
    bot.reply_to(message, f"✅ **{category.upper()}** Stock အသစ် `{added}` ကောင့် ထည့်ပြီးပါပြီ။", parse_mode="Markdown")

@bot.message_handler(commands=['delacc'])
def del_acc(message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/delacc", "").strip().split()
    if len(text) < 2 or text[0].lower() not in ['x', 'outlook']:
        bot.reply_to(message, "⚠️ ပုံစံအမှန်: `/delacc x 5` (သို့) `/delacc outlook 10`", parse_mode="Markdown")
        return
    
    category, count = text[0].lower(), int(text[1])
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM accounts WHERE id IN (SELECT id FROM accounts WHERE category = ? AND status = 'available' LIMIT ?)", (category, count))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    
    if deleted > 0:
        auto_backup_to_admin(f"Deleted {deleted} {category.upper()} Stock")
    bot.reply_to(message, f"🗑️ **{category.upper()}** လက်ကျန် Stock ထဲမှ `{deleted}` ကောင့် ဖျက်ပြီးပါပြီ။", parse_mode="Markdown")

@bot.message_handler(commands=['stock'])
def check_stock(message):
    if message.from_user.id != ADMIN_ID: return
    x_count = get_stock_count('x')
    outlook_count = get_stock_count('outlook')
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM accounts WHERE status = 'sold'")
    total_sold = cursor.fetchone()[0]
    conn.close()
    
    msg = f"📊 **Current Store Status**\n\n" \
          f"🔹 **X Available:** `{x_count}` accs\n" \
          f"🔹 **Outlook Available:** `{outlook_count}` accs\n" \
          f"🔹 **Total Sold:** `{total_sold}` accs"
    bot.reply_to(message, msg, parse_mode="Markdown")

@bot.message_handler(commands=['allstock'])
def send_all_stock(message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, category, account_info FROM accounts WHERE status = 'available' ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        bot.reply_to(message, "📂 လက်ရှိ မရောင်းရသေးသော Stock လုံးဝ မရှိပါ။")
        return

    file_path = "available_stock_list.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("=== ALL AVAILABLE STOCK LIST ===\n\n")
        for r in rows:
            f.write(f"ID: #{r[0]} | Category: {r[1].upper()} | Account: {r[2]}\n")

    with open(file_path, "rb") as f:
        bot.send_document(ADMIN_ID, f, caption=f"📦 **Available Stock List** ({len(rows)} accounts)")
    os.remove(file_path)

@bot.message_handler(commands=['history'])
def show_history(message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT order_id, user_id, category, qty, coin, status, created_at FROM orders ORDER BY order_id DESC LIMIT 10")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        bot.reply_to(message, "📜 အရောင်းမှတ်တမ်း မရှိသေးပါ။")
        return
        
    history_text = "📜 **Recent 10 Orders History:**\n\n"
    for r in rows:
        status_icon = "✅" if r[5] == 'completed' else ("❌" if r[5] == 'expired' else "⏳")
        history_text += f"{status_icon} **Order #{r[0]}** | User: `{r[1]}` | {r[3]} x {r[2].upper()} | {r[4].upper()} | Status: {r[5]}\n"
        
    bot.reply_to(message, history_text, parse_mode="Markdown")

@bot.message_handler(commands=['allhistory'])
def show_all_history(message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT order_id, user_id, category, qty, coin, address, status, created_at FROM orders ORDER BY order_id DESC")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        bot.reply_to(message, "📜 Order မှတ်တမ်း လုံးဝ မရှိသေးပါ။")
        return

    file_path = "all_orders_history.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("=== ALL ORDERS HISTORY ===\n\n")
        for r in rows:
            f.write(f"Order ID: #{r[0]} | User ID: {r[1]} | Category: {r[2].upper()} | Qty: {r[3]} | Coin: {r[4].upper()}\n")
            f.write(f"Address: {r[5]}\n")
            f.write(f"Status: {r[6]} | Date: {r[7]}\n")
            f.write("-" * 50 + "\n")

    with open(file_path, "rb") as f:
        bot.send_document(ADMIN_ID, f, caption=f"📜 **All Orders History Export** ({len(rows)} orders)")
    os.remove(file_path)

@bot.message_handler(commands=['forcepay'])
def force_pay(message):
    if message.from_user.id != ADMIN_ID: return
    
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "⚠️ ပုံစံအမှန်: `/forcepay <order_id>`", parse_mode="Markdown")
        return
        
    order_id = int(parts[1])
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, category, qty, status FROM orders WHERE order_id = ?", (order_id,))
    order = cursor.fetchone()
    
    if not order:
        bot.reply_to(message, "❌ အဆိုပါ Order ID ရှာမတွေ့ပါ။")
        conn.close()
        return
        
    user_id, category, qty, status = order
    
    if status == 'completed':
        bot.reply_to(message, "⚠️ ဤ Order သည် အကောင့်ထုတ်ပေးပြီးသား ဖြစ်နေပါသည်။")
        conn.close()
        return
        
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
        
        success_msg = f"🔧 **[Admin Bypass] Payment Successful!**\n\n" \
                      f"📦 **Your Accounts / ဝယ်ယူလိုက်သော အကောင့်များ:**\n" \
                      f"`{acc_text}`\n\n" \
                      f"💡 **Code ယူနည်း / How to get OTP Code:**\n" \
                      f"🇲🇲 အကောင့်ဝင်စဉ် OTP Code တောင်းပါက စာကြောင်းထဲတွင်ပါသော Link ကို Browser တွင် ဖွင့်၍ Code ယူပါ။\n" \
                      f"🇬🇧 If prompted for OTP code, open the included link in your browser to get the code.\n\n" \
                      f"━━━━━━━━━━━━━━━━━━━\n" \
                      f"📌 **Note / သတိပြုရန်:**\n" \
                      f"🇲🇲 ကျေးဇူးတင်ပါသည်။ အကောင့်ရရှိပြီးပါက Mail Password ကို ချက်ချင်း ပြောင်းလဲပေးပါ။\n" \
                      f"🇬🇧 Thank you for your purchase! Please change your email password immediately."
                      
        bot.send_message(user_id, success_msg, parse_mode="Markdown")
        bot.reply_to(message, f"✅ Order `#{order_id}` ကို Force Pay ဖြင့် အကောင့်ထုတ်ပေးလိုက်ပါပြီ။")
        
        admin_noti = f"🔧 **[FORCE PAY EXECUTED]**\n\n" \
                     f"👤 **Buyer User ID:** `{user_id}`\n" \
                     f"🆔 **Order ID:** `#{order_id}`\n" \
                     f"📦 **Category:** `{category.upper()}` ({qty} accs)"
        try:
            bot.send_message(ADMIN_ID, admin_noti, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Failed to send Admin Noti: {e}")

        auto_backup_to_admin(f"Force Pay Order #{order_id}")
    else:
        bot.reply_to(message, f"❌ Stock မလုံလောက်ပါ။ (လိုအပ်ချက်: {qty})")
        
    conn.close()

@bot.message_handler(commands=['backup'])
def send_backup(message):
    if message.from_user.id != ADMIN_ID: return
    auto_backup_to_admin("Manual Requested Backup")

if __name__ == "__main__":
    print("Bot is running as worker...")
    bot.infinity_polling(skip_pending=True)
