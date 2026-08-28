import logging
import os
import sqlite3
import datetime
import shutil
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext

# --- Config Setup ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7613605178"))
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")

ADMIN_USERNAME = "EchoWhisper"
DB_FILE = "store.db"
BANNED_FILE = "banned_users.txt"
BACKUP_DIR = "backups"
PRICE_USDT = 0.15

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# --- Database Setup & Helpers ---
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

def backup_db():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    mm_now = datetime.datetime.utcnow() + datetime.timedelta(hours=6, minutes=30)
    backup_filename = f"{BACKUP_DIR}/store_backup_{mm_now.strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(DB_FILE, backup_filename)
    return backup_filename

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

def check_banned(update: Update) -> bool:
    user = update.effective_user
    if user and str(user.id) in get_banned_users():
        if update.message:
            update.message.reply_text("🚫 အကောင့်ကို ဘော့တ်အသုံးပြုခွင့် ပိတ်ပင် (Ban) ထားပါသည်။", parse_mode="Markdown")
        elif update.callback_query:
            update.callback_query.answer("Banned User.", show_alert=True)
        return True
    return False

# --- NOWPayments API Helper ---
def create_nowpayments_invoice(amount, order_id, description):
    if not NOWPAYMENTS_API_KEY:
        return None
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

# --- User UI Handlers ---
def start(update: Update, context: CallbackContext):
    if check_banned(update): return
    keyboard = [
        [InlineKeyboardButton("🇲🇲 မြန်မာစာ", callback_data="lang_mm"), InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
    ]
    update.message.reply_text("🌐 **ကျေးဇူးပြု၍ ဘာသာစကား ရွေးချယ်ပါ**\n **Please select your language**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

def button_handler(update: Update, context: CallbackContext):
    if check_banned(update): return
    query = update.callback_query
    query.answer()
    data = query.data

    if data.startswith("lang_"):
        context.user_data['lang'] = data.split("_")[1]
        data = "show_home"

    lang = context.user_data.get('lang', 'mm')

    if data in ["show_home", "back_home"]:
        stock_qty = get_stock_count()
        welcome_text = (
            f"🛒 **Welcome to X (Twitter) Vault Store**\n\n"
            f"📦 **Item:** X (Twitter) New Account (Fresh)\n"
            f"💰 **Price:** `${PRICE_USDT} USDT` (per acc)\n"
            f"📊 **Available Stock:** `{stock_qty}` Accounts\n\n"
            f"👇 Click the button below to buy"
            if lang == "en" else
            f"🛒 **X (Twitter) Vault Store မှ ကြိုဆိုပါသည်**\n\n"
            f"📦 **အကောင့်အမျိုးအစား:** New X Account (Fresh)\n"
            f"💰 **၁ ကောင့် ဈေးနှုန်း:** `${PRICE_USDT} USDT`\n"
            f"📊 **ရနိုင်သော Stock:** `{stock_qty}` ကောင့်\n\n"
            f"👇 ဝယ်ယူလိုပါက အောက်ပါခလုတ်ကို နှိပ်ပါ"
        )
        keyboard = [
            [InlineKeyboardButton("🛒 Buy X Accounts" if lang == "en" else "🛒 X Account ဝယ်ယူမည်", callback_data="buy_x_acc")],
            [InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{ADMIN_USERNAME}")]
        ]
        query.edit_message_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data in ["buy_x_acc", "back_pay_select"]:
        stock_qty = get_stock_count()
        if stock_qty == 0:
            msg = "⚠️ Stock is currently empty." if lang == "en" else "⚠️ **လက်ရှိ Stock ကုန်နေပါသည်**"
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_home")]]
            query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            return

        keyboard = [
            [InlineKeyboardButton("2 accs", callback_data="qty_2"), InlineKeyboardButton("3 accs", callback_data="qty_3")],
            [InlineKeyboardButton("5 accs", callback_data="qty_5"), InlineKeyboardButton("10 accs", callback_data="qty_10")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_home")]
        ]
        select_msg = (
            f"🔢 Select quantity of accounts to buy (Available: `{stock_qty}`):"
            if lang == "en" else
            f"🔢 ဝယ်ယူလိုသော **အကောင့် အရေအတွက်** ကို ရွေးချယ်ပါ (ရရှိနိုင်သမျှ: `{stock_qty}`):"
        )
        query.edit_message_text(select_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("qty_"):
        qty = int(data.split("_")[1])
        stock_qty = get_stock_count()
        if qty > stock_qty:
            msg = f"⚠️ Only {stock_qty} accs left." if lang == "en" else f"⚠️ Stock တွင် {stock_qty} ကောင့်သာ ကျန်ပါတော့သည်။"
            query.answer(msg, show_alert=True)
            return
        
        total_price = round(qty * PRICE_USDT, 2)
        invoice_url = create_nowpayments_invoice(total_price, f"{query.from_user.id}_{qty}", f"{qty} X Accounts")
        
        if invoice_url:
            keyboard = [
                [InlineKeyboardButton("💳 Pay via NOWPayments (Crypto)", url=invoice_url)],
                [InlineKeyboardButton("🔙 Back", callback_data="buy_x_acc")]
            ]
            msg = (
                f"💳 **Crypto Instant Payment**\n\nQty: `{qty}` accs\nTotal Amount: `${total_price} USDT`\n\nClick the payment button below to complete purchase."
                if lang == "en" else
                f"💳 **Crypto အလိုအလျောက် ငွေပေးချေရန်**\n\nအရေအတွက်: `{qty}` ကောင့်\nကျသင့်ငွေ: `${total_price} USDT`\n\nအောက်ပါ ခလုတ်ကို နှိပ်၍ ငွေလွှဲပေးချေနိုင်ပါသည်။"
            )
        else:
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="buy_x_acc")]]
            msg = "⚠️ Payment Gateway connection error. Please contact Admin."

        query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# --- Admin Commands ---
def add_accounts_cmd(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID: return
    text = update.message.text.replace("/addacc", "").strip()
    if not text:
        update.message.reply_text("⚠️ ပုံစံ: `/addacc user|pass|link`", parse_mode="Markdown")
        return
    accs = [line.strip() for line in text.split("\n") if line.strip()]
    added_count = add_accounts_to_db(accs)
    update.message.reply_text(f"✅ Stock အသစ် `{added_count}` ကောင့် ထည့်ပြီးပါပြီ။\nလက်ရှိ Stock: `{get_stock_count()}` ကောင့်", parse_mode="Markdown")

def delete_acc_cmd(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID: return
    if not context.args:
        update.message.reply_text("⚠️ ပုံစံ: `/delacc @username`", parse_mode="Markdown")
        return
    username = context.args[0].strip().lstrip("@")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM accounts WHERE account_info LIKE ? AND status = 'available'", (f"%{username}%",))
    deleted_rows = cursor.rowcount
    conn.commit()
    conn.close()
    
    if deleted_rows > 0:
        update.message.reply_text(f"🗑️ `{username}` ({deleted_rows}) ခုကို ဖယ်ရှားပြီးပါပြီ။\nStock: `{get_stock_count()}`", parse_mode="Markdown")
    else:
        update.message.reply_text(f"❌ `{username}` ကို Stock ထဲတွင် မတွေ့ပါ၊", parse_mode="Markdown")

def check_stock_cmd(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT account_info FROM accounts WHERE status = 'available'")
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        update.message.reply_text("📦 Stock ထဲတွင် အကောင့်မရှိပါ။")
        return
    
    chunk_size = 50
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        accs_str = "\n".join([f"{i+j+1}. `{r[0]}`" for j, r in enumerate(chunk)])
        update.message.reply_text(f"📦 **လက်ရှိ Stock စာရင်း ({i+1} မှ {i+len(chunk)}):**\n\n{accs_str}", parse_mode="Markdown")

def history_cmd(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID: return
    limit = int(context.args[0]) if context.args and context.args[0].isdigit() else 20
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT account_info, buyer_id, sold_at FROM accounts WHERE status = 'sold' ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        update.message.reply_text("📜 ရောင်းရသည့် မှတ်တမ်း မရှိသေးပါ။")
        return
    
    history_msg = f"📜 **နောက်ဆုံး ရောင်းရသည့် အကောင့် ({len(rows)}) ခု:**\n\n"
    for r in rows:
        history_msg += f"👤 Buyer ID: `{r[1]}`\n📦 Acc: `{r[0]}`\n⏰ Time: `{r[2]}`\n-------------------\n"
    
    update.message.reply_text(history_msg[:4000], parse_mode="Markdown")

def backup_cmd(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID: return
    update.message.reply_text("🔄 Database ကို Backup ယူနေပါသည်...")
    try:
        backup_file = backup_db()
        update.message.reply_document(document=open(backup_file, 'rb'), caption=f"✅ Backup ပြီးပါပြီ: `{os.path.basename(backup_file)}`", parse_mode="Markdown")
    except Exception as e:
        update.message.reply_text(f"❌ Backup Error: `{e}`", parse_mode="Markdown")

def ban_user_cmd(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID: return
    if context.args:
        ban_user(context.args[0])
        update.message.reply_text(f"🚫 User ID `{context.args[0]}` ကို Ban လိုက်ပါပြီ။", parse_mode="Markdown")

def unban_user_cmd(update: Update, context: CallbackContext):
    if update.message.from_user.id != ADMIN_ID: return
    if context.args:
        unban_user(context.args[0])
        update.message.reply_text(f"✅ User ID `{context.args[0]}` ကို Unban လိုက်ပါပြီ။", parse_mode="Markdown")

def main():
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN is not set in Heroku Config Vars!")
        return

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("addacc", add_accounts_cmd))
    dp.add_handler(CommandHandler("delacc", delete_acc_cmd))
    dp.add_handler(CommandHandler("stock", check_stock_cmd))
    dp.add_handler(CommandHandler("history", history_cmd))
    dp.add_handler(CommandHandler("backup", backup_cmd))
    dp.add_handler(CommandHandler("ban", ban_user_cmd))
    dp.add_handler(CommandHandler("unban", unban_user_cmd))
    
    dp.add_handler(CallbackQueryHandler(button_handler))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
