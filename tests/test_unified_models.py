"""Birleşik Baş Trader ortak modelleri (radar/unified/models.py).

Bütün girdiler SENTETİKTİR (elle yazılmış küçük sayılar); gerçek piyasa verisi değildir.
"""
from __future__ import annotations

import dataclasses
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.unified import models as M  # noqa: E402

NOW = 1_800_000_000_000


def _legacy_idea(**kw):
    """Sentetik legacy fikir sözlüğü (radar/team._idea biçiminde)."""
    i = {"mgr": "kemal", "inst": "X-USDT-SWAP", "sym": "X", "tf": "1D", "side": "long", "kind": "trend",
         "entry": 100.0, "market_px": 100.0, "sl": 94.0, "tp": 160.0, "tp1": 106.0, "atr": 2.0, "conviction": 0.67,
         "max_saat": 2880, "entry_type": "piyasa", "limit_px": None, "setup": "kemal", "core": "günlük trend",
         "checks": [["200 günlük ortalamanın doğru tarafında", True], ["BTC havası aynı yönde", False]],
         "feat": {"rsi": 55.0, "adx": 22.0}}
    i.update(kw)
    return i


def test_sentetik_modeller_degismez():
    fv = M.FactorValue("close_1D", 100.0, source="sentetik", metadata={"a": 1})
    with pytest.raises(dataclasses.FrozenInstanceError):
        fv.value = 5  # type: ignore[misc]
    with pytest.raises(TypeError):
        fv.metadata["a"] = 2  # type: ignore[index]


def test_sentetik_factor_value_gecerlilik_normallestirilir():
    assert M.FactorValue("x", None).validity is M.Validity.MISSING
    assert M.FactorValue("x", float("nan")).validity is M.Validity.INVALID
    assert M.FactorValue("x", float("inf")).validity is M.Validity.INVALID
    st = M.FactorValue("x", 1.0, stale=True)
    assert st.validity is M.Validity.STALE and not st.usable and st.missing_reason == "bayat"
    ok = M.FactorValue("x", 1.0, confidence=3.0)
    assert ok.usable and ok.confidence == 1.0
    miss = M.FactorValue.missing("oi", "OKX OI akışı yok", required=False)
    assert miss.validity is M.Validity.MISSING and miss.missing_reason == "OKX OI akışı yok"


def test_sentetik_side_ve_horizon_ayrisma():
    assert M.Side.parse("LONG") is M.Side.LONG and M.Side.parse(-1) is M.Side.SHORT
    with pytest.raises(ValueError):
        M.Side.parse("yukarı")
    assert M.Horizon.from_timeframe("5m") is M.Horizon.MICRO
    assert M.Horizon.from_timeframe("1H") is M.Horizon.INTRADAY
    assert M.Horizon.from_timeframe("1D") is M.Horizon.SWING
    assert M.OrderStatus.FILLED.terminal and not M.OrderStatus.PARTIALLY_FILLED.terminal


def test_sentetik_mum_geometrisi():
    c = M.CandleContext("1D", NOW, open=100, high=110, low=95, close=108, atr=5.0, prev_high=111, prev_low=94)
    assert c.valid and c.direction == 1
    assert c.range == 15 and c.body == 8
    assert math.isclose(c.close_pos, 13 / 15)
    assert math.isclose(c.upper_wick, 2) and math.isclose(c.lower_wick, 5)
    assert math.isclose(c.range_atr, 3.0)
    assert c.inside is True and c.outside is False
    bad = M.CandleContext("1D", NOW, open=100, high=99, low=95, close=98)
    assert not bad.valid


