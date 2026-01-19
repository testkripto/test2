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

    @staticmethod
    def _norm(asset: str) -> str:
        # Your rule: USDT & USDC priced as "USDC"
        a = asset.upper()
        if a in ("USDT", "USDC"):
            return "USDC"
        return a

    def _refresh_symbols(self) -> None:
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
                self._symbols = set()
                self._exchange_info_ts = now
                return
            data = r.json()
            self._symbols = {s["symbol"] for s in data.get("symbols", []) if s.get("status") == "TRADING" and s.get("symbol")}
            self._exchange_info_ts = now
        except Exception:
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
            return float(r.json()["price"])
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
            # fallback mode
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

    def _eur_to_pln(self) -> Optional[float]:
        try:
            r = requests.get(FRANKFURTER, params={"from": "EUR", "to": "PLN"}, timeout=self.timeout)
            if r.status_code != 200:
                return None
            return float(r.json()["rates"]["PLN"])
        except Exception:
            return None

    def _usdc_to_usdt(self) -> float:
        """
        Convert USDC->USDT if market exists; else assume ~1.0.
        This keeps your 'price as USDC' logic but allows using USDT pairs like USDTTRY.
        """
        p = self.get_price("USDC", "USDT")
        if p is None:
            p = self.get_price("USDT", "USDC")
            if p and p != 0:
                return 1.0 / p
        return float(p) if p else 1.0

    def quote(self, from_asset: str, to_asset: str) -> RateQuote:
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

        crypto = {"USDC", "ETH", "SOL"}  # USDT normalized away
        fiat = {"TRY", "PLN"}

        # ---- TRY pricing ----
        # Use USDTTRY (more likely) and bridge USDC->USDT if needed.
        def usdc_to_try_rate() -> Optional[float]:
            usdt_try = self.get_price("USDT", "TRY")
            if usdt_try is None:
                return None
            usdc_usdt = self._usdc_to_usdt()
            # 1 USDC ~= X USDT, so 1 USDC in TRY:
            return usdc_usdt * usdt_try

        # ---- PLN pricing ----
        # Use EURUSDT + EURPLN(Frankfurter). Much more reliable than USDC/EUR.
        def usdc_to_pln_rate() -> Optional[float]:
            eur_pln = self._eur_to_pln()
            if eur_pln is None:
                return None
            eur_usdt = self.get_price("EUR", "USDT")  # 1 EUR = X USDT
            if eur_usdt is None or eur_usdt == 0:
                return None
            usdt_eur = 1.0 / eur_usdt                 # 1 USDT = X EUR
            usdt_pln = usdt_eur * eur_pln             # 1 USDT = X PLN
            usdc_usdt = self._usdc_to_usdt()
            return usdc_usdt * usdt_pln               # 1 USDC = X PLN

        # direct market for non-fiat conversions
        direct = self.get_price(f, t)
        if direct is not None:
            return RateQuote(rate=direct, path=f"{f_show}->{t_show}")

        # Crypto -> Fiat
        if f in crypto and t_disp in fiat:
            f_usdc = self.get_price(f, "USDC") if f != "USDC" else 1.0
            if f_usdc is None:
                raise ValueError("No USDC route for crypto")

            if t_disp == "TRY":
                r = usdc_to_try_rate()
                if r is None:
                    raise ValueError("TRY route not available (USDTTRY)")
                return RateQuote(rate=f_usdc * r, path=f"{f_show}->USDC->(via USDTTRY)->TRY")

            if t_disp == "PLN":
                r = usdc_to_pln_rate()
                if r is None:
                    raise ValueError("PLN route not available (EURUSDT + EURPLN)")
                return RateQuote(rate=f_usdc * r, path=f"{f_show}->USDC->(via EURUSDT+ECB)->PLN")

        # Fiat -> Crypto
        if f_disp in fiat and t in crypto:
            if f_disp == "TRY":
                r = usdc_to_try_rate()
                if r is None or r == 0:
                    raise ValueError("TRY route not available (USDTTRY)")
                # 1 TRY = 1/r USDC
                try_usdc = 1.0 / r
                usdc_t = self.get_price("USDC", t) if t != "USDC" else 1.0
                if usdc_t is None:
                    raise ValueError("No USDC route for crypto")
                return RateQuote(rate=try_usdc * usdc_t, path=f"TRY->(via USDTTRY)->USDC->{t_show}")

            if f_disp == "PLN":
                r = usdc_to_pln_rate()
                if r is None or r == 0:
                    raise ValueError("PLN route not available (EURUSDT + EURPLN)")
                # 1 PLN = 1/r USDC
                pln_usdc = 1.0 / r
                usdc_t = self.get_price("USDC", t) if t != "USDC" else 1.0
                if usdc_t is None:
                    raise ValueError("No USDC route for crypto")
                return RateQuote(rate=pln_usdc * usdc_t, path=f"PLN->(via EURUSDT+ECB)->USDC->{t_show}")

        # Crypto -> Crypto via USDC
        if f in crypto and t in crypto:
            f_usdc = self.get_price(f, "USDC") if f != "USDC" else 1.0
            usdc_t = self.get_price("USDC", t) if t != "USDC" else 1.0
            if f_usdc is None or usdc_t is None:
                raise ValueError("No route for crypto pair")
            return RateQuote(rate=f_usdc * usdc_t, path=f"{f_show}->USDC->{t_show}")

        raise ValueError("Unsupported conversion")
