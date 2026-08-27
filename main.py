import os
import sqlite3
import time
import requests
import telebot
import json
import logging
from telebot import types
from flask import Flask, request
import threading

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
BOT_TOKEN = "8683965691:AAEthMpBt_RJNY1NPNDPtH-hSnTcpWFU0L8"
ADMIN_ID = 7613605178
NOWPAYMENTS_API_KEY = os.environ.get("NOWPAYMENTS_API_KEY", "test_key")
HEROKU_APP_NAME = os.environ.get("HEROKU_APP_NAME", "x-vault-bot-20----26")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ---------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------
def init_db():
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS stock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        account_data TEXT
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        balance REAL DEFAULT 0.0
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        category TEXT,
        account_data TEXT,
        amount REAL,
        date TEXT
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS orders (
        payment_id TEXT PRIMARY KEY,
        user_id INTEGER,
        amount REAL,
        status TEXT
    )''')
    
    conn.commit()
    conn.close()

init_db()

# ---------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------
def is_admin(user_id):
    return str(user_id) == str(ADMIN_ID)

def get_user_balance(user_id):
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    if not res:
        cursor.execute("INSERT INTO users (user_id, balance) VALUES (?, 0.0)", (user_id,))
        conn.commit()
        balance = 0.0
    else:
        balance = res[0]
    conn.close()
    return balance

def update_user_balance(user_id, amount):
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def get_stock_count(category):
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM stock WHERE category=?", (category,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def add_stock(category, account_data):
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO stock (category, account_data) VALUES (?, ?)", (category, account_data))
    conn.commit()
    conn.close()

def clear_stock(category):
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM stock WHERE category=?", (category,))
    conn.commit()
    conn.close()

def get_user_history(user_id, limit=5):
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    cursor.execute("SELECT category, account_data, date FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))
    records = cursor.fetchall()
    conn.close()
    return records

# ---------------------------------------------------------
# NOWPAYMENTS API
# ---------------------------------------------------------
def create_nowpayments_deposit(amount_usd, order_id):
    url = "https://api.nowpayments.io/v1/invoice"
    headers = {
        "x-api-key": NOWPAYMENTS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "price_amount": amount_usd,
        "price_currency": "usd",
        "order_id": order_id,
        "order_description": "Wallet Deposit Top-up",
        "ipn_callback_url": f"https://{HEROKU_APP_NAME}.herokuapp.com/nowpayments_webhook",
        "success_url": "https://t.me",
        "cancel_url": "https://t.me"
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=30)
        if res.status_code == 200:
            return res.json()
        return None
    except:
        return None

def save_order(payment_id, user_id, amount):
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO orders (payment_id, user_id, amount, status) VALUES (?, ?, ?, 'waiting')", 
                   (payment_id, user_id, amount))
    conn.commit()
    conn.close()

def get_order(payment_id):
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, amount, status FROM orders WHERE payment_id=?", (payment_id,))
    order = cursor.fetchone()
    conn.close()
    return order

def update_order_status(payment_id, status):
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status=? WHERE payment_id=?", (status, payment_id))
    conn.commit()
    conn.close()

# ---------------------------------------------------------
# TELEGRAM BOT COMMANDS
# ---------------------------------------------------------
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    balance = get_user_balance(user_id)
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_x = types.InlineKeyboardButton("🛒 Buy X Acc ($0.15)", callback_data="buy_x")
    btn_mail = types.InlineKeyboardButton("📧 Buy Outlook ($0.10)", callback_data="buy_mail")
    btn_deposit = types.InlineKeyboardButton("💳 Deposit", callback_data="deposit_menu")
    btn_history = types.InlineKeyboardButton("📜 History", callback_data="user_history")
    btn_balance = types.InlineKeyboardButton("💰 Balance", callback_data="check_balance")
    markup.add(btn_x, btn_mail, btn_deposit, btn_history, btn_balance)
    
    if is_admin(user_id):
        btn_admin = types.InlineKeyboardButton("🔐 Admin", callback_data="admin_panel")
        markup.add(btn_admin)
    
    welcome_text = (
        f"✨ *X Vault Bot* ✨\n\n"
        f"👤 ID: `{user_id}`\n"
        f"💰 Balance: *${balance:.2f}*\n\n"
        f"အောက်ပါ Menu မှ ရွေးချယ်ပါ-"
    )
    bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    
    if call.data == "buy_x":
        stock_count = get_stock_count('x')
        if stock_count == 0:
            bot.send_message(call.message.chat.id, "❌ X Account မရှိပါ။")
            return
        bot.send_message(call.message.chat.id, f"🛒 X Account ($0.15)\nStock: {stock_count}\n\nအရေအတွက် ရိုက်ထည့်ပါ:")
        bot.register_next_step_handler(call.message, process_purchase, 'x', 0.15)
    
    elif call.data == "buy_mail":
        stock_count = get_stock_count('mail')
        if stock_count == 0:
            bot.send_message(call.message.chat.id, "❌ Outlook Mail မရှိပါ။")
            return
        bot.send_message(call.message.chat.id, f"📧 Outlook Mail ($0.10)\nStock: {stock_count}\n\nအရေအတွက် ရိုက်ထည့်ပါ:")
        bot.register_next_step_handler(call.message, process_purchase, 'mail', 0.10)
    
    elif call.data == "deposit_menu":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("$1", callback_data="dep_1"),
            types.InlineKeyboardButton("$2", callback_data="dep_2"),
            types.InlineKeyboardButton("$5", callback_data="dep_5"),
            types.InlineKeyboardButton("$10", callback_data="dep_10"),
            types.InlineKeyboardButton("✏️ Custom", callback_data="dep_custom")
        )
        bot.edit_message_text(
            "💳 *Deposit*\n\nပမာဏ ရွေးပါ (အနည်းဆုံး $0.5):",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
    
    elif call.data == "dep_custom":
        bot.send_message(call.message.chat.id, "💳 ပမာဏ ရိုက်ထည့်ပါ (ဥပမာ: 5.50):")
        bot.register_next_step_handler(call.message, process_custom_deposit)
    
    elif call.data.startswith("dep_"):
        amount = float(call.data.replace("dep_", ""))
        create_deposit_invoice(call.message, amount)
    
    elif call.data == "user_history":
        records = get_user_history(user_id)
        if not records:
            bot.send_message(call.message.chat.id, "📜 မှတ်တမ်း မရှိသေးပါ။")
        else:
            msg = "📜 *Last 5 Purchases:*\n\n"
            for r in records:
                msg += f"🔹 [{r[2]}] ({r[0].upper()}): `{r[1]}`\n"
            bot.send_message(call.message.chat.id, msg, parse_mode="Markdown")
    
    elif call.data == "check_balance":
        balance = get_user_balance(user_id)
        bot.send_message(call.message.chat.id, f"💰 *Balance*\n\n${balance:.2f}", parse_mode="Markdown")
    
    elif call.data == "admin_panel":
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "⛔️ Admin မဟုတ်ပါ။")
            return
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("➕ Add X", callback_data="admin_addx"),
            types.InlineKeyboardButton("➕ Add Mail", callback_data="admin_addmail"),
            types.InlineKeyboardButton("🗑️ Clear X", callback_data="admin_clearx"),
            types.InlineKeyboardButton("🗑️ Clear Mail", callback_data="admin_clearmail"),
            types.InlineKeyboardButton("📊 Stock", callback_data="admin_stock"),
            types.InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
            types.InlineKeyboardButton("💾 Backup", callback_data="admin_backup"),
            types.InlineKeyboardButton("🔙 Back", callback_data="back_to_main")
        )
        bot.edit_message_text(
            "🔐 *Admin Panel*",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
    
    elif call.data == "admin_addx":
        bot.send_message(call.message.chat.id, "✏️ X Accounts များ ရိုက်ထည့်ပါ:\n`/addx user1|pass1\nuser2|pass2`", parse_mode="Markdown")
    
    elif call.data == "admin_addmail":
        bot.send_message(call.message.chat.id, "✏️ Outlook Mails များ ရိုက်ထည့်ပါ:\n`/addmail mail1|pass1\nmail2|pass2`", parse_mode="Markdown")
    
    elif call.data == "admin_clearx":
        clear_stock('x')
        bot.send_message(call.message.chat.id, "✅ X Stock အားလုံး ဖျက်ပြီးပါပြီ။")
    
    elif call.data == "admin_clearmail":
        clear_stock('mail')
        bot.send_message(call.message.chat.id, "✅ Outlook Stock အားလုံး ဖျက်ပြီးပါပြီ။")
    
    elif call.data == "admin_stock":
        x_count = get_stock_count('x')
        mail_count = get_stock_count('mail')
        bot.send_message(call.message.chat.id, f"📊 *Stock*\n\nX: {x_count}\nMail: {mail_count}", parse_mode="Markdown")
    
    elif call.data == "admin_broadcast":
        bot.send_message(call.message.chat.id, "📢 Broadcast Message ရိုက်ထည့်ပါ:\n`/bc Your message`", parse_mode="Markdown")
    
    elif call.data == "admin_backup":
        try:
            with open("bot_vault.db", "rb") as f:
                bot.send_document(call.message.chat.id, f, caption="📦 Database Backup")
        except:
            bot.send_message(call.message.chat.id, "❌ Backup မရပါ။")
    
    elif call.data == "back_to_main":
        send_welcome(call.message)

# ---------------------------------------------------------
# PURCHASE LOGIC
# ---------------------------------------------------------
def process_purchase(message, category, unit_price):
    try:
        qty = int(message.text.strip())
        if qty <= 0:
            bot.send_message(message.chat.id, "❌ အနည်းဆုံး 1 ခု ရိုက်ထည့်ပါ။")
            return
        
        user_id = message.from_user.id
        total = round(qty * unit_price, 2)
        balance = get_user_balance(user_id)
        
        if balance < total:
            bot.send_message(message.chat.id, f"❌ Balance မလုံလောက်ပါ။\n💰 သင့်မှာ: ${balance:.2f}\n💰 လိုအပ်: ${total:.2f}")
            return
        
        conn = sqlite3.connect("bot_vault.db")
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, account_data FROM stock WHERE category=? LIMIT ?", (category, qty))
        items = cursor.fetchall()
        
        if len(items) < qty:
            bot.send_message(message.chat.id, "❌ Stock မလုံလောက်ပါ။")
            conn.close()
            return
        
        # Update balance
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (total, user_id))
        
        date_now = time.strftime("%Y-%m-%d %H:%M:%S")
        delivered = []
        
        for item_id, acc_data in items:
            delivered.append(acc_data)
            cursor.execute("DELETE FROM stock WHERE id=?", (item_id,))
            cursor.execute("INSERT INTO history (user_id, category, account_data, amount, date) VALUES (?, ?, ?, ?, ?)",
                          (user_id, category, acc_data, unit_price, date_now))
        
        conn.commit()
        conn.close()
        
        acc_text = "\n".join([f"`{acc}`" for acc in delivered])
        bot.send_message(
            message.chat.id,
            f"✅ *Purchase Successful!*\n\n"
            f"📦 {category.upper()} x{qty}\n"
            f"💰 ${total:.2f}\n\n"
            f"Accounts:\n{acc_text}",
            parse_mode="Markdown"
        )
        
    except ValueError:
        bot.send_message(message.chat.id, "❌ ဂဏန်း ရိုက်ထည့်ပါ။")

def process_custom_deposit(message):
    try:
        amount = float(message.text.strip())
        if amount < 0.5:
            bot.send_message(message.chat.id, "❌ အနည်းဆုံး $0.5 ရိုက်ထည့်ပါ။")
            return
        create_deposit_invoice(message, amount)
    except ValueError:
        bot.send_message(message.chat.id, "❌ ဂဏန်း ရိုက်ထည့်ပါ။")

def create_deposit_invoice(message, amount):
    user_id = message.from_user.id
    order_id = f"DEP-{int(time.time())}-{user_id}"
    
    msg = bot.send_message(message.chat.id, "⏳ Invoice ထုတ်နေပါသည်...")
    
    invoice = create_nowpayments_deposit(amount, order_id)
    
    if invoice and "invoice_url" in invoice:
        payment_id = str(invoice["id"])
        save_order(payment_id, user_id, amount)
        
        bot.edit_message_text(
            f"💳 *Deposit Invoice*\n\n"
            f"💰 ${amount:.2f}\n\n"
            f"🔗 [Pay Now]({invoice['invoice_url']})\n\n"
            f"⏳ ငွေလွှဲပြီးရင် Balance ထဲ အလိုအလျောက်ဝင်ပါမယ်။",
            message.chat.id,
            msg.message_id,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    else:
        bot.edit_message_text(
            "❌ Invoice မရပါ။ ခဏကြာမှ ထပ်စမ်းပါ။",
            message.chat.id,
            msg.message_id
        )

# ---------------------------------------------------------
# ADMIN COMMANDS
# ---------------------------------------------------------
@bot.message_handler(commands=['addx'])
def add_x_command(message):
    if not is_admin(message.from_user.id):
        return
    
    accounts = message.text.replace("/addx", "").strip().split("\n")
    valid = [a.strip() for a in accounts if a.strip()]
    
    if not valid:
        bot.send_message(message.chat.id, "⚠️ `/addx user|pass`", parse_mode="Markdown")
        return
    
    for acc in valid:
        add_stock('x', acc)
    
    bot.send_message(message.chat.id, f"✅ X {len(valid)} ခု ထည့်ပြီးပါပြီ။")

@bot.message_handler(commands=['addmail'])
def add_mail_command(message):
    if not is_admin(message.from_user.id):
        return
    
    accounts = message.text.replace("/addmail", "").strip().split("\n")
    valid = [a.strip() for a in accounts if a.strip()]
    
    if not valid:
        bot.send_message(message.chat.id, "⚠️ `/addmail mail|pass`", parse_mode="Markdown")
        return
    
    for acc in valid:
        add_stock('mail', acc)
    
    bot.send_message(message.chat.id, f"✅ Mail {len(valid)} ခု ထည့်ပြီးပါပြီ။")

@bot.message_handler(commands=['bc'])
def broadcast_command(message):
    if not is_admin(message.from_user.id):
        return
    
    text = message.text.replace("/bc", "").strip()
    if not text:
        bot.send_message(message.chat.id, "⚠️ `/bc Your message`", parse_mode="Markdown")
        return
    
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    
    success = 0
    for user in users:
        try:
            bot.send_message(user[0], f"📢 *Announcement*\n\n{text}", parse_mode="Markdown")
            success += 1
            time.sleep(0.05)
        except:
            pass
    
    bot.send_message(message.chat.id, f"✅ {success} users ဆီသို့ ပို့ပြီးပါပြီ။")

# ---------------------------------------------------------
# NOWPAYMENTS WEBHOOK
# ---------------------------------------------------------
@app.route('/nowpayments_webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        if data and data.get('payment_status') in ['finished', 'confirmed']:
            payment_id = str(data.get('payment_id'))
            order = get_order(payment_id)
            
            if order and order[2] == 'waiting':
                user_id, amount, _ = order
                update_user_balance(user_id, amount)
                update_order_status(payment_id, 'finished')
                
                try:
                    bot.send_message(
                        user_id,
                        f"✅ *Deposit Confirmed!*\n\n💰 ${amount:.2f} ထည့်သွင်းပြီးပါပြီ။",
                        parse_mode="Markdown"
                    )
                except:
                    pass
        
        return "OK", 200
    except:
        return "Error", 500

@app.route('/')
def index():
    return "Bot is running!", 200

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
if __name__ == "__main__":
    # Start Flask
    def run_flask():
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port)
    
    thread = threading.Thread(target=run_flask)
    thread.daemon = True
    thread.start()
    
    # Start Bot
    print("Bot starting...")
    bot.remove_webhook()
    bot.polling(none_stop=True, interval=1)
