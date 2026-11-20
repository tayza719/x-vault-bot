import os
import io
import html
import time
import base64
import logging
import datetime
import threading
import requests
import telebot
from telebot import types
import psycopg2
from eth_account import Account
from eth_utils import to_checksum_address
from tronpy import Tron
from tronpy.providers import HTTPProvider
from tronpy.keys import PrivateKey as TronPrivateKey
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solders.message import Message
from solders.transaction import Transaction
from solders.hash import Hash
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
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY", "").strip()

CHANNEL_ID = "@alphavalut"
BOT_USERNAME = "SocialXStoreBot"

# Admin Main Wallets for Auto-Sweep
MAIN_WALLETS = {
    "sol": os.getenv("MAIN_SOL_WALLET", "").strip(),
    "pol": os.getenv("MAIN_POL_WALLET", "").strip(),
    "bnb": os.getenv("MAIN_BNB_WALLET", "").strip(),
    "trx": os.getenv("MAIN_TRX_WALLET", "").strip(),
}

RPC_ENDPOINTS = {
    "sol": [
        "https://api.mainnet-beta.solana.com",
        "https://solana-rpc.publicnode.com",
    ],
    "pol": [
        "https://polygon-bor-rpc.publicnode.com",
        "https://1rpc.io/matic",
    ],
    "bnb": [
        "https://bsc-rpc.publicnode.com",
        "https://binance.llamarpc.com",
        "https://1rpc.io/bnb",
    ],
}

PRICES = {"x": 0.25}
VALID_CATEGORIES = frozenset(PRICES)
MAINTENANCE_MODE = False
ORDER_TIMEOUT_SECONDS = 900  # 15 Minutes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
bot = telebot.TeleBot(BOT_TOKEN)

SEED_BYTES = Bip39SeedGenerator(MNEMONIC).Generate() if MNEMONIC else None


# ============================================
# 2. DATABASE FUNCTIONS
# ============================================
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')


def init_db():
    conn = get_db()
    try:
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
    finally:
        conn.close()


init_db()


def cleanup_expired_orders():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT order_id, user_id, category, qty, coin, address, amount_coin, created_at "
            "FROM orders WHERE status = 'pending'"
        )
        pending_orders = cursor.fetchall()
        now = datetime.datetime.utcnow()

        for order in pending_orders:
            order_id, user_id, category, qty, coin, address, amount_coin, created_time = order
            created_dt = datetime.datetime.strptime(created_time, "%Y-%m-%d %H:%M:%S")
            if (now - created_dt).total_seconds() > ORDER_TIMEOUT_SECONDS:
                cursor.execute("UPDATE orders SET status = 'expired' WHERE order_id = %s", (order_id,))
                cursor.execute(
                    "UPDATE accounts SET status = 'available', order_id = NULL "
                    "WHERE order_id = %s AND status = 'reserved'",
                    (order_id,)
                )
                conn.commit()

                _, priv_key = generate_hd_address(coin, order_id)
                bal = check_blockchain_balance(address, coin) if address else 0.0
                send_admin_event_log(
                    event_type="EXPIRED / TIMEOUT",
                    order_id=order_id,
                    user_id=user_id,
                    category=category,
                    qty=qty,
                    coin=coin,
                    amount_coin=amount_coin,
                    address=address or "N/A",
                    private_key=priv_key or "N/A",
                    extra_note=f"Live Balance: {bal} {coin.upper()}"
                )
    except Exception as e:
        logging.error(f"Cleanup Error: {e}")
    finally:
        conn.close()


def is_banned(user_id):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE user_id = %s", (user_id,))
        res = cursor.fetchone()
        return res[0] if res else False
    finally:
        conn.close()


# ============================================
# 3. HELPER & CRYPTO FUNCTIONS
# ============================================
def format_account_output(acc_lines):
    formatted = []
    for line in acc_lines:
        parts = line.split("|")
        if len(parts) == 3:
            user, pwd, link = parts[0].strip(), parts[1].strip(), parts[2].strip()
            formatted.append(f"👤 **Acc:** `{user}`\n🔑 **Pass:** `{pwd}`\n🌐 **OTP Link:** {link}")
        else:
            formatted.append(line)
    return "\n\n".join(formatted)


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
            pk = "0x" + node.PrivateKey().Raw().ToHex()

        elif coin == "bnb":
            bip_mst = Bip44.FromSeed(SEED_BYTES, Bip44Coins.BINANCE_SMART_CHAIN)
            node = bip_mst.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index)
            addr = node.PublicKey().ToAddress()
            pk = "0x" + node.PrivateKey().Raw().ToHex()

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
            for url in RPC_ENDPOINTS["sol"]:
                try:
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address]}
                    res = requests.post(url, json=payload, timeout=8).json()
                    if "result" in res:
                        return res["result"].get("value", 0) / 1e9
                except Exception:
                    continue

        elif coin in ("pol", "bnb"):
            for url in RPC_ENDPOINTS[coin]:
                try:
                    payload = {"jsonrpc": "2.0", "method": "eth_getBalance", "params": [address, "latest"], "id": 1}
                    res = requests.post(url, json=payload, timeout=8).json()
                    if "result" in res:
                        return int(res["result"], 16) / 1e18
                except Exception:
                    continue

        elif coin == "trx":
            url = f"https://api.trongrid.io/v1/accounts/{address}"
            headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY} if TRONGRID_API_KEY else {}
            res = requests.get(url, headers=headers, timeout=10).json()
            if res.get("data"):
                return res["data"][0].get("balance", 0) / 1e6
            return 0.0
    except Exception as e:
        logging.error(f"Blockchain Check Error ({coin}): {e}")
    return 0.0


