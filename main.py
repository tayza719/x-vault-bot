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
PRICE_USDT = 1.0

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

# Network တစ်ခုတည်း အတင်းမချုပ်တော့ဘဲ User ဘက်မှာ ကြိုက်တာရွေးလို့ရမည့် Function
def create_nowpayments_invoice(amount, order_id, description):
    url = "https://api-sandbox.nowpayments.io/v1/invoice"  # Test အတွက် (Live တင်လျှင် api-sandbox ဖြုတ်ပါ)
    headers = {
        "x-api-key": NOWPAYMENTS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "price_amount": amount,
        "price_currency": "usd",
        "order_id": str(order_id),
        "order_description": description
        # pay_currency ကို ဖြုတ်ထားလိုက်သဖြင့် User ဘက်တွင် Coin/Network အစုံမှ ရွေးချယ်နိုင်မည်
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
        
        # အရေအတွက် အများအပြား (1 မှ 5 အထိ) ရွေးချယ်နိုင်ရန် ပြုလုပ်ထားသည်
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

        total_price = round(qty * PRICE_USDT, 2)
        order_id = f"{call.from_user.id}_{category}_{qty}_{int(datetime.datetime.utcnow().timestamp())}"
        
        invoice_url = create_nowpayments_invoice(total_price, order_id, f"{qty} {category.upper()} Accounts")
        
        if invoice_url:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("💳 Choose Currency & Pay (Auto)", url=invoice_url))
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"cat_{category}"))
            bot.edit_message_text(f"💳 **Crypto Auto Payment**\n\nQty: `{qty}` x `{category.upper()}`\nTotal: `${total_price} USD`\n\nအောက်ပါခလုတ်ကိုနှိပ်၍ လိုချင်သော Coin / Network (USDT, BNB, SOL, TON စသည်ဖြင့်) ကို ရွေးချယ်ပေးချေနိုင်ပါသည်။", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.answer_callback_query(call.id, "Payment Gateway Error.", show_alert=True)

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
    print("Bot is running...")
    bot.infinity_polling()
