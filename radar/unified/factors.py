"""FactorEngine: göstergeleri tek yerde toplayıp `MarketContext` üretir (PR 2).

Bu sürümde kaynak, mevcut motorun günlük bağlam sözlüğüdür (radar/engine.live_daily_ctx = quant.ctx_at + canlı alanlar +
team.extra_features). Göstergeler YENİDEN hesaplanmaz; aynı değerler tipli modellere taşınır, kapsam ölçülür:
  - CandleContext 1D (canlı mum ise complete=False → kesin sinyal sayılmaz)
  - MarketStructure 1D (fiyat/50g ortalama ve eğimi → UP/DOWN/RANGE; Donchian, EMA, ADX)
  - Volume, Derivatives (funding, OI, long/short), Regime (BTC havası), DataQuality (legacy veri sorunu)
  - Faktörler + FactorCoverage (coverage.SWING_SPECS)
Gün içi (15m/1H/4H) ve mikro yapı bağlamı bu sürümde kurulmaz: o stratejiler veri yoksa fırsat üretmez (SHADOW_ONLY).
"""
from __future__ import annotations

from typing import Any, Mapping

from . import coverage as cov
from .flags import unified_cfg
from .models import (CandleContext, CoinClass, DataQualityContext, DerivativesContext, LiquidityContext, MarketContext,
                     MarketStructure, RegimeContext, Trend, VolumeContext, Horizon, is_finite_number)

DAY_MS = 86_400_000


def _num(v: Any) -> float | None:
    return float(v) if is_finite_number(v) else None


def trend_from_ctx(ctx: Mapping[str, Any]) -> Trend:
    """Fiyat 50 günlük ortalamanın üstünde ve ortalama yükseliyorsa UP, tersi DOWN, aksi RANGE; veri yoksa UNKNOWN."""
    c, s50, s50p = _num(ctx.get("close")), _num(ctx.get("sma50")), _num(ctx.get("sma50_prev5"))
    if c is None or s50 is None:
        return Trend.UNKNOWN
    if s50p is None:
        return Trend.UP if c > s50 else Trend.DOWN if c < s50 else Trend.RANGE
    if c > s50 and s50 > s50p:
        return Trend.UP
    if c < s50 and s50 < s50p:
        return Trend.DOWN
    return Trend.RANGE


def _liquidity(ctx, ws_healthy) -> LiquidityContext:
    """Motorun public BBO verisi (bid/ask/yaş) → likidite bağlamı. Durum spread_ok değilse bid/ask verilmez."""
    from .liquidity import spread_status
    bid, ask, age = ctx.get("bid"), ctx.get("ask"), ctx.get("book_age_s")
    st, _ = spread_status(bid, ask, age)
    if st != "spread_ok":
        return LiquidityContext(ws_healthy=ws_healthy, spread_durum=st, book_age_s=_num(age))
    return LiquidityContext(bid=float(bid), ask=float(ask), book_age_s=float(age), spread_durum=st,
                            ws_healthy=ws_healthy)


class FactorEngine:
    def __init__(self, cfg: Mapping | None = None):
        self.cfg = cfg or {}
        self.min_ratio = float(unified_cfg(self.cfg)["kapsam"]["min_oran"])

    def from_legacy_ctx(self, ctx: Mapping[str, Any], *, now_ms: int, bar_open_ms: int | None = None,
                        live_ts_ms: int | None = None, data_issue: str | None = None, ws_healthy: bool | None = None,
                        list_ts_ms: float | None = None, coin_class: str | None = None) -> MarketContext:
        tf = str(ctx.get("tf") or "1D")
        complete = not bool(ctx.get("live_bar"))
        if bar_open_ms is None:
            bar_open_ms = (now_ms // DAY_MS) * DAY_MS if not complete else (now_ms // DAY_MS - 1) * DAY_MS
        candle = CandleContext(timeframe=tf, open_ts_ms=int(bar_open_ms), open=_num(ctx.get("open")) or 0.0,
                               high=_num(ctx.get("high")) or 0.0, low=_num(ctx.get("low")) or 0.0,
                               close=_num(ctx.get("close")) or 0.0, volume=_num(ctx.get("volume")), complete=complete,
                               atr=_num(ctx.get("atr")), prev_high=_num(ctx.get("prev_high")),
                               prev_low=_num(ctx.get("prev_low")))
        structure = MarketStructure(timeframe=tf, trend=trend_from_ctx(ctx), ema_fast=_num(ctx.get("ema20")),
                                    ema_slow=_num(ctx.get("ema50")), adx=_num(ctx.get("adx")),
                                    ema_slope=(_num(ctx.get("sma50")) / _num(ctx.get("sma50_prev5")) - 1
                                               if _num(ctx.get("sma50")) and _num(ctx.get("sma50_prev5")) else None),
                                    donchian_high=_num(ctx.get("hh20")), donchian_low=_num(ctx.get("ll20")),
                                    prev_day_high=_num(ctx.get("prev_high")), prev_day_low=_num(ctx.get("prev_low")))
        imb = _num(ctx.get("taker_imb"))
        volume = VolumeContext(timeframe=tf, volume=_num(ctx.get("volume")), rel_volume=_num(ctx.get("vol_ratio")),
                               quote_volume_24h=_num(ctx.get("vol24_usd")),
                               taker_buy_ratio=(imb + 1) / 2 if imb is not None and -1 <= imb <= 1 else None)
        crowd = ctx.get("crowd") or {}
        deriv = DerivativesContext(funding_rate=_num(ctx.get("funding_rate")), funding_interval_h=_num(ctx.get("interval_h")),
                                   funding_ann_pct=_num(ctx.get("funding_ann")),
                                   oi_change_24h_pct=_num(ctx.get("oi_chg24")),
                                   long_short_ratio=_num(crowd.get("ls_acct")) if isinstance(crowd, Mapping) else None)
        regime = RegimeContext.from_legacy_weather({"trend": ctx.get("w_trend"), "high_vol": ctx.get("high_vol")})
        issue = data_issue or ctx.get("suspect")
        dq = DataQualityContext.from_legacy_issue(issue, ws_healthy=ws_healthy)
        age_days = (now_ms - float(list_ts_ms)) / DAY_MS if is_finite_number(list_ts_ms) and list_ts_ms > 0 else None
        cls = None
        cc = coin_class or ctx.get("coin_class")
        if cc in ("A", "B", "C"):
            cls = CoinClass(cc)
        extended = dict(ctx)
        if age_days is not None:
            extended["coin_age_days"] = age_days
        factors = cov.factors_from_legacy_ctx(extended, now_ms=now_ms, specs=cov.SWING_SPECS,
                                              bar_close_ms=(now_ms if not complete else bar_open_ms + DAY_MS),
                                              live_ts_ms=live_ts_ms, static_ts_ms=now_ms)
        coverage_by_horizon = {
            horizon: cov.compute_coverage(specs, factors, now_ms=now_ms, min_ratio=self.min_ratio)
            for horizon, specs in cov.DEFAULT_SPECS.items()
        }
        coverage = coverage_by_horizon[Horizon.SWING]
        return MarketContext(symbol=str(ctx.get("sym") or ""), inst=str(ctx.get("inst") or ""), ts_ms=int(now_ms),
                             timeframe=tf, candles={tf: candle}, structure={tf: structure}, volume=volume,
                             liquidity=_liquidity(ctx, ws_healthy), derivatives=deriv, regime=regime,
                             data_quality=dq, coin_class=cls, coin_age_days=age_days, factors=factors,
                             coverage=coverage, coverage_by_horizon=coverage_by_horizon, raw=dict(ctx))
