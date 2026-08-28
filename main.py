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

    if data.startswith("lang_"):
        lang = data.split("_")[1]
        x_stock = get_stock_count('x')
        outlook_stock = get_stock_count('outlook')
        
        welcome_text = (
            f"🛒 **Vault Store မှ ကြိုဆိုልပါသည်**\n\n"
            f"🔹 **X (Twitter) Stock:** `{x_stock}` ကောင့်\n"
            f"🔹 **Outlook Stock:** `{outlook_stock}` ကောင့်\n\n"
            f"အောက်ပါတို့မှ ဝယ်ယူလိုသော အမျိုးအစားကို ရွေးချယ်ပါ -"
            if lang == "mm" else
            f"🛒 **Welcome to Vault Store**\n\n"
            f"🔹 **X (Twitter) Stock:** `{x_stock}` Accounts\n"
            f"🔹 **Outlook Stock:** `{outlook_stock}` Accounts\n\n"
            f"Select category to buy -"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("𝕏 X Accounts", callback_data="cat_x"),
            types.InlineKeyboardButton("📧 Outlook Accounts", callback_data="cat_outlook")
        )
        markup.add(types.InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{ADMIN_USERNAME}"))
        bot.edit_message_text(welcome_text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data in ["cat_x", "cat_outlook"]:
        category = data.split("_")[1]
        stock_qty = get_stock_count(category)
        if stock_qty == 0:
            bot.answer_callback_query(call.id, "Stock ကုန်နေပါသည်", show_alert=True)
            return
        
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("2 accs", callback_data=f"qty_{category}_2"),
            types.InlineKeyboardButton("3 accs", callback_data=f"qty_{category}_3")
        )
        markup.add(
            types.InlineKeyboardButton("5 accs", callback_data=f"qty_{category}_5"),
            types.InlineKeyboardButton("10 accs", callback_data=f"qty_{category}_10")
        )
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="lang_mm"))
        bot.edit_message_text(f"🔢 ဝယ်ယူလိုသော **{category.upper()}** အကောင့် အရေအတွက်ကို ရွေးချယ်ပါ (Stock: {stock_qty}):", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("qty_"):
        parts = data.split("_")
        category = parts[1]
        qty = int(parts[2])
        total_price = round(qty * PRICE_USDT, 2)
        
        invoice_url = create_nowpayments_invoice(total_price, f"{call.from_user.id}_{category}_{qty}", f"{qty} {category.upper()} Accounts")
        
        if invoice_url:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("💳 Pay via NOWPayments (Crypto)", url=invoice_url))
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"cat_{category}"))
            bot.edit_message_text(f"💳 **{category.upper()} Account ({qty} ခု)**\nကျသင့်ငွေ: `{total_price} USDT`\n\nအောက်ပါခလုတ်ကိုနှိပ်၍ ငွေပေးချေနိုင်ပါသည်။", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.answer_callback_query(call.id, "Payment Gateway Error. Try again later.", show_alert=True)

@bot.message_handler(commands=['addacc'])
def add_acc(message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/addacc", "").strip()
    # ပုံစံ: /addacc x user|pass|link သို့မဟုတ် /addacc outlook email|pass
    parts = text.split(" ", 1)
    if len(parts) < 2 or parts[0].lower() not in ['x', 'outlook']:
        bot.reply_to(message, "⚠️ ပုံစံအမှန်:\n`/addacc x user|pass|link`\n(သို့)\n`/addacc outlook email|pass`", parse_mode="Markdown")
        return
    
    category = parts[0].lower()
    acc_lines = [line.strip() for line in parts[1].split("\n") if line.strip()]
    added = add_accounts_to_db(category, acc_lines)
    bot.reply_to(message, f"✅ **{category.upper()}** Stock အသစ် `{added}` ကောင့် ထည့်ပြီးပါပြီ။", parse_mode="Markdown")

@bot.message_handler(commands=['stock'])
def stock_cmd(message):
    if message.from_user.id != ADMIN_ID: return
    x_stock = get_stock_count('x')
    outlook_stock = get_stock_count('outlook')
    bot.reply_to(message, f"📦 **လက်ရှိ Stock အခြေအနေ:**\n🔹 X Accounts: `{x_stock}` ခု\n🔹 Outlook Accounts: `{outlook_stock}` ခု", parse_mode="Markdown")

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