def test_sentetik_firsat_dogrulama_ve_anahtarlar():
    o = M.Opportunity(symbol="X", inst="X-USDT-SWAP", side="long", strategy="swing_trend", setup="trend",
                      timeframe="1D", horizon=M.Horizon.SWING, created_ts_ms=NOW, bar_ts_ms=NOW - 1, entry=100.0,
                      stop=95.0, targets=(110.0,))
    assert o.side is M.Side.LONG and math.isclose(o.stop_bp, 500.0) and math.isclose(o.rr, 2.0)
    o2 = dataclasses.replace(o, created_ts_ms=NOW + 999)        # aynı mum → aynı idempotency anahtarı
    assert o.idempotency_key == o2.idempotency_key
    o3 = dataclasses.replace(o, bar_ts_ms=NOW + 86_400_000)      # sonraki mum → farklı anahtar
    assert o.idempotency_key != o3.idempotency_key
    assert o.dedup_key == "X|long|trend|swing"
    with pytest.raises(ValueError):
        M.Opportunity(symbol="X", inst="X", side="long", strategy="s", setup="s", timeframe="1D",
                      horizon=M.Horizon.SWING, created_ts_ms=NOW, entry=100.0, stop=101.0)   # stop yanlış tarafta
    with pytest.raises(ValueError):
        M.Opportunity(symbol="X", inst="X", side="short", strategy="s", setup="s", timeframe="1D",
                      horizon=M.Horizon.SWING, created_ts_ms=NOW, entry=100.0, stop=105.0, targets=(101.0,))


def test_sentetik_legacy_fikir_eslemesi():
    o = M.opportunity_from_legacy_idea(_legacy_idea(), now_ms=NOW)
    assert o.strategy == "swing_trend" and o.authority is M.StrategyAuthority.PAPER
    assert o.targets == (106.0, 160.0) and o.max_holding_hours == 2880.0
    assert o.gross_edge_bp is None                       # legacy brüt avantaj tahmin etmiyor → uydurulmaz
    assert o.evidence_for == ("200 günlük ortalamanın doğru tarafında",)
    assert o.evidence_against == ("BTC havası aynı yönde",)
    assert o.source == "legacy" and o.legacy["mgr"] == "kemal"
    assert o.factors_used == ("adx", "rsi")
    t = M.opportunity_from_legacy_idea(_legacy_idea(mgr="turbo"), now_ms=NOW)
    assert t.authority is M.StrategyAuthority.SHADOW_ONLY            # Turbo bağımsız işlem açamaz
    d = M.opportunity_from_legacy_idea(_legacy_idea(mgr="deniz", tf="15m"), now_ms=NOW)
    assert d.authority is M.StrategyAuthority.DISABLED and d.horizon is M.Horizon.INTRADAY
    lim = M.opportunity_from_legacy_idea(_legacy_idea(entry_type="limit", limit_px=99.0, entry=99.0), now_ms=NOW)
    assert lim.entry_type is M.OrderType.LIMIT and lim.limit_price == 99.0
    short = M.opportunity_from_legacy_idea(_legacy_idea(side="short", entry=100.0, sl=104.0, tp=90.0, tp1=96.0),
                                          now_ms=NOW)
    assert short.targets == (96.0, 90.0)


def test_sentetik_rejim_ve_veri_kalitesi_eslemesi():
    assert M.RegimeContext.from_legacy_weather({"trend": "boga"}).regime is M.Regime.BULL
    assert M.RegimeContext.from_legacy_weather({"trend": "ayi", "high_vol": True}).regime is M.Regime.CRISIS
    assert M.RegimeContext.from_legacy_weather({"trend": "yatay"}).regime is M.Regime.RANGE
    assert M.RegimeContext.from_legacy_weather(None).regime is M.Regime.UNKNOWN
    dq = M.DataQualityContext.from_legacy_issue("son mum 45 dk gecikmiş")
    assert dq.fail_closed and not dq.ok
    assert M.DataQualityContext.from_legacy_issue(None).ok


def test_sentetik_turev_funding_yilliklama():
    d = M.DerivativesContext(funding_rate=0.0001, funding_interval_h=8)
    assert math.isclose(d.funding_ann_pct, 10.95, rel_tol=1e-9)
    assert M.DerivativesContext().funding_ann_pct is None


def test_sentetik_likidite_spread():
    lq = M.LiquidityContext(bid=99.95, ask=100.05)
    assert math.isclose(lq.spread_bp, 10.0, rel_tol=1e-9)
    assert M.LiquidityContext(bid=None, ask=100).spread_bp is None


