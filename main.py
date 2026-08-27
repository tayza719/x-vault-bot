import os
import sqlite3
import json
import time
import requests
from flask import Flask, request
import telebot
from telebot import types

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8683965691:AAEthMpBt_RJNY1NPNDPtH-hSnTcpWFU0L8")
NOWPAYMENTS_API_KEY = os.environ.get("NOWPAYMENTS_API_KEY", "YOUR_NOWPAYMENTS_API_KEY")
ADMIN_ID = 7613605178
HEROKU_APP_NAME = os.environ.get("HEROKU_APP_NAME", "x-vault-bot-20----26")

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)

# ---------------------------------------------------------
# DATABASE SETUP (SQLite)
# ---------------------------------------------------------
def init_db():
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS stock (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT,
                        account_data TEXT
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
                        category TEXT,
                        qty INTEGER,
                        status TEXT
                    )''')
    conn.commit()
    conn.close()

init_db()

def is_admin(user):
    return user.id == ADMIN_ID or str(user.id) == str(os.environ.get("ADMIN_ID", ADMIN_ID))

# ---------------------------------------------------------
# NOWPAYMENTS API FUNCTIONS
# ---------------------------------------------------------
def create_nowpayments_invoice(amount_usd, order_id):
    url = "https://api.nowpayments.io/v1/invoice"
    headers = {
        "x-api-key": NOWPAYMENTS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "price_amount": amount_usd,
        "price_currency": "usd",
        "order_id": order_id,
        "order_description": "Vault Bot Purchase",
        "ipn_callback_url": f"https://{HEROKU_APP_NAME}.herokuapp.com/nowpayments_webhook",
        "success_url": "https://t.me",
        "cancel_url": "https://t.me"
    }
    try:
        res = requests.post(url, json=payload, headers=headers)
        return res.json()
    except Exception as e:
        print(f"API Error: {e}")
        return None

# ---------------------------------------------------------
# TELEGRAM BOT HANDLERS
# ---------------------------------------------------------
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_x = types.InlineKeyboardButton("🛒 Buy X Acc ($0.15)", callback_data="buy_x")
    btn_mail = types.InlineKeyboardButton("📧 Buy Outlook ($0.10)", callback_data="buy_mail")
    btn_history = types.InlineKeyboardButton("📜 History", callback_data="user_history")
    markup.add(btn_x, btn_mail, btn_history)
    
    welcome_text = "✨ *Welcome to X Vault Bot* ✨\n\nကျေးဇူးပြု၍ ဝယ်ယူလိုသော Product ကို ရွေးချယ်ပါ-"
    bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_listener(call):
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()

    if call.data == "buy_x":
        cursor.execute("SELECT COUNT(*) FROM stock WHERE category='x'")
        stock_count = cursor.fetchone()[0]
        bot.send_message(call.message.chat.id, f"🛒 *X Account ($0.15)*\nAvailable Stock: {stock_count}\n\nဝယ်ယူလိုသော အရေအတွက်ကို ရိုက်ပို့ပေးပါ (ဥပမာ- 2):", parse_mode="Markdown")
        bot.register_next_step_handler(call.message, process_qty, 'x', 0.15)

    elif call.data == "buy_mail":
        cursor.execute("SELECT COUNT(*) FROM stock WHERE category='mail'")
        stock_count = cursor.fetchone()[0]
        bot.send_message(call.message.chat.id, f"📧 *Outlook Mail ($0.10)*\nAvailable Stock: {stock_count}\n\nဝယ်ယူလိုသော အရေအတွက်ကို ရိုက်ပို့ပေးပါ (ဥပမာ- 5):", parse_mode="Markdown")
        bot.register_next_step_handler(call.message, process_qty, 'mail', 0.10)

    elif call.data == "user_history":
        cursor.execute("SELECT category, account_data, date FROM history WHERE user_id=? ORDER BY id DESC LIMIT 5", (call.from_user.id,))
        records = cursor.fetchall()
        if not records:
            bot.send_message(call.message.chat.id, "📜 ဝယ်ယူထားသော မှတ်တမ်း မရှိသေးပါ။")
        else:
            msg = "📜 *Your Last 5 Purchases:*\n\n"
            for r in records:
                msg += f"🔹 [{r[2]}] ({r[0].upper()}): `{r[1]}`\n"
            bot.send_message(call.message.chat.id, msg, parse_mode="Markdown")
            
    conn.close()

def process_qty(message, category, unit_price):
    try:
        qty = int(message.text)
        if qty <= 0:
            bot.send_message(message.chat.id, "❌ အရေအတွက် မှားယွင်းနေပါသည်။")
            return
        
        conn = sqlite3.connect("bot_vault.db")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM stock WHERE category=?", (category,))
        stock_count = cursor.fetchone()[0]
        conn.close()

        if qty > stock_count:
            bot.send_message(message.chat.id, f"❌ Stock မလုံလောက်ပါ။ (လက်ရှိ Stock: {stock_count})")
            return

        total_price = round(qty * unit_price, 2)
        order_id = f"ORD-{int(time.time())}-{message.from_user.id}"
        
        invoice = create_nowpayments_invoice(total_price, order_id)
        if invoice and "invoice_url" in invoice:
            payment_id = str(invoice["id"])
            
            conn = sqlite3.connect("bot_vault.db")
            cursor = conn.cursor()
            cursor.execute("INSERT INTO orders VALUES (?, ?, ?, ?, 'waiting')", 
                           (payment_id, message.from_user.id, category, qty))
            conn.commit()
            conn.close()

            pay_msg = (
                f"🧾 *Payment Invoice Generated*\n\n"
                f"📦 Item: *{category.upper()}*\n"
                f"🔢 Quantity: *{qty}*\n"
                f"💰 Total Amount: *${total_price} USDT*\n\n"
                f"🔗 ငွေလွှဲရန် Link ကို နှိပ်ပါ:\n{invoice['invoice_url']}\n\n"
                f"⏳ *30 မိနစ်အတွင်း ငွေလွှဲ အပြီးလုပ်ပေးပါ။ ငွေဝင်သည်နှင့် အကောင့် အလိုအလျောက် ရောက်လာပါမည်။*"
            )
            bot.send_message(message.chat.id, pay_msg, parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id, "❌ Payment Gateway ဖွင့်၍ မရပါ။ ခဏအကြာမှ ပြန်စမ်းပါ။")

    except ValueError:
        bot.send_message(message.chat.id, "❌ ကျေးဇူးပြု၍ ဂဏန်း အမှန် ရိုက်ထည့်ပါ။")

# ---------------------------------------------------------
# ADMIN COMMANDS
# ---------------------------------------------------------
@bot.message_handler(commands=['addx'])
def add_x_stock(message):
    if not is_admin(message.from_user): return
    accounts = message.text.replace("/addx", "").strip().split("\n")
    valid_accs = [a.strip() for a in accounts if a.strip()]
    if not valid_accs:
        bot.send_message(message.chat.id, "⚠️ ထည့်သွင်းမည့် X Stock Data ထည့်ပေးပါ (ဥပမာ- `/addx user|pass`)")
        return
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    for acc in valid_accs:
        cursor.execute("INSERT INTO stock (category, account_data) VALUES ('x', ?)", (acc,))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, f"✅ X Stock ({len(valid_accs)}) ခု ထည့်သွင်းပြီးပါပြီ။")

@bot.message_handler(commands=['addmail'])
def add_mail_stock(message):
    if not is_admin(message.from_user): return
    accounts = message.text.replace("/addmail", "").strip().split("\n")
    valid_accs = [a.strip() for a in accounts if a.strip()]
    if not valid_accs:
        bot.send_message(message.chat.id, "⚠️ ထည့်သွင်းမည့် Mail Stock Data ထည့်ပေးပါ (ဥပမာ- `/addmail mail|pass`)")
        return
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    for acc in valid_accs:
        cursor.execute("INSERT INTO stock (category, account_data) VALUES ('mail', ?)", (acc,))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, f"✅ Outlook Mail Stock ({len(valid_accs)}) ခု ထည့်သွင်းပြီးပါပြီ။")

@bot.message_handler(commands=['clearx'])
def clear_x_stock(message):
    if not is_admin(message.from_user): return
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM stock WHERE category='x'")
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "🗑️ X Stock အားလုံးကို ဖျက်ပြီးပါပြီ။")

@bot.message_handler(commands=['clearmail'])
def clear_mail_stock(message):
    if not is_admin(message.from_user): return
    conn = sqlite3.connect("bot_vault.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM stock WHERE category='mail'")
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "🗑️ Outlook Mail Stock အားလုံးကို ဖျက်ပြီးပါပြီ။")

@bot.message_handler(commands=['backup'])
def backup_db(message):
    if not is_admin(message.from_user): return
    try:
        with open("bot_vault.db", "rb") as doc:
            bot.send_document(message.chat.id, doc, caption="📦 Database Backup File")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Backup ယူ၍ မရပါ: {e}")

# ---------------------------------------------------------
# TELEGRAM WEBHOOK ROUTE (IMPORTANT FIX)
# ---------------------------------------------------------
@app.route(f"/{BOT_TOKEN}", methods=['POST'])
def telegram_webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "OK", 200
    return "Forbidden", 403

# ---------------------------------------------------------
# NOWPAYMENTS WEBHOOK
# ---------------------------------------------------------
@app.route('/nowpayments_webhook', methods=['POST'])
def nowpayments_webhook():
    data = request.json
    if data and data.get('payment_status') in ['finished', 'confirmed']:
        payment_id = str(data.get('payment_id'))
        
        conn = sqlite3.connect("bot_vault.db")
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, category, qty, status FROM orders WHERE payment_id=?", (payment_id,))
        order = cursor.fetchone()
        
        if order and order[3] == 'waiting':
            user_id, category, qty, _ = order
            cursor.execute("SELECT id, account_data FROM stock WHERE category=? LIMIT ?", (category, qty))
            items = cursor.fetchall()
            
            if len(items) >= qty:
                delivered_items = []
                date_now = time.strftime("%Y-%m-%d %H:%M:%S")
                for item_id, acc_data in items:
                    delivered_items.append(acc_data)
                    cursor.execute("DELETE FROM stock WHERE id=?", (item_id,))
                    cursor.execute("INSERT INTO history (user_id, category, account_data, amount, date) VALUES (?, ?, ?, 0, ?)",
                                   (user_id, category, acc_data, date_now))
                
                cursor.execute("UPDATE orders SET status='finished' WHERE payment_id=?", (payment_id,))
                conn.commit()
                
                acc_text = "\n".join([f"`{acc}`" for acc in delivered_items])
                success_msg = f"✅ *Payment Received & Confirmed!*\n\nသင့်အကောင့်(များ) ရောက်ရှိပါပြီ:\n\n{acc_text}\n\nဝယ်ယူအားပေးမှုကို ကျေးဇူးတင်ပါသည်။"
                bot.send_message(user_id, success_msg, parse_mode="Markdown")

        conn.close()
    return "OK", 200

@app.route('/')
def index():
    return "Bot Server is Running!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
