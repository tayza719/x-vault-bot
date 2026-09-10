import os
import logging
import datetime
import requests
import telebot
from telebot import types
import psycopg2
from psycopg2 import IntegrityError
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes

# ============================================
# 1. Environment Variables & Setup
# ============================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
ADMIN_CHANNEL_ID = os.getenv("ADMIN_CHANNEL_ID")  # ✅ ဒါကို Private Channel ID ထည့်ရပါမယ်
MNEMONIC = os.getenv("MASTER_MNEMONIC")
DATABASE_URL = os.getenv("DATABASE_URL")
CHANNEL_ID = "@alphavalut"
BOT_USERNAME = "SocialXStoreBot"

# ✅ Outlook ဖြုတ်ပြီး X တစ်ခုထဲပဲ ထားပါတယ်
PRICES = {"x": 0.15}
VALID_CATEGORIES = frozenset(PRICES)
MAINTENANCE_MODE = False

logging.basicConfig(level=logging.INFO)
bot = telebot.TeleBot(BOT_TOKEN)

SEED_BYTES = Bip39SeedGenerator(MNEMONIC).Generate() if MNEMONIC else None

# ============================================
# 2. Database Functions
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
            order_id INT,
            forcepay_test BOOLEAN DEFAULT FALSE
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
            payment_method VARCHAR(20) DEFAULT 'crypto'
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
# 3. Crypto & Blockchain Functions
# ============================================
def generate_hd_address(coin: str, index: int) -> str:
    if not SEED_BYTES:
        return None
    addr = None
    if coin == "sol":
        bip_mst = Bip44.FromSeed(SEED_BYTES, Bip44Coins.SOLANA)
        addr = bip_mst.Purpose().Coin().Account(index).Change(Bip44Changes.CHAIN_EXT).PublicKey().ToAddress()
    elif coin == "pol":
        bip_mst = Bip44.FromSeed(SEED_BYTES, Bip44Coins.POLYGON)
        addr = bip_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
        if addr: addr = addr.lower()
    elif coin == "bnb":
        bip_mst = Bip44.FromSeed(SEED_BYTES, Bip44Coins.BINANCE_SMART_CHAIN)
        addr = bip_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
        if addr: addr = addr.lower()
    elif coin == "trx":
        bip_mst = Bip44.FromSeed(SEED_BYTES, Bip44Coins.TRON)
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
            if res.get('data'):
                return res.get('data')[0].get('balance', 0) / 1e6
        return 0.0
    except Exception as e:
        logging.error(f"Blockchain Check Error ({coin}): {e}")
        return 0.0

def get_stock_count(category):
    category = category.lower()
    if category not in VALID_CATEGORIES: return 0
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
            cursor.execute("INSERT INTO accounts (category, account_info) VALUES (%s, %s)", (category, acc))
            added += 1
        except IntegrityError:
            conn.rollback()
            duplicates += 1
        except Exception:
            conn.rollback()
    conn.commit()
    conn.close()
    return added, duplicates