def test_sentetik_yurutme_niyeti_belirleyici_id():
    a = M.ExecutionIntent(idempotency_key="k1", symbol="X", inst="X-USDT-SWAP", side="long", action="open",
                          order_type=M.OrderType.MARKET, qty=1.5, created_ts_ms=NOW)
    b = dataclasses.replace(a, created_ts_ms=NOW + 5)
    assert a.client_order_id == b.client_order_id and len(a.client_order_id) <= 32 and a.client_order_id.isalnum()
    assert a.mode is M.ExecutionMode.PAPER
    assert dataclasses.replace(a, seq=1).client_order_id != a.client_order_id
    with pytest.raises(ValueError):
        M.ExecutionIntent(idempotency_key="k", symbol="X", inst="X", side="long", action="open",
                          order_type=M.OrderType.LIMIT, qty=1, created_ts_ms=NOW)          # limitte fiyat yok
    with pytest.raises(ValueError):
        M.ExecutionIntent(idempotency_key="k", symbol="X", inst="X", side="long", action="open",
                          order_type=M.OrderType.MARKET, qty=0, created_ts_ms=NOW)


def test_sentetik_yurutme_sonucu():
    r = M.ExecutionResult("kr1", M.OrderStatus.PARTIALLY_FILLED, requested_qty=2.0, filled_qty=0.5)
    assert r.fill_ratio == 0.25 and not r.is_terminal


def test_sentetik_etkinlik_ozeti_bootstrap():
    rs = [1.0, -0.5, 2.0, -1.0, 0.3, -0.2, 1.5, -1.0, 0.1, 0.4] * 4      # sentetik R dizisi (40 işlem)
    s = M.EffectivenessSnapshot.from_r("sentetik", NOW, rs)
    assert s.n_trades == 40 and math.isclose(s.avg_r, sum(rs) / 40)
    assert s.ci_low_r < s.avg_r < s.ci_high_r
    assert s.profit_factor and s.profit_factor > 1
    assert 0 < s.top3_share <= 1
    s2 = M.EffectivenessSnapshot.from_r("sentetik", NOW, rs)
    assert (s.ci_low_r, s.ci_high_r) == (s2.ci_low_r, s2.ci_high_r)     # sabit tohum → tekrar üretilebilir
    small = M.EffectivenessSnapshot.from_r("sentetik", NOW, [1.0, -1.0])
    assert small.ci_low_r is None and small.notes and not small.proven_positive
    assert M.EffectivenessSnapshot.from_r("sentetik", NOW, []).n_trades == 0


def test_sentetik_json_donusumu():
    o = M.opportunity_from_legacy_idea(_legacy_idea(), now_ms=NOW)
    d = M.DecisionResult(decision=M.Decision.SHADOW, symbol="X", side="long", strategy=o.strategy, timeframe="1D",
                         confidence=0.5, gross_edge_bp=None, costs=None, net_edge_bp=float("nan"), entry=o.entry,
                         stop=o.stop, targets=o.targets, reason_codes=["BRUT_AVANTAJ_BILINMIYOR"],
                         explanation_tr="brüt avantaj bilinmiyor → gölgede", ts_ms=NOW,
                         idempotency_key=o.idempotency_key, strategies=["swing_trend", "controlled_breakout"])
    js = json.dumps(M.to_jsonable(d), ensure_ascii=False)
    back = json.loads(js)
    assert back["decision"] == "SHADOW" and back["side"] == "long" and back["net_edge_bp"] is None
    assert d.evidence_count == 2
    assert json.dumps(M.to_jsonable(o))                                   # Opportunity de serileşir


def test_sentetik_risk_karari_onay():
    assert M.RiskDecision(M.Decision.OPEN).approved
    assert M.RiskDecision(M.Decision.REDUCE_SIZE).approved
    assert not M.RiskDecision(M.Decision.SHADOW).approved
    assert not M.RiskDecision(M.Decision.REJECT).approved
