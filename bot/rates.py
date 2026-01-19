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

    # --- NEW: normalize stablecoins for pricing ---
    @staticmethod
    def _norm(asset: str) -> str:
        """
        Pricing normalization:
        - USDT and USDC are priced as USDC
        """
        a = asset.upper()
        if a in ("USDT", "USDC"):
            return "USDC"
        return a

    @staticmethod
    def _disp(asset: str) -> str:
        return asset.upper()

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

        NOTE (your requested behavior):
        - If user chooses USDT, we price it as USDC (USDT/USDC normalized to USDC).
        """

        # Keep what user selected for display
        f_disp = self._disp(from_asset)
        t_disp = self._disp(to_asset)

        # Normalize for pricing (USDT -> USDC)
        f = self._norm(f_disp)
        t = self._norm(t_disp)

        # Helper to show the user that USDT is being priced as USDC
        def fmt(a_disp: str, a_norm: str) -> str:
            return f"{a_disp}(priced as {a_norm})" if a_disp != a_norm else a_disp

        f_show = fmt(f_disp, f)
        t_show = fmt(t_disp, t)

        if f == t:
            # Even if user picked USDT->USDC, normalization makes them equal
            return RateQuote(rate=1.0, path=f"{f_show}->{t_show}")

        # First try direct market using normalized symbols
        direct = self.get_price(f, t)
        if direct is not None:
            return RateQuote(rate=direct, path=f"{f_show}->{t_show}")

        crypto = {"USDT", "USDC", "SOL", "ETH"}
        fiat = {"PLN", "TRY"}

        # IMPORTANT: treat normalized stables as crypto too
        # (because f/t are normalized, they may be USDC not USDT)
        crypto_norm = {"USDC", "SOL", "ETH"}  # USDT is normalized away
        # But user can still pick USDT; f_disp/t_disp may include it.
        # We check membership using normalized f/t against crypto_norm,
        # and also allow original crypto set checks where needed.

        if f in crypto_norm and t in crypto_norm:
            # Bridge through USDC (instead of USDT)
            f_usdc = self.get_price(f, "USDC") if f != "USDC" else 1.0
            usdc_t = self.get_price("USDC", t) if t != "USDC" else 1.0
            if f_usdc is None or usdc_t is None:
                raise ValueError("No route on Binance for crypto pair")
            return RateQuote(rate=f_usdc * usdc_t, path=f"{f_show}->USDC->{t_show}")

        # Crypto -> Fiat
        if f in crypto_norm and t_disp in fiat:
            f_usdc = self.get_price(f, "USDC") if f != "USDC" else 1.0
            if f_usdc is None:
                raise ValueError("No USDC route for crypto")

            if t_disp == "TRY":
                usdc_try = self.get_price("USDC", "TRY")
                if usdc_try is None:
                    raise ValueError("USDCTRY not available")
                return RateQuote(rate=f_usdc * usdc_try, path=f"{f_show}->USDC->TRY")

            if t_disp == "PLN":
                # USDC -> EUR via EURUSDC (invert if needed), then EUR->PLN
                eur_usdc = self.get_price("EUR", "USDC")
                eur_pln = self.get_price("EUR", "PLN")
                if eur_usdc is None or eur_usdc == 0 or eur_pln is None:
                    raise ValueError("EUR bridges (EURUSDC/EURPLN) not available")
                usdc_eur = 1.0 / eur_usdc
                usdc_pln = usdc_eur * eur_pln
                return RateQuote(rate=f_usdc * usdc_pln, path=f"{f_show}->USDC->EUR->PLN")

        # Fiat -> Crypto
        if f_disp in fiat and t in crypto_norm:
            if f_disp == "TRY":
                usdc_try = self.get_price("USDC", "TRY")
                if usdc_try is None or usdc_try == 0:
                    raise ValueError("USDCTRY not available")
                # 1 TRY = (1/usdc_try) USDC
                try_usdc = 1.0 / usdc_try
                usdc_to_t = self.get_price("USDC", t) if t != "USDC" else 1.0
                if usdc_to_t is None or usdc_to_t == 0:
                    raise ValueError("No USDC route for crypto")
                return RateQuote(rate=try_usdc * usdc_to_t, path=f"TRY->USDC->{t_show}")

            if f_disp == "PLN":
                eur_pln = self.get_price("EUR", "PLN")
                eur_usdc = self.get_price("EUR", "USDC")
                if eur_pln is None or eur_pln == 0 or eur_usdc is None:
                    raise ValueError("EUR bridges (EURPLN/EURUSDC) not available")
                # 1 PLN = (1/eur_pln) EUR
                pln_eur = 1.0 / eur_pln
                # EUR -> USDC
                eur_to_usdc = eur_usdc
                pln_usdc = pln_eur * eur_to_usdc
                usdc_to_t = self.get_price("USDC", t) if t != "USDC" else 1.0
                if usdc_to_t is None:
                    raise ValueError("No USDC route for crypto")
                return RateQuote(rate=pln_usdc * usdc_to_t, path=f"PLN->EUR->USDC->{t_show}")

        # Fiat -> Fiat
        if f_disp in fiat and t_disp in fiat:
            direct = self.get_price(f_disp, t_disp)
            if direct is not None:
                return RateQuote(rate=direct, path=f"{f_disp}->{t_disp}")
            # Bridge via USDC
            f_usdc = self.quote(f_disp, "USDC").rate
            usdc_t = self.quote("USDC", t_disp).rate
            return RateQuote(rate=f_usdc * usdc_t, path=f"{f_disp}->USDC->{t_disp}")

        raise ValueError("Unsupported conversion")
