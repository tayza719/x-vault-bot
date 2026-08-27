import os
import sqlite3
import time
import requests
import telebot
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
# DATABASE
# ---------------------------------------------------------
def init_db():
    conn = sqlite3.connect("bot_vault.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS stock (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, account_data TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0.0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, category TEXT, account_data TEXT, amount REAL, date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders (payment_id TEXT PRIMARY KEY, user_id INTEGER, amount REAL, status TEXT)''')
    conn.commit()
    conn.close()

init_db()

def is_admin(user_id):
    return str(user_id) == str(ADMIN_ID)

def get_balance(user_id):
    conn = sqlite3.connect("bot_vault.db")
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    res = c.fetchone()
    if not res:
        c.execute("INSERT INTO users (user_id, balance) VALUES (?, 0.0)", (user_id,))
        conn.commit()
        balance = 0.0
    else:
        balance = res[0]
    conn.close()
    return balance

def update_balance(user_id, amount):
    conn = sqlite3.connect("bot_vault.db")
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def get_stock(category):
    conn = sqlite3.connect("bot_vault.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM stock WHERE category=?", (category,))
    count = c.fetchone()[0]
    conn.close()
    return count

def add_stock(category, data):
    conn = sqlite3.connect("bot_vault.db")
    c = conn.cursor()
    c.execute("INSERT INTO stock (category, account_data) VALUES (?, ?)", (category, data))
    conn.commit()
    conn.close()

def clear_stock(category):
    conn = sqlite3.connect("bot_vault.db")
    c = conn.cursor()
    c.execute("DELETE FROM stock WHERE category=?", (category,))
    conn.commit()
    conn.close()

def get_history(user_id):
    conn = sqlite3.connect("bot_vault.db")
    c = conn.cursor()
    c.execute("SELECT category, account_data, date FROM history WHERE user_id=? ORDER BY id DESC LIMIT 5", (user_id,))
    res = c.fetchall()
    conn.close()
    return res

# ---------------------------------------------------------
# NOWPAYMENTS
# ---------------------------------------------------------
def create_invoice(amount, order_id):
    url = "https://api.nowpayments.io/v1/invoice"
    headers = {"x-api-key": NOWPAYMENTS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "price_amount": amount,
        "price_currency": "usd",
        "order_id": order_id,
        "order_description": "Deposit",
        "ipn_callback_url": f"https://{HEROKU_APP_NAME}.herokuapp.com/webhook",
        "success_url": "https://t.me",
        "cancel_url": "https://t.me"
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None

def save_order(payment_id, user_id, amount):
    conn = sqlite3.connect("bot_vault.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO orders (payment_id, user_id, amount, status) VALUES (?, ?, ?, 'waiting')", (payment_id, user_id, amount))
    conn.commit()
    conn.close()

def get_order(payment_id):
    conn = sqlite3.connect("bot_vault.db")
    c = conn.cursor()
    c.execute("SELECT user_id, amount, status FROM orders WHERE payment_id=?", (payment_id,))
    res = c.fetchone()
    conn.close()
    return res

def update_order(payment_id, status):
    conn = sqlite3.connect("bot_vault.db")
    c = conn.cursor()
    c.execute("UPDATE orders SET status=? WHERE payment_id=?", (status, payment_id))
    conn.commit()
    conn.close()

# ---------------------------------------------------------
# BOT COMMANDS
# ---------------------------------------------------------
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    balance = get_balance(user_id)
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🛒 Buy X ($0.15)", callback_data="buy_x"),
        types.InlineKeyboardButton("📧 Buy Mail ($0.10)", callback_data="buy_mail"),
        types.InlineKeyboardButton("💳 Deposit", callback_data="deposit"),
        types.InlineKeyboardButton("📜 History", callback_data="history"),
        types.InlineKeyboardButton("💰 Balance", callback_data="balance")
    )
    if is_admin(user_id):
        markup.add(types.InlineKeyboardButton("🔐 Admin", callback_data="admin"))
    
    bot.send_message(
        message.chat.id,
        f"✨ X Vault Bot ✨\n\n👤 ID: `{user_id}`\n💰 Balance: ${balance:.2f}\n\nရွေးချယ်ပါ-",
        parse_mode="Markdown",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    user_id = call.from_user.id
    
    if call.data == "buy_x":
        stock = get_stock('x')
        if stock == 0:
            bot.send_message(call.message.chat.id, "❌ X Account မရှိပါ")
            return
        bot.send_message(call.message.chat.id, f"🛒 X Account ($0.15)\nStock: {stock}\n\nအရေအတွက် ရိုက်ထည့်ပါ:")
        bot.register_next_step_handler(call.message, purchase, 'x', 0.15)
    
    elif call.data == "buy_mail":
        stock = get_stock('mail')
        if stock == 0:
            bot.send_message(call.message.chat.id, "❌ Outlook Mail မရှိပါ")
            return
        bot.send_message(call.message.chat.id, f"📧 Outlook Mail ($0.10)\nStock: {stock}\n\nအရေအတွက် ရိုက်ထည့်ပါ:")
        bot.register_next_step_handler(call.message, purchase, 'mail', 0.10)
    
    elif call.data == "deposit":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("$1", callback_data="dep_1"),
            types.InlineKeyboardButton("$5", callback_data="dep_5"),
            types.InlineKeyboardButton("$10", callback_data="dep_10"),
            types.InlineKeyboardButton("✏️ Custom", callback_data="dep_custom")
        )
        bot.edit_message_text("💳 Deposit\n\nပမာဏ ရွေးပါ:", call.message.chat.id, call.message.message_id, reply_markup=markup)
    
    elif call.data == "dep_custom":
        bot.send_message(call.message.chat.id, "💳 ပမာဏ ရိုက်ထည့်ပါ (အနည်းဆုံး $0.5):")
        bot.register_next_step_handler(call.message, custom_deposit)
    
    elif call.data.startswith("dep_"):
        amount = float(call.data.replace("dep_", ""))
        deposit_invoice(call.message, amount)
    
    elif call.data == "history":
        records = get_history(user_id)
        if not records:
            bot.send_message(call.message.chat.id, "📜 မှတ်တမ်း မရှိပါ")
        else:
            msg = "📜 Last 5 Purchases:\n\n"
            for r in records:
                msg += f"🔹 [{r[2]}] ({r[0].upper()}): `{r[1]}`\n"
            bot.send_message(call.message.chat.id, msg, parse_mode="Markdown")
    
    elif call.data == "balance":
        balance = get_balance(user_id)
        bot.send_message(call.message.chat.id, f"💰 Balance: ${balance:.2f}", parse_mode="Markdown")
    
    elif call.data == "admin":
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "⛔️ Admin မဟုတ်ပါ")
            return
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("➕ Add X", callback_data="a_addx"),
            types.InlineKeyboardButton("➕ Add Mail", callback_data="a_addmail"),
            types.InlineKeyboardButton("🗑️ Clear X", callback_data="a_clearx"),
            types.InlineKeyboardButton("🗑️ Clear Mail", callback_data="a_clearmail"),
            types.InlineKeyboardButton("📊 Stock", callback_data="a_stock"),
            types.InlineKeyboardButton("📢 Broadcast", callback_data="a_bc"),
            types.InlineKeyboardButton("💾 Backup", callback_data="a_backup")
        )
        bot.edit_message_text("🔐 Admin Panel", call.message.chat.id, call.message.message_id, reply_markup=markup)
    
    elif call.data == "a_addx":
        bot.send_message(call.message.chat.id, "✏️ `/addx user|pass`", parse_mode="Markdown")
    elif call.data == "a_addmail":
        bot.send_message(call.message.chat.id, "✏️ `/addmail mail|pass`", parse_mode="Markdown")
    elif call.data == "a_clearx":
        clear_stock('x')
        bot.send_message(call.message.chat.id, "✅ X Stock ဖျက်ပြီး")
    elif call.data == "a_clearmail":
        clear_stock('mail')
        bot.send_message(call.message.chat.id, "✅ Mail Stock ဖျက်ပြီး")
    elif call.data == "a_stock":
        bot.send_message(call.message.chat.id, f"📊 Stock\n\nX: {get_stock('x')}\nMail: {get_stock('mail')}", parse_mode="Markdown")
    elif call.data == "a_bc":
        bot.send_message(call.message.chat.id, "📢 `/bc message`", parse_mode="Markdown")
    elif call.data == "a_backup":
        try:
            with open("bot_vault.db", "rb") as f:
                bot.send_document(call.message.chat.id, f, caption="📦 Backup")
        except:
            bot.send_message(call.message.chat.id, "❌ Backup မရပါ")

