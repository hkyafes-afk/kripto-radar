"""FactorEngine (radar/unified/factors.py). Bağlamlar SENTETİKTİR (elle yazılmış sayılar, gerçek piyasa değil)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.unified.factors import FactorEngine, trend_from_ctx  # noqa: E402
from radar.unified.models import CoinClass, Regime, Trend  # noqa: E402

NOW = 1_800_000_000_000
DAY = 86_400_000


def sentetik_ctx(**kw):
    c = {"inst": "X-USDT-SWAP", "sym": "X", "tf": "1D", "price": 101.0, "open": 98.0, "high": 102.0, "low": 97.0,
         "close": 101.0, "volume": 5000.0, "atr": 3.0, "sma20": 97.0, "sma50": 95.0, "sma50_prev5": 94.0,
         "sma100": 90.0, "sma200": 80.0, "rsi": 60.0, "adx": 25.0, "vol_ratio": 1.4, "ema20": 97.5, "ema50": 95.5,
         "hh20": 100.0, "ll20": 88.0, "prev_high": 100.5, "prev_low": 96.0, "w_trend": "boga", "high_vol": False,
         "funding_rate": 0.0001, "interval_h": 8, "funding_ann": 10.95, "oi_chg24": 3.0, "taker_imb": 0.2,
         "vol24_usd": 50e6, "live_bar": True, "crowd": {"ls_acct": 1.2}}
    c.update(kw)
    return c


def test_sentetik_baglam_tipli_modele_tasinir():
    m = FactorEngine({}).from_legacy_ctx(sentetik_ctx(), now_ms=NOW, live_ts_ms=NOW, list_ts_ms=NOW - 800 * DAY)
    c = m.candles["1D"]
    assert c.valid and not c.complete                     # canlı mum → kesin sinyal değil
    assert m.structure["1D"].trend is Trend.UP and m.structure["1D"].donchian_high == 100.0
    assert m.regime.regime is Regime.BULL
    assert abs(m.volume.taker_buy_ratio - 0.6) < 1e-9 and m.volume.rel_volume == 1.4
    assert m.derivatives.oi_change_24h_pct == 3.0 and m.derivatives.long_short_ratio == 1.2
    assert round(m.coin_age_days) == 800 and m.data_quality.ok
    assert m.coverage.sufficient_for_paper and m.value("close_1D") == 101.0
    assert m.raw["sym"] == "X"


def test_sentetik_veri_sorunu_fail_closed():
    m = FactorEngine({}).from_legacy_ctx(sentetik_ctx(suspect="Bitget ile fiyat farkı %4"), now_ms=NOW, live_ts_ms=NOW)
    assert m.data_quality.fail_closed and "Bitget" in m.data_quality.critical[0]


def test_sentetik_eksik_zorunlu_kapsam():
    m = FactorEngine({}).from_legacy_ctx(sentetik_ctx(atr=None, w_trend=None), now_ms=NOW, live_ts_ms=NOW)
    assert not m.coverage.sufficient_for_paper
    assert {"atr_1D", "btc_regime"} <= set(m.coverage.required_missing)


def test_sentetik_kapanmis_mum_ve_sinif():
    m = FactorEngine({}).from_legacy_ctx(sentetik_ctx(live_bar=False, coin_class="C"), now_ms=NOW, live_ts_ms=NOW)
    assert m.candles["1D"].complete and m.coin_class is CoinClass.C
    assert m.candles["1D"].open_ts_ms == (NOW // DAY - 1) * DAY


def test_sentetik_trend_tespiti():
    assert trend_from_ctx({"close": 90, "sma50": 95, "sma50_prev5": 96}) is Trend.DOWN
    assert trend_from_ctx({"close": 96, "sma50": 95, "sma50_prev5": 96}) is Trend.RANGE
    assert trend_from_ctx({}) is Trend.UNKNOWN
