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