# ---------------------------------------------------------
# PURCHASE
# ---------------------------------------------------------
def purchase(message, category, price):
    try:
        qty = int(message.text.strip())
        if qty <= 0:
            bot.send_message(message.chat.id, "❌ 1 ခုအနည်းဆုံး")
            return
        
        user_id = message.from_user.id
        total = round(qty * price, 2)
        balance = get_balance(user_id)
        
        if balance < total:
            bot.send_message(message.chat.id, f"❌ Balance မလုံလောက်\n💰 သင့်မှာ: ${balance:.2f}\n💰 လိုအပ်: ${total:.2f}")
            return
        
        conn = sqlite3.connect("bot_vault.db")
        c = conn.cursor()
        c.execute("SELECT id, account_data FROM stock WHERE category=? LIMIT ?", (category, qty))
        items = c.fetchall()
        
        if len(items) < qty:
            bot.send_message(message.chat.id, "❌ Stock မလုံလောက်")
            conn.close()
            return
        
        c.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (total, user_id))
        date_now = time.strftime("%Y-%m-%d %H:%M:%S")
        delivered = []
        
        for item_id, data in items:
            delivered.append(data)
            c.execute("DELETE FROM stock WHERE id=?", (item_id,))
            c.execute("INSERT INTO history (user_id, category, account_data, amount, date) VALUES (?, ?, ?, ?, ?)",
                     (user_id, category, data, price, date_now))
        
        conn.commit()
        conn.close()
        
        bot.send_message(
            message.chat.id,
            f"✅ Purchase Successful!\n\n📦 {category.upper()} x{qty}\n💰 ${total:.2f}\n\n" + "\n".join([f"`{d}`" for d in delivered]),
            parse_mode="Markdown"
        )
    except:
        bot.send_message(message.chat.id, "❌ ဂဏန်း ရိုက်ထည့်ပါ")

