import logging
import os
import sqlite3
import datetime
import shutil
import requests
import telebot
from telebot import types

BOT_TOKEN = "8683965691:AAEthMpBt_RJNY1NPNDPtH-hSnTcpWFU0L8"
ADMIN_ID = 7613605178
NOWPAYMENTS_API_KEY = "KGG6CA4-KRDM70D-M9WVWG7-XRVTCPJ"

ADMIN_USERNAME = "EchoWhisper"
DB_FILE = "store.db"
BANNED_FILE = "banned_users.txt"
BACKUP_DIR = "backups"
PRICE_USDT = 0.15

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
bot = telebot.TeleBot(BOT_TOKEN)

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_info TEXT UNIQUE,
            status TEXT DEFAULT 'available',
            buyer_id INTEGER,
            sold_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_banned_users():
    if not os.path.exists(BANNED_FILE):
        return set()
    with open(BANNED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def ban_user(user_id):
    banned = get_banned_users()
    banned.add(str(user_id))
    with open(BANNED_FILE, "w", encoding="utf-8") as f:
        for u in banned:
            f.write(f"{u}\n")

def unban_user(user_id):
    banned = get_banned_users()
    banned.discard(str(user_id))
    with open(BANNED_FILE, "w", encoding="utf-8") as f:
        for u in banned:
            f.write(f"{u}\n")

def get_stock_count():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM accounts WHERE status = 'available'")
    count = cursor.fetchone()[0]
    conn.close()
    return count

def add_accounts_to_db(acc_list):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    added = 0
    for acc in acc_list:
        try:
            cursor.execute("INSERT INTO accounts (account_info, status) VALUES (?, 'available')", (acc,))
            added += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return added

def create_nowpayments_invoice(amount, order_id, description):
    url = "https://api.nowpayments.io/v1/invoice"
    headers = {
        "x-api-key": NOWPAYMENTS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "price_amount": amount,
        "price_currency": "usd",
        "pay_currency": "usdttrc20",
        "order_id": str(order_id),
        "order_description": description
    }
    try:
        res = requests.post(url, json=payload, headers=headers)
        if res.status_code in [200, 201]:
            return res.json().get("invoice_url")
    except Exception as e:
        logging.error(f"NOWPayments Error: {e}")
    return None

@bot.message_handler(commands=['start'])
def send_welcome(message):
    if str(message.from_user.id) in get_banned_users():
        bot.reply_to(message, "🚫 အကောင့်ကို ဘော့တ်အသုံးပြုခွင့် ပိတ်ပင်ထားပါသည်။")
        return
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🇲🇲 မြန်မာစာ", callback_data="lang_mm"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
    )
    bot.send_message(message.chat.id, "🌐 **ကျေးဇူးပြု၍ ဘာသာစကား ရွေးချယ်ပါ**\n **Please select your language**", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if str(call.from_user.id) in get_banned_users():
        bot.answer_callback_query(call.id, "Banned User.", show_alert=True)
        return

    data = call.data
    lang = "mm"

    if data.startswith("lang_"):
        lang = data.split("_")[1]
        stock_qty = get_stock_count()
        welcome_text = (
            f"🛒 **X (Twitter) Vault Store မှ ကြိုဆိုပါသည်**\n\n"
            f"📦 **အကောင့်အမျိုးအစား:** New X Account (Fresh)\n"
            f"💰 **၁ ကောင့် ဈေးနှုန်း:** `${PRICE_USDT} USDT`\n"
            f"📊 **ရနိုင်သော Stock:** `{stock_qty}` ကောင့်\n\n"
            f"👇 ဝယ်ယူလိုပါက အောက်ပါခလုတ်ကို နှိပ်ပါ"
            if lang == "mm" else
            f"🛒 **Welcome to X (Twitter) Vault Store**\n\n"
            f"📦 **Item:** X (Twitter) New Account (Fresh)\n"
            f"💰 **Price:** `${PRICE_USDT} USDT` (per acc)\n"
            f"📊 **Available Stock:** `{stock_qty}` Accounts\n\n"
            f"👇 Click the button below to buy"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🛒 X Account ဝယ်ယူမည်" if lang == "mm" else "🛒 Buy X Accounts", callback_data="buy_x_acc"))
        markup.add(types.InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{ADMIN_USERNAME}"))
        bot.edit_message_text(welcome_text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data == "buy_x_acc":
        stock_qty = get_stock_count()
        if stock_qty == 0:
            bot.answer_callback_query(call.id, "Stock ကုန်နေပါသည်", show_alert=True)
            return
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("2 accs", callback_data="qty_2"),
            types.InlineKeyboardButton("3 accs", callback_data="qty_3")
        )
        markup.add(
            types.InlineKeyboardButton("5 accs", callback_data="qty_5"),
            types.InlineKeyboardButton("10 accs", callback_data="qty_10")
        )
        bot.edit_message_text("🔢 ဝယ်ယူလိုသော အရေအတွက်ကို ရွေးချယ်ပါ:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data.startswith("qty_"):
        qty = int(data.split("_")[1])
        total_price = round(qty * PRICE_USDT, 2)
        invoice_url = create_nowpayments_invoice(total_price, f"{call.from_user.id}_{qty}", f"{qty} X Accounts")
        
        if invoice_url:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("💳 Pay via NOWPayments", url=invoice_url))
            bot.edit_message_text(f"💳 ကျသင့်ငွေ: `{total_price} USDT`\nအောက်ပါခလုတ်ကိုနှိပ်၍ ငွေပေးချေပါ။", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.answer_callback_query(call.id, "Payment Gateway Error", show_alert=True)

@bot.message_handler(commands=['addacc'])
def add_acc(message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/addacc", "").strip()
    if not text:
        bot.reply_to(message, "⚠️ ပုံစံ: `/addacc user|pass|link`", parse_mode="Markdown")
        return
    accs = [line.strip() for line in text.split("\n") if line.strip()]
    added = add_accounts_to_db(accs)
    bot.reply_to(message, f"✅ Stock အသစ် `{added}` ကောင့် ထည့်ပြီးပါပြီ။", parse_mode="Markdown")

@bot.message_handler(commands=['stock'])
def stock_cmd(message):
    if message.from_user.id != ADMIN_ID: return
    bot.reply_to(message, f"📦 လက်ရှိ Stock: `{get_stock_count()}` ကောင့်", parse_mode="Markdown")

@bot.message_handler(commands=['ban'])
def ban_cmd(message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) > 1:
        ban_user(parts[1])
        bot.reply_to(message, f"🚫 User ID `{parts[1]}` ကို Ban လိုက်ပါပြီ။", parse_mode="Markdown")

@bot.message_handler(commands=['unban'])
def unban_cmd(message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) > 1:
        unban_user(parts[1])
        bot.reply_to(message, f"✅ User ID `{parts[1]}` ကို Unban လိုက်ပါပြီ။", parse_mode="Markdown")

if __name__ == "__main__":
    print("Bot is running...")
    bot.infinity_polling()
