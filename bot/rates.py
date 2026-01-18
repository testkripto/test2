from __future__ import annotations

import time
import requests
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

BINANCE_BASE = "https://api.binance.com"


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

    def _refresh_symbols(self) -> None:
        now = time.time()
        if self._symbols and (now - self._exchange_info_ts) < 3600:
            return
        r = requests.get(f"{BINANCE_BASE}/api/v3/exchangeInfo", timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        syms = set()
        for s in data.get("symbols", []):
            if s.get("status") == "TRADING":
                syms.add(s.get("symbol"))
        self._symbols = syms
        self._exchange_info_ts = now

    def get_price(self, base: str, quote: str) -> Optional[float]:
        """Return price: 1 base = X quote."""
        base = base.upper()
        quote = quote.upper()
        if base == quote:
            return 1.0

        self._refresh_symbols()

        # Cached direct
        key = (base, quote)
        now = time.time()
        if key in self._price_cache:
            ts, val = self._price_cache[key]
            if now - ts < self.cache_ttl:
                return val

        direct = f"{base}{quote}"
        inverse = f"{quote}{base}"

        # Try direct
        if direct in self._symbols:
            val = self._fetch_ticker(direct)
            if val is not None:
                self._price_cache[key] = (now, val)
                return val

        # Try inverse and invert
        if inverse in self._symbols:
            inv = self._fetch_ticker(inverse)
            if inv and inv != 0:
                val = 1.0 / inv
                self._price_cache[key] = (now, val)
                return val

        return None

    def _fetch_ticker(self, symbol: str) -> Optional[float]:
        r = requests.get(
            f"{BINANCE_BASE}/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=self.timeout,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        try:
            return float(data["price"])
        except Exception:
            return None

    def quote(self, from_asset: str, to_asset: str) -> RateQuote:
        """Compute rate using Binance spot prices with simple bridges.

        Supported:
        - Crypto: USDT, USDC, SOL, ETH
        - Fiat: PLN, TRY

        For TRY: bridge via USDTTRY.
        For PLN: bridge via EUR using EURPLN and EURUSDT (inversion if needed).
        """
        f = from_asset.upper()
        t = to_asset.upper()

        if f == t:
            return RateQuote(rate=1.0, path=f"{f}->{t}")

        # First try direct market
        direct = self.get_price(f, t)
        if direct is not None:
            return RateQuote(rate=direct, path=f"{f}->{t}")

        crypto = {"USDT", "USDC", "SOL", "ETH"}
        fiat = {"PLN", "TRY"}

        if f in crypto and t in crypto:
            # Bridge through USDT
            f_usdt = self.get_price(f, "USDT") if f != "USDT" else 1.0
            usdt_t = self.get_price("USDT", t) if t != "USDT" else 1.0
            if f_usdt is None or usdt_t is None:
                raise ValueError("No route on Binance for crypto pair")
            return RateQuote(rate=f_usdt * usdt_t, path=f"{f}->USDT->{t}")

        # Crypto -> Fiat
        if f in crypto and t in fiat:
            f_usdt = self.get_price(f, "USDT") if f != "USDT" else 1.0
            if f_usdt is None:
                raise ValueError("No USDT route for crypto")
            if t == "TRY":
                usdt_try = self.get_price("USDT", "TRY")
                if usdt_try is None:
                    raise ValueError("USDTTRY not available")
                return RateQuote(rate=f_usdt * usdt_try, path=f"{f}->USDT->TRY")
            if t == "PLN":
                # USDT -> EUR via EURUSDT (invert)
                eur_usdt = self.get_price("EUR", "USDT")
                eur_pln = self.get_price("EUR", "PLN")
                if eur_usdt is None or eur_usdt == 0 or eur_pln is None:
                    raise ValueError("EUR bridges (EURUSDT/EURPLN) not available")
                usdt_eur = 1.0 / eur_usdt
                usdt_pln = usdt_eur * eur_pln
                return RateQuote(rate=f_usdt * usdt_pln, path=f"{f}->USDT->EUR->PLN")

        # Fiat -> Crypto
        if f in fiat and t in crypto:
            if f == "TRY":
                usdt_try = self.get_price("USDT", "TRY")
                if usdt_try is None or usdt_try == 0:
                    raise ValueError("USDTTRY not available")
                # 1 TRY = (1/usdt_try) USDT
                try_usdt = 1.0 / usdt_try
                usdt_to_t = self.get_price("USDT", t) if t != "USDT" else 1.0
                if usdt_to_t is None or usdt_to_t == 0:
                    raise ValueError("No USDT route for crypto")
                return RateQuote(rate=try_usdt * usdt_to_t, path=f"TRY->USDT->{t}")

            if f == "PLN":
                eur_pln = self.get_price("EUR", "PLN")
                eur_usdt = self.get_price("EUR", "USDT")
                if eur_pln is None or eur_pln == 0 or eur_usdt is None:
                    raise ValueError("EUR bridges (EURPLN/EURUSDT) not available")
                # 1 PLN = (1/eur_pln) EUR
                pln_eur = 1.0 / eur_pln
                # EUR -> USDT
                eur_to_usdt = eur_usdt
                pln_usdt = pln_eur * eur_to_usdt
                usdt_to_t = self.get_price("USDT", t) if t != "USDT" else 1.0
                if usdt_to_t is None:
                    raise ValueError("No USDT route for crypto")
                return RateQuote(rate=pln_usdt * usdt_to_t, path=f"PLN->EUR->USDT->{t}")

        # Fiat -> Fiat
        if f in fiat and t in fiat:
            direct = self.get_price(f, t)
            if direct is not None:
                return RateQuote(rate=direct, path=f"{f}->{t}")
            # Bridge via USDT
            f_usdt = self.quote(f, "USDT").rate
            usdt_t = self.quote("USDT", t).rate
            return RateQuote(rate=f_usdt * usdt_t, path=f"{f}->USDT->{t}")

        raise ValueError("Unsupported conversion")
