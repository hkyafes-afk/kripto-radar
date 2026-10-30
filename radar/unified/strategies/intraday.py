"""Kısa ufuklu stratejiler — ikisi de varsayılan ve kalıcı olarak SHADOW_ONLY.

Araştırma: gün içi 9 kuralın hiçbiri saklı testte kazandırmadı; Polat (gün içi) −0,27R; Deniz (15 dk) −%94 → durduruldu.
Bu stratejiler eski Deniz/Polat sistemini YENİDEN ETKİNLEŞTİRMEZ; yalnız ölçmek için gölgede çalışır. Bu sürümde motor
gün içi/mikro bağlam kurmadığı için canlıda fırsat üretmezler (veri gelince PR 3+ ile).
"""
from __future__ import annotations

from ..models import Horizon, MarketContext, Opportunity, OrderType, Regime, Side, StrategyAuthority, Trend
from .base import Strategy, stop_target


class IntradayTrendStrategy(Strategy):
    """4H ana yön + 1H kontrollü geri çekilme (EMA'ya dokunup üstünde kapanış), geri çekilmede hacim daralması,
    yeniden hızlanma (güçlü kapanış). Yapısal stop mumun dibinin 0,25 ATR altı, hedef 2R, en fazla 24 saat.
    Tamamlanmamış 1H mumda sinyal yok."""
    name = "intraday_trend"
    horizon = Horizon.INTRADAY
    timeframes = ("15m", "1H", "4H")
    default_authority = StrategyAuthority.SHADOW_ONLY
    description = "Gün içi trend geri çekilmesi (gölgede)"

    def __init__(self, cfg=None):
        super().__init__(cfg)
        if self.authority is StrategyAuthority.PAPER:               # config genişletemez; yine de açıkça kilitli
            self.authority = StrategyAuthority.SHADOW_ONLY

    def evaluate(self, ctx: MarketContext) -> list[Opportunity]:
        if self.authority is StrategyAuthority.DISABLED:
            return []
        htf, c1, s1 = ctx.structure.get("4H"), ctx.candles.get("1H"), ctx.structure.get("1H")
        if htf is None or c1 is None or s1 is None or not c1.complete or not c1.valid:
            return []
        atr, ema = c1.atr, s1.ema_fast
        if not atr or atr <= 0 or not ema:
            return []
        vr = ctx.value("vol_ratio_1H")
        if vr is None or vr >= 1.0:                                  # geri çekilmede hacim daralması şart
            return []
        out = []
        if htf.trend is Trend.UP and ctx.regime.regime not in (Regime.BEAR, Regime.CRISIS):
            if c1.low <= ema * 1.002 and c1.close > ema and c1.direction == 1 and (c1.close_pos or 0) >= 0.6:
                dist = c1.close - (c1.low - 0.25 * atr)
                stop, tg = stop_target(c1.close, 1, dist, (2.0,))
                out.append((Side.LONG, stop, tg))
        if htf.trend is Trend.DOWN and ctx.regime.regime in (Regime.BEAR, Regime.DETERIORATION):
            if c1.high >= ema * 0.998 and c1.close < ema and c1.direction == -1 and (c1.close_pos or 1) <= 0.4:
                dist = (c1.high + 0.25 * atr) - c1.close
                stop, tg = stop_target(c1.close, -1, dist, (2.0,))
                out.append((Side.SHORT, stop, tg))
        return [Opportunity(symbol=ctx.symbol, inst=ctx.inst, side=side, strategy=self.name, setup="intraday_pullback",
                            timeframe="1H", horizon=self.horizon, created_ts_ms=ctx.ts_ms, bar_ts_ms=c1.open_ts_ms,
                            entry=c1.close, stop=stop, targets=tg, entry_type=OrderType.MARKET,
                            expected_holding_hours=6.0, max_holding_hours=24.0, gross_edge_bp=None, confidence=0.4,
                            htf_direction=htf.trend, decision_setup="1H geri çekilme + yeniden hızlanma",
                            ltf_entry_quality=c1.close_pos if side is Side.LONG else (1 - (c1.close_pos or 0)),
                            evidence_for=("4H yönü aynı", "EMA'ya kontrollü geri çekilme", "hacim daralması",
                                          "güçlü kapanışla yeniden hızlanma"),
                            authority=self.authority, source="unified")
                for side, stop, tg in out if tg]


class MicrostructureStrategy(Strategy):
    """1m–15m emir defteri dengesizliği + işlem akışı dengesizliği + dar spread. Mum kapanışından BAĞIMSIZ çalışır
    (anlık akış). Tick ve emir defteri geçmişi olmadan gerçek paper risk kullanmaz: kalıcı SHADOW_ONLY. Zorunlu
    faktörler (coverage.MICRO_SPECS) eksikse hiç fırsat üretmez."""
    name = "microstructure"
    horizon = Horizon.MICRO
    timeframes = ("1m", "5m", "15m")
    default_authority = StrategyAuthority.SHADOW_ONLY
    description = "Mikro yapı (gölgede, veri gerekli)"
    required_factors = ("price", "spread_bp", "book_depth_usd", "book_imbalance", "trade_imbalance", "seq_ok")

    def __init__(self, cfg=None):
        super().__init__(cfg)
        if self.authority is StrategyAuthority.PAPER:
            self.authority = StrategyAuthority.SHADOW_ONLY

    def evaluate(self, ctx: MarketContext) -> list[Opportunity]:
        if self.authority is StrategyAuthority.DISABLED:
            return []
        vals = {n: ctx.value(n) for n in self.required_factors}
        if any(v is None for v in vals.values()) or vals["seq_ok"] is not True:
            return []
        px, sp, bi, ti = vals["price"], vals["spread_bp"], vals["book_imbalance"], vals["trade_imbalance"]
        if sp > 3:
            return []
        if bi >= 0.3 and ti >= 0.2:
            side = Side.LONG
        elif bi <= -0.3 and ti <= -0.2:
            side = Side.SHORT
        else:
            return []
        stop, tg = stop_target(px, side.sign, px * 0.0015, (2.0,))
        return [Opportunity(symbol=ctx.symbol, inst=ctx.inst, side=side, strategy=self.name, setup="micro_pressure",
                            timeframe="1m", horizon=self.horizon, created_ts_ms=ctx.ts_ms, bar_ts_ms=ctx.ts_ms,
                            entry=px, stop=stop, targets=tg, entry_type=OrderType.IOC_LIMIT, limit_price=px,
                            expected_holding_hours=0.25, max_holding_hours=0.5, gross_edge_bp=None, confidence=0.3,
                            evidence_for=("emir defteri dengesizliği", "işlem akışı aynı yönde", "spread dar"),
                            authority=self.authority, source="unified")]
