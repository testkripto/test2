from __future__ import annotations

import os
import logging
import yaml
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from .i18n import get_i18n
from .keyboards import lang_kb, direction_kb, asset_kb, confirm_sent_kb
from .rates import ManualVipRates
from .db import DB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
S_LANG, S_DIR, S_FROM, S_TO, S_AMOUNT, S_FEE, S_CONFIRM, S_PROOF, S_PAYOUT = range(9)


def load_config() -> dict:
    base = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(base, "config", "exchange.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


CONFIG = load_config()
FIAT = [c.upper() for c in CONFIG.get("fiat_currencies", ["PLN", "TRY"])]
CRYPTO = [c.upper() for c in CONFIG.get("crypto_currencies", ["USDT", "USDC", "SOL", "ETH"])]


def admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "").strip()
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except Exception:
            continue
    return ids


def is_admin(user_id: int) -> bool:
    return user_id in admin_ids()


def get_lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("lang", "en")


def fee_pct_from_code(code: str) -> float:
    code = (code or "").strip()
    c1 = (os.getenv("FEE_CODE_1P", "") or "").strip()
    c15 = (os.getenv("FEE_CODE_15P", "") or "").strip()
    c2 = (os.getenv("FEE_CODE_2P", "") or "").strip()
    default_fee = float(os.getenv("DEFAULT_FEE_PCT", "2.5"))

    if code and c1 and code == c1:
        return 1.0
    if code and c15 and code == c15:
        return 1.5
    if code and c2 and code == c2:
        return 2.0
    return default_fee


def eta_for(direction: str, from_asset: str, to_asset: str) -> str:
    """
    Supports either:
      estimated_transfer_times:
        crypto_to_fiat:
          USDT_TRY: "5–30 minutes"
        fiat_to_crypto:
          PLN_USDT: "30–90 minutes"
    """
    etas = CONFIG.get("estimated_transfer_times", {}) or {}
    d = etas.get(direction)
    if isinstance(d, dict):
        return str(d.get(f"{from_asset}_{to_asset}", "") or "")
    return ""


def admin_payment_details(direction: str, from_asset: str) -> str:
    """
    Admin sees what the user was shown:
      - crypto_to_fiat: our deposit address
      - fiat_to_crypto: our bank details
    """
    if direction == "crypto_to_fiat":
        addr = (CONFIG.get("crypto_deposit_addresses", {}) or {}).get(from_asset, {}) or {}
        return (
            f"💳 Deposit shown to user\n"
            f"Asset: {from_asset}\n"
            f"Network: {addr.get('network', '')}\n"
            f"Address: {addr.get('address', '')}"
        )
    else:
        bank = (CONFIG.get("bank_accounts", {}) or {}).get(from_asset, {}) or {}
        holder = bank.get("account_name", "") or bank.get("account_holder", "")
        swift = bank.get("swift", "")
        note = bank.get("note", "") or bank.get("title_hint", "")
        lines = [
            "🏦 Bank shown to user",
            f"Currency: {from_asset}",
            f"Bank: {bank.get('bank_name','')}",
            f"Holder: {holder}",
            f"IBAN: {bank.get('iban','')}",
        ]
        if swift:
            lines.append(f"SWIFT: {swift}")
        if note:
            lines.append(f"Note: {note}")
        return "\n".join(lines)


def payout_prompt_text(lang: str, direction: str) -> str:
    """
    After proof:
      - crypto_to_fiat => ask bank account (user payout destination)
      - fiat_to_crypto => ask crypto address (user payout destination)
    """
    if lang == "tr":
        if direction == "crypto_to_fiat":
            return (
                "✅ TXID alındı.\n\n"
                "Lütfen *ödeme yapılacak banka bilgilerini* gönder.\n"
                "Örnek:\n"
                "IBAN: PL...\n"
                "Ad Soyad: ...\n"
                "Banka: ... (opsiyonel)"
            )
        return (
            "✅ Dekont alındı.\n\n"
            "Lütfen *coin gönderilecek cüzdan adresini* gönder.\n"
            "Örnek:\n"
            "Adres: 0x...\n"
            "Network: ERC20/TRC20/SOL (opsiyonel)"
        )
    else:
        if direction == "crypto_to_fiat":
            return (
                "✅ TXID received.\n\n"
                "Please send your *bank payout details*.\n"
                "Example:\n"
                "IBAN: PL...\n"
                "Name: John Smith\n"
                "Bank: mBank (optional)"
            )
        return (
            "✅ Receipt received.\n\n"
            "Please send your *crypto receiving address*.\n"
            "Example:\n"
            "Address: 0x...\n"
            "Network: ERC20/TRC20/SOL (optional)"
        )