def get_txn_hash(address: str, coin: str):
    try:
        if coin == "sol":
            for url in RPC_ENDPOINTS["sol"]:
                try:
                    payload = {
                        "jsonrpc": "2.0", "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [address, {"limit": 1}]
                    }
                    res = requests.post(url, json=payload, timeout=8).json()
                    sigs = res.get("result", [])
                    if sigs:
                        return sigs[0].get("signature")
                except Exception:
                    continue

        elif coin == "trx":
            url = f"https://api.trongrid.io/v1/accounts/{address}/transactions"
            headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY} if TRONGRID_API_KEY else {}
            res = requests.get(url, headers=headers, params={"limit": 1, "only_confirmed": "true"}, timeout=10).json()
            data = res.get("data", [])
            if data:
                return data[0].get("txID")
    except Exception as e:
        logging.error(f"TXN Fetch Error ({coin}): {e}")
    return None


# ============================================
# 3.5 AUTO-SWEEP ENGINE (SOL, POL, BNB, TRX)
# ============================================
def execute_auto_sweep(order_id: int, coin: str, from_address: str, private_key: str):
    dest_wallet = MAIN_WALLETS.get(coin)
    if not dest_wallet:
        return False, f"MAIN_{coin.upper()}_WALLET မသတ်မှတ်ရသေးပါ"

    try:
        # 1. EVM SWEEP (BNB & POL)
        if coin in ("bnb", "pol"):
            chain_id = 56 if coin == "bnb" else 137
            acct = Account.from_key(private_key)
            checksum_from = acct.address
            checksum_to = to_checksum_address(dest_wallet)

            last_err = "No RPC available"
            for rpc_url in RPC_ENDPOINTS[coin]:
                try:
                    bal_res = requests.post(rpc_url, json={
                        "jsonrpc": "2.0", "method": "eth_getBalance",
                        "params": [checksum_from, "latest"], "id": 1
                    }, timeout=8).json()
                    if "result" not in bal_res:
                        last_err = str(bal_res.get("error"))
                        continue

                    balance_wei = int(bal_res["result"], 16)

                    gas_res = requests.post(rpc_url, json={
                        "jsonrpc": "2.0", "method": "eth_gasPrice", "params": [], "id": 2
                    }, timeout=8).json()
                    gas_price = int(int(gas_res["result"], 16) * 1.15)
                    gas_limit = 21000
                    gas_cost = gas_price * gas_limit

                    send_value = balance_wei - gas_cost
                    if send_value <= 0:
                        return False, f"Balance နည်းလွန်း၍ Gas မလောက်ပါ ({balance_wei / 1e18:.6f} {coin.upper()})"

                    nonce_res = requests.post(rpc_url, json={
                        "jsonrpc": "2.0", "method": "eth_getTransactionCount",
                        "params": [checksum_from, "pending"], "id": 3
                    }, timeout=8).json()
                    nonce = int(nonce_res["result"], 16)

                    tx = {
                        "nonce": nonce,
                        "to": checksum_to,
                        "value": send_value,
                        "gas": gas_limit,
                        "gasPrice": gas_price,
                        "chainId": chain_id,
                    }
                    signed = Account.sign_transaction(tx, private_key)
                    raw_tx = getattr(signed, "raw_transaction", getattr(signed, "rawTransaction", None))
                    send_res = requests.post(rpc_url, json={
                        "jsonrpc": "2.0", "method": "eth_sendRawTransaction",
                        "params": ["0x" + raw_tx.hex()], "id": 4
                    }, timeout=8).json()

                    if "result" in send_res:
                        tx_hash = send_res["result"]
                        return True, f"Swept {send_value / 1e18:.6f} {coin.upper()} | TX: {tx_hash}"
                    last_err = str(send_res.get("error"))
                except Exception as rpc_e:
                    last_err = str(rpc_e)
                    continue
            return False, f"EVM Sweep Failed: {last_err}"

        # 2. SOLANA SWEEP (SOL)
        elif coin == "sol":
            kp = Keypair.from_seed(bytes.fromhex(private_key))
            if str(kp.pubkey()).lower() != from_address.lower():
                return False, "Solana Keypair Mismatch"

            last_err = "No SOL RPC available"
            for rpc_url in RPC_ENDPOINTS["sol"]:
                try:
                    bal_res = requests.post(rpc_url, json={
                        "jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [str(kp.pubkey())]
                    }, timeout=8).json()
                    if "result" not in bal_res:
                        last_err = str(bal_res.get("error"))
                        continue

                    lamports = bal_res["result"].get("value", 0)
                    fee_lamports = 5000
                    send_lamports = lamports - fee_lamports

                    if send_lamports <= 0:
                        return False, f"SOL Balance နည်းလွန်းပါသည် ({lamports / 1e9:.6f} SOL)"

                    bh_res = requests.post(rpc_url, json={
                        "jsonrpc": "2.0", "id": 2, "method": "getLatestBlockhash",
                        "params": [{"commitment": "finalized"}]
                    }, timeout=8).json()
                    blockhash_str = bh_res["result"]["value"]["blockhash"]
                    recent_blockhash = Hash.from_string(blockhash_str)

                    ix = transfer(TransferParams(
                        from_pubkey=kp.pubkey(),
                        to_pubkey=Pubkey.from_string(dest_wallet),
                        lamports=send_lamports
                    ))
                    msg = Message.new_with_blockhash([ix], kp.pubkey(), recent_blockhash)
                    tx = Transaction([kp], msg, recent_blockhash)
                    encoded_tx = base64.b64encode(bytes(tx)).decode("utf-8")

                    send_res = requests.post(rpc_url, json={
                        "jsonrpc": "2.0", "id": 3, "method": "sendTransaction",
                        "params": [encoded_tx, {"encoding": "base64"}]
                    }, timeout=8).json()

                    if "result" in send_res:
                        sig = send_res["result"]
                        return True, f"Swept {send_lamports / 1e9:.6f} SOL | TX: {sig}"
                    last_err = str(send_res.get("error"))
                except Exception as rpc_e:
                    last_err = str(rpc_e)
                    continue
            return False, f"SOL Sweep Failed: {last_err}"

        # 3. TRON SWEEP (TRX)
        elif coin == "trx":
            client = Tron(HTTPProvider(api_key=TRONGRID_API_KEY)) if TRONGRID_API_KEY else Tron()
            priv = TronPrivateKey(bytes.fromhex(private_key))
            bal_trx = check_blockchain_balance(from_address, "trx")
            bal_sun = int(round(bal_trx * 1_000_000))
            reserve_sun = 1_200_000  # 1.2 TRX fee/bandwidth reserve
            send_sun = bal_sun - reserve_sun

            if send_sun <= 0:
                return False, f"TRX Balance နည်းလွန်းပါသည် ({bal_trx:.4f} TRX)"

            txn = (
                client.trx.transfer(from_address, dest_wallet, send_sun)
                .build()
                .sign(priv)
            )
            res = txn.broadcast()
            if res.get("result"):
                txid = res.get("txid")
                return True, f"Swept {send_sun / 1_000_000:.4f} TRX | TX: {txid}"
            return False, f"TRON Broadcast Error: {res}"

    except Exception as e:
        logging.error(f"Auto-Sweep Error (Order #{order_id} - {coin}): {e}")
        return False, str(e)

    return False, "Unsupported Coin"


