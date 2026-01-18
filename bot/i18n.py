from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class I18N:
    lang: str
    strings: Dict[str, str]

    def t(self, key: str, **kwargs) -> str:
        s = self.strings.get(key, key)
        try:
            return s.format(**kwargs)
        except Exception:
            return s


STRINGS = {
    "en": {
        "choose_lang": "Choose language / Dil secin:",
        "lang_set": "Language set to English.",
        "menu_title": "What do you want to do?",
        "dir_crypto_to_fiat": "Crypto -> Fiat",
        "dir_fiat_to_crypto": "Fiat -> Crypto",
        "choose_from": "Choose FROM currency:",
        "choose_to": "Choose TO currency:",
        "enter_amount": "Enter amount in {currency} (numbers only).",
        "enter_fee_code": "Enter fee code (optional).\n\nCodes: 1% or 1.5%. If you skip, default fee {default_fee}% is used.\n\nSend '-' to skip.",
        "quote_title": "Quote",
        "rate": "Rate",
        "fee": "Fee",
        "you_send": "You send",
        "you_receive": "You receive (est.)",
        "instructions": "Deposit instructions",
        "bank_details": "Bank transfer to:\nBank: {bank}\nHolder: {holder}\nIBAN: {iban}\nSWIFT: {swift}\nNote: {hint}\n\nTransfer title/reference: {order_id}",
        "crypto_details": "Send {asset} to:\nAddress: {address}\nNetwork: {note}\n\nMemo/Tag: (if required by your wallet)\n\nOrder ID: {order_id}",
        "confirm_sent": "After you send, press 'I sent'.",
        "btn_sent": "I sent",
        "btn_cancel": "Cancel",
        "ask_txid": "Please paste the TXID (transaction hash).",
        "ask_receipt": "Please upload bank receipt (photo or document). If you can't, send transfer reference text.",
        "proof_received": "Proof received. Estimated transfer time: {eta}\n\nYour order is now in processing.\nOrder ID: {order_id}",
        "cancelled": "Cancelled.",
        "bad_amount": "Please send a valid number (example: 100 or 100.5).",
        "unknown": "Something went wrong. Use /start to begin again.",
        "admin_new_order": "New order #{order_id}\nUser: {user}\nDirection: {direction}\nPair: {pair}\nAmount: {amount_from} {from_asset} -> {amount_to} {to_asset}\nFee: {fee_pct}%\nStatus: {status}",
        "admin_proof": "Order #{order_id} proof submitted.\nType: {proof_type}\nValue: {proof_value}",
        "admin_list_header": "Last orders:",
        "admin_marked": "Order #{order_id} marked as {status}.",
        "not_admin": "Not allowed.",
        "help": "Commands:\n/start - restart\n/lang - change language\n\nAdmins:\n/admin_orders - list\n/admin_done <id> - mark done\n/admin_cancel <id> - mark cancelled",
    },
    "tr": {
        "choose_lang": "Dil secin / Choose language:",
        "lang_set": "Dil Turkce olarak ayarlandi.",
        "menu_title": "Ne yapmak istiyorsunuz?",
        "dir_crypto_to_fiat": "Kripto -> Fiat",
        "dir_fiat_to_crypto": "Fiat -> Kripto",
        "choose_from": "GONDEREN para birimini secin:",
        "choose_to": "ALAN para birimini secin:",
        "enter_amount": "{currency} tutarini girin (sadece sayi).",
        "enter_fee_code": "Komisyon kodu girin (opsiyonel).\n\nKodlar: %1 veya %1.5. Gecerseniz varsayilan komisyon %{default_fee}.\n\nAtlamak icin '-' gonderin.",
        "quote_title": "Teklif",
        "rate": "Kur",
        "fee": "Komisyon",
        "you_send": "Siz gonderirsiniz",
        "you_receive": "Siz alirsiniz (tahmini)",
        "instructions": "Odeme bilgileri",
        "bank_details": "Banka havalesi:\nBanka: {bank}\nAlici: {holder}\nIBAN: {iban}\nSWIFT: {swift}\nNot: {hint}\n\nAciklama/Referans: {order_id}",
        "crypto_details": "{asset} gonderin:\nAdres: {address}\nAg: {note}\n\nGerekirse Memo/Tag\n\nSiparis ID: {order_id}",
        "confirm_sent": "Gonderim yaptiktan sonra 'Gonderdim' tusuna basin.",
        "btn_sent": "Gonderdim",
        "btn_cancel": "Iptal",
        "ask_txid": "Lutfen TXID (islem hash) gonderin.",
        "ask_receipt": "Lutfen banka dekontunu yukleyin (fotograf veya dosya). Yapamiyorsaniz transfer referans metnini yazin.",
        "proof_received": "Kanıt alindi. Tahmini transfer suresi: {eta}\n\nSiparisiniz isleme alindi.\nSiparis ID: {order_id}",
        "cancelled": "Iptal edildi.",
        "bad_amount": "Lutfen gecerli bir sayi gonderin (ornek: 100 veya 100.5).",
        "unknown": "Bir hata olustu. Yeniden baslamak icin /start.",
        "admin_new_order": "Yeni siparis #{order_id}\nKullanici: {user}\nYon: {direction}\nParite: {pair}\nTutar: {amount_from} {from_asset} -> {amount_to} {to_asset}\nKomisyon: %{fee_pct}\nDurum: {status}",
        "admin_proof": "Siparis #{order_id} kanıt gonderildi.\nTur: {proof_type}\nDeger: {proof_value}",
        "admin_list_header": "Son siparisler:",
        "admin_marked": "Siparis #{order_id} durumu: {status}.",
        "not_admin": "Yetkiniz yok.",
        "help": "Komutlar:\n/start - yeniden baslat\n/lang - dil degistir\n\nAdmin:\n/admin_orders - liste\n/admin_done <id> - tamamlandi\n/admin_cancel <id> - iptal",
    },
}


def get_i18n(lang: str) -> I18N:
    lang = (lang or "en").lower()
    if lang not in STRINGS:
        lang = "en"
    return I18N(lang=lang, strings=STRINGS[lang])