# ---------------- User Flow ----------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(get_i18n("en").t("choose_lang"), reply_markup=lang_kb())
    return S_LANG


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_i18n(get_lang(context)).t("choose_lang"), reply_markup=lang_kb())
    return S_LANG


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    i18n = get_i18n(get_lang(context))
    await update.message.reply_text(i18n.t("help"))


async def on_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, lang = q.data.split(":", 1)
    context.user_data["lang"] = lang
    i18n = get_i18n(lang)
    await q.edit_message_text(i18n.t("menu_title"), reply_markup=direction_kb(i18n))
    return S_DIR


async def on_dir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, direction = q.data.split(":", 1)
    context.user_data["direction"] = direction
    i18n = get_i18n(get_lang(context))

    if direction == "crypto_to_fiat":
        await q.edit_message_text(i18n.t("choose_from"), reply_markup=asset_kb(CRYPTO, "from"))
    else:
        await q.edit_message_text(i18n.t("choose_from"), reply_markup=asset_kb(FIAT, "from"))
    return S_FROM


async def on_from_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, asset = q.data.split(":", 1)
    context.user_data["from"] = asset
    i18n = get_i18n(get_lang(context))

    direction = context.user_data.get("direction")
    to_list = FIAT if direction == "crypto_to_fiat" else CRYPTO
    await q.edit_message_text(i18n.t("choose_to"), reply_markup=asset_kb(to_list, "to"))
    return S_TO


async def on_to_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, asset = q.data.split(":", 1)
    context.user_data["to"] = asset
    i18n = get_i18n(get_lang(context))
    await q.edit_message_text(i18n.t("enter_amount", currency=context.user_data["from"]))
    return S_AMOUNT


async def on_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    i18n = get_i18n(get_lang(context))
    txt = (update.message.text or "").strip().replace(",", ".")
    try:
        amount = float(txt)
        if amount <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text(i18n.t("bad_amount"))
        return S_AMOUNT

    context.user_data["amount_from"] = amount
    default_fee = float(os.getenv("DEFAULT_FEE_PCT", "2.5"))
    await update.message.reply_text(i18n.t("enter_fee_code", default_fee=default_fee))
    return S_FEE


