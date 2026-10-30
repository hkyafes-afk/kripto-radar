"""Faktör kapsamı (radar/unified/coverage.py). Bütün bağlamlar SENTETİKTİR (elle yazılmış sayılar)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.unified import coverage as C  # noqa: E402
from radar.unified.models import Horizon, Validity  # noqa: E402

NOW = 1_800_000_000_000
H_MS = 3_600_000


def _sentetik_ctx(**kw):
    ctx = {"price": 101.0, "close": 100.0, "atr": 3.0, "sma50": 95.0, "sma200": 80.0, "rsi": 58.0, "adx": 24.0,
           "vol_ratio": 1.3, "w_trend": "boga", "funding_ann": 12.0, "oi_chg24": 4.0, "taker_imb": 0.1,
           "spread_bp": 2.0, "coin_age_days": 900.0}
    ctx.update(kw)
    return ctx


def _cov(ctx, **kw):
    f = C.factors_from_legacy_ctx(ctx, now_ms=NOW, bar_close_ms=NOW - H_MS, live_ts_ms=NOW - 5_000,
                                  static_ts_ms=NOW, **kw)
    return f, C.coverage_for(Horizon.SWING, f, now_ms=NOW)


def test_sentetik_tam_veri_paper_icin_yeterli():
    f, cov = _cov(_sentetik_ctx())
    assert cov.sufficient_for_paper and cov.coverage_ratio == 1.0
    assert cov.required_all_ok and not cov.missing and not cov.reason_codes
    assert f["btc_regime"].value == "boga" and f["close_1D"].timeframe == "1D"


def test_sentetik_zorunlu_eksik_shadowa_iter():
    _, cov = _cov(_sentetik_ctx(atr=None))
    assert not cov.sufficient_for_paper
    assert "atr_1D" in cov.required_missing and "ZORUNLU_EKSIK:atr_1D" in cov.reason_codes


def test_sentetik_zorunlu_gecersiz():
    _, cov = _cov(_sentetik_ctx(close=-5.0, rsi=140.0))
    assert not cov.sufficient_for_paper
    assert "close_1D" in cov.required_invalid and "rsi_1D" in cov.invalid
    assert "ZORUNLU_GECERSIZ:close_1D" in cov.reason_codes


def test_sentetik_bayat_canli_fiyat():
    f = C.factors_from_legacy_ctx(_sentetik_ctx(), now_ms=NOW, bar_close_ms=NOW - H_MS, live_ts_ms=NOW - 600_000)
    assert f["price"].validity is Validity.STALE and f["price"].stale
    cov = C.coverage_for(Horizon.SWING, f, now_ms=NOW)
    assert not cov.sufficient_for_paper and "price" in cov.required_stale
    assert "ZORUNLU_BAYAT:price" in cov.reason_codes


def test_sentetik_istege_bagli_eksikler_engellemez_ama_gorunur():
    ctx = _sentetik_ctx(oi_chg24=None, taker_imb=None)
    f, cov = _cov(ctx)
    assert cov.sufficient_for_paper                                   # 12/14 ≥ %80, zorunlular tam
    assert set(cov.missing) == {"oi_change_24h", "taker_imbalance"}
    assert "OI" in f["oi_change_24h"].missing_reason                  # sebebi yazılı, uydurulmadı
    assert f["oi_change_24h"].value is None


def test_sentetik_kapsam_orani_dusukse_yetersiz():
    ctx = _sentetik_ctx(oi_chg24=None, taker_imb=None, spread_bp=None, funding_ann=None, sma200=None)
    _, cov = _cov(ctx)
    assert cov.required_all_ok and not cov.sufficient_for_paper
    assert any(c.startswith("KAPSAM_DUSUK") for c in cov.reason_codes)


def test_sentetik_bilinmeyen_rejim_zorunlu_eksik():
    _, cov = _cov(_sentetik_ctx(w_trend=None))
    assert "btc_regime" in cov.required_missing and not cov.sufficient_for_paper


def test_sentetik_yas_bilinmiyorsa_kod_eklenir():
    f = C.factors_from_legacy_ctx(_sentetik_ctx(), now_ms=NOW)     # hiçbir zaman damgası verilmedi
    cov = C.coverage_for(Horizon.SWING, f, now_ms=NOW)
    assert cov.sufficient_for_paper
    assert "YAS_BILINMIYOR:price" in cov.reason_codes


def test_sentetik_mikro_veri_yoksa_paper_yok():
    f = C.factors_from_legacy_ctx(_sentetik_ctx(), now_ms=NOW, specs=C.MICRO_SPECS, live_ts_ms=NOW)
    cov = C.compute_coverage(C.MICRO_SPECS, f, now_ms=NOW)
    assert not cov.sufficient_for_paper
    assert {"book_depth_usd", "book_imbalance", "trade_imbalance", "seq_ok"} <= set(cov.required_missing)


def test_sentetik_tanimsiz_kapsam():
    cov = C.compute_coverage([], {}, now_ms=NOW)
    assert not cov.sufficient_for_paper and cov.reason_codes == ("KAPSAM_TANIMSIZ",)


def test_sentetik_make_factor_dogrulayici_istisnasi_gecersiz_sayar():
    f = C.make_factor("x", "abc", source="sentetik", validator=lambda v: v > 0)
    assert f.validity is Validity.INVALID