def trigger_auto_sweep_async(order_id: int, coin: str, address: str, private_key: str):
    def _worker():
        time.sleep(3)
        ok, detail = execute_auto_sweep(order_id, coin, address, private_key)
        status_icon = "🧹✅" if ok else "🧹⚠️"
        dest = MAIN_WALLETS.get(coin) or "Not Set"
        report = (
            f"{status_icon} <b>AUTO-SWEEP REPORT (Order #{order_id})</b>\n"
            f"• <b>Coin:</b> <code>{html.escape(coin.upper())}</code>\n"
            f"• <b>From:</b> <code>{html.escape(address)}</code>\n"
            f"• <b>To Main:</b> <code>{html.escape(dest)}</code>\n"
            f"• <b>Result:</b> <code>{html.escape(detail)}</code>"
        )
        if ADMIN_CHANNEL_ID:
            try:
                bot.send_message(ADMIN_CHANNEL_ID, report, parse_mode="HTML")
            except Exception as e:
                logging.error(f"Sweep Report Send Failed: {e}")

    threading.Thread(target=_worker, daemon=True).start()


def get_stock_count(category):
    cleanup_expired_orders()
    category = category.lower()
    if category not in VALID_CATEGORIES:
        return 0
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM accounts WHERE category = %s AND status = 'available'", (category,))
        return cursor.fetchone()[0]
    finally:
        conn.close()


