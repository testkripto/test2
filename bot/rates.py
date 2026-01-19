from __future__ import annotations

import time
import requests
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

BINANCE_BASE = "https://api.binance.com"
FRANKFURTER = "https://api.frankfurter.app/latest"


@dataclass
class RateQuote:
    rate: float  # to_asset per 1 from_asset
    path: str


class BinanceRates:
    def __init__(self, cache_ttl: int = 10, timeout: int = 8):
        self.cache_ttl = cache_ttl
        self.timeout = timeout
        self._exchange_info_ts: float = 0
        self._symbols: set[str] = set()
        self._price_cache: Dict[Tuple[str, str], Tuple[float, float]] = {}  # (base,quote)->(ts,price)

    # --- Stablecoin normalization: USDT and USDC priced as USDC ---
    @staticmethod
    def _norm(asset: str) -> str:
        a = asset.upper()
        if a in ("USDT", "USDC"):
            return "USDC"
        return a

    def _refresh_symbols(self) -> None:
        """
        Try to refresh Binance symbols, but NEVER hard-fail the whole bot.
        If Binance blocks /exchangeInfo, we fall back to "try ticker directly".
        """
        now = time.time()
        if self._symbols and (now - self._exchange_info_ts) < 3600:
            return

        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/exchangeInfo",
                timeout=self.timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if r.status_code != 200:
                # Keep symbols empty -> fallback mode
                self._symbols = set()
                self._exchange_info_ts = now
                return

            data = r.json()
            syms = set()
            for s in data.get("symbols", []):
                if s.get("status") == "TRADING":
                    sym = s.get("symbol")
                    if sym:
                        syms.add(sym)
            self._symbols = syms
            self._exchange_info_ts = now
        except Exception:
            # Keep empty -> fallback mode
            self._symbols = set()
            self._exchange_info_ts = now

    def _fetch_ticker(self, symbol: str) -> Optional[float]:
        try:
            r = requests.get(
                f"{BINANCE_BASE}/api/v3/ticker/price",
                params={"symbol": symbol},
                timeout=self.timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if r.status_code != 200:
                return None
            data = r.json()
            return float(data["price"])
        except Exception:
            return None

    def get_price(self, base: str, quote: str) -> Optional[float]:
        """Return price: 1 base = X quote."""
        base = base.upper()
        quote = quote.upper()
        if base == quote:
            return 1.0

        self._refresh_symbols()

        key = (base, quote)
        now = time.time()
        if key in self._price_cache:
            ts, val = self._price_cache[key]
            if now - ts < self.cache_ttl:
                return val

        direct = f"{base}{quote}"
        inverse = f"{quote}{base}"

        # If we have symbols list, use it. Otherwise fallback: try tickers anyway.
        if self._symbols:
            if direct in self._symbols:
                val = self._fetch_ticker(direct)
                if val is not None:
                    self._price_cache[key] = (now, val)
                    return val
            if inverse in self._symbols:
                inv = self._fetch_ticker(inverse)
                if inv and inv != 0:
                    val = 1.0 / inv
                    self._price_cache[key] = (now, val)
                    return val
        else:
            # Fallback mode: attempt direct, then inverse regardless of symbol list
            val = self._fetch_ticker(direct)
            if val is not None:
                self._price_cache[key] = (now, val)
                return val
            inv = self._fetch_ticker(inverse)
            if inv and inv != 0:
                val = 1.0 / inv
                self._price_cache[key] = (now, val)
                return val

        return None

    # --- Fiat FX fallback (for PLN) ---
    def _eur_to_pln(self) -> Optional[float]:
        try:
            r = requests.get(FRANKFURTER, params={"from": "EUR", "to": "PLN"}, timeout=self.timeout)
            if r.status_code != 200:
                return None
            data = r.json()
            return float(data["rates"]["PLN"])
        except Exception:
            return None

    def quote(self, from_asset: str, to_asset: str) -> RateQuote:
        """
        Computes rate with:
        - Stablecoin normalization: USDT and USDC are priced as USDC
        - TRY: Binance USDCTRY
        - PLN: Binance USDC/EUR + Frankfurter EUR/PLN
        """
        f_disp = from_asset.upper()
        t_disp = to_asset.upper()

        f = self._norm(f_disp)
        t = self._norm(t_disp)

        def show(a_disp: str, a_norm: str) -> str:
            return f"{a_disp}(as {a_norm})" if a_disp != a_norm else a_disp

        f_show = show(f_disp, f)
        t_show = show(t_disp, t)

        if f == t:
            return RateQuote(rate=1.0, path=f"{f_show}->{t_show}")

        # direct market (normalized)
        direct = self.get_price(f, t)
        if direct is not None:
            return RateQuote(rate=direct, path=f"{f_show}->{t_show}")

        crypto = {"USDC", "SOL", "ETH"}  # USDT normalized to USDC
        fiat = {"TRY", "PLN"}

        # Crypto->Crypto via USDC
        if f in crypto and t in crypto:
            f_usdc = self.get_price(f, "USDC") if f != "USDC" else 1.0
            usdc_t = self.get_price("USDC", t) if t != "USDC" else 1.0
            if f_usdc is None or usdc_t is None:
                raise ValueError("No route for crypto pair")
            return RateQuote(rate=f_usdc * usdc_t, path=f"{f_show}->USDC->{t_show}")

        # Crypto->Fiat
        if f in crypto and t_disp in fiat:
            f_usdc = self.get_price(f, "USDC") if f != "USDC" else 1.0
            if f_usdc is None:
                raise ValueError("No USDC route for crypto")

            if t_disp == "TRY":
                usdc_try = self.get_price("USDC", "TRY")
                if usdc_try is None:
                    raise ValueError("USDCTRY not available")
                return RateQuote(rate=f_usdc * usdc_try, path=f"{f_show}->USDC->TRY")

            if t_disp == "PLN":
                usdc_eur = self.get_price("USDC", "EUR")
                if usdc_eur is None:
                    # try inverse EURUSDC
                    eur_usdc = self.get_price("EUR", "USDC")
                    if eur_usdc and eur_usdc != 0:
                        usdc_eur = 1.0 / eur_usdc
                eur_pln = self._eur_to_pln()
                if usdc_eur is None or eur_pln is None:
                    raise ValueError("PLN route not available (USDC/EUR or EUR/PLN)")
                return RateQuote(rate=f_usdc * (usdc_eur * eur_pln), path=f"{f_show}->USDC->EUR->PLN")

        # Fiat->Crypto
        if f_disp in fiat and t in crypto:
            if f_disp == "TRY":
                usdc_try = self.get_price("USDC", "TRY")
                if usdc_try is None or usdc_try == 0:
                    raise ValueError("USDCTRY not available")
                try_usdc = 1.0 / usdc_try
                usdc_t = self.get_price("USDC", t) if t != "USDC" else 1.0
                if usdc_t is None:
                    raise ValueError("No USDC route for crypto")
                return RateQuote(rate=try_usdc * usdc_t, path=f"TRY->USDC->{t_show}")

            if f_disp == "PLN":
                eur_pln = self._eur_to_pln()
                usdc_eur = self.get_price("USDC", "EUR")
                if usdc_eur is None:
                    eur_usdc = self.get_price("EUR", "USDC")
                    if eur_usdc and eur_usdc != 0:
                        usdc_eur = 1.0 / eur_usdc
                if eur_pln is None or eur_pln == 0 or usdc_eur is None or usdc_eur == 0:
                    raise ValueError("PLN route not available (EUR/PLN or USDC/EUR)")
                # 1 PLN -> EUR -> USDC
                pln_eur = 1.0 / eur_pln
                eur_usdc_price = 1.0 / usdc_eur  # since usdc_eur = USDC per EUR? careful:
                # usdc_eur = 1 USDC = X EUR, so 1 EUR = 1/usdc_eur USDC
                eur_to_usdc = 1.0 / usdc_eur
                pln_usdc = pln_eur * eur_to_usdc
                usdc_t = self.get_price("USDC", t) if t != "USDC" else 1.0
                if usdc_t is None:
                    raise ValueError("No USDC route for crypto")
                return RateQuote(rate=pln_usdc * usdc_t, path=f"PLN->EUR->USDC->{t_show}")

        raise ValueError("Unsupported conversion")
