# Crypto <-> Fiat Telegram Bot (EN/TR)

This project is a Telegram bot (Python) that creates exchange orders for:

- **Crypto**: USDT, USDC, SOL, ETH
- **Fiat**: PLN, TRY
- **Directions**: Crypto->Fiat and Fiat->Crypto
- **Rate**: fetched from Binance Spot public API
- **Fees**: user enters a code to get 1% or 1.5% fee; otherwise default 2.5%
- **Proof**: after the user clicks "I sent", the bot collects:
  - TXID for crypto payments
  - Receipt (photo/document) or transfer reference text for fiat payments
- **Estimated transfer time**: shown after proof is submitted (configurable)
- **Languages**: English + Turkish

> Important: The bot **does not** automatically verify blockchain txids or bank transfers. It stores proof and notifies admins.

---

## 1) Edit config

You said you only want to change addresses/banks/estimated times.

### A) Copy env

```bash
cp .env.example .env
```

Edit `.env`:
- `TELEGRAM_BOT_TOKEN` (from BotFather)
- `ADMIN_IDS` (optional but recommended; comma-separated numeric Telegram IDs)
- `FEE_CODE_1P` and `FEE_CODE_15P` (your secret discount codes)

### B) Edit exchange config

Edit `config/exchange.yaml`:
- `bank_accounts`
- `crypto_deposit_addresses`
- `estimated_transfer_times`

---

## 2) Run locally

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m bot.main
```

---

## 3) Run on a server (simple)

### A) Ubuntu/Debian VPS

1. Upload the project folder (or unzip the ZIP) to the server.
2. Install requirements:

```bash
sudo apt update
sudo apt install -y python3 python3-venv unzip
```

3. Start:

```bash
cd crypto_exchange_bot
cp .env.example .env
nano .env
nano config/exchange.yaml

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m bot.main
```

---

## 4) Keep it running 24/7 (systemd)

Create service:

```bash
sudo nano /etc/systemd/system/crypto-exchange-bot.service
```

Paste (replace paths):

```ini
[Unit]
Description=Crypto Exchange Telegram Bot
After=network.target

[Service]
WorkingDirectory=/opt/crypto_exchange_bot
ExecStart=/opt/crypto_exchange_bot/.venv/bin/python -m bot.main
Restart=always
EnvironmentFile=/opt/crypto_exchange_bot/.env

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now crypto-exchange-bot
sudo systemctl status crypto-exchange-bot
```

Logs:

```bash
journalctl -u crypto-exchange-bot -f
```

---

## 5) Admin commands

- `/admin_orders` - list last 20 orders
- `/admin_done <id>` - mark done
- `/admin_cancel <id>` - mark cancelled

---

## Notes on rates

The bot tries to compute rates using Binance spot pairs.
- TRY usually works through `USDTTRY`.
- PLN is computed through EUR bridging: `EURUSDT` and `EURPLN`.

If Binance removes/adds pairs, the bot may fail for some routes.
