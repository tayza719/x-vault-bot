import os
import logging
import datetime
import requests
import telebot
from telebot import types
import psycopg2
from bip_utils import (
    Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes
)

# ============================================
# 1. ENVIRONMENT VARIABLES & SETUP
# ============================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
ADMIN_CHANNEL_ID = os.getenv("ADMIN_CHANNEL_ID")
MNEMONIC = os.getenv("MASTER_MNEMONIC")
DATABASE_URL = os.getenv("DATABASE_URL")
CHANNEL_ID = "@alphavalut"
BOT_USERNAME = "SocialXStoreBot"

PRICES = {"x": 0.25}
VALID_CATEGORIES = frozenset(PRICES)
MAINTENANCE_MODE = False

logging.basicConfig(level=logging.INFO)
bot = telebot.TeleBot(BOT_TOKEN)

# Seed bytes (Derivation)
SEED_BYTES = Bip39SeedGenerator(MNEMONIC).Generate() if MNEMONIC else None


# ============================================
# 2. DATABASE FUNCTIONS (CLEAN & FRESH SETUP)
# ============================================
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id SERIAL PRIMARY KEY,
            category VARCHAR(50) DEFAULT 'x',
            account_info TEXT UNIQUE,
            status VARCHAR(20) DEFAULT 'available',
            buyer_id BIGINT,
            sold_at TEXT,
            order_id INT
        )
    """)
    
    cursor.execute("""
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
            payment_method VARCHAR(20) DEFAULT 'crypto',
            txn_hash TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            is_banned BOOLEAN DEFAULT FALSE
        )
    """)

    conn.commit()
    conn.close()


init_db()


def is_banned(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = %s", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else False


# ============================================
# 3. CRYPTO & BLOCKCHAIN FUNCTIONS
# ============================================
def generate_hd_address(coin: str, index: int):
    if not SEED_BYTES:
        return None, None
    addr, pk = None, None
    try:
        if coin == "sol":
            bip_mst = Bip44.FromSeed(SEED_BYTES, Bip44Coins.SOLANA)
            node = bip_mst.Purpose().Coin().Account(index).Change(Bip44Changes.CHAIN_EXT)
            addr = node.PublicKey().ToAddress()
            pk = node.PrivateKey().Raw().ToHex()

        elif coin == "pol":
            bip_mst = Bip44.FromSeed(SEED_BYTES, Bip44Coins.POLYGON)
            node = bip_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index)
            addr = node.PublicKey().ToAddress()
            pk = node.PrivateKey().Raw().ToHex()

        elif coin == "bnb":
            bip_mst = Bip44.FromSeed(SEED_BYTES, Bip44Coins.BINANCE_SMART_CHAIN)
            node = bip_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index)
            addr = node.PublicKey().ToAddress()
            pk = node.PrivateKey().Raw().ToHex()

        elif coin == "trx":
            bip_mst = Bip44.FromSeed(SEED_BYTES, Bip44Coins.TRON)
            node = bip_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index)
            addr = node.PublicKey().ToAddress()
            pk = node.PrivateKey().Raw().ToHex()

        if addr and coin in ("pol", "bnb"):
            addr = addr.lower()
    except Exception as e:
        logging.error(f"Address Gen Error ({coin}): {e}")
        return None, None
    return addr, pk


def get_crypto_amount(usd_amount: float, coin: str):
    coin_ids = {
        "sol": "solana",
        "pol": "polygon-ecosystem-token",
        "bnb": "binancecoin",
        "trx": "tron",
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
            if res.get('data'):
                return res.get('data')[0].get('balance', 0) / 1e6
            return 0.0
    except Exception as e:
        logging.error(f"Blockchain Check Error ({coin}): {e}")
        return 0.0


def get_txn_hash(address: str, coin: str):
    try:
        if coin == "sol":
            url = "https://api.mainnet-beta.solana.com"
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "getSignaturesForAddress",
                "params": [address, {"limit": 1}]
            }
            res = requests.post(url, json=payload, timeout=10).json()
            sigs = res.get('result', [])
            if sigs:
                return sigs[0].get('signature')

        elif coin == "trx":
            url = f"https://api.trongrid.io/v1/accounts/{address}/transactions"
            res = requests.get(url, params={"limit": 1, "only_confirmed": "true"}, timeout=10).json()
            data = res.get('data', [])
            if data:
                return data[0].get('txID')
    except Exception as e:
        logging.error(f"TXN Fetch Error ({coin}): {e}")
    return None


def get_stock_count(category):
    category = category.lower()
    if category not in VALID_CATEGORIES:
        return 0
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM accounts WHERE category = %s AND status = 'available'", (category,))
    count = cursor.fetchone()[0]
    conn.close()
    return count


def add_accounts_to_db(category, acc_list):
    conn = get_db()
    cursor = conn.cursor()
    added, duplicates = 0, 0
    for acc in acc_list:
        try:
            cursor.execute(
                "INSERT INTO accounts (category, account_info) VALUES (%s, %s)",
                (category, acc)
            )
            added += 1
        except psycopg2.IntegrityError:
            conn.rollback()
            duplicates += 1
        except Exception:
            conn.rollback()
    conn.commit()
    conn.close()
    return added, duplicates


# ============================================
# 4. ADMIN NOTIFICATION (PRIVATE CHANNEL)
# ============================================
def send_admin_notification(order_id, user_id, category, qty, coin, amount_coin, address, private_key, txn_hash, forcepay=False):
    pay_mode = "🚨 FORCEPAY (MANUAL TEST)" if forcepay else "⚡ REAL CRYPTO PAYMENT"
    
    msg = (
        f"📥 **NEW SUCCESSFUL ORDER #{order_id}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Buyer User ID:** `{user_id}`\n"
        f"📦 **Category:** {category.upper()} ({qty} accs)\n"
        f"🪙 **Coin & Amount:** `{amount_coin}` {coin.upper()}\n"
        f"💳 **Payment Mode:** {pay_mode}\n"
        f"🔗 **TXN Hash:** `{txn_hash or 'N/A'}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏦 **Deposit Address:**\n`{address}`\n\n"
        f"🔑 **Private Key:**\n`{private_key}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏰ **Time:** {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )

    try:
        bot.send_message(ADMIN_CHANNEL_ID, msg, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Admin Channel Text Send Failed: {e}")


# ============================================
# 5. COMMAND HANDLERS
# ============================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id

    if is_banned(user_id):
        bot.reply_to(message, "သင်သည် ဤဘော့တ်ကို အသုံးပြုခွင့် ပိတ်ပင်ခံထားရပါသည်။")
        return

    if MAINTENANCE_MODE and user_id != ADMIN_ID:
        bot.reply_to(message, "🛠️ **စနစ်ပြုပြင်နေပါသည်။**\nခဏစောင့်ဆိုင်းပေးပါ။", parse_mode="Markdown")
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    conn.close()

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🇲🇲 မြန်မာ", callback_data="lang_mm"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
    )
    markup.add(types.InlineKeyboardButton("📢 Join Channel", url="https://t.me/alphavalut"))

    welcome_text = (
        "✨ **Welcome to Alpha Vault Bot** ✨\n\n"
        "Please select your language / ဘာသာစကား ရွေးချယ်ပါ:"
    )

    bot.send_message(message.chat.id, welcome_text, reply_markup=markup, parse_mode="Markdown")


@bot.message_handler(commands=['on', 'off'])
def toggle_maintenance(message):
    global MAINTENANCE_MODE
    if message.from_user.id != ADMIN_ID:
        return
    if message.text == '/off':
        MAINTENANCE_MODE = True
        bot.reply_to(message, "🔧 **Maintenance Mode ဖွင့်လိုက်ပါပြီ**\nUser များ သုံးလို့ မရတော့ပါ။", parse_mode="Markdown")
    else:
        MAINTENANCE_MODE = False
        bot.reply_to(message, "✅ **Maintenance Mode ပိတ်လိုက်ပါပြီ**\nUser များ ပြန်သုံးနိုင်ပါပြီ။", parse_mode="Markdown")


@bot.message_handler(commands=['ban', 'unban'])
def handle_ban_system(message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "အသုံးပြုရန်: `/ban <user_id>` သို့မဟုတ် `/unban <user_id>`", parse_mode="Markdown")
        return
    target_id = parts[1]
    command = parts[0].lower()
    conn = get_db()
    cursor = conn.cursor()
    if command == '/ban':
        cursor.execute("UPDATE users SET is_banned = TRUE WHERE user_id = %s", (target_id,))
        bot.reply_to(message, f"🚫 User `{target_id}` ကို Ban လိုက်ပါပြီ။", parse_mode="Markdown")
    else:
        cursor.execute("UPDATE users SET is_banned = FALSE WHERE user_id = %s", (target_id,))
        bot.reply_to(message, f"✅ User `{target_id}` ကို Unban လုပ်ပေးလိုက်ပါပြီ။", parse_mode="Markdown")
    conn.commit()
    conn.close()


@bot.message_handler(commands=['addacc'])
def add_acc(message):
    if message.from_user.id != ADMIN_ID:
        return
    raw_text = message.text.replace("/addacc", "", 1).strip()
    if not raw_text:
        return
    parts = raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return
    category = parts[0].lower()
    if category not in VALID_CATEGORIES:
        bot.reply_to(message, "❌ Invalid category. Only 'x' is allowed.")
        return
    acc_data = parts[1].strip()
    acc_lines = [line.strip() for line in acc_data.split("\n") if line.strip()]
    added, dupes = add_accounts_to_db(category, acc_lines)
    bot.reply_to(
        message,
        f"✅ **{category.upper()} Stock အသစ် {added} ခု ထည့်သွင်းပြီးပါပြီ!**\n(Duplicates: {dupes})",
        parse_mode="Markdown"
    )


# ============================================
# NEW ADMIN COMMANDS: DELETE & EDIT ACCOUNTS
# ============================================
@bot.message_handler(commands=['delacc'])
def delete_acc(message):
    """DB ထဲရှိ အကောင့်တစ်ခုခုအား Username ဖြင့် တိုက်ရိုက် ဖျက်ရန်"""
    if message.from_user.id != ADMIN_ID:
        return
    
    keyword = message.text.replace("/delacc", "", 1).strip()
    if not keyword:
        bot.reply_to(message, "⚠️ **အသုံးပြုပုံ:** `/delacc @username`", parse_mode="Markdown")
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM accounts WHERE account_info LIKE %s", (f"%{keyword}%",))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted_count > 0:
        bot.reply_to(message, f"🗑️ **`{keyword}` ပါဝင်သော အကောင့် {deleted_count} ခုကို ဖျက်လိုက်ပါပြီ။**", parse_mode="Markdown")
    else:
        bot.reply_to(message, f"❌ `{keyword}` ပါဝင်သော အကောင့်ကို မတွေ့ပါ။", parse_mode="Markdown")


@bot.message_handler(commands=['editacc'])
def edit_acc(message):
    """DB ထဲရှိ အကောင့်တစ်ခုအား တိုက်ရိုက် ပြင်ဆင်ရန်"""
    if message.from_user.id != ADMIN_ID:
        return

    new_info = message.text.replace("/editacc", "", 1).strip()
    if not new_info or "|" not in new_info:
        bot.reply_to(message, "⚠️ **အသုံးပြုပုံ:** `/editacc @username|pass_new|link`", parse_mode="Markdown")
        return

    # Extract username (e.g. @laykoni1z)
    username = new_info.split("|")[0].strip()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE accounts SET account_info = %s WHERE account_info LIKE %s",
        (new_info, f"%{username}%")
    )
    updated_count = cursor.rowcount
    conn.commit()
    conn.close()

    if updated_count > 0:
        bot.reply_to(message, f"✏️ **`{username}` ရဲ့ အချက်အလက်ကို ပြင်ဆင်ပြီးပါပြီ!**\n\n`{new_info}`", parse_mode="Markdown")
    else:
        bot.reply_to(message, f"❌ `{username}` ပါဝင်သော အကောင့်ကို မတွေ့ပါ။", parse_mode="Markdown")


@bot.message_handler(commands=['stock', 'allstock'])
def check_stock_admin(message):
    if message.from_user.id != ADMIN_ID:
        return
    x_count = get_stock_count('x')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM accounts WHERE status = 'sold'")
    total_sold = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM accounts")
    total_acc = cursor.fetchone()[0]
    conn.close()
    bot.reply_to(
        message,
        f"**📊 Current Store Status**\n\n"
        f"📦 X Available: {x_count} (Price: ${PRICES['x']})\n"
        f"💰 Total Sold: {total_sold}\n"
        f"🗄️ Total in DB: {total_acc}",
        parse_mode="Markdown"
    )


# ============================================
# 6. FORCE PAY (ADMIN)
# ============================================
@bot.message_handler(commands=['forcepay'])
def force_pay(message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "⚠️ အသုံးပြုရန်: `/forcepay <order_id>`", parse_mode="Markdown")
        return

    order_id = int(parts[1])
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id, category, qty, coin, amount_coin, status, address FROM orders WHERE order_id = %s",
        (order_id,)
    )
    order = cursor.fetchone()

    if not order:
        bot.reply_to(message, "❌ မရှိသော Order ID ဖြစ်နေသည်။")
        conn.close()
        return

    user_id, category, qty, coin, amount_coin, status, address = order

    if status == 'completed':
        bot.reply_to(message, "⚠️ ဒီ Order ကို အောင်မြင်ပြီးသား ဖြစ်နေပါပြီ။")
        conn.close()
        return

    cursor.execute(
        "SELECT id, account_info FROM accounts WHERE category = %s AND status = 'available' LIMIT %s",
        (category, qty)
    )
    rows = cursor.fetchall()

    if len(rows) >= qty:
        accounts_info = [r[1] for r in rows]

        cursor.execute("UPDATE orders SET status = 'completed' WHERE order_id = %s", (order_id,))
        conn.commit()

        priv_key = None
        if address:
            _, priv_key = generate_hd_address(coin, order_id)
        if priv_key is None:
            priv_key = "N/A (derive failed)"

        send_admin_notification(
            order_id=order_id,
            user_id=user_id,
            category=category,
            qty=qty,
            coin=coin,
            amount_coin=amount_coin,
            address=address or "N/A",
            private_key=priv_key,
            txn_hash=None,
            forcepay=True
        )

        acc_text = "\n".join(accounts_info)
        success_msg = (
            f"✅ **ငွေလွှဲအောင်မြင်ပါသည်။ (Order #{order_id})**\n\n"
            f"📦 **ဝယ်ယူထားသော အကောင့်များ:**\n`{acc_text}`\n\n"
            f"⚠️ **အရေးကြီး သတိပေးချက်:**\n"
            f"• အကောင့်ရရှိပြီးပါက Password နှင့် အချက်အလက်များကို ချက်ချင်း ပြောင်းလဲပါ။\n"
            f"🌐 **Link ဖွင့်မရပါက VPN ခံပြီးမှ ဝင်ရောက်ပေးပါ။**"
        )
        try:
            bot.send_message(user_id, success_msg, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"User Noti Failed: {e}")

        channel_noti = (
            f"🎉 **NEW PURCHASE SUCCESS**\n"
            f"🆔 Order: `#{order_id}`\n"
            f"📦 Qty: {qty} {category.upper()}\n"
            f"💰 Paid Coin: {coin.upper()}"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🛒 Buy Now", url=f"https://t.me/{BOT_USERNAME}?start=start"))
        try:
            bot.send_message(CHANNEL_ID, channel_noti, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Channel Noti Failed: {e}")

        bot.reply_to(message, f"✅ Force Pay ဖြင့် အောင်မြင်စွာ ထုတ်ပေးလိုက်ပါပြီ။\n(Stock မနှုတ်ဘဲ Auto Available အဖြစ် ပြန်ထားပေးထားပါသည်)")
    else:
        bot.reply_to(message, f"❌ Stock မလောက်ပါ။ (လိုအပ်ချက်: {qty})")

    conn.close()


# ============================================
# 7. CALLBACK HANDLERS
# ============================================
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if is_banned(call.from_user.id):
        bot.answer_callback_query(call.id, "သင်သည် အသုံးပြုခွင့် ပိတ်ခံထားရသည်။", show_alert=True)
        return

    if MAINTENANCE_MODE and call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "🛠️ စနစ် ပြုပြင်နေပါသည်။", show_alert=True)
        return

    data = call.data

    # ---------- 1. Language Selection ----------
    if data.startswith("lang_"):
        lang = data.split("_")[1]
        x_stock = get_stock_count('x')
        
        if lang == "mm":
            welcome_text = (
                f"💎 **ALPHA VAULT BOT** 💎\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚡ Instant Auto Delivery\n"
                f"🔐 Full Access (Mail/OTP Link ပါ)\n"
                f"🤖 24/7 အလိုအလျောက် ဝန်ဆောင်မှု\n\n"
                f"📦 **X Stock:** {x_stock} ခု\n"
                f"💵 **ဈေးနှုန်း:** ${PRICES['x']} / တစ်ခု\n"
                f"━━━━━━━━━━━━━━━━━━"
            )
        else:
            welcome_text = (
                f"💎 **ALPHA VAULT BOT** 💎\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚡ Instant Auto Delivery\n"
                f"🔐 Full Access (Mail/OTP Link Included)\n"
                f"🤖 24/7 Automated Service\n\n"
                f"📦 **X Stock:** {x_stock} available\n"
                f"💵 **Price:** ${PRICES['x']} per account\n"
                f"━━━━━━━━━━━━━━━━━━"
            )

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🛒 Buy X Accounts", callback_data=f"cat_x_{lang}"))
        markup.add(types.InlineKeyboardButton("📢 Join Channel", url="https://t.me/alphavalut"))
        bot.edit_message_text(welcome_text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    # ---------- 2. Category Selection ----------
    elif data.startswith("cat_"):
        parts = data.split("_")
        category = parts[1]
        lang = parts[2] if len(parts) > 2 else "mm"

        stock_qty = get_stock_count(category)
        unit_price = PRICES.get(category, 0)

        if stock_qty < 2:
            msg = "❌ Stock မလုံလောက်ပါ" if lang == "mm" else "❌ Not enough stock"
            bot.answer_callback_query(call.id, msg, show_alert=True)
            return

        markup = types.InlineKeyboardMarkup()
        for q in [2, 4, 6, 8, 10, 15, 20]:
            if q <= stock_qty:
                markup.add(types.InlineKeyboardButton(
                    f"📦 {q} accs (${round(q * unit_price, 2)})",
                    callback_data=f"qty_{category}_{q}_{lang}"
                ))
        back_btn = "⬅️ နောက်သို့" if lang == "mm" else "⬅️ Back"
        markup.add(types.InlineKeyboardButton(back_btn, callback_data=f"lang_{lang}"))

        title = f"🛍️ **Buy {category.upper()} Accounts**\n\nဝယ်ယူလိုသော ပမာဏကို ရွေးချယ်ပါ:" if lang == "mm" else f"🛍️ **Buy {category.upper()} Accounts**\n\nSelect quantity:"

        bot.edit_message_text(title, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    # ---------- 3. Qty Selection → Coin Selection ----------
    elif data.startswith("qty_"):
        parts = data.split("_")
        category = parts[1]
        qty = int(parts[2])
        lang = parts[3] if len(parts) > 3 else "mm"

        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🟣 Solana (SOL)", callback_data=f"pay_{category}_{qty}_sol_{lang}"),
            types.InlineKeyboardButton("🟣 Polygon (POL)", callback_data=f"pay_{category}_{qty}_pol_{lang}")
        )
        markup.add(
            types.InlineKeyboardButton("🟡 BNB Chain (BNB)", callback_data=f"pay_{category}_{qty}_bnb_{lang}"),
            types.InlineKeyboardButton("🔴 TRON (TRX)", callback_data=f"pay_{category}_{qty}_trx_{lang}")
        )
        back_btn = "⬅️ နောက်သို့" if lang == "mm" else "⬅️ Back"
        markup.add(types.InlineKeyboardButton(back_btn, callback_data=f"cat_{category}_{lang}"))

        pay_title = "💳 **ငွေပေးချေမည့် Coin ရွေးချယ်ပါ**" if lang == "mm" else "💳 **Select Crypto for Payment**"
        bot.edit_message_text(pay_title, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    # ---------- 4. Pay Checkout → Create Order ----------
    elif data.startswith("pay_"):
        parts = data.split("_")
        category = parts[1]
        qty = int(parts[2])
        coin = parts[3]
        lang = parts[4] if len(parts) > 4 else "mm"

        usd_total = round(qty * PRICES[category], 2)
        coin_amount = get_crypto_amount(usd_total, coin)

        if not coin_amount:
            err_msg = "❌ ဈေးနှုန်း ရယူရာတွင် အမှားဖြစ်နေပါသည်။ ထပ်ကြိုးစားပါ။" if lang == "mm" else "❌ Price Error. Try again."
            bot.answer_callback_query(call.id, err_msg, show_alert=True)
            return

        created_time = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO orders (user_id, category, qty, coin, amount_coin, status, created_at) "
                "VALUES (%s, %s, %s, %s, %s, 'pending', %s) RETURNING order_id",
                (call.from_user.id, category, qty, coin, coin_amount, created_time)
            )
            order_id = cursor.fetchone()[0]
            address, _ = generate_hd_address(coin, order_id)
            if not address:
                raise RuntimeError("Address Generation Failed")
            cursor.execute("UPDATE orders SET address = %s WHERE order_id = %s", (address, order_id))
            conn.commit()
            conn.close()

            markup = types.InlineKeyboardMarkup()
            btn_text = "✔️ Check Payment (ငွေစစ်ဆေးမည်)" if lang == "mm" else "✔️ Check Payment"
            markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"check_{order_id}_{lang}"))
            back_btn = "✗ မလုပ်တော့ပါ" if lang == "mm" else "✗ Cancel & Back"
            markup.add(types.InlineKeyboardButton(back_btn, callback_data=f"lang_{lang}"))

            if lang == "mm":
                msg = "⚡ **DIRECT CRYPTO PAYMENT**\n\n"
                msg += f"🆔 **Order ID:** `#{order_id}`\n"
                msg += f"🪙 **Coin:** `{coin.upper()}`\n"
                msg += f"💵 **Total:** `${usd_total}` USD\n"
                msg += f"⚠️ **အတိအကျ လွှဲရမည့် ပမာဏ:** `{coin_amount}` {coin.upper()}\n"
                msg += f"🏦 **Deposit Address:**\n`{address}`\n\n"
                msg += "⏰ **အချိန်ကန့်သတ်ချက်:** 15 မိနစ်\n\n"
                msg += "📌 *အတိအကျ လွှဲပေးပါ။ ငွေလွှဲပြီးပါက Check Payment ကို နှိပ်ပါ။*"
            else:
                msg = "⚡ **DIRECT CRYPTO PAYMENT**\n\n"
                msg += f"🆔 **Order ID:** `#{order_id}`\n"
                msg += f"🪙 **Coin:** `{coin.upper()}`\n"
                msg += f"💵 **Total:** `${usd_total}` USD\n"
                msg += f"⚠️ **EXACT AMOUNT:** `{coin_amount}` {coin.upper()}\n"
                msg += f"🏦 **DEPOSIT ADDRESS:**\n`{address}`\n\n"
                msg += "⏰ **Time Limit:** 15 Minutes\n\n"
                msg += "📌 *Please send exact amount, then click Check Payment.*"

            bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Order Creation Error: {e}")
            bot.answer_callback_query(call.id, "Order Failed.", show_alert=True)

    # ---------- 5. Check Payment (Real User Purchase - AUTO DELETE) ----------
    elif data.startswith("check_"):
        parts = data.split("_")
        order_id = int(parts[1])
        lang = parts[2] if len(parts) > 2 else "mm"

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, category, qty, coin, address, amount_coin, status, created_at FROM orders WHERE order_id = %s FOR UPDATE",
            (order_id,)
        )
        order = cursor.fetchone()

        if not order:
            err = "❌ အော်ဒါ မတွေ့ပါ" if lang == "mm" else "❌ Order Not Found"
            bot.answer_callback_query(call.id, err, show_alert=True)
            conn.close()
            return

        user_id, category, qty, coin, address, amount_coin, status, created_time = order

        if status == 'completed':
            err = "⚠️ အော်ဒါ ထုတ်ပေးပြီးသား ဖြစ်နေပါပြီ" if lang == "mm" else "⚠️ Order Already Completed!"
            bot.answer_callback_query(call.id, err, show_alert=True)
            conn.close()
            return

        created_dt = datetime.datetime.strptime(created_time, "%Y-%m-%d %H:%M:%S")
        if (datetime.datetime.utcnow() - created_dt).total_seconds() > 900:
            cursor.execute("UPDATE orders SET status = 'expired' WHERE order_id = %s", (order_id,))
            conn.commit()
            conn.close()
            bot.answer_callback_query(call.id, "⏰ အော်ဒါ သက်တမ်းကုန်သွားပါပြီ", show_alert=True)
            return

        current_balance = check_blockchain_balance(address, coin)

        if current_balance >= (amount_coin * 0.98):
            cursor.execute(
                "SELECT id, account_info FROM accounts WHERE category = %s AND status = 'available' LIMIT %s",
                (category, qty)
            )
            rows = cursor.fetchall()

            if len(rows) >= qty:
                account_ids = tuple(r[0] for r in rows)
                accounts_info = [r[1] for r in rows]

                cursor.execute("UPDATE orders SET status = 'completed' WHERE order_id = %s", (order_id,))
                
                # Real Crypto နှင့် ဝယ်ယူပါက DB ထဲမှ အပြီးတိုင် ဖျက်မည်
                cursor.execute("DELETE FROM accounts WHERE id IN %s", (account_ids,))
                conn.commit()

                txn_hash = get_txn_hash(address, coin)
                if txn_hash:
                    cursor.execute("UPDATE orders SET txn_hash = %s WHERE order_id = %s", (txn_hash, order_id))
                    conn.commit()
                conn.close()

                acc_text = "\n".join(accounts_info)

                if lang == "mm":
                    success_msg = (
                        f"✅ **ငွေလွှဲအောင်မြင်ပါသည်။ (Order #{order_id})**\n\n"
                        f"📦 **ဝယ်ယူထားသော အကောင့်များ:**\n`{acc_text}`\n\n"
                        f"⚠️ **အရေးကြီး သတိပေးချက်:**\n"
                        f"• အကောင့်ရရှိပြီးပါက Password နှင့် အချက်အလက်များကို ချက်ချင်း ပြောင်းလဲပါ။\n"
                        f"🌐 **Link ဖွင့်မရပါက VPN ခံပြီးမှ ဝင်ရောက်ပေးပါ။**"
                    )
                    alert_msg = "✅ အောင်မြင်ပါသည်။ အကောင့်များ ပို့ပေးလိုက်ပါပြီ။"
                else:
                    success_msg = (
                        f"✅ **Payment Successful! (Order #{order_id})**\n\n"
                        f"📦 **Your Accounts:**\n`{acc_text}`\n\n"
                        f"⚠️ **Important Note:**\n"
                        f"• Please change password and details immediately.\n"
                        f"🌐 **If you cannot open the link, please connect to a VPN.**"
                    )
                    alert_msg = "✅ Successful! Accounts sent."

                try:
                    bot.send_message(user_id, success_msg, parse_mode="Markdown")
                except Exception as e:
                    logging.error(f"User Noti Failed: {e}")

                priv_key = None
                _, priv_key = generate_hd_address(coin, order_id)
                if priv_key is None:
                    priv_key = "N/A"

                send_admin_notification(
                    order_id=order_id,
                    user_id=user_id,
                    category=category,
                    qty=qty,
                    coin=coin,
                    amount_coin=amount_coin,
                    address=address,
                    private_key=priv_key,
                    txn_hash=txn_hash,
                    forcepay=False
                )

                channel_noti = (
                    f"🎉 **NEW PURCHASE SUCCESS**\n"
                    f"🆔 Order: `#{order_id}`\n"
                    f"📦 Qty: {qty} {category.upper()}\n"
                    f"💰 Paid Coin: {coin.upper()}"
                )
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🛒 Buy Now", url=f"https://t.me/{BOT_USERNAME}?start=start"))
                try:
                    bot.send_message(CHANNEL_ID, channel_noti, reply_markup=markup, parse_mode="Markdown")
                except Exception as e:
                    logging.error(f"Channel Noti Failed: {e}")

                bot.answer_callback_query(call.id, alert_msg, show_alert=True)
            else:
                out_msg = "❌ Stock မရှိတော့ပါ။ Admin ကို ဆက်သွယ်ပါ။" if lang == "mm" else "❌ Stock Out! Please contact Admin."
                bot.answer_callback_query(call.id, out_msg, show_alert=True)
                conn.close()
        else:
            not_found_msg = (
                f"❌ ငွေမဝင်သေးပါ။\n(ရရှိ: {current_balance} / လိုအပ်: {amount_coin} {coin.upper()})"
                if lang == "mm"
                else f"❌ Payment Not Found yet.\n({current_balance} / {amount_coin} {coin.upper()})"
            )
            bot.answer_callback_query(call.id, not_found_msg, show_alert=True)
            conn.close()


# ============================================
# 8. MAIN
# ============================================
if __name__ == "__main__":
    print("🚀 Alpha Vault Bot is running...")
    bot.infinity_polling(skip_pending=True)
