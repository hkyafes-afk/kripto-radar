"""ShortTrendStrategy: long kurallarını ters çevirmek DEĞİL; ayrı ve daha sıkı short kuralları. Başlangıçta yalnız gölge.

Araştırma (data/TEK_AJAN_ARASTIRMA.md): naif short sepeti (en zayıf 10 coini açığa sat) saklı testte −%80; zayıf coinler
sert pompalanıyor. Kemal'in stoplu trend short'ları ise OKX yarışında artı. Bu yüzden zorunlu koşullar:
  1. Rejim: BTC ayı ya da bozulma (kriz rejiminde squeeze riski yüksek → karşı kanıt)
  2. Coinin kendi aşağı trendi: kapanış < 50g ort. < 100g ort., 50g ortalama düşüyor
  3. Aşağı yapısal kırılım: kapanış önceki 20 günün dibinin altında
  4. Likidite: 24 saatlik hacim ≥ 20 milyon $
  5. Short kalabalığı yok: yıllık funding ≥ −%20 (policy.kalabalik_short ile aynı eşik)
  6. Squeeze riski yok: OI 24 saatte > %10 artarken funding < −%10 değil
  7. Kaçmış fiyat yok: kapanış 50g ortalamanın en fazla 4 ATR altında
  8. Net stop: girişten 2 ATR yukarıda; hedefler 2R ve 4R
Long'dan düşük risk tavanı RiskGate'te (PR 3).
"""
from __future__ import annotations

from ..models import Horizon, MarketContext, Opportunity, OrderType, Regime, Side, StrategyAuthority
from .base import Strategy, num, stop_target

MIN_VOL_USD = 20e6
CROWDED_SHORT_ANN = -20.0


class ShortTrendStrategy(Strategy):
    name = "short_trend"
    horizon = Horizon.SWING
    timeframes = ("1D",)
    default_authority = StrategyAuthority.SHADOW_ONLY
    description = "Ayı rejiminde stoplu trend short'u (gölgede)"

    def evaluate(self, ctx: MarketContext) -> list[Opportunity]:
        if self.authority is StrategyAuthority.DISABLED or ctx.timeframe not in self.timeframes:
            return []
        r = ctx.raw
        if ctx.regime.regime not in (Regime.BEAR, Regime.DETERIORATION, Regime.CRISIS):
            return []
        c, s50, s100, s50p = num(r.get("close")), num(r.get("sma50")), num(r.get("sma100")), num(r.get("sma50_prev5"))
        ll20, atr, px = num(r.get("ll20")), num(r.get("atr")), num(r.get("price")) or num(r.get("close"))
        vol = num(r.get("vol24_usd"))
        if None in (c, s50, s100, s50p, ll20, atr, px) or atr <= 0:
            return []
        if not (c < s50 < s100 and s50 < s50p):
            return []
        if not c < ll20:
            return []
        if vol is None or vol < MIN_VOL_USD:
            return []
        fa, oi = num(r.get("funding_ann")), num(r.get("oi_chg24"))
        if fa is not None and fa < CROWDED_SHORT_ANN:
            return []
        if oi is not None and fa is not None and oi > 10 and fa < -10:
            return []
        if (s50 - c) / atr > 4:
            return []
        stop, targets = stop_target(px, -1, 2.0 * atr, (2.0, 4.0))
        if len(targets) < 2:
            return []
        ev_for = ("BTC ayı/bozulma rejimi", "kapanış 50 ve 100 günlük ortalamanın altında, 50'lik düşüyor",
                  "önceki 20 günün dibi kırıldı", "likidite yeterli", "short tarafı kalabalık değil")
        ev_against = []
        if ctx.regime.regime is Regime.CRISIS:
            ev_against.append("kriz rejimi: ani short squeeze riski yüksek")
        if fa is None:
            ev_against.append("funding verisi yok (kalabalık kontrol edilemedi)")
        if oi is None:
            ev_against.append("OI verisi yok (squeeze riski kontrol edilemedi)")
        candle = ctx.candles.get("1D")
        return [Opportunity(symbol=ctx.symbol, inst=ctx.inst, side=Side.SHORT, strategy=self.name, setup="short_trend",
                            timeframe="1D", horizon=self.horizon, created_ts_ms=ctx.ts_ms,
                            bar_ts_ms=candle.open_ts_ms if candle else None, entry=px, stop=stop, targets=targets,
                            entry_type=OrderType.MARKET, max_holding_hours=30 * 24.0, gross_edge_bp=None,
                            confidence=0.5, decision_setup="aşağı yapısal kırılım", evidence_for=ev_for,
                            evidence_against=tuple(ev_against), factors_used=("close", "sma50", "sma100", "ll20", "atr",
                                                                               "funding_ann", "oi_chg24", "vol24_usd"),
                            authority=self.authority, source="unified")]
