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
MNEMONIC = os.getenv("MASTER_MNEMONIC", "your twelve words seed phrase go here")

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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()

init_db()

def auto_backup_to_admin(reason="Auto Backup"):
    try:
        with open(DB_FILE, 'rb') as f:
            bot.send_document(ADMIN_ID, f, caption=f"📦 **DB Backup System** ({reason})", parse_mode="Markdown")
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
    duplicates = 0
    for acc in acc_list:
        try:
            cursor.execute("INSERT INTO accounts (category, account_info) VALUES (?, ?)", (category, acc))
            added += 1
        except sqlite3.IntegrityError:
            duplicates += 1
        except Exception as e:
            pass
    conn.commit()
    conn.close()
    if added > 0:
        auto_backup_to_admin(f"Added {added} {category.upper()} Stock")
    return added, duplicates

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

@bot.message_handler(commands=["start"])
def send_welcome(message):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.from_user.id,))
    conn.commit()
    conn.close()

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🇲🇲 မြန်မာစာ", callback_data="lang_mn"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
    )
    bot.send_message(message.chat.id, "🌐 **Please select your language**", parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    data = call.data

    if data.startswith("lang_"):
        lang = data.split("_")[1]
        x_stock = get_stock_count('x')
        outlook_stock = get_stock_count('outlook')

        if lang == "mn":
            welcome_text = f"🛒 **Alpha Vault Store မှ ကြိုဆိုပါတယ်**\n\n🔹 **X Stock:** {x_stock} (Price: $0.15)\n🔹 **Outlook Stock:** {outlook_stock} (Price: $0.10)\n\nဝယ်ယူလိုသော အမျိုးအစားကို ရွေးချယ်ပါ -"
        else:
            welcome_text = f"🛒 **Welcome to Alpha Vault Store**\n\n🔹 **X Stock:** {x_stock} (Price: $0.15)\n🔹 **Outlook Stock:** {outlook_stock} (Price: $0.10)\n\nPlease select an item -"

        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(f"🛒 X Accounts", callback_data=f"cat_x_{lang}"),
            types.InlineKeyboardButton(f"📧 Outlook Accounts", callback_data=f"cat_outlook_{lang}")
        )
        bot.edit_message_text(welcome_text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)

    elif data.startswith("cat_"):
        parts = data.split("_")
        category = parts[1]
        lang = parts[2] if len(parts) > 2 else "mn"

        stock_qty = get_stock_count(category)
        unit_price = PRICES[category]

        if stock_qty < 2:
            alert_msg = "Stock မလုံလောက်ပါ (အနည်းဆုံး 2 ကောင့် လိုအပ်ပါသည်)" if lang == "mn" else "Insufficient Stock (Min 2 required)"
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

        title = f"🛒 **{category.upper()}** အရေအတွက်ကို ရွေးချယ်ပါ" if lang == "mn" else f"🛒 Select quantity for **{category.upper()}**"
        bot.edit_message_text(
            f"{title}\n(1 acc = ${unit_price} | Stock: {stock_qty})",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup
        )

    elif data.startswith("qty_"):
        parts = data.split("_")
        category = parts[1]
        qty = int(parts[2])
        lang = parts[3] if len(parts) > 3 else "mn"

        stock_qty = get_stock_count(category)
        if qty > stock_qty:
            alert_msg = f"Stock မလုံလောက်ပါ (လက်ကျန်: {stock_qty})" if lang == "mn" else f"Insufficient stock (Available: {stock_qty})"
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

        pay_title = "💳 **ငွေပေးချေလိုသော Native Coin ကို ရွေးချယ်ပါ -**" if lang == "mn" else "💳 **Select Native Coin for Payment -**"
        bot.edit_message_text(pay_title, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)

    elif data.startswith("pay_"):
        parts = data.split("_")
        category = parts[1]
        qty = int(parts[2])
        coin = parts[3]
        lang = parts[4] if len(parts) > 4 else "mn"

        usd_total = round(qty * PRICES[category], 2)
        coin_amount = get_crypto_amount(usd_total, coin)

        if not coin_amount:
            alert_msg = "Crypto ဈေးနှုန်း ရယူ၍ မရပါ၊ ခဏအကြာမှ ထပ်စမ်းပါ" if lang == "mn" else "Could not fetch crypto price. Try again later."
            bot.answer_callback_query(call.id, alert_msg, show_alert=True)
            return

        created_time = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO orders (user_id, category, qty, coin, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                       (call.from_user.id, category, qty, coin, created_time))
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
        btn_text = "🔄 Check Payment (ငွေစစ်မည်)" if lang == "mn" else "🔄 Check Payment"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"check_{order_id}_{lang}"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"qty_{category}_{qty}_{lang}"))

        if lang == "mn":
            msg = f"💳 **Direct Native Crypto Payment**\n\n" \
                  f"🆔 **Order ID:** `{order_id}`\n" \
                  f"🪙 **Coin:** `{coin.upper()}`\n" \
                  f"💵 **Total Value:** `${usd_total}` USD\n\n" \
                  f"⚠️ **EXACT AMOUNT TO SEND (တိကျစွာ လွှဲပေးပါ):**\n" \
                  f"`{coin_amount}` **{coin.upper()}**\n\n" \
                  f"📍 **DEPOSIT ADDRESS (လွှဲပြောင်းရမည့် Address):**\n" \
                  f"`{address}`\n\n" \
                  f"⏳ **ငွေလွှဲချိန် ကန့်သတ်ချက်:** `15 မိနစ်`\n" \
                  f"📌 **Network Fee မပါဝင်ဘဲ အထက်ပါ **`{coin_amount}` **{coin.upper()}** အတိအကျ ရောက်ရပါမည်။"
        else:
            msg = f"💳 **Direct Native Crypto Payment**\n\n" \
                  f"🆔 **Order ID:** `{order_id}`\n" \
                  f"🪙 **Coin:** `{coin.upper()}`\n" \
                  f"💵 **Total Value:** `${usd_total}` USD\n\n" \
                  f"⚠️ **EXACT AMOUNT TO SEND:**\n" \
                  f"`{coin_amount}` **{coin.upper()}**\n\n" \
                  f"📍 **DEPOSIT ADDRESS:**\n" \
                  f"`{address}`\n\n" \
                  f"⏳ **Payment Time Limit:** `15 Minutes`\n" \
                  f"📌 **Please ensure exact `{coin_amount}` {coin.upper()} reaches the address.**"

        bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)

    elif data.startswith("check_"):
        parts = data.split("_")
        order_id = int(parts[1])
        lang = parts[2] if len(parts) > 2 else "mn"

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, category, qty, coin, address, amount_coin, status, created_at FROM orders WHERE order_id = ?", (order_id,))
        order = cursor.fetchone()

        if not order:
            alert_msg = "Order မရှိတော့ပါ" if lang == "mn" else "Order not found"
            bot.answer_callback_query(call.id, alert_msg, show_alert=True)
            conn.close()
            return

        user_id, category, qty, coin, address, amount_coin, status, created_at_str = order

        if status == "completed":
            alert_msg = "ဒီ Order အတွက် အကောင့် ထုတ်ပေးပြီးပါပြီ" if lang == "mn" else "Order already completed"
            bot.answer_callback_query(call.id, alert_msg, show_alert=True)
            conn.close()
            return

        if status == "expired":
            alert_msg = "အချိန် ၁၅ မိနစ် ကျော်လွန်သွားသဖြင့် Order ပယ်ဖျက်ပြီးပါပြီ" if lang == "mn" else "Order expired"
            bot.answer_callback_query(call.id, alert_msg, show_alert=True)
            conn.close()
            return

        created_time = datetime.datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
        time_diff = (datetime.datetime.utcnow() - created_time).total_seconds() / 60

        if time_diff > 15:
            cursor.execute("UPDATE orders SET status = 'expired' WHERE order_id = ?", (order_id,))
            conn.commit()
            conn.close()
            
            cancel_msg = f"❌ **Order Expired / အချိန်ကျော်လွန်သွားပါပြီ**\n\nOrder `#{order_id}` သည် ၁၅ မိနစ်အတွင်း ငွေလွှဲခြင်းမရှိပါသဖြင့် ပယ်ဖျက်လိုက်ပါပြီ။" if lang == "mn" else f"❌ **Order Expired**\n\nOrder `#{order_id}` has been cancelled due to 15-minute timeout."
            try:
                bot.send_message(user_id, cancel_msg, parse_mode="Markdown")
            except:
                pass
                
            bot.answer_callback_query(call.id, "⌛ Order expired", show_alert=True)
            return

        current_balance = check_blockchain_balance(address, coin)

        if current_balance >= (amount_coin * 0.98):
            cursor.execute("SELECT id, account_info FROM accounts WHERE category = ? AND status = 'available' LIMIT ?", (category, qty))
            rows = cursor.fetchall()

            if len(rows) >= qty:
                account_ids = [r[0] for r in rows]
                accounts_info = [r[1] for r in rows]

                placeholders = ', '.join(['?'] * len(account_ids))
                cursor.execute(f"UPDATE accounts SET status = 'sold', buyer_id = ?, sold_at = ? WHERE id IN ({placeholders})",
                               [user_id, datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")] + account_ids)
                cursor.execute("UPDATE orders SET status = 'completed' WHERE order_id = ?", (order_id,))
                conn.commit()

                acc_text = "\n".join(accounts_info)

                success_msg = f"🎉 **Payment Successful / ငွေလက်ခံရရှိပါပြီ!**\n\n" \
                              f"📦 **Your Accounts / ဝယ်ယူထားသော အကောင့်များ:**\n" \
                              f"`{acc_text}`\n\n" \
                              f"🔑 **Code ယူနည်း / How to get OTP Code:**\n" \
                              f"👉 အကောင့်ဝင်စဉ် OTP Code တောင်းပါက စာကြောင်းထဲတွင် ပါဝင်သော Mail Password ဖြင့် ဝင်ယူပါ။\n" \
                              f"-------------------------------\n" \
                              f"📌 **Note / သတိပြုရန်:**\n" \
                              f"ကျေးဇူးပြု၍အကောင့်ရသည်နှင့် အကောင့်ထဲရှိပြောင်းလို့ရတာအကုန်ပြောင်းပါ။\n" \
                              f"🙏 Thank you for your purchase! Please change password immediately."

                bot.send_message(user_id, success_msg, parse_mode="Markdown")
                bot.answer_callback_query(call.id, "Success!", show_alert=False)

                admin_noti = f"🔔 **[NEW PURCHASE ALERT]**\n\n" \
                             f"👤 **Buyer User ID:** `{user_id}`\n" \
                             f"🆔 **Order ID:** `#{order_id}`\n" \
                             f"📦 **Category:** `{category.upper()}` ({qty} accs)\n" \
                             f"💰 **Amount Received:** `{current_balance}` {coin.upper()}\n" \
                             f"📍 **Address:** `{address}`"
                try:
                    bot.send_message(ADMIN_ID, admin_noti, parse_mode="Markdown")
                except Exception as e:
                    logging.error(f"Failed to send Admin Noti: {e}")

                auto_backup_to_admin(f"User {user_id} bought {qty} {category.upper()}")
            else:
                alert_msg = "Stock မလုံလောက်တော့ပါ Admin ကို ဆက်သွယ်ပါ" if lang == "mn" else "Stock out. Contact Admin."
                bot.answer_callback_query(call.id, alert_msg, show_alert=True)
        else:
            alert_msg = f"ငွေမရောက်သေးပါ (ရောက်ရှိငွေ: {current_balance} / လိုအပ်ငွေ: {amount_coin})" if lang == "mn" else f"Payment not detected ({current_balance}/{amount_coin})"
            bot.answer_callback_query(call.id, alert_msg, show_alert=True)

        conn.close()

# --- ADMIN COMMAND HANDLERS ---
@bot.message_handler(commands=['addacc'])
def add_acc(message):
    if message.from_user.id != ADMIN_ID: return
    raw_text = message.text.replace("/addacc", "").strip()
    if not raw_text:
        bot.reply_to(message, "⚠️ ပုံစံမှား: `/addacc x user|pass|link` သို့မဟုတ် `/addacc outlook mail|pass`", parse_mode="Markdown")
        return

    parts = raw_text.split(maxsplit=1)
    category = parts[0].lower()

    if category not in ['x', 'outlook'] or len(parts) < 2:
        bot.reply_to(message, "⚠️ ကျေးဇူးပြု၍ Category အမျိုးအစား (x သို့မဟုတ် outlook) ထည့်ပါ။", parse_mode="Markdown")
        return

    acc_data = parts[1].strip()
    acc_lines = [line.strip() for line in acc_data.split("\n") if line.strip()]

    added, duplicates = add_accounts_to_db(category, acc_lines)
    total_stock = get_stock_count(category)

    res_msg = f"✅ **{category.upper()} Stock အသစ် {added} ကောင့် ထည့်သွင်းပြီးပါပြီ။**\n"
    if duplicates > 0:
        res_msg += f"⚠️ (ထပ်နေ၍ ပယ်လိုက်သော အကောင့်: {duplicates} ကောင့်)\n"
    res_msg += f"📊 **စုစုပေါင်း {category.upper()} Stock : {total_stock} ကောင့်**"

    bot.reply_to(message, res_msg, parse_mode="Markdown")

@bot.message_handler(commands=['delacc'])
def del_acc(message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/delacc", "").strip().split()
    if len(text) < 2 or text[0].lower() not in ['x', 'outlook']:
        bot.reply_to(message, "⚠️ ပုံစံမှား: `/delacc x 5` (သို့) `/delacc outlook 10`", parse_mode="Markdown")
        return

    category, count = text[0].lower(), int(text[1])
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM accounts WHERE id IN (SELECT id FROM accounts WHERE category = ? AND status = 'available' LIMIT ?)", (category, count))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted > 0:
        total_stock = get_stock_count(category)
        auto_backup_to_admin(f"Deleted {deleted} {category.upper()} Stock")
        bot.reply_to(message, f"🗑️ **{category.upper()}** လက်ရှိ Stock ထဲမှ **{deleted}** ကောင့်ကို ဖျက်လိုက်ပါပြီ။\n📊 **ကျန်ရှိ စုစုပေါင်း Stock : {total_stock} ကောင့်**", parse_mode="Markdown")

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
    cursor.execute("SELECT id, category, account_info FROM accounts WHERE status = 'available'")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        bot.reply_to(message, "📂 လက်ရှိ အရောင်းအတွက် ရရှိနိုင်သော Stock လုံးဝ မရှိသေးပါ")
        return

    file_path = "available_stock_list.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("=== ALL AVAILABLE STOCK LIST ===\n\n")
        for r in rows:
            f.write(f"ID: #{r[0]} | Category: {r[1].upper()} | Account: {r[2]}\n")

    with open(file_path, "rb") as f:
        bot.send_document(ADMIN_ID, f, caption="📦 **Available Stock List Backup**", parse_mode="Markdown")
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
        bot.reply_to(message, "📜 အရောင်းမှတ်တမ်း လုံးဝ မရှိသေးပါ")
        return

    history_text = "📜 **Recent 10 Orders History:**\n\n"
    for r in rows:
        status_icon = "✅" if r[5] == 'completed' else ("❌" if r[5] == 'expired' else "⏳")
        history_text += f"{status_icon} **Order #{r[0]}** | User: `{r[1]}` | {r[2].upper()} (x{r[3]}) | {r[4].upper()} | {r[6]}\n"

    bot.reply_to(message, history_text, parse_mode="Markdown")

@bot.message_handler(commands=['allhistory'])
def show_all_history(message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT order_id, user_id, category, qty, coin, address, amount_coin, status, created_at FROM orders ORDER BY order_id DESC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        bot.reply_to(message, "📜 Order မှတ်တမ်း လုံးဝ မရှိသေးပါ")
        return

    file_path = "all_orders_history.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("=== ALL ORDERS HISTORY ===\n\n")
        for r in rows:
            f.write(f"Order ID: #{r[0]} | User ID: {r[1]} | Category: {r[2].upper()} (x{r[3]})\n")
            f.write(f"Address: {r[5]}\n")
            f.write(f"Status: {r[7]} | Date: {r[8]}\n")
            f.write("-" * 50 + "\n")

    with open(file_path, "rb") as f:
        bot.send_document(ADMIN_ID, f, caption="📜 **All Orders History Backup**", parse_mode="Markdown")
    os.remove(file_path)

@bot.message_handler(commands=['forcepay'])
def force_pay(message):
    if message.from_user.id != ADMIN_ID: return

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "⚠️ ပုံစံမှား: `/forcepay <order_id>`", parse_mode="Markdown")
        return

    order_id = int(parts[1])

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, category, qty, status FROM orders WHERE order_id = ?", (order_id,))
    order = cursor.fetchone()

    if not order:
        bot.reply_to(message, "❌ မရှိသော Order ID ဖြစ်နေသည်")
        conn.close()
        return

    user_id, category, qty, status = order

    if status == 'completed':
        bot.reply_to(message, "⚠️ ဒီ Order သည် အကောင့်ထုတ်ပေးပြီးသား ဖြစ်နေသည်")
        conn.close()
        return

    cursor.execute("SELECT id, account_info FROM accounts WHERE category = ? AND status = 'available' LIMIT ?", (category, qty))
    rows = cursor.fetchall()

    if len(rows) >= qty:
        account_ids = [r[0] for r in rows]
        accounts_info = [r[1] for r in rows]

        placeholders = ', '.join(['?'] * len(account_ids))
        cursor.execute(f"UPDATE accounts SET status = 'sold', buyer_id = ?, sold_at = ? WHERE id IN ({placeholders})",
                       [user_id, datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")] + account_ids)
        cursor.execute("UPDATE orders SET status = 'completed' WHERE order_id = ?", (order_id,))
        conn.commit()

        acc_text = "\n".join(accounts_info)

        success_msg = f"🎉 **[Admin Bypass] Payment Successful!**\n\n" \
                      f"📦 **Your Accounts / ဝယ်ယူထားသော အကောင့်များ:**\n" \
                      f"`{acc_text}`\n\n" \
                      f"🔑 **Code ယူနည်း / How to get OTP Code:**\n" \
                      f"👉 အကောင့်ဝင်စဉ် OTP Code တောင်းပါက စာကြောင်းထဲတွင် ပါဝင်သော Mail Password ဖြင့် ဝင်ယူပါ။\n" \
                      f"-------------------------------\n" \
                      f"📌 **Note / သတိပြုရန်:**\n" \
                      f"ကျေးဇူးပြု၍အကောင့်ရသည်နှင့် အကောင့်ထဲရှိပြောင်းလို့ရတာအကုန်ပြောင်းပါ။\n" \
                      f"🙏 Thank you for your purchase! Please change password immediately."

        bot.send_message(user_id, success_msg, parse_mode="Markdown")
        bot.reply_to(message, f"✅ Order #{order_id} ကို Force Pay ဖြင့် အကောင့် ထုတ်ပေးလိုက်ပါပြီ။")

        admin_noti = f"⚡ **[FORCE PAY EXECUTED]**\n\n" \
                     f"👤 **Buyer User ID:** `{user_id}`\n" \
                     f"🆔 **Order ID:** `#{order_id}`\n" \
                     f"📦 **Category:** `{category.upper()}` ({qty} accs)"

        try:
            bot.send_message(ADMIN_ID, admin_noti, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Failed to send Admin Noti: {e}")

        auto_backup_to_admin(f"Force Pay Order #{order_id}")
    else:
        bot.reply_to(message, f"❌ Stock မလုံလောက်ပါ (လိုအပ်ချက်: {qty})")

    conn.close()

@bot.message_handler(commands=['bc'])
def broadcast_message(message):
    if message.from_user.id != ADMIN_ID: return
    bc_text = message.text.replace("/bc", "").strip()
    if not bc_text:
        bot.reply_to(message, "⚠️ ပုံစံမှား: `/bc <Your Message Here>`", parse_mode="Markdown")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()

    sent = 0
    failed = 0
    for u in users:
        try:
            bot.send_message(u[0], f"📢 **ANNOUNCEMENT**\n\n{bc_text}", parse_mode="Markdown")
            sent += 1
        except:
            failed += 1

    bot.reply_to(message, f"✅ Broadcast အောင်မြင်စွာ ပို့ပြီးပါပြီ။\n\n🟢 အောင်မြင်: {sent} ယောက်\n🔴 ကျရှုံး: {failed} ယောက်")

@bot.message_handler(commands=['backup'])
def send_backup(message):
    if message.from_user.id != ADMIN_ID: return
    auto_backup_to_admin("Manual Requested Backup")

if __name__ == "__main__":
    print("Bot is running as worker...")
    bot.infinity_polling(skip_pending=True)