# ============================================
# 4. Command Handlers
# ============================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        bot.reply_to(message, "သင်သည် ဤဘော့တ်ကို အသုံးပြုခွင့် ပိတ်ပင်ခံထားရပါသည်။")
        return
    if MAINTENANCE_MODE and user_id != ADMIN_ID:
        bot.reply_to(message, "**စနစ်ပြုပြင်နေပါသည်။** ခေတ္တခဏ စောင့်ဆိုင်းပေးပါခင်ဗျာ။", parse_mode="Markdown")
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
    bot.send_message(message.chat.id, "**Please select your language / ဘာသာစကား ရွေးချယ်ပါ**", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(commands=['on', 'off'])
def toggle_maintenance(message):
    global MAINTENANCE_MODE
    if message.from_user.id != ADMIN_ID: return
    if message.text == '/off':
        MAINTENANCE_MODE = True
        bot.reply_to(message, "🔴 **Maintenance Mode ဖွင့်လိုက်ပါပြီ** User များ သုံး၍မရတော့ပါ။")
    else:
        MAINTENANCE_MODE = False
        bot.reply_to(message, "🟢 **Maintenance Mode ပိတ်လိုက်ပါပြီ** User များ ပြန်သုံးနိုင်ပါပြီ။")

@bot.message_handler(commands=['ban', 'unban'])
def handle_ban_system(message):
    if message.from_user.id != ADMIN_ID: return
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
    if message.from_user.id != ADMIN_ID: return
    raw_text = message.text.replace("/addacc", "").strip()
    if not raw_text: return
    parts = raw_text.split(maxsplit=1)
    if len(parts) < 2: return
    category = parts[0].lower()
    if category not in VALID_CATEGORIES:
        bot.reply_to(message, "❌ Invalid category. Only 'x' is allowed.")
        return
    acc_data = parts[1].strip()
    acc_lines = [line.strip() for line in acc_data.split("\n") if line.strip()]
    added, dupes = add_accounts_to_db(category, acc_lines)
    bot.reply_to(message, f"✅ **{category.upper()} Stock အသစ် {added} ကောင့် ထည့်သွင်းပြီးပါပြီ!**\n(Duplicates: {dupes})", parse_mode="Markdown")

@bot.message_handler(commands=['stock', 'allstock'])
def check_stock_admin(message):
    if message.from_user.id != ADMIN_ID: return
    x_count = get_stock_count('x')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM accounts WHERE status = 'sold'")
    total_sold = cursor.fetchone()[0]
    conn.close()
    bot.reply_to(message, f"**Current Store Status**\n\n✦ X Available: {x_count} (Price: ${PRICES['x']})\n✦ Total Sold: {total_sold}", parse_mode="Markdown")

# ============================================
# 5. Force Pay (Admin Channel ကို ပြန်ပို့ပါပြီ)
# ============================================
@bot.message_handler(commands=['forcepay'])
def force_pay(message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "⚠️ အသုံးပြုရန်: `/forcepay <order_id>`", parse_mode="Markdown")
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

    cursor.execute("SELECT id, account_info FROM accounts WHERE category = %s AND status = 'available' LIMIT %s", (category, qty))
    rows = cursor.fetchall()

    if len(rows) >= qty:
        account_ids = tuple(r[0] for r in rows)
        accounts_info = [r[1] for r in rows]
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("UPDATE accounts SET status = 'sold', buyer_id = %s, sold_at = %s, order_id = %s WHERE id IN %s", (user_id, now_str, order_id, account_ids))
        cursor.execute("UPDATE orders SET status = 'completed' WHERE order_id = %s", (order_id,))

        # ✅ Auto Replenish (SS ထဲက အတိုင်း ပြန်ဖြည့်ပါ)
        cursor.execute("""
            UPDATE accounts 
            SET status = 'available', buyer_id = NULL, sold_at = NULL, order_id = NULL, forcepay_test = FALSE 
            WHERE id IN (
                SELECT id FROM accounts 
                WHERE category = %s AND status = 'sold' 
                LIMIT %s
            )
        """, (category, qty))

        conn.commit()

        acc_text = "\n".join(accounts_info)
        success_msg = (
            f"✅ **Payment Successful!**\n\n"
            f"**Your Accounts:**\n`{acc_text}`\n\n"
            f"⚠️ ကျေးဇူးပြု၍ Password ချက်ချင်းပြောင်းပါ။"
        )
        try:
            bot.send_message(user_id, success_msg, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"User Noti Failed: {e}")

        # 1. Private Channel သို့ File ပို့ခြင်း (ADMIN_CHANNEL_ID ကို ပြန်သုံးပါပြီ)
        file_path = f"sold_order_{order_id}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(acc_text)

        with open(file_path, "rb") as f:
            try:
                bot.send_document(ADMIN_CHANNEL_ID, f, caption=f"🧰 **ADMIN FORCEPAY ALERT**\n👤 Buyer User ID: `{user_id}`\n🆔 Order ID: `#{order_id}`\n📦 Category: {category.upper()} ({qty} accs)\n💰 Amount Received: MANUAL\n📍 Address: ADMIN_FORCE_PAY", parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Admin Channel File Send Failed: {e}")
        os.remove(file_path)

        # 2. Public Channel သို့ Noti ပို့ခြင်း
        channel_noti = f"🧧 **NEW PURCHASE SUCCESS**\n🆔 Order: `#{order_id}`\n📦 Qty: {qty} {category.upper()}\n🪙 Paid Coin: {coin.upper()}"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🛒 Buy Now / ဝယ်ယူရန်", url=f"https://t.me/{BOT_USERNAME}?start=start"))
        try:
            bot.send_message(CHANNEL_ID, channel_noti, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Channel Purchase Noti Failed: {e}")

        # Admin ကို ပြန်ကြားခြင်း
        bot.reply_to(message, f"✅ Order #{order_id} ကို Force Pay ဖြင့် အောင်မြင်စွာ ထုတ်ပေးလိုက်ပါပြီ။\n♻️ အကောင့်များကို Stock ထဲသို့ အလိုအလျောက် ပြန်ထည့်ပေးလိုက်ပါပြီ။")
    else:
        bot.reply_to(message, f"❌ Stock မလောက်ပါ။ (လိုအပ်ချက်: {qty})")
    conn.close()

# ============================================
# 6. Callback Handlers (X တစ်ခုထဲပဲ ပြပါမည်)
# ============================================
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if is_banned(call.from_user.id):
        bot.answer_callback_query(call.id, "သင်သည် အသုံးပြုခွင့် ပိတ်ခံထားရသည်။", show_alert=True)
        return

    if MAINTENANCE_MODE and call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "စနစ် ပြုပြင်နေပါသည်။", show_alert=True)
        return

    data = call.data

    # 1. Language Selection (X တစ်ခုတည်း ပြပါမည်)
    if data.startswith("lang_"):
        lang = data.split("_")[1]
        x_stock = get_stock_count('x')

        if lang == "mm":
            welcome_text = f"**X Stock: {x_stock} (Price: ${PRICES['x']})**"
        else:
            welcome_text = f"**X Stock: {x_stock} (Price: ${PRICES['x']})**"

        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🐦 X  Accounts", callback_data=f"cat_x_{lang}")
        )
        markup.add(types.InlineKeyboardButton("📢 Join Channel", url="https://t.me/alphavalut"))
        bot.edit_message_text(welcome_text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    # 2. Category Selection -> Qty (X တစ်ခုတည်း)
    elif data.startswith("cat_"):
        parts = data.split("_")
        category = parts[1]
        lang = parts[2] if len(parts) > 2 else "mm"

        stock_qty = get_stock_count(category)
        unit_price = PRICES.get(category, 0)

        if stock_qty < 2:
            msg = "❌ Stock မလောက်ပါ" if lang == "mm" else "❌ Stock not enough"
            bot.answer_callback_query(call.id, msg, show_alert=True)
            return

        markup = types.InlineKeyboardMarkup()
        for q in [2, 4, 6, 8, 10, 15, 20]:
            if q <= stock_qty:
                markup.add(types.InlineKeyboardButton(f"🛒 {q} accs (${round(q*unit_price, 2)})", callback_data=f"qty_{category}_{q}_{lang}"))

        back_btn = "⬅️ နောက်သို့" if lang == "mm" else "⬅️ Back"
        markup.add(types.InlineKeyboardButton(back_btn, callback_data=f"lang_{lang}"))

        if lang == "mm":
            title = f"🛒 **{category.upper()}**\nဝယ်ယူမည့် ပမာဏကို ရွေးချယ်ပါ။"
        else:
            title = f"🛒 **{category.upper()}**\nSelect quantity to buy."

        bot.edit_message_text(title, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    # 3. Qty Selection -> Coin Selection (Emoji အရောင်များဖြင့်)
    elif data.startswith("qty_"):
        parts = data.split("_")
        category = parts[1]
        qty = int(parts[2])
        lang = parts[3] if len(parts) > 3 else "mm"

        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🟢 Solana (SOL)", callback_data=f"pay_{category}_{qty}_sol_{lang}"),
            types.InlineKeyboardButton("🟣 Polygon (POL)", callback_data=f"pay_{category}_{qty}_pol_{lang}")
        )
        markup.add(
            types.InlineKeyboardButton("🟡 BNB Chain (BNB)", callback_data=f"pay_{category}_{qty}_bnb_{lang}"),
            types.InlineKeyboardButton("🔴 TRON (TRX)", callback_data=f"pay_{category}_{qty}_trx_{lang}")
        )
        back_btn = "⬅️ နောက်သို့" if lang == "mm" else "⬅️ Back"
        markup.add(types.InlineKeyboardButton(back_btn, callback_data=f"cat_{category}_{lang}"))

        pay_title = "💰 ငွေပေးချေမည့် ကို ရွေးချယ်ပါ" if lang == "mm" else "💰 Select Crypto for payment"
        bot.edit_message_text(f"**{pay_title}**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    # 4. Pay Checkout -> Generate Order (Payment Logic)
    elif data.startswith("pay_"):
        parts = data.split("_")
        category = parts[1]
        qty = int(parts[2])
        coin = parts[3]
        lang = parts[4] if len(parts) > 4 else "mm"

        usd_total = round(qty * PRICES[category], 2)
        coin_amount = get_crypto_amount(usd_total, coin)

        if not coin_amount:
            err_msg = "စျေးနှုန်းရယူရာတွင် အမှားဖြစ်နေပါသည်။" if lang == "mm" else "Price Error. Try again."
            bot.answer_callback_query(call.id, err_msg, show_alert=True)
            return

        created_time = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO orders (user_id, category, qty, coin, amount_coin, status, created_at) VALUES (%s, %s, %s, %s, %s, 'pending', %s) RETURNING order_id", (call.from_user.id, category, qty, coin, coin_amount, created_time))
            order_id = cursor.fetchone()[0]

            address = generate_hd_address(coin, order_id)
            if not address:
                raise RuntimeError("Address Generation Failed")

            cursor.execute("UPDATE orders SET address = %s WHERE order_id = %s", (address, order_id))
            conn.commit()
            conn.close()

            markup = types.InlineKeyboardMarkup()
            btn_text = "✅ Check Payment (ငွေစစ်ဆေးမည်)" if lang == "mm" else "✅ Check Payment"
            markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"check_{order_id}_{lang}"))
            back_btn = "❌ မလုပ်တော့ပါ" if lang == "mm" else "❌ Cancel & Back"
            markup.add(types.InlineKeyboardButton(back_btn, callback_data=f"lang_{lang}"))

            if lang == "mm":
                msg = f"⚡ **တိုက်ရိုက် Crypto ငွေပေးချေမှု**\n\n"
                msg += f"📊 **အော်ဒါ (Order ID):** `#{order_id}`\n"
                msg += f"🪙 **ပေးချေရမည့် Coin:** `{coin.upper()}`\n"
                msg += f"💵 **ကျသင့်ငွေ:** `${usd_total}` USD\n"
                msg += f"⚠️ **အတိအကျ လွှဲရမည့် ပမာဏ:** `{coin_amount}` `{coin.upper()}`\n"
                msg += f"🏦 **ငွေလွှဲရမည့် လိပ်စာ (Address):**\n`{address}`\n\n"
                msg += f"⏳ **အချိန်ကန့်သတ်ချက်:** 15 မိနစ်အတွင်း လွှဲပေးပါ။\n\n"
                msg += f"📌 *ဆုံးရှုံးမှုမဖြစ်စေရန် အတိအကျ လွှဲပေးပါ။*"
            else:
                msg = f"⚡ **Direct Native Crypto Payment**\n\n"
                msg += f"📊 **Order ID:** `#{order_id}`\n"
                msg += f"🪙 **Coin:** `{coin.upper()}`\n"
                msg += f"💵 **Total Value:** `${usd_total}` USD\n"
                msg += f"⚠️ **EXACT AMOUNT TO SEND:** `{coin_amount}` {coin.upper()}\n"
                msg += f"🏦 **DEPOSIT ADDRESS:**\n`{address}`\n\n"
                msg += f"⏳ **Payment Time Limit:** 15 Minutes\n\n"
                msg += f"📌 *Please ensure exact amount to avoid loss.*"

            bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

        except Exception as e:
            logging.error(f"Order Creation Error: {e}")
            bot.answer_callback_query(call.id, "Order Failed.", show_alert=True)

    # 5. Check Payment Handler
    elif data.startswith("check_"):
        parts = data.split("_")
        order_id = int(parts[1])
        lang = parts[2] if len(parts) > 2 else "mm"

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, category, qty, coin, address, amount_coin, status, created_at FROM orders WHERE order_id = %s FOR UPDATE", (order_id,))
        order = cursor.fetchone()

        if not order:
            err = "အော်ဒါ မတွေ့ပါ" if lang == "mm" else "Order Not Found"
            bot.answer_callback_query(call.id, err, show_alert=True)
            conn.close()
            return

        user_id, category, qty, coin, address, amount_coin, status, created_time = order

        if status == 'completed':
            err = "အော်ဒါ ထုတ်ပေးပြီးသား ဖြစ်နေပါပြီ" if lang == "mm" else "Order Already Completed!"
            bot.answer_callback_query(call.id, err, show_alert=True)
            conn.close()
            return

        created_time = datetime.datetime.strptime(created_time, "%Y-%m-%d %H:%M:%S")
        time_diff = (datetime.datetime.utcnow() - created_time).total_seconds()
        if time_diff > 900:
            cursor.execute("UPDATE orders SET status = 'expired' WHERE order_id = %s", (order_id,))
            conn.commit()
            conn.close()
            bot.answer_callback_query(call.id, "အော်ဒါ သက်တမ်းကုန်သွားပါပြီ။", show_alert=True)
            return

        current_balance = check_blockchain_balance(address, coin)

        if current_balance >= (amount_coin * 0.98):
            cursor.execute("SELECT id, account_info FROM accounts WHERE category = %s AND status = 'available' LIMIT %s", (category, qty))
            rows = cursor.fetchall()

            if len(rows) >= qty:
                account_ids = tuple(r[0] for r in rows)
                accounts_info = [r[1] for r in rows]
                now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

                cursor.execute("UPDATE accounts SET status = 'sold', buyer_id = %s, sold_at = %s, order_id = %s WHERE id IN %s", (user_id, now_str, order_id, account_ids))
                cursor.execute("UPDATE orders SET status = 'completed' WHERE order_id = %s", (order_id,))
                conn.commit()

                acc_text = "\n".
