from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("English", callback_data="lang:en"), InlineKeyboardButton("Turkce", callback_data="lang:tr")]
        ]
    )


def direction_kb(i18n) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(i18n.t("dir_crypto_to_fiat"), callback_data="dir:crypto_to_fiat")],
            [InlineKeyboardButton(i18n.t("dir_fiat_to_crypto"), callback_data="dir:fiat_to_crypto")],
        ]
    )


def asset_kb(assets: list[str], prefix: str) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for a in assets:
        row.append(InlineKeyboardButton(a, callback_data=f"{prefix}:{a}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("\u2716", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def confirm_sent_kb(i18n) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(i18n.t("btn_sent"), callback_data="sent")],
            [InlineKeyboardButton(i18n.t("btn_cancel"), callback_data="cancel")],
        ]
    )