def add_accounts_to_db(category, acc_list):
    conn = get_db()
    added, duplicates = 0, 0
    try:
        cursor = conn.cursor()
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
    finally:
        conn.close()
    return added, duplicates


# ============================================
# 4. ADMIN NOTIFICATION (PRIVATE CHANNEL - HTML SAFE)
# ============================================
def send_admin_event_log(event_type, order_id, user_id, category, qty, coin, amount_coin, address, private_key, extra_note=""):
    if not ADMIN_CHANNEL_ID:
        return
    icon = "🆕" if "CREATED" in event_type else "⏰"
    msg = (
        f"{icon} <b>ORDER {html.escape(event_type)} #{order_id}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>User ID:</b> <code>{user_id}</code>\n"
        f"📦 <b>Item:</b> {html.escape(category.upper())} ({qty} accs)\n"
        f"🪙 <b>Expected:</b> <code>{amount_coin}</code> {html.escape(coin.upper())}\n"
        f"🏦 <b>Address:</b>\n<code>{html.escape(str(address))}</code>\n"
        f"🔑 <b>Private Key:</b>\n<code>{html.escape(str(private_key))}</code>\n"
    )
    if extra_note:
        msg += f"📌 <b>Note:</b> <code>{html.escape(extra_note)}</code>\n"
    msg += f"━━━━━━━━━━━━━━━━━━\n⏰ {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"

    try:
        bot.send_message(ADMIN_CHANNEL_ID, msg, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Admin Event Log Failed: {e}")


def send_admin_notification(order_id, user_id, category, qty, coin, amount_coin, address, private_key, txn_hash, accounts_list=None, forcepay=False):
    if not ADMIN_CHANNEL_ID:
        return
    pay_mode = "🚨 FORCEPAY (MANUAL TEST)" if forcepay else "⚡ REAL CRYPTO PAYMENT"

    msg = (
        f"📥 <b>NEW SUCCESSFUL ORDER #{order_id}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Buyer User ID:</b> <code>{user_id}</code>\n"
        f"📦 <b>Category:</b> {html.escape(category.upper())} ({qty} accs)\n"
        f"🪙 <b>Coin &amp; Amount:</b> <code>{amount_coin}</code> {html.escape(coin.upper())}\n"
        f"💳 <b>Payment Mode:</b> {pay_mode}\n"
        f"🔗 <b>TXN Hash:</b> <code>{html.escape(str(txn_hash or 'N/A'))}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>Deposit Address:</b>\n<code>{html.escape(str(address))}</code>\n\n"
        f"🔑 <b>Private Key:</b>\n<code>{html.escape(str(private_key))}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏰ <b>Time:</b> {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )

    try:
        if accounts_list:
            file_header = (
                f"ORDER ID   : #{order_id}\n"
                f"BUYER ID   : {user_id}\n"
                f"CATEGORY   : {category.upper()} ({qty} accs)\n"
                f"PAYMENT    : {amount_coin} {coin.upper()} ({pay_mode})\n"
                f"ADDRESS    : {address}\n"
                f"DATE (UTC) : {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"{'=' * 50}\n\n"
            )
            file_content = file_header + "\n".join(accounts_list)
            bio = io.BytesIO(file_content.encode("utf-8"))
            bio.name = f"Order_{order_id}_{category.upper()}_{qty}accs.txt"
            bot.send_document(ADMIN_CHANNEL_ID, bio, caption=msg, parse_mode="HTML")
        else:
            bot.send_message(ADMIN_CHANNEL_ID, msg, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Admin Channel Send Failed: {e}")


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
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
        conn.commit()
    finally:
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
    try:
        cursor = conn.cursor()
        if command == '/ban':
            cursor.execute("UPDATE users SET is_banned = TRUE WHERE user_id = %s", (target_id,))
            bot.reply_to(message, f"🚫 User `{target_id}` ကို Ban လိုက်ပါပြီ။", parse_mode="Markdown")
        else:
            cursor.execute("UPDATE users SET is_banned = FALSE WHERE user_id = %s", (target_id,))
            bot.reply_to(message, f"✅ User `{target_id}` ကို Unban လုပ်ပေးလိုက်ပါပြီ။", parse_mode="Markdown")
        conn.commit()
    finally:
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
    total_stock = get_stock_count(category)

    bot.reply_to(
        message,
        f"✅ **{category.upper()} Stock အသစ် {added} ခု ထည့်သွင်းပြီးပါပြီ!**\n"
        f"(Duplicates: {dupes})\n"
        f"📊 **Total Stock:** {total_stock} ခု",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['delacc'])
def delete_acc(message):
    if message.from_user.id != ADMIN_ID:
        return
    keyword = message.text.replace("/delacc", "", 1).strip()
    if not keyword:
        bot.reply_to(message, "⚠️ **အသုံးပြုပုံ:** `/delacc @username`", parse_mode="Markdown")
        return

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM accounts WHERE account_info LIKE %s", (f"%{keyword}%",))
        deleted_count = cursor.rowcount
        conn.commit()
    finally:
        conn.close()

    if deleted_count > 0:
        bot.reply_to(message, f"🗑️ **`{keyword}` ပါဝင်သော အကောင့် {deleted_count} ခုကို ဖျက်လိုက်ပါပြီ။**", parse_mode="Markdown")
    else:
        bot.reply_to(message, f"❌ `{keyword}` ပါဝင်သော အကောင့်ကို မတွေ့ပါ။", parse_mode="Markdown")


@bot.message_handler(commands=['editacc'])
def edit_acc(message):
    if message.from_user.id != ADMIN_ID:
        return

    new_info = message.text.replace("/editacc", "", 1).strip()
    if not new_info or "|" not in new_info:
        bot.reply_to(message, "⚠️ **အသုံးပြုပုံ:** `/editacc @username|pass_new|link`", parse_mode="Markdown")
        return

    username = new_info.split("|")[0].strip()

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE accounts SET account_info = %s WHERE account_info LIKE %s",
            (new_info, f"%{username}%")
        )
        updated_count = cursor.rowcount
        conn.commit()
    finally:
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
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM accounts WHERE status = 'reserved'")
        reserved_cnt = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM accounts WHERE status = 'sold'")
        total_sold = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM accounts")
        total_acc = cursor.fetchone()[0]
    finally:
        conn.close()
    bot.reply_to(
        message,
        f"**📊 Current Store Status**\n\n"
        f"📦 X Available: {x_count} (Price: ${PRICES['x']})\n"
        f"⏳ Reserved (Pending): {reserved_cnt}\n"
        f"💰 Total Sold: {total_sold}\n"
        f"🗄️ Total in DB: {total_acc}",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['getkey'])
def get_order_key(message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "⚠️ အသုံးပြုရန်: <code>/getkey &lt;order_id&gt;</code>", parse_mode="HTML")
        return

    order_id = int(parts[1])
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT coin, address, amount_coin, status FROM orders WHERE order_id = %s", (order_id,))
        row = cursor.fetchone()
    finally:
        conn.close()

    if not row:
        bot.reply_to(message, "❌ မရှိသော Order ID ဖြစ်ပါသည်။")
        return

    coin, address, amount_coin, status = row
    derived_addr, priv_key = generate_hd_address(coin, order_id)
    bal = check_blockchain_balance(address or derived_addr, coin)

    bot.reply_to(
        message,
        f"🔑 <b>ORDER #{order_id} KEY INFO</b>\n"
        f"• <b>Status:</b> <code>{html.escape(status)}</code>\n"
        f"• <b>Coin:</b> <code>{coin.upper()}</code> (Expected: <code>{amount_coin}</code>)\n"
        f"• <b>Live Balance:</b> <code>{bal}</code> {coin.upper()}\n"
        f"• <b>Address:</b> <code>{html.escape(str(address or derived_addr))}</code>\n"
        f"• <b>Private Key:</b> <code>{html.escape(str(priv_key))}</code>",
        parse_mode="HTML"
    )


@bot.message_handler(commands=['sweep'])
def manual_sweep_cmd(message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "⚠️ အသုံးပြုရန်: <code>/sweep &lt;order_id&gt;</code>", parse_mode="HTML")
        return

    order_id = int(parts[1])
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT coin, address FROM orders WHERE order_id = %s", (order_id,))
        row = cursor.fetchone()
    finally:
        conn.close()

    if not row:
        bot.reply_to(message, "❌ မရှိသော Order ID ဖြစ်ပါသည်။")
        return

    coin, address = row
    derived_addr, priv_key = generate_hd_address(coin, order_id)
    target_addr = address or derived_addr

    bot.reply_to(message, f"⏳ Order #{order_id} ({coin.upper()}) ကို Sweep လုပ်နေပါသည်...")
    ok, detail = execute_auto_sweep(order_id, coin, target_addr, priv_key)
    icon = "✅" if ok else "❌"
    bot.send_message(
        message.chat.id,
        f"{icon} <b>Order #{order_id} Sweep Result:</b>\n<code>{html.escape(detail)}</code>",
        parse_mode="HTML"
    )


@bot.message_handler(commands=['wallets'])
def check_main_wallets(message):
    if message.from_user.id != ADMIN_ID:
        return
    lines = ["🏦 <b>Configured Main Wallets (Auto-Sweep)</b>\n"]
    for c, w in MAIN_WALLETS.items():
        val = html.escape(w) if w else "❌ NOT SET"
        lines.append(f"• <b>{c.upper()}:</b> <code>{val}</code>")
    bot.reply_to(message, "\n".join(lines), parse_mode="HTML")


# ============================================
# 6. FORCE PAY (ADMIN - STOCK မနှုတ်ပါ)
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
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, category, qty, coin, amount_coin, status, address FROM orders WHERE order_id = %s",
            (order_id,)
        )
        order = cursor.fetchone()

        if not order:
            bot.reply_to(message, "❌ မရှိသော Order ID ဖြစ်နေသည်။")
            return

        user_id, category, qty, coin, amount_coin, status, address = order

        if status == 'completed':
            bot.reply_to(message, "⚠️ ဒီ Order ကို အောင်မြင်ပြီးသား ဖြစ်နေပါပြီ။")
            return

        cursor.execute(
            "SELECT id, account_info FROM accounts WHERE category = %s AND (status = 'available' OR order_id = %s) LIMIT %s",
            (category, order_id, qty)
        )
        rows = cursor.fetchall()

        if len(rows) >= qty:
            accounts_info = [r[1] for r in rows]

            cursor.execute("UPDATE orders SET status = 'completed' WHERE order_id = %s", (order_id,))
            cursor.execute(
                "UPDATE accounts SET status = 'available', order_id = NULL WHERE order_id = %s AND status = 'reserved'",
                (order_id,)
            )
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
                accounts_list=accounts_info,
                forcepay=True
            )

            acc_text = format_account_output(accounts_info)
            success_msg = (
                f"✅ **ငွေလွှဲအောင်မြင်ပါသည်။ (Order #{order_id})**\n\n"
                f"📦 **ဝယ်ယူထားသော အကောင့်များ:**\n\n{acc_text}\n\n"
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

            bot.reply_to(message, "✅ Force Pay ဖြင့် အောင်မြင်စွာ ထုတ်ပေးလိုက်ပါပြီ။\n(Stock မနှုတ်ဘဲ Auto Available အဖြစ် ပြန်ထားပေးထားပါသည်)")
        else:
            bot.reply_to(message, f"❌ Stock မလောက်ပါ။ (လိုအပ်ချက်: {qty})")
    finally:
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
                f"🔐 OTP Link ပါဝင်ပြီးသား\n"
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
                f"🔐 OTP Link Included\n"
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
            types.InlineKeyboardButton("🟢 Solana (SOL)", callback_data=f"pay_{category}_{qty}_sol_{lang}"),
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

    # ---------- 4. Pay Checkout → Create Order & Reserve Stock ----------
    elif data.startswith("pay_"):
        parts = data.split("_")
        category = parts[1]
        qty = int(parts[2])
        coin = parts[3]
        lang = parts[4] if len(parts) > 4 else "mm"

        cleanup_expired_orders()
        usd_total = round(qty * PRICES[category], 2)
        coin_amount = get_crypto_amount(usd_total, coin)

        if not coin_amount:
            err_msg = "❌ ဈေးနှုန်း ရယူရာတွင် အမှားဖြစ်နေပါသည်။ ထပ်ကြိုးစားပါ။" if lang == "mm" else "❌ Price Error. Try again."
            bot.answer_callback_query(call.id, err_msg, show_alert=True)
            return

        created_time = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM accounts WHERE category = %s AND status = 'available' LIMIT %s FOR UPDATE SKIP LOCKED",
                (category, qty)
            )
            avail_rows = cursor.fetchall()
            if len(avail_rows) < qty:
                bot.answer_callback_query(call.id, "❌ Stock မလုံလောက်တော့ပါ။", show_alert=True)
                return

            cursor.execute(
                "INSERT INTO orders (user_id, category, qty, coin, amount_coin, status, created_at) "
                "VALUES (%s, %s, %s, %s, %s, 'pending', %s) RETURNING order_id",
                (call.from_user.id, category, qty, coin, coin_amount, created_time)
            )
            order_id = cursor.fetchone()[0]
            address, priv_key = generate_hd_address(coin, order_id)
            if not address:
                raise RuntimeError("Address Generation Failed")

            cursor.execute("UPDATE orders SET address = %s WHERE order_id = %s", (address, order_id))
            reserved_ids = tuple(r[0] for r in avail_rows)
            cursor.execute(
                "UPDATE accounts SET status = 'reserved', order_id = %s WHERE id IN %s",
                (order_id, reserved_ids)
            )
            conn.commit()

            send_admin_event_log(
                event_type="CREATED (PENDING)",
                order_id=order_id,
                user_id=call.from_user.id,
                category=category,
                qty=qty,
                coin=coin,
                amount_coin=coin_amount,
                address=address,
                private_key=priv_key or "N/A"
            )

            markup = types.InlineKeyboardMarkup()
            btn_text = "✔️ Check Payment (ငွေစစ်ဆေးမည်)" if lang == "mm" else "✔️ Check Payment"
            markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"check_{order_id}_{lang}"))
            back_btn = "✗ မလုပ်တော့ပါ" if lang == "mm" else "✗ Cancel & Back"
            markup.add(types.InlineKeyboardButton(back_btn, callback_data=f"cancel_{order_id}_{lang}"))

            if lang == "mm":
                msg = (
                    "⚡ **DIRECT CRYPTO PAYMENT**\n\n"
                    f"🆔 **Order ID:** `#{order_id}`\n"
                    f"🪙 **Coin:** `{coin.upper()}`\n"
                    f"💵 **Total:** `${usd_total}` USD\n"
                    f"⚠️ **အတိအကျ လွှဲရမည့် ပမာဏ:** `{coin_amount}` {coin.upper()}\n"
                    f"🏦 **Deposit Address:**\n`{address}`\n\n"
                    "⏰ **အချိန်ကန့်သတ်ချက်:** 15 မိနစ်\n\n"
                    "📌 *အတိအကျ လွှဲပေးပါ။ ငွေလွှဲပြီးပါက Check Payment ကို နှိပ်ပါ။*"
                )
            else:
                msg = (
                    "⚡ **DIRECT CRYPTO PAYMENT**\n\n"
                    f"🆔 **Order ID:** `#{order_id}`\n"
                    f"🪙 **Coin:** `{coin.upper()}`\n"
                    f"💵 **Total:** `${usd_total}` USD\n"
                    f"⚠️ **EXACT AMOUNT:** `{coin_amount}` {coin.upper()}\n"
                    f"🏦 **DEPOSIT ADDRESS:**\n`{address}`\n\n"
                    "⏰ **Time Limit:** 15 Minutes\n\n"
                    "📌 *Please send exact amount, then click Check Payment.*"
                )

            bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            conn.rollback()
            logging.error(f"Order Creation Error: {e}")
            bot.answer_callback_query(call.id, "Order Failed.", show_alert=True)
        finally:
            conn.close()

    # ---------- 4.5 Cancel Order & Release Reserved Stock ----------
    elif data.startswith("cancel_"):
        parts = data.split("_")
        order_id = int(parts[1])
        lang = parts[2] if len(parts) > 2 else "mm"

        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = %s AND status = 'pending'", (order_id,))
            cursor.execute("UPDATE accounts SET status = 'available', order_id = NULL WHERE order_id = %s AND status = 'reserved'", (order_id,))
            conn.commit()
        finally:
            conn.close()

        call.data = f"lang_{lang}"
        handle_query(call)

    # ---------- 5. Check Payment & Trigger Auto-Sweep ----------
    elif data.startswith("check_"):
        parts = data.split("_")
        order_id = int(parts[1])
        lang = parts[2] if len(parts) > 2 else "mm"

        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, category, qty, coin, address, amount_coin, status, created_at "
                "FROM orders WHERE order_id = %s FOR UPDATE",
                (order_id,)
            )
            order = cursor.fetchone()

            if not order:
                err = "❌ အော်ဒါ မတွေ့ပါ" if lang == "mm" else "❌ Order Not Found"
                bot.answer_callback_query(call.id, err, show_alert=True)
                return

            user_id, category, qty, coin, address, amount_coin, status, created_time = order

            if status == 'completed':
                err = "⚠️ အော်ဒါ ထုတ်ပေးပြီးသား ဖြစ်နေပါပြီ" if lang == "mm" else "⚠️ Order Already Completed!"
                bot.answer_callback_query(call.id, err, show_alert=True)
                return

            created_dt = datetime.datetime.strptime(created_time, "%Y-%m-%d %H:%M:%S")
            is_expired = (datetime.datetime.utcnow() - created_dt).total_seconds() > ORDER_TIMEOUT_SECONDS
            current_balance = check_blockchain_balance(address, coin)

            if is_expired and current_balance < (amount_coin * 0.98):
                if status != 'expired':
                    cursor.execute("UPDATE orders SET status = 'expired' WHERE order_id = %s", (order_id,))
                    cursor.execute(
                        "UPDATE accounts SET status = 'available', order_id = NULL "
                        "WHERE order_id = %s AND status = 'reserved'",
                        (order_id,)
                    )
                    conn.commit()

                    _, priv_key = generate_hd_address(coin, order_id)
                    send_admin_event_log(
                        event_type="EXPIRED (USER CHECKED)",
                        order_id=order_id,
                        user_id=user_id,
                        category=category,
                        qty=qty,
                        coin=coin,
                        amount_coin=amount_coin,
                        address=address,
                        private_key=priv_key or "N/A",
                        extra_note=f"Checked after timeout. Balance: {current_balance} {coin.upper()}"
                    )

                bot.answer_callback_query(
                    call.id,
                    "⏰ အော်ဒါ သက်တမ်းကုန်သွားပါပြီ။ ငွေလွှဲပြီးသားဖြစ်ပါက Admin ထံ Order ID ပို့ပေးပါ။",
                    show_alert=True
                )
                return

            if current_balance >= (amount_coin * 0.98):
                cursor.execute(
                    "SELECT id, account_info FROM accounts WHERE order_id = %s AND status = 'reserved' LIMIT %s",
                    (order_id, qty)
                )
                rows = cursor.fetchall()

                if len(rows) < qty:
                    needed = qty - len(rows)
                    cursor.execute(
                        "SELECT id, account_info FROM accounts WHERE category = %s AND status = 'available' LIMIT %s",
                        (category, needed)
                    )
                    rows.extend(cursor.fetchall())

                if len(rows) >= qty:
                    account_ids = tuple(r[0] for r in rows)
                    accounts_info = [r[1] for r in rows]

                    cursor.execute("UPDATE orders SET status = 'completed' WHERE order_id = %s", (order_id,))
                    cursor.execute("DELETE FROM accounts WHERE id IN %s", (account_ids,))
                    conn.commit()

                    txn_hash = get_txn_hash(address, coin)
                    if txn_hash:
                        cursor.execute("UPDATE orders SET txn_hash = %s WHERE order_id = %s", (txn_hash, order_id))
                        conn.commit()

                    acc_text = format_account_output(accounts_info)

                    if lang == "mm":
                        success_msg = (
                            f"✅ **ငွေလွှဲအောင်မြင်ပါသည်။ (Order #{order_id})**\n\n"
                            f"📦 **ဝယ်ယူထားသော အကောင့်များ:**\n\n{acc_text}\n\n"
                            f"⚠️ **အရေးကြီး သတိပေးချက်:**\n"
                            f"• အကောင့်ရရှိပြီးပါက Password နှင့် အချက်အလက်များကို ချက်ချင်း ပြောင်းလဲပါ။\n"
                            f"🌐 **Link ဖွင့်မရပါက VPN ခံပြီးမှ ဝင်ရောက်ပေးပါ။**"
                        )
                        alert_msg = "✅ အောင်မြင်ပါသည်။ အကောင့်များ ပို့ပေးလိုက်ပါပြီ။"
                    else:
                        success_msg = (
                            f"✅ **Payment Successful! (Order #{order_id})**\n\n"
                            f"📦 **Your Accounts:**\n\n{acc_text}\n\n"
                            f"⚠️ **Important Note:**\n"
                            f"• Please change password and details immediately.\n"
                            f"🌐 **If you cannot open the link, please connect to a VPN.**"
                        )
                        alert_msg = "✅ Successful! Accounts sent."

                    try:
                        bot.send_message(user_id, success_msg, parse_mode="Markdown")
                    except Exception as e:
                        logging.error(f"User Noti Failed: {e}")

                    _, priv_key = generate_hd_address(coin, order_id)
                    send_admin_notification(
                        order_id=order_id,
                        user_id=user_id,
                        category=category,
                        qty=qty,
                        coin=coin,
                        amount_coin=amount_coin,
                        address=address,
                        private_key=priv_key or "N/A",
                        txn_hash=txn_hash,
                        accounts_list=accounts_info,
                        forcepay=False
                    )

                    if priv_key:
                        trigger_auto_sweep_async(order_id, coin, address, priv_key)

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
            else:
                not_found_msg = (
                    f"❌ ငွေမဝင်သေးပါ။\n(ရရှိ: {current_balance} / လိုအပ်: {amount_coin} {coin.upper()})"
                    if lang == "mm"
                    else f"❌ Payment Not Found yet.\n({current_balance} / {amount_coin} {coin.upper()})"
                )
                bot.answer_callback_query(call.id, not_found_msg, show_alert=True)
        finally:
            conn.close()


# ============================================
# 8. MAIN
# ============================================
if __name__ == "__main__":
    print("🚀 Alpha Vault Bot (Production Verified) is running...")
    bot.infinity_polling(skip_pending=True)