async def on_fee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    i18n = get_i18n(get_lang(context))
    code = (update.message.text or "").strip()
    fee_pct = fee_pct_from_code(code)
    context.user_data["fee_pct"] = fee_pct

    from_asset = context.user_data["from"]
    to_asset = context.user_data["to"]
    amount_from = float(context.user_data["amount_from"])
    direction = context.user_data["direction"]

    rates: ManualVipRates = context.application.bot_data["rates"]
    try:
        quote = rates.quote(from_asset, to_asset, fee_pct=fee_pct)
    except Exception:
        logger.exception("rate error")
        await update.message.reply_text(i18n.t("unknown"))
        return ConversationHandler.END

    rate = quote.rate
    amount_to_gross = amount_from * rate
    amount_to_net = amount_to_gross * (1.0 - fee_pct / 100.0)

    db: DB = context.application.bot_data["db"]
    user = update.effective_user
    order_id = db.create_order(
        user_id=user.id,
        username=user.username or user.full_name,
        lang=get_lang(context),
        direction=direction,
        from_asset=from_asset,
        to_asset=to_asset,
        amount_from=amount_from,
        amount_to=amount_to_net,
        rate=rate,
        fee_pct=fee_pct,
        status="awaiting_proof",
    )
    context.user_data["order_id"] = order_id

    # User instructions (send to our deposit/bank)
    if direction == "crypto_to_fiat":
        addr = (CONFIG.get("crypto_deposit_addresses", {}) or {}).get(from_asset, {}) or {}
        instructions_text = i18n.t(
            "crypto_details",
            asset=from_asset,
            address=addr.get("address", ""),
            note=addr.get("network_note", "") or addr.get("network", ""),
            order_id=order_id,
        )
    else:
        bank = (CONFIG.get("bank_accounts", {}) or {}).get(from_asset, {}) or {}
        instructions_text = i18n.t(
            "bank_details",
            bank=bank.get("bank_name", ""),
            holder=bank.get("account_name", "") or bank.get("account_holder", ""),
            iban=bank.get("iban", ""),
            swift=bank.get("swift", ""),
            hint=bank.get("note", "") or bank.get("title_hint", ""),
            order_id=order_id,
        )

    msg = (
        f"*{i18n.t('quote_title')}*\n"
        f"{i18n.t('rate')}: `{rate:.8g}` ({quote.path})\n"
        f"{i18n.t('fee')}: `{fee_pct:.2f}%`\n\n"
        f"{i18n.t('you_send')}: `{amount_from:.8g} {from_asset}`\n"
        f"{i18n.t('you_receive')}: `{amount_to_net:.8g} {to_asset}`\n\n"
        f"*{i18n.t('instructions')}*\n{instructions_text}\n\n"
        f"{i18n.t('confirm_sent')}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=confirm_sent_kb(i18n))

    # Notify admins with details
    details = admin_payment_details(direction, from_asset)
    for aid in admin_ids():
        try:
            await context.bot.send_message(
                chat_id=aid,
                text=(
                    f"🆕 NEW ORDER #{order_id}\n"
                    f"User: @{user.username}" if user.username else f"User: {user.full_name}"
                ),
            )
            await context.bot.send_message(
                chat_id=aid,
                text=(
                    f"Direction: {direction}\n"
                    f"Pair: {from_asset}->{to_asset}\n"
                    f"Send: {amount_from:.8g} {from_asset}\n"
                    f"Receive: {amount_to_net:.8g} {to_asset}\n"
                    f"Fee: {fee_pct:.2f}%\n"
                    f"Rate: {rate:.8g} ({quote.path})\n\n"
                    f"{details}"
                ),
            )
        except Exception:
            pass

    return S_CONFIRM


async def on_confirm_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    i18n = get_i18n(get_lang(context))

    if q.data == "cancel":
        order_id = context.user_data.get("order_id")
        if order_id:
            db: DB = context.application.bot_data["db"]
            db.update_order(order_id, status="cancelled")
        await q.edit_message_text(i18n.t("cancelled"))
        return ConversationHandler.END

    if q.data == "sent":
        direction = context.user_data.get("direction")
        if direction == "crypto_to_fiat":
            await q.edit_message_text(i18n.t("ask_txid"))
        else:
            await q.edit_message_text(i18n.t("ask_receipt"))
        return S_PROOF

    return S_CONFIRM


async def on_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    User submits proof:
      - crypto_to_fiat: TXID (text)
      - fiat_to_crypto: receipt (photo/doc) OR reference (text)

    After proof, ask payout destination:
      - crypto_to_fiat: ask user's BANK details
      - fiat_to_crypto: ask user's CRYPTO address
    """
    i18n = get_i18n(get_lang(context))
    order_id = context.user_data.get("order_id")
    if not order_id:
        await update.message.reply_text(i18n.t("unknown"))
        return ConversationHandler.END

    direction = context.user_data.get("direction")
    db: DB = context.application.bot_data["db"]

    proof_type = None
    proof_value = None
    proof_file_id = None

    if direction == "crypto_to_fiat":
        txt = (update.message.text or "").strip()
        if not txt:
            await update.message.reply_text(i18n.t("ask_txid"))
            return S_PROOF
        proof_type = "txid"
        proof_value = txt
    else:
        if update.message.photo:
            proof_type = "receipt"
            proof_file_id = update.message.photo[-1].file_id
            proof_value = "photo"
        elif update.message.document:
            proof_type = "receipt"
            proof_file_id = update.message.document.file_id
            proof_value = update.message.document.file_name or "document"
        else:
            txt = (update.message.text or "").strip()
            if not txt:
                await update.message.reply_text(i18n.t("ask_receipt"))
                return S_PROOF
            proof_type = "reference"
            proof_value = txt

    db.update_order(
        int(order_id),
        status="awaiting_payout_details",
        proof_type=proof_type,
        proof_value=proof_value,
        proof_file_id=proof_file_id,
    )

    # Notify admins proof arrived
    for aid in admin_ids():
        try:
            await context.bot.send_message(
                chat_id=aid,
                text=f"✅ ORDER #{order_id} proof received: {proof_type} | {proof_value or ''}",
            )
            if proof_file_id and proof_type == "receipt":
                try:
                    await context.bot.send_photo(chat_id=aid, photo=proof_file_id, caption=f"Order #{order_id} receipt")
                except Exception:
                    await context.bot.send_document(chat_id=aid, document=proof_file_id, caption=f"Order #{order_id} receipt")
        except Exception:
            pass

    # Ask payout destination from user
    await update.message.reply_text(
        payout_prompt_text(get_lang(context), direction),
        parse_mode="Markdown",
    )
    return S_PAYOUT


async def on_payout_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Save user's payout destination text:
      - crypto_to_fiat => bank details
      - fiat_to_crypto => crypto address
    """
    i18n = get_i18n(get_lang(context))
    order_id = context.user_data.get("order_id")
    if not order_id:
        await update.message.reply_text(i18n.t("unknown"))
        return ConversationHandler.END

    txt = (update.message.text or "").strip()
    if not txt:
        await update.message.reply_text("Please send the details as text.")
        return S_PAYOUT

    direction = context.user_data.get("direction")
    payout_type = "bank" if direction == "crypto_to_fiat" else "crypto_address"

    db: DB = context.application.bot_data["db"]
    db.update_order(
        int(order_id),
        status="processing",
        payout_type=payout_type,
        payout_details=txt,
    )

    eta = eta_for(direction, context.user_data["from"], context.user_data["to"])
    if get_lang(context) == "tr":
        await update.message.reply_text(f"✅ Kaydedildi. İşleme alındı. ETA: {eta or 'yakında'}\nOrder #{order_id}")
    else:
        await update.message.reply_text(f"✅ Saved. We will process it. ETA: {eta or 'soon'}\nOrder #{order_id}")

    # Notify admins payout destination
    for aid in admin_ids():
        try:
            await context.bot.send_message(
                chat_id=aid,
                text=f"📌 ORDER #{order_id} payout details ({payout_type}):\n{txt}",
            )
        except Exception:
            pass

    return ConversationHandler.END


async def on_cancel_any(update: Update, context: ContextTypes.DEFAULT_TYPE):
    i18n = get_i18n(get_lang(context))
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(i18n.t("cancelled"))
    else:
        await update.message.reply_text(i18n.t("cancelled"))
    return ConversationHandler.END


# ---------------- Admin Commands ----------------

async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("Not admin.")
        return

    db: DB = context.application.bot_data["db"]
    orders = db.list_orders(20)
    lines = ["Last 20 orders:"]
    for o in orders:
        lines.append(
            f"#{o.id} {o.status} | {o.direction} | {o.from_asset}->{o.to_asset} | fee {o.fee_pct:.2f}%"
        )
    await update.message.reply_text("\n".join(lines))


async def admin_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage:
      /admin_complete <order_id> <txid_or_takeid>
    Marks order done + stores admin_transfer_id + notifies user.
    """
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("Not admin.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /admin_complete <order_id> <txid_or_takeid>")
        return

    try:
        oid = int(context.args[0])
    except Exception:
        await update.message.reply_text("Order id must be a number.")
        return

    transfer_id = " ".join(context.args[1:]).strip()

    db: DB = context.application.bot_data["db"]
    order = db.get_order(oid)
    if not order:
        await update.message.reply_text("Order not found.")
        return

    db.update_order(oid, status="done", admin_transfer_id=transfer_id)

    # Notify user with transfer id
    try:
        if order.lang == "tr":
            await context.bot.send_message(
                chat_id=order.user_id,
                text=f"✅ Order #{oid} tamamlandı.\nTransfer ID / TXID:\n{transfer_id}",
            )
        else:
            await context.bot.send_message(
                chat_id=order.user_id,
                text=f"✅ Order #{oid} completed.\nTransfer ID / TXID:\n{transfer_id}",
            )
    except Exception:
        pass

    await update.message.reply_text(f"✅ Marked order #{oid} as DONE and notified user.")


async def admin_receipt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage:
      /admin_receipt <order_id>
    Then send a photo/doc (receipt) in the next message.
    """
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("Not admin.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /admin_receipt <order_id>")
        return

    try:
        oid = int(context.args[0])
    except Exception:
        await update.message.reply_text("Order id must be a number. Example: /admin_receipt 25")
        return

    db: DB = context.application.bot_data["db"]
    order = db.get_order(oid)
    if not order:
        await update.message.reply_text("Order not found.")
        return

    context.user_data["awaiting_admin_receipt_order_id"] = oid
    await update.message.reply_text(f"✅ OK. Now send the receipt photo/document for Order #{oid}.")


async def on_admin_receipt_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin sends a photo/doc after /admin_receipt <order_id>.
    Attach it to DB and (optionally) forward to user.
    """
    user = update.effective_user
    if not is_admin(user.id):
        return

    oid = context.user_data.get("awaiting_admin_receipt_order_id")
    if not oid:
        return

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    if not file_id:
        await update.message.reply_text("Please send a photo or document.")
        return

    db: DB = context.application.bot_data["db"]
    order = db.get_order(int(oid))
    if not order:
        await update.message.reply_text("Order not found.")
        context.user_data.pop("awaiting_admin_receipt_order_id", None)
        return

    db.update_order(int(oid), admin_receipt_file_id=file_id)
    context.user_data.pop("awaiting_admin_receipt_order_id", None)

    await update.message.reply_text(f"✅ Receipt attached to Order #{oid}.")

    # Forward receipt to user (optional)
    try:
        if order.lang == "tr":
            await context.bot.send_message(chat_id=order.user_id, text=f"📎 Order #{oid} için dekont yüklendi.")
        else:
            await context.bot.send_message(chat_id=order.user_id, text=f"📎 Receipt uploaded for Order #{oid}.")
        try:
            await context.bot.send_photo(chat_id=order.user_id, photo=file_id)
        except Exception:
            await context.bot.send_document(chat_id=order.user_id, document=file_id)
    except Exception:
        pass


def build_app() -> Application:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

    db_path = os.getenv("DB_PATH", "./data/bot.sqlite3")

    app = Application.builder().token(token).build()

    # Manual VIP rates (per fee tier)
    app.bot_data["rates"] = ManualVipRates(CONFIG.get("manual_rates_by_fee", {}), default_fee=2.5)
    app.bot_data["db"] = DB(db_path)

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            S_LANG: [CallbackQueryHandler(on_lang, pattern=r"^lang:")],
            S_DIR: [CallbackQueryHandler(on_dir, pattern=r"^dir:")],
            S_FROM: [CallbackQueryHandler(on_from_asset, pattern=r"^from:")],
            S_TO: [CallbackQueryHandler(on_to_asset, pattern=r"^to:")],
            S_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_amount)],
            S_FEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_fee)],
            S_CONFIRM: [CallbackQueryHandler(on_confirm_buttons, pattern=r"^(sent|cancel)$")],
            S_PROOF: [
                MessageHandler(
                    (filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
                    on_proof,
                )
            ],
            S_PAYOUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_payout_details)],
        },
        fallbacks=[
            CallbackQueryHandler(on_cancel_any, pattern=r"^cancel$"),
            CommandHandler("start", cmd_start),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("lang", cmd_lang))

    app.add_handler(CommandHandler("admin_orders", admin_orders))
    app.add_handler(CommandHandler("admin_complete", admin_complete))
    app.add_handler(CommandHandler("admin_receipt", admin_receipt_cmd))

    # Admin receipt catcher (photo/doc) – only useful after /admin_receipt
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, on_admin_receipt_file))

    return app


def main() -> None:
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
