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
# Sandbox (Test) အတွက် NOWPayments API Key ထည့်ရန်
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
            category TEXT DEFAULT 'x',
            account_info TEXT UNIQUE,
            status TEXT DEFAULT 'available',
            buyer_id INTEGER,
            sold_at TEXT
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

# Sandbox Endpoint သို့ ချိတ်ဆက်ခြင်း
def create_nowpayments_invoice(amount, order_id, description):
    url = "https://api-sandbox.nowpayments.io/v1/invoice"
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
        else:
            logging.error(f"Sandbox Error Response: {res.text}")
    except Exception as e:
        logging.error(f"NOWPayments Sandbox Error: {e}")
    return None

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🇲🇲 မြန်မာစာ", callback_data="lang_mm"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
    )
    bot.send_message(message.chat.id, "🌐 **Test Mode - ဘာသာစကား ရွေးချယ်ပါ**", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    data = call.data

    if data.startswith("lang_"):
        x_stock = get_stock_count('x')
        outlook_stock = get_stock_count('outlook')
        
        welcome_text = f"🧪 **Test Mode Store**\n\n🔹 X Stock: `{x_stock}`\n🔹 Outlook Stock: `{outlook_stock}`"
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
        markup.add(types.InlineKeyboardButton("2 accs", callback_data=f"qty_{category}_2"))
        bot.edit_message_text(f"🔢 Test လုပ်ရန် **{category.upper()}** ၂ ကောင့် ရွေးချယ်ပြီးပါပြီ။", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("qty_"):
        parts = data.split("_")
        category = parts[1]
        qty = int(parts[2])
        total_price = round(qty * PRICE_USDT, 2)
        order_id = f"test_{call.from_user.id}_{int(datetime.datetime.utcnow().timestamp())}"
        
        invoice_url = create_nowpayments_invoice(total_price, order_id, f"Test {qty} {category.upper()}")
        
        if invoice_url:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("💳 Test Pay Link", url=invoice_url))
            bot.edit_message_text(f"🧪 **Sandbox Test Invoice Link ထွက်လာပါပြီ**\n\nLink ကိုနှိပ်၍ စမ်းသပ်နိုင်ပါသည်။", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.answer_callback_query(call.id, "Sandbox API Error. Key ကို စစ်ဆေးပါ။", show_alert=True)

@bot.message_handler(commands=['addacc'])
def add_acc(message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/addacc", "").strip()
    parts = text.split(" ", 1)
    if len(parts) < 2: return
    category = parts[0].lower()
    acc_lines = [line.strip() for line in parts[1].split("\n") if line.strip()]
    added = add_accounts_to_db(category, acc_lines)
    bot.reply_to(message, f"✅ Test Stock `{added}` ခု ထည့်ပြီးပါပြီ။")

if __name__ == "__main__":
    print("Test Bot is running...")
    bot.infinity_polling()