# ---------------------------------------------------------
# DEPOSIT
# ---------------------------------------------------------
def custom_deposit(message):
    try:
        amount = float(message.text.strip())
        if amount < 0.5:
            bot.send_message(message.chat.id, "❌ အနည်းဆုံး $0.5")
            return
        deposit_invoice(message, amount)
    except:
        bot.send_message(message.chat.id, "❌ ဂဏန်း ရိုက်ထည့်ပါ")

def deposit_invoice(message, amount):
    user_id = message.from_user.id
    order_id = f"DEP-{int(time.time())}-{user_id}"
    
    msg = bot.send_message(message.chat.id, "⏳ Generating invoice...")
    invoice = create_invoice(amount, order_id)
    
    if invoice and "invoice_url" in invoice:
        save_order(str(invoice["id"]), user_id, amount)
        bot.edit_message_text(
            f"💳 Deposit Invoice\n\n💰 ${amount:.2f}\n\n🔗 [Pay Now]({invoice['invoice_url']})",
            message.chat.id,
            msg.message_id,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    else:
        bot.edit_message_text("❌ Invoice မရပါ", message.chat.id, msg.message_id)

# ---------------------------------------------------------
# ADMIN COMMANDS
# ---------------------------------------------------------
@bot.message_handler(commands=['addx'])
def addx(message):
    if not is_admin(message.from_user.id):
        return
    accounts = message.text.replace("/addx", "").strip().split("\n")
    valid = [a.strip() for a in accounts if a.strip()]
    if not valid:
        bot.send_message(message.chat.id, "⚠️ `/addx user|pass`", parse_mode="Markdown")
        return
    for acc in valid:
        add_stock('x', acc)
    bot.send_message(message.chat.id, f"✅ X {len(valid)} ခုထည့်ပြီး")

@bot.message_handler(commands=['addmail'])
def addmail(message):
    if not is_admin(message.from_user.id):
        return
    accounts = message.text.replace("/addmail", "").strip().split("\n")
    valid = [a.strip() for a in accounts if a.strip()]
    if not valid:
        bot.send_message(message.chat.id, "⚠️ `/addmail mail|pass`", parse_mode="Markdown")
        return
    for acc in valid:
        add_stock('mail', acc)
    bot.send_message(message.chat.id, f"✅ Mail {len(valid)} ခုထည့်ပြီး")

@bot.message_handler(commands=['bc'])
def broadcast(message):
    if not is_admin(message.from_user.id):
        return
    text = message.text.replace("/bc", "").strip()
    if not text:
        bot.send_message(message.chat.id, "⚠️ `/bc message`", parse_mode="Markdown")
        return
    
    conn = sqlite3.connect("bot_vault.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users = c.fetchall()
    conn.close()
    
    success = 0
    for u in users:
        try:
            bot.send_message(u[0], f"📢 Announcement\n\n{text}")
            success += 1
            time.sleep(0.05)
        except:
            pass
    bot.send_message(message.chat.id, f"✅ {success} users ပို့ပြီး")

# ---------------------------------------------------------
# WEBHOOK
# ---------------------------------------------------------
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        if data and data.get('payment_status') in ['finished', 'confirmed']:
            payment_id = str(data.get('payment_id'))
            order = get_order(payment_id)
            if order and order[2] == 'waiting':
                user_id, amount, _ = order
                update_balance(user_id, amount)
                update_order(payment_id, 'finished')
                try:
                    bot.send_message(user_id, f"✅ Deposit Confirmed!\n\n💰 ${amount:.2f} ထည့်သွင်းပြီး")
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
    def run_flask():
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port)
    
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    
    print("Bot starting...")
    bot.remove_webhook()
    bot.polling(none_stop=True, interval=1)
