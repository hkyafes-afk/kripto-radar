"""COMBO PAPER sabitleri ve doğrulaması."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class ComboConfig:
    symbol: str = "BTC-USDT-SWAP"  # OKX public perp enstrüman kimliği
    low_price: float = 72_426.7
    high_price: float = 83_608.5
    grid_intervals: int = 24
    grid_step_pct: float = 0.006
    order_qty_btc: float = 0.013
    take_profit_pct: float = 0.015
    paper_capital_usdt: float = 2_500.0
    leverage_simulation: float = 10.0
    hard_stop_below_low_pct: float = 0.01
    quote_max_age_ms: int = 15_000
    price_tick: float = 0.1
    maker_fee_pct: float = 0.02
    taker_fee_pct: float = 0.05
    stop_slippage_bps: float = 10.0
    execution_mode: str = "paper"
    private_api_enabled: bool = False
    live_order_submission: bool = False
    allow_short: bool = False
    allow_hedge: bool = False

    def __post_init__(self) -> None:
        finite_positive = (
            self.low_price, self.high_price, self.grid_step_pct, self.order_qty_btc,
            self.take_profit_pct, self.paper_capital_usdt, self.leverage_simulation,
            self.price_tick,
        )
        if not all(math.isfinite(float(x)) and float(x) > 0 for x in finite_positive):
            raise ValueError("COMBO sayısal ayarları sonlu ve pozitif olmalı")
        if self.high_price <= self.low_price:
            raise ValueError("Grid üst sınırı alt sınırdan büyük olmalı")
        if self.grid_intervals != 24:
            raise ValueError("İlk COMBO sürümünde tam 24 grid aralığı sabittir")
        if self.quote_max_age_ms <= 0:
            raise ValueError("quote_max_age_ms pozitif olmalı")
        if not math.isfinite(self.hard_stop_below_low_pct) or not math.isfinite(self.stop_slippage_bps):
            raise ValueError("Stop ve kayma ayarları sonlu olmalı")
        if self.hard_stop_below_low_pct < 0 or self.stop_slippage_bps < 0:
            raise ValueError("Stop ve kayma ayarları negatif olamaz")
        if (not math.isfinite(self.maker_fee_pct) or not math.isfinite(self.taker_fee_pct)
                or self.maker_fee_pct < 0 or self.taker_fee_pct < 0):
            raise ValueError("Fee yüzdeleri sonlu ve negatif olmayan değerler olmalı")
        if self.execution_mode != "paper":
            raise ValueError("COMBO ilk sürüm yalnız PAPER modunu kabul eder")
        if self.private_api_enabled or self.live_order_submission:
            raise ValueError("Private API ve gerçek emir gönderimi COMBO'da kapalıdır")
        if self.allow_short or self.allow_hedge:
            raise ValueError("COMBO ilk sürüm LONG-only; SHORT/hedge kapalıdır")
        observed_step = (self.high_price / self.low_price) ** (1 / self.grid_intervals) - 1
        if abs(observed_step - self.grid_step_pct) > 1e-6:
            raise ValueError(
                f"Fiyat bandı 24 aralıkta %{observed_step * 100:.8f} adım üretiyor; "
                f"istenen %{self.grid_step_pct * 100:.8f} ile uyuşmuyor"
            )

    @property
    def hard_stop_price(self) -> float:
        return self.low_price * (1.0 - self.hard_stop_below_low_pct)

    @property
    def maker_fee_rate(self) -> float:
        return self.maker_fee_pct / 100.0

    @property
    def taker_fee_rate(self) -> float:
        return self.taker_fee_pct / 100.0

    @property
    def max_gross_notional_usdt(self) -> float:
        return self.paper_capital_usdt * self.leverage_simulation
