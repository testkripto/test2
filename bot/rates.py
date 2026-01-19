from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class RateQuote:
    rate: float  # to_asset per 1 from_asset
    path: str


class ManualVipRates:
    """
    Manual (admin-entered) rates with VIP tiers by fee percentage:
      - 1.0
      - 1.5
      - 2.0
      - 2.5 (default)

    Pricing model:
      - USDT and USDC are priced as USDC (stable normalization).
      - If a direct pair is not present, we route via USDC.
      - This module does NOT apply the fee; it selects a tier's base rate.
        (Your main.py still applies fee as its own logic.)
    """

    ALLOWED_TIERS = (1.0, 1.5, 2.0, 2.5)

    def __init__(self, rates_by_fee: Dict[str, Dict[str, float]], default_fee: float = 2.5):
        """
        rates_by_fee example structure (strings are fine):

        {
          "1":   {"USDC_TRY": 32.50, "USDC_PLN": 4.10, "ETH_USDC": 3400, "SOL_USDC": 145},
          "1.5": {"USDC_TRY": 32.40, "USDC_PLN": 4.08, "ETH_USDC": 3380, "SOL_USDC": 144},
          "2":   {...},
          "2.5": {...}
        }

        Keys inside a tier:
          - "USDC_TRY"  means 1 USDC = X TRY
          - "ETH_USDC"  means 1 ETH  = X USDC
          - etc.

        You may also add direct pairs like "ETH_TRY" if you want, but not required.
        """
        self.default_fee = float(default_fee)
        self.rates_by_fee: Dict[float, Dict[str, float]] = {}

        for fee_key, table in (rates_by_fee or {}).items():
            try:
                fee = float(str(fee_key).strip().replace("%", ""))
            except Exception:
                continue
            self.rates_by_fee[fee] = {k.upper(): float(v) for k, v in (table or {}).items()}

        # Ensure default tier exists if possible
        if self.default_fee not in self.rates_by_fee and self.rates_by_fee:
            # pick closest available tier
            self.default_fee = self._closest_tier(self.default_fee)

    @staticmethod
    def _norm(asset: str) -> str:
        a = asset.upper()
        if a in ("USDT", "USDC"):
            return "USDC"
        return a

    def _closest_tier(self, fee: float) -> float:
        if fee in self.rates_by_fee:
            return fee
        if not self.rates_by_fee:
            return fee
        return min(self.rates_by_fee.keys(), key=lambda x: abs(x - fee))

    def _get_table(self, fee_pct: Optional[float]) -> tuple[float, Dict[str, float]]:
        """
        Returns: (selected_fee_tier, table)
        """
        if not self.rates_by_fee:
            raise ValueError("No manual VIP rates configured")

        if fee_pct is None:
            tier = self.default_fee
            return tier, self.rates_by_fee[tier]

        fee = float(fee_pct)
        tier = self._closest_tier(fee)
        return tier, self.rates_by_fee[tier]

    @staticmethod
    def _get_direct(table: Dict[str, float], a: str, b: str) -> Optional[float]:
        """
        Returns 1 a = X b if either A_B exists or B_A exists (inverted).
        """
        a = a.upper()
        b = b.upper()
        if a == b:
            return 1.0

        k = f"{a}_{b}"
        if k in table:
            return table[k]

        inv = f"{b}_{a}"
        if inv in table and table[inv] != 0:
            return 1.0 / table[inv]

        return None

    def quote(self, from_asset: str, to_asset: str, fee_pct: Optional[float] = None) -> RateQuote:
        """
        Returns manual VIP-tier rate quote:
          - fee_pct selects which tier table is used
          - if fee_pct is None -> uses default_fee tier (2.5% by default)

        NOTE: This returns the raw rate. Your main.py can still compute fee separately.
        """
        tier, table = self._get_table(fee_pct)

        f_disp = from_asset.upper()
        t_disp = to_asset.upper()

        f = self._norm(f_disp)
        t = self._norm(t_disp)

        def show(a_disp: str, a_norm: str) -> str:
            return f"{a_disp}(as {a_norm})" if a_disp != a_norm else a_disp

        f_show = show(f_disp, f)
        t_show = show(t_disp, t)

        # 1) Try direct
        direct = self._get_direct(table, f, t)
        if direct is not None:
            return RateQuote(rate=direct, path=f"{f_show}->{t_show} (manual tier {tier}%)")

        # 2) Route via USDC
        f_to_usdc = self._get_direct(table, f, "USDC")
        if f_to_usdc is None:
            raise ValueError(f"Missing manual rate in {tier}% tier for {f}_USDC or USDC_{f}")

        usdc_to_t = self._get_direct(table, "USDC", t)
        if usdc_to_t is None:
            raise ValueError(f"Missing manual rate in {tier}% tier for USDC_{t} or {t}_USDC")

        rate = f_to_usdc * usdc_to_t
        return RateQuote(rate=rate, path=f"{f_show}->USDC->{t_show} (manual tier {tier}%)")
