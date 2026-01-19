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
from .rates import BinanceRates
from .db import DB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
S_LANG, S_DIR, S_FROM, S_TO, S_AMOUNT, S_FEE, S_CONFIRM, S_PROOF = range(8)


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


def eta_for(direction: str, from_asset: str, to_asset: str) -> str:
    etas = CONFIG.get("estimated_transfer_times", {})
    defaults = etas.get("defaults", {})
    overrides = etas.get("overrides", {}).get(direction, {})
    key = f"{from_asset}->{to_asset}"
    if key in overrides:
        return str(overrides[key])
    return str(defaults.get(direction, "")) or ""


def fee_pct_from_code(code: str) -> float:
    code = (code or "").strip()
    c1 = (os.getenv("FEE_CODE_1P", "") or "").strip()
    c15 = (os.getenv("FEE_CODE_15P", "") or "").strip()
    default_fee = float(os.getenv("DEFAULT_FEE_PCT", "2.5"))
    if code and c1 and code == c1:
        return 1.0
    if code and c15 and code == c15:
        return 1.5
    if code in ("-", "", None):
        return default_fee
    # unknown code => default fee
    return default_fee


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
    if direction == "crypto_to_fiat":
        to_list = FIAT
    else:
        to_list = CRYPTO
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

    # Build quote
    from_asset = context.user_data["from"]
    to_asset = context.user_data["to"]
    amount_from = float(context.user_data["amount_from"])

    rates: BinanceRates = context.application.bot_data["rates"]
    try:
        quote = rates.quote(from_asset, to_asset, fee_pct=fee_pct)
        
    except Exception as e:
        logger.exception("rate error")
        await update.message.reply_text(i18n.t("unknown"))
        return ConversationHandler.END

    rate = quote.rate
    # Apply fee: user receives less in the TO currency
    amount_to_gross = amount_from * rate
    amount_to_net = amount_to_gross * (1.0 - fee_pct / 100.0)

    # Create order in DB
    db: DB = context.application.bot_data["db"]
    user = update.effective_user
    order_id = db.create_order(
        user_id=user.id,
        username=user.username or user.full_name,
        lang=get_lang(context),
        direction=context.user_data["direction"],
        from_asset=from_asset,
        to_asset=to_asset,
        amount_from=amount_from,
        amount_to=amount_to_net,
        rate=rate,
        fee_pct=fee_pct,
        status="awaiting_proof",
    )
    context.user_data["order_id"] = order_id

    # Instructions
    direction = context.user_data["direction"]
    instructions_text = ""
    if direction == "crypto_to_fiat":
        # user sends crypto to our address
        addr = CONFIG.get("crypto_deposit_addresses", {}).get(from_asset, {})
        instructions_text = i18n.t(
            "crypto_details",
            asset=from_asset,
            address=addr.get("address", ""),
            note=addr.get("network_note", ""),
            order_id=order_id,
        )
    else:
        # user sends fiat to our bank
        bank = CONFIG.get("bank_accounts", {}).get(from_asset, {})
        instructions_text = i18n.t(
            "bank_details",
            bank=bank.get("bank_name", ""),
            holder=bank.get("account_holder", ""),
            iban=bank.get("iban", ""),
            swift=bank.get("swift", ""),
            hint=bank.get("title_hint", ""),
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

    # Notify admins
    for aid in admin_ids():
        try:
            await context.bot.send_message(
                chat_id=aid,
                text=i18n.t(
                    "admin_new_order",
                    order_id=order_id,
                    user=f"@{user.username}" if user.username else user.full_name,
                    direction=direction,
                    pair=f"{from_asset}->{to_asset}",
                    amount_from=f"{amount_from:.8g}",
                    from_asset=from_asset,
                    amount_to=f"{amount_to_net:.8g}",
                    to_asset=to_asset,
                    fee_pct=f"{fee_pct:.2f}",
                    status="awaiting_proof",
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
        # Expect TXID as text
        txt = (update.message.text or "").strip()
        if not txt:
            await update.message.reply_text(i18n.t("ask_txid"))
            return S_PROOF
        proof_type = "txid"
        proof_value = txt
    else:
        # Expect receipt photo/doc, or reference text
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
        status="processing",
        proof_type=proof_type,
        proof_value=proof_value,
        proof_file_id=proof_file_id,
    )

    eta = eta_for(direction, context.user_data["from"], context.user_data["to"])
    await update.message.reply_text(i18n.t("proof_received", eta=eta, order_id=order_id))

    # Notify admins (include file if any)
    for aid in admin_ids():
        try:
            await context.bot.send_message(
                chat_id=aid,
                text=i18n.t(
                    "admin_proof",
                    order_id=order_id,
                    proof_type=proof_type,
                    proof_value=proof_value or "",
                ),
            )
            if proof_file_id and proof_type == "receipt":
                # Try to send as photo first; if it fails, send as document.
                try:
                    await context.bot.send_photo(chat_id=aid, photo=proof_file_id, caption=f"Order #{order_id} receipt")
                except Exception:
                    await context.bot.send_document(chat_id=aid, document=proof_file_id, caption=f"Order #{order_id} receipt")
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


# ---------------- Admin ----------------
async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text(get_i18n(get_lang(context)).t("not_admin"))
        return
    db: DB = context.application.bot_data["db"]
    orders = db.list_orders(20)
    lines = [get_i18n(get_lang(context)).t("admin_list_header")]
    for o in orders:
        lines.append(
            f"#{o.id} {o.status} | {o.direction} | {o.from_asset}->{o.to_asset} | {o.amount_from:.4g}->{o.amount_to:.4g} | fee {o.fee_pct:.2f}%"
        )
    await update.message.reply_text("\n".join(lines))


async def admin_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text(get_i18n(get_lang(context)).t("not_admin"))
        return
    if not context.args:
        return
    oid = int(context.args[0])
    db: DB = context.application.bot_data["db"]
    db.update_order(oid, status="done")
    await update.message.reply_text(get_i18n(get_lang(context)).t("admin_marked", order_id=oid, status="done"))


async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text(get_i18n(get_lang(context)).t("not_admin"))
        return
    if not context.args:
        return
    oid = int(context.args[0])
    db: DB = context.application.bot_data["db"]
    db.update_order(oid, status="cancelled")
    await update.message.reply_text(get_i18n(get_lang(context)).t("admin_marked", order_id=oid, status="cancelled"))


def build_app() -> Application:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

    db_path = os.getenv("DB_PATH", "./data/bot.sqlite3")
    cache_ttl = int(os.getenv("RATE_CACHE_TTL", "10"))

    app = Application.builder().token(token).build()
    app.bot_data["rates"] = BinanceRates(cache_ttl=cache_ttl)
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
            S_PROOF: [MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, on_proof)],
        },
        fallbacks=[CallbackQueryHandler(on_cancel_any, pattern=r"^cancel$"), CommandHandler("start", cmd_start)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("lang", cmd_lang))

    app.add_handler(CommandHandler("admin_orders", admin_orders))
    app.add_handler(CommandHandler("admin_done", admin_done))
    app.add_handler(CommandHandler("admin_cancel", admin_cancel))

    return app


def main() -> None:
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
