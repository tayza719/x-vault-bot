import os
import logging
import datetime
import requests
import telebot
from telebot import types
import psycopg2
from psycopg2 import IntegrityError
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
ADMIN_CHANNEL_ID = -1004306575654  # Admin Private Noti Channel
MNEMONIC = os.getenv("MASTER_MNEMONIC")
DATABASE_URL = os.getenv("DATABASE_URL")
CHANNEL_ID = "@alphavalut" # Public Channel
BOT_USERNAME = "SocialXStoreBot"

PRICES = {"x": 0.15, "outlook": 0.10}
VALID_CATEGORIES = frozenset(PRICES)
MAINTENANCE_MODE = False

logging.basicConfig(level=logging.INFO)
bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# DATABASE FUNCTIONS
# ==========================================
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS accounts (
            id SERIAL PRIMARY KEY,
            category VARCHAR(50) DEFAULT 'x',
            account_info TEXT UNIQUE,
            status VARCHAR(20) DEFAULT 'available',
            buyer_id BIGINT,
            sold_at TEXT,
            order_id INT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            order_id SERIAL PRIMARY KEY,
            user_id BIGINT,
            category VARCHAR(50),
            qty INT,
            coin VARCHAR(20),
            address TEXT,
            amount_coin REAL,
            status VARCHAR(20) DEFAULT 'pending',
            created_at TEXT,
            payment_method VARCHAR(20) DEFAULT 'crypto'
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            is_banned BOOLEAN DEFAULT FALSE
        )
    ''')
    
    # Small, idempotent migrations for databases created by older versions.
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='is_banned'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE users ADD COLUMN is_banned BOOLEAN DEFAULT FALSE")

    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='accounts' AND column_name='order_id'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE accounts ADD COLUMN order_id INT")

    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='orders' AND column_name='payment_method'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE orders ADD COLUMN payment_method VARCHAR(20) DEFAULT 'crypto'")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_accounts_stock ON accounts (category, status)")
    conn.commit()
    conn.close()

init_db()

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def is_banned(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = %s", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else False

def send_admin_noti(message_text, file_path=None):
    """File ပါလျှင် Caption အနေဖြင့်တွဲပို့ပြီး၊ မပါလျှင် စာချည်းသက်သက်ပို့သော Admin Noti စနစ်"""
    try:
        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as f:
                bot.send_document(ADMIN_CHANNEL_ID, f, caption=message_text, parse_mode="Markdown")
        else:
            bot.send_message(ADMIN_CHANNEL_ID, message_text, parse_mode="Markdown")
        logging.info("Admin Channel Noti Sent.")
    except Exception as e:
        logging.error(f"Admin Channel Error: {e}")

def generate_hd_address(coin: str, index: int) -> str:
    seed_bytes = Bip39SeedGenerator(MNEMONIC).Generate()
    addr = None
    if coin == "sol":
        bip_mst = Bip44.FromSeed(seed_bytes, Bip44Coins.SOLANA)
        addr = bip_mst.Purpose().Coin().Account(index).PublicKey().ToAddress()
    elif coin == "pol":
        bip_mst = Bip44.FromSeed(seed_bytes, Bip44Coins.POLYGON)
        addr = bip_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
        if addr: addr = addr.lower()
    elif coin == "bnb":
        bip_mst = Bip44.FromSeed(seed_bytes, Bip44Coins.BINANCE_SMART_CHAIN)
        addr = bip_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
        if addr: addr = addr.lower()
    elif coin == "trx":
        bip_mst = Bip44.FromSeed(seed_bytes, Bip44Coins.TRON)
        addr = bip_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
    return addr

def get_crypto_amount(usd_amount: float, coin: str) -> float:
    coin_ids = {"sol": "solana", "pol": "polygon-ecosystem-token", "bnb": "binancecoin", "trx": "tron"}
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
            return res.get('result', {}).get('value', 0) / 1e9
        elif coin == "pol":
            url = "https://polygon-bor-rpc.publicnode.com"
            payload = {"jsonrpc": "2.0", "method": "eth_getBalance", "params": [address, "latest"], "id": 1}
            res = requests.post(url, json=payload, timeout=10).json()
            return int(res.get('result', '0x0'), 16) / 1e18
        elif coin == "bnb":
            url = "https://bsc-rpc.publicnode.com"
            payload = {"jsonrpc": "2.0", "method": "eth_getBalance", "params": [address, "latest"], "id": 1}
            res = requests.post(url, json=payload, timeout=10).json()
            return int(res.get('result', '0x0'), 16) / 1e18
        elif coin == "trx":
            url = f"https://api.trongrid.io/v1/accounts/{address}"
            res = requests.get(url, timeout=10).json()
            if res.get('data'): return res.get('data')[0].get('balance', 0) / 1e6
            return 0.0
    except Exception as e:
        logging.error(f"Blockchain Check Error ({coin}): {e}")
        return 0.0

def get_stock_count(category):
    category = category.strip().lower()
    if category not in VALID_CATEGORIES:
        return 0
    with get_db() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM accounts WHERE category = %s AND status = 'available'",
            (category,),
        )
        return int(cursor.fetchone()[0])


def add_accounts_to_db(category, acc_list):
    """Insert accounts atomically and count only rows actually inserted.

    ON CONFLICT avoids rolling back earlier successful inserts when one line is
    a duplicate, which was the main reason the displayed count could be wrong.
    """
    category = category.strip().lower()
    if category not in VALID_CATEGORIES:
        return 0, 0

    clean_accounts = list(dict.fromkeys(line.strip() for line in acc_list if line.strip()))
    if not clean_accounts:
        return 0, 0

    with get_db() as conn, conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO accounts (category, account_info)
            VALUES (%s, %s)
            ON CONFLICT (account_info) DO NOTHING
            """,
            [(category, account_info) for account_info in clean_accounts],
        )
        added = cursor.rowcount
    return int(added), len(clean_accounts) - int(added)

# ==========================================
# COMMAND HANDLERS
# ==========================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        bot.reply_to(message, "🚫 သင်သည် ဤစနစ်ကို အသုံးပြုခွင့် ပိတ်ပင်ခံထားရပါသည်။")
        return

    if MAINTENANCE_MODE and user_id != ADMIN_ID:
        bot.reply_to(message, "🛠️ **စနစ် အဆင့်မြှင့်တင်နေပါသည်။**\nခေတ္တခဏ စောင့်ဆိုင်းပေးပါခင်ဗျာ။", parse_mode="Markdown")
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    conn.close()
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🇲🇲 မြန်မာစာ", callback_data="lang_mm"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
    )
    markup.add(types.InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}"))
    bot.send_message(message.chat.id, "🌐 **Please select your language**", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(commands=['ban', 'unban'])
def handle_ban_system(message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ အသုံးပြုရန်: `/ban <user_id>` သို့မဟုတ် `/unban <user_id>`", parse_mode="Markdown")
        return
        
    target_id = parts[1]
    command = parts[0].lower()
    conn = get_db()
    cursor = conn.cursor()
    
    if command == '/ban':
        cursor.execute("UPDATE users SET is_banned = TRUE WHERE user_id = %s", (target_id,))
        bot.reply_to(message, f"✅ User {target_id} ကို Ban လိုက်ပါပြီ။")
    else:
        cursor.execute("UPDATE users SET is_banned = FALSE WHERE user_id = %s", (target_id,))
        bot.reply_to(message, f"✅ User {target_id} ကို Unban လုပ်ပေးလိုက်ပါပြီ။")
        
    conn.commit()
    conn.close()

@bot.message_handler(commands=['on', 'off'])
def toggle_maintenance(message):
    global MAINTENANCE_MODE
    if message.from_user.id != ADMIN_ID: return
    if message.text == '/off':
        MAINTENANCE_MODE = True
        bot.reply_to(message, "⛔️ **Maintenance Mode ဖွင့်လိုက်ပါပြီ။** User များ သုံး၍မရတော့ပါ။")
    else:
        MAINTENANCE_MODE = False
        bot.reply_to(message, "✅ **Maintenance Mode ပိတ်လိုက်ပါပြီ။** User များ ပြန်သုံးနိုင်ပါပြီ။")

@bot.message_handler(commands=['resetacc'])
def reset_sold_accounts(message):
    if message.from_user.id != ADMIN_ID: return
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE accounts SET status = 'available', buyer_id = NULL, sold_at = NULL WHERE status = 'sold'")
    reset_count = cursor.rowcount
    conn.commit()
    conn.close()
    bot.reply_to(message, f"🔄 **{reset_count}** ကောင့်ကို ရောင်းရန် (Available) အဖြစ် ပြန်ပြောင်းပေးလိုက်ပါပြီ။", parse_mode="Markdown")

@bot.message_handler(commands=['addacc'])
def add_acc(message):
    if message.from_user.id != ADMIN_ID: return
    raw_text = message.text.replace("/addacc", "").strip()
    if not raw_text: return
    parts = raw_text.split(maxsplit=1)
    if len(parts) < 2: return
    category = parts[0].lower()
    if category not in VALID_CATEGORIES:
        bot.reply_to(message, "⚠️ Category သည် `x` သို့မဟုတ် `outlook` ဖြစ်ရပါမည်။", parse_mode="Markdown")
        return
    acc_data = parts[1].strip()
    acc_lines = [line.strip() for line in acc_data.split("\n") if line.strip()]
    
    added, dupes = add_accounts_to_db(category, acc_lines)
    bot.reply_to(message, f"✅ **{category.upper()} Stock အသစ် {added} ကောင့် ထည့်သွင်းပြီးပါပြီ!**\n(Duplicates: {dupes})", parse_mode="Markdown")
    
    if added > 0:
        file_path = f"added_stock_{category}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(acc_lines))
        
        # 1. Admin Noti Channel သို့ File အပါ Noti ပို့ခြင်း
        admin_stock_msg = f"📦 **NEW STOCK ADDED BY ADMIN**\n🔹 Category: `{category.upper()}`\n📈 Qty Added: `{added}` Accounts"
        send_admin_noti(admin_stock_msg, file_path)
        os.remove(file_path)

        # 2. Public Channel သို့ Noti ပို့ခြင်း
        channel_noti = f"📦 **[NEW STOCK ADDED]**\n🔹 Category: `{category.upper()}`\n📈 Qty Added: `{added}` Accounts\n🛒 ဝယ်ယူလိုပါက အောက်ပါခလုတ်ကို နှိပ်ပါ -"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🛒 Buy Now / ဝယ်ယူရန်", url=f"https://t.me/{BOT_USERNAME}?start=start"))
        try:
            bot.send_message(CHANNEL_ID, channel_noti, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Channel Stock Noti Failed: {e}")

@bot.message_handler(commands=['delacc'])
def delete_acc(message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "⚠️ အသုံးပြုပုံ:\n• အရေအတွက်ဖြင့် ဖျက်ရန်: `/delacc x 2`\n• ID ဖြင့်ဖျက်ရန်: `/delacc id 5`\n• စာသားဖြင့်ဖျက်ရန်: `/delacc info <text>`", parse_mode="Markdown")
        return
        
    mode_or_cat = parts[1].lower()
    conn = get_db()
    cursor = conn.cursor()
    
    if mode_or_cat in ["id", "info"]:
        target = parts[2].strip()
        if mode_or_cat == "id" and target.isdigit():
            cursor.execute("DELETE FROM accounts WHERE id = %s", (int(target),))
            row_count = cursor.rowcount
            conn.commit()
            conn.close()
            if row_count > 0: bot.reply_to(message, f"🗑️ **ID #{target} ပါသော အကောင့်ကို ဖျက်လိုက်ပါပြီ။**")
            else: bot.reply_to(message, f"❌ ID #{target} မရှိပါ။")
        elif mode_or_cat == "info":
            cursor.execute("DELETE FROM accounts WHERE account_info = %s", (target,))
            row_count = cursor.rowcount
            conn.commit()
            conn.close()
            if row_count > 0: bot.reply_to(message, f"🗑️ **အဆိုပါ အကောင့်ကို ဖျက်လိုက်ပါပြီ။**")
            else: bot.reply_to(message, f"❌ ထိုကဲ့သို့သော အကောင့် မရှိပါ။")
        else:
            conn.close()
            bot.reply_to(message, "⚠️ ပုံစံ မှားယွင်းနေပါသည်။")
    else:
        category = mode_or_cat
        if parts[2].isdigit():
            qty = int(parts[2])
            cursor.execute("DELETE FROM accounts WHERE id IN (SELECT id FROM accounts WHERE category = %s AND status = 'available' LIMIT %s)", (category, qty))
            row_count = cursor.rowcount
            conn.commit()
            conn.close()
            bot.reply_to(message, f"🗑️ **{category.upper()} Stock ထဲမှ အကောင့် {row_count} ကောင့်ကို ဖျက်လိုက်ပါပြီ။**", parse_mode="Markdown")
        else:
            conn.close()
            bot.reply_to(message, "⚠️ အရေအတွက် ထည့်ရန် မှန်ကန်မှု မရှိပါ။", parse_mode="Markdown")

@bot.message_handler(commands=['stock', 'allstock', 'all stock'])
def check_stock_admin(message):
    if message.from_user.id != ADMIN_ID: return
    x_count = get_stock_count('x')
    out_count = get_stock_count('outlook')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM accounts WHERE status = 'sold'")
    total_sold = cursor.fetchone()[0]
    bot.reply_to(message, f"📊 **Current Store Status**\n\n🔹 X Available: {x_count}\n🔹 Outlook Available: {out_count}\n🔸 Total Sold: {total_sold}", parse_mode="Markdown")
    
    cursor.execute("SELECT id, category, account_info FROM accounts WHERE status = 'available'")
    rows = cursor.fetchall()
    conn.close()
    
    if rows:
        file_path = "available_stock_list.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("=== ALL AVAILABLE STOCK LIST ===\n\n")
            for r in rows:
                f.write(f"ID: {r[0]} | Category: {r[1].upper()} | Account: {r[2]}\n")
        
        # Command ရိုက်သည့် Admin Chat ဆီသို့သာ File ပို့မည်
        with open(file_path, "rb") as f:
            bot.send_document(message.chat.id, f, caption=f"📦 **Available Stock List (Total: {len(rows)})**", parse_mode="Markdown")
        os.remove(file_path)

@bot.message_handler(commands=['history'])
def show_history(message):
    if message.from_user.id != ADMIN_ID: return
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT order_id, user_id, category, qty, coin, status FROM orders ORDER BY order_id DESC LIMIT 10")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        bot.reply_to(message, "⚠️ Order မှတ်တမ်း လုံးဝ မရှိသေးပါ။")
        return
        
    history_text = "📜 **Recent 10 Orders History:**\n\n"
    for r in rows:
        status_icon = "✅" if r[5] == 'completed' else "❌" if r[5] == 'expired' else "⏳"
        history_text += f"{status_icon} Order `#{r[0]}` | User: `{r[1]}` | {r[3]} {r[2].upper()} | Pay: {r[4].upper()}\n"
    bot.reply_to(message, history_text, parse_mode="Markdown")

@bot.message_handler(commands=['allhistory'])
def show_all_history(message):
    if message.from_user.id != ADMIN_ID: return
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT order_id, user_id, category, qty, coin, address, status, created_at FROM orders ORDER BY order_id DESC")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        bot.reply_to(message, "⚠️ Order မှတ်တမ်း လုံးဝ မရှိသေးပါ။")
        return
        
    file_path = "all_orders_history.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("=== ALL ORDERS HISTORY ===\n\n")
        for r in rows:
            f.write(f"Order ID: #{r[0]} | User ID: {r[1]} | Category: {r[2].upper()} | Qty: {r[3]}\n")
            f.write(f"Coin: {r[4].upper()} | Address: {r[5]}\n")
            f.write(f"Status: {r[6]} | Date: {r[7]}\n")
            f.write("-" * 50 + "\n")
            
    # Command ရိုက်သည့် Admin Chat ဆီသို့သာ File ပို့မည်
    with open(file_path, "rb") as f:
        bot.send_document(message.chat.id, f, caption="📜 **All Orders History File**", parse_mode="Markdown")
    os.remove(file_path)

@bot.message_handler(commands=['forcepay'])
def force_pay(message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "⚠️ အသုံးပြုပုံ: `/forcepay <order_id>`", parse_mode="Markdown")
        return

    order_id = int(parts[1])
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, category, qty, coin, amount_coin, status FROM orders WHERE order_id = %s", (order_id,))
    order = cursor.fetchone()
    
    if not order:
        bot.reply_to(message, "❌ မရှိသော Order ID ဖြစ်နေသည်။")
        conn.close()
        return

    user_id, category, qty, coin, amount_coin, status = order
    if status == 'completed':
        bot.reply_to(message, "⚠️ ဒီ Order သည် အကောင့်ထုတ်ပေးပြီးသား ဖြစ်နေပါပြီ။")
        conn.close()
        return

    cursor.execute("SELECT id, account_info FROM accounts WHERE category = %s AND status = 'available' ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED", (category, qty))
    rows = cursor.fetchall()
    
    if len(rows) >= qty:
        account_ids = tuple(r[0] for r in rows)
        accounts_info = [r[1] for r in rows]

        # Keep the rows so an admin can safely re-add this specific forcepay order.
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "UPDATE accounts SET status = 'sold', buyer_id = %s, sold_at = %s, order_id = %s WHERE id IN %s",
            (user_id, now_str, order_id, account_ids),
        )
        cursor.execute(
            "UPDATE orders SET status = 'completed', payment_method = 'forcepay' WHERE order_id = %s",
            (order_id,),
        )
        conn.commit()
        
        acc_text = "\n".join(accounts_info)
        success_msg = (
            f"🎉 **Payment Successful / ငွေပေးချေမှု အောင်မြင်ပါသည်!**\n\n"
            f"📦 **Your Accounts / ဝယ်ယူထားသော အကောင့်များ:**\n`{acc_text}`\n\n"
            f"📌 **Note / သတိပေးချက်:**\n"
            f"အကောင့်ရပြီဆိုတာနဲ့ Password နဲ့ အချက်အလက်များကို ချက်ချင်းပြောင်းလဲ အသုံးပြုပါရန်။\n"
            f"Please change password and details immediately after receiving accounts. Thank you!"
        )
        
        try:
            bot.send_message(user_id, success_msg, parse_mode="Markdown")
            bot.reply_to(message, f"✅ Order #{order_id} ကို Force Pay ဖြင့် အောင်မြင်စွာ ထုတ်ပေးလိုက်ပါပြီ။ (Database မှ ဖယ်ထုတ်ပေးခဲ့၍ နောက်ပိုင်း ပြန် Add လို့ ရပါမည်)")
        except Exception as e:
            bot.reply_to(message, f"⚠️ User ထံ ပို့၍မရပါ: {e}")
            
        file_path = f"sold_order_{order_id}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(acc_text)
            
        # Admin Channel သို့ Noti အပြည့်အစုံ File ပါတွဲပို့ခြင်း
        force_admin_msg = f"""
🛠 **ADMIN FORCEPAY ALERT**
👤 **Buyer User ID:** `{user_id}`
🆔 **Order ID:** `#{order_id}`
📦 **Category:** `{category.upper()}` ({qty} accs)
💰 **Amount Received:** `MANUAL`
📍 **Address:** `ADMIN_FORCE_PAY`
"""
        send_admin_noti(force_admin_msg, file_path)
        os.remove(file_path)

        channel_noti = f"🛍️ **[NEW PURCHASE SUCCESS]**\n🆔 Order: `#{order_id}`\n📦 Qty: `{qty}` {category.upper()}\n🪙 Paid Coin: `{coin.upper()}`"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🛒 Buy Now / ဝယ်ယူရန်", url=f"https://t.me/{BOT_USERNAME}?start=start"))
        try:
            bot.send_message(CHANNEL_ID, channel_noti, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Channel Purchase Noti Failed: {e}")
    else:
        bot.reply_to(message, f"❌ Stock မလုံလောက်ပါ (လိုအပ်ချက်: {qty})")
        
    conn.close()


@bot.message_handler(commands=['readdforce'])
def readd_forcepay_accounts(message):
    """Admin-only: return accounts sold by one forcepay order to available stock."""
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "⚠️ အသုံးပြုပုံ: `/readdforce <order_id>`", parse_mode="Markdown")
        return

    order_id = int(parts[1])
    with get_db() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT status, payment_method FROM orders WHERE order_id = %s FOR UPDATE",
            (order_id,),
        )
        order = cursor.fetchone()
        if not order:
            bot.reply_to(message, f"❌ Order #{order_id} မရှိပါ။")
            return
        status, payment_method = order
        if status != 'completed' or payment_method != 'forcepay':
            bot.reply_to(message, "⚠️ ဒီ Order သည် admin forcepay order မဟုတ်ပါ၊ သို့မဟုတ် ပြီးစီးပြီးသား flow မဟုတ်ပါ။")
            return

        cursor.execute(
            """
            UPDATE accounts
               SET status = 'available', buyer_id = NULL, sold_at = NULL, order_id = NULL
             WHERE order_id = %s AND status = 'sold'
            """,
            (order_id,),
        )
        restored = cursor.rowcount

    bot.reply_to(
        message,
        f"♻️ Forcepay Order #{order_id} မှ account {restored} ခုကို Available stock သို့ ပြန်ထည့်ပြီးပါပြီ။",
    )

# ==========================================
# CALLBACK HANDLER (INLINE BUTTONS)
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if is_banned(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 သင်သည် အသုံးပြုခွင့် ပိတ်ခံထားရပါသည်။", show_alert=True)
        return

    if MAINTENANCE_MODE and call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "🛠️ စနစ် ပြုပြင်နေသဖြင့် ခေတ္တပိတ်ထားပါသည်ခင်ဗျာ။", show_alert=True)
        return

    data = call.data
    
    if data.startswith("lang_"):
        lang = data.split("_")[1]
        x_stock = get_stock_count('x')
        outlook_stock = get_stock_count('outlook')
        
        if lang == "mm":
            welcome_text = f"🛒 **Alpha Vault Store မှ ကြိုဆိုပါတယ်**\n\n🔹 X Stock: {x_stock} (Price: ${PRICES['x']})\n🔹 Outlook Stock: {outlook_stock} (Price: ${PRICES['outlook']})\n\nဝယ်ယူလိုသော အမျိုးအစားကို ရွေးချယ်ပါ -"
        else:
            welcome_text = f"🛒 **Welcome to Alpha Vault Store**\n\n🔹 X Stock: {x_stock} (Price: ${PRICES['x']})\n🔹 Outlook Stock: {outlook_stock} (Price: ${PRICES['outlook']})\n\nSelect category -"
            
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🛒 X Accounts", callback_data=f"cat_x_{lang}"),
            types.InlineKeyboardButton("📧 Outlook Accounts", callback_data=f"cat_outlook_{lang}")
        )
        markup.add(types.InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}"))
        bot.edit_message_text(welcome_text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("cat_"):
        parts = data.split("_")
        category = parts[1]
        lang = parts[2] if len(parts) > 2 else "mm"
        
        stock_qty = get_stock_count(category)
        unit_price = PRICES[category]
        
        if stock_qty < 2:
            bot.answer_callback_query(call.id, "Stock မလုံလောက်ပါ (အနည်းဆုံး ၂ ကောင့် လိုအပ်ပါသည်)" if lang=="mm" else "Stock not enough (Min 2 required)", show_alert=True)
            return
            
        markup = types.InlineKeyboardMarkup()
        for q in [2, 4, 6, 8, 10, 15, 20]:
            if q <= stock_qty:
                markup.add(types.InlineKeyboardButton(f"🛒 {q} accs (${round(q*unit_price, 2)})", callback_data=f"qty_{category}_{q}_{lang}"))
        markup.add(types.InlineKeyboardButton("🔙 Back" if lang=="en" else "🔙 နောက်သို့", callback_data=f"lang_{lang}"))
        
        title = f"🛒 **{category.upper()}** ဝယ်ယူမည့်ပမာဏကို ရွေးချယ်ပါ -" if lang == "mm" else f"🛒 Select quantity for **{category.upper()}** -"
        bot.edit_message_text(title, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("qty_"):
        parts = data.split("_")
        category = parts[1]
        qty = int(parts[2])
        lang = parts[3] if len(parts) > 3 else "mm"
        
        stock_qty = get_stock_count(category)
        if qty > stock_qty:
            bot.answer_callback_query(call.id, f"Stock မလုံလောက်ပါ (လက်ကျန်: {stock_qty})" if lang=="mm" else f"Stock not enough (Available: {stock_qty})", show_alert=True)
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
        markup.add(types.InlineKeyboardButton("🔙 Back" if lang=="en" else "🔙 နောက်သို့", callback_data=f"cat_{category}_{lang}"))
        
        pay_title = "🪙 **ငွေပေးချေလိုသော Native Coin ကို ရွေးချယ်ပါ။**" if lang == "mm" else "🪙 **Select payment coin.**"
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
            bot.answer_callback_query(call.id, "Crypto ဈေးနှုန်း ယူ၍မရပါ။ ခေတ္တစောင့်ဆိုင်းပါ။" if lang=="mm" else "Failed to get crypto price.", show_alert=True)
            return
            
        created_time = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO orders (user_id, category, qty, coin, address, amount_coin, status, created_at) VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s) RETURNING order_id",
            (call.from_user.id, category, qty, coin, "pending", coin_amount, created_time)
        )
        order_id = cursor.fetchone()[0]
        conn.commit()
        
        address = generate_hd_address(coin, order_id)
        cursor.execute("UPDATE orders SET address = %s WHERE order_id = %s", (address, order_id))
        conn.commit()
        conn.close()
        
        markup = types.InlineKeyboardMarkup()
        btn_text = "✅ Check Payment (ငွေစစ်မည်)" if lang == "mm" else "✅ Check Payment"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"check_{order_id}_{lang}"))
        markup.add(types.InlineKeyboardButton("🔙 Back" if lang=="en" else "🔙 နောက်သို့", callback_data=f"qty_{category}_{qty}_{lang}"))
        
        if lang == "mm":
            msg = f"💳 **Direct Native Crypto Payment**\n\n" \
                  f"🆔 **Order ID:** `#{order_id}`\n" \
                  f"🪙 **Coin:** `{coin.upper()}`\n" \
                  f"💵 **Total Value:** `${usd_total}` USD\n" \
                  f"⚠️ **EXACT AMOUNT TO SEND:**\n`{coin_amount}` `{coin.upper()}`\n\n" \
                  f"📍 **DEPOSIT ADDRESS:**\n`{address}`\n\n" \
                  f"⏱️ **ငွေလွှဲရန် အချိန်ကန့်သတ်ချက်:** `15 မိနစ်`\n" \
                  f"📌 **Network Fee ပိုလွှဲစရာ မလိုပါ (`{coin_amount}` အတိအကျလွှဲပါ)"
        else:
            msg = f"💳 **Direct Native Crypto Payment**\n\n" \
                  f"🆔 **Order ID:** `#{order_id}`\n" \
                  f"🪙 **Coin:** `{coin.upper()}`\n" \
                  f"💵 **Total Value:** `${usd_total}` USD\n" \
                  f"⚠️ **EXACT AMOUNT TO SEND:**\n`{coin_amount}` `{coin.upper()}`\n\n" \
                  f"📍 **DEPOSIT ADDRESS:**\n`{address}`\n\n" \
                  f"⏱️ **Payment Time Limit:** `15 Minutes`\n" \
                  f"📌 **Please ensure exact `{coin_amount}` `{coin.upper()}` transfer.**"

        bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("check_"):
        parts = data.split("_")
        order_id = int(parts[1])
        lang = parts[2] if len(parts) > 2 else "mm"
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, category, qty, coin, address, amount_coin, status, created_at FROM orders WHERE order_id = %s", (order_id,))
        order = cursor.fetchone()
        
        if not order:
            bot.answer_callback_query(call.id, "Order မရှိတော့ပါ!", show_alert=True)
            conn.close()
            return
            
        user_id, category, qty, coin, address, amount_coin, status, created_at_str = order
        
        if status == 'completed':
            bot.answer_callback_query(call.id, "ဒီ Order သည် အကောင့်ထုတ်ပေးပြီးသား ဖြစ်နေပါပြီ!", show_alert=True)
            conn.close()
            return
            
        created_time = datetime.datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
        time_diff = (datetime.datetime.utcnow() - created_time).total_seconds() / 60
        if time_diff > 15:
            cursor.execute("UPDATE orders SET status = 'expired' WHERE order_id = %s", (order_id,))
            conn.commit()
            conn.close()
            bot.answer_callback_query(call.id, "⏳ Order သက်တမ်း (၁၅) မိနစ် ကျော်လွန်သွားပါပြီ!", show_alert=True)
            return

        current_balance = check_blockchain_balance(address, coin)
        
        if current_balance >= (amount_coin * 0.98):
            cursor.execute("SELECT id, account_info FROM accounts WHERE category = %s AND status = 'available' ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED", (category, qty))
            rows = cursor.fetchall()
            
            if len(rows) >= qty:
                account_ids = tuple(r[0] for r in rows)
                accounts_info = [r[1] for r in rows]
                now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                
                # ⚠️ တကယ့် Real Crypto Purchase ဖြစ်၍ DB တွင် 'sold' သို့ပြောင်းပြီး Block ထားမည် (ပြန် Add မရပါ)
                cursor.execute("UPDATE accounts SET status = 'sold', buyer_id = %s, sold_at = %s, order_id = %s WHERE id IN %s", (user_id, now_str, order_id, account_ids))
                cursor.execute("UPDATE orders SET status = 'completed', payment_method = 'crypto' WHERE order_id = %s", (order_id,))
                conn.commit()
                
                acc_text = "\n".join(accounts_info)
                success_msg = (
                    f"🎉 **Payment Successful / ငွေပေးချေမှု အောင်မြင်ပါသည်!**\n\n"
                    f"📦 **Your Accounts / ဝယ်ယူထားသော အကောင့်များ:**\n`{acc_text}`\n\n"
                    f"📌 **Note / သတိပေးချက်:**\n"
                    f"အကောင့်ရပြီဆိုတာနဲ့ Password နဲ့ အချက်အလက်များကို ချက်ချင်းပြောင်းလဲ အသုံးပြုပါရန်။\n"
                    f"Please change password and details immediately after receiving accounts. Thank you!"
                )
                
                try:
                    bot.send_message(user_id, success_msg, parse_mode="Markdown")
                    bot.answer_callback_query(call.id, "Success! Account ထုတ်ပေးလိုက်ပါပြီ။", show_alert=True)
                except Exception as e:
                    logging.error(f"User Message Failed: {e}")
                
                file_path = f"sold_order_{order_id}.txt"
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(acc_text)
                    
                # Admin Noti Channel သို့ Noti အပြည့်အစုံ File ပါတွဲပို့ခြင်း
                admin_msg = f"""
🔔 **NEW PURCHASE ALERT**
👤 **Buyer User ID:** `{user_id}`
🆔 **Order ID:** `#{order_id}`
📦 **Category:** `{category.upper()}` ({qty} accs)
💰 **Amount Received:** `{amount_coin}` {coin.upper()}
📍 **Address:** `{address}`
"""
                send_admin_noti(admin_msg, file_path)
                os.remove(file_path)

                channel_noti = f"🛍️ **[NEW PURCHASE SUCCESS]**\n🆔 Order: `#{order_id}`\n📦 Qty: `{qty}` {category.upper()}\n🪙 Paid Coin: `{coin.upper()}`"
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🛒 Buy Now / ဝယ်ယူရန်", url=f"https://t.me/{BOT_USERNAME}?start=start"))
                try:
                    bot.send_message(CHANNEL_ID, channel_noti, reply_markup=markup, parse_mode="Markdown")
                except Exception as e:
                    logging.error(f"Channel Purchase Noti Failed: {e}")
            else:
                bot.answer_callback_query(call.id, "❌ Stock မလုံလောက်ပါ။ Admin ကို ဆက်သွယ်ပါ။", show_alert=True)
        else:
            bot.answer_callback_query(call.id, f"⚠️ ငွေဝင်ရန် ကျန်သေးသည် (ဝင်ထားသောငွေ: {current_balance} {coin.upper()})", show_alert=True)
            
        conn.close()

if __name__ == "__main__":
    bot.infinity_polling(skip_pending=True)
