"""CostEngine (radar/unified/costs.py). Fiyatlar, emir defterleri ve oranlar SENTETİKTİR (elle yazılmış sayılar).

Ücretler gerçek config.yaml'dan okunur (ucret: taker %0,10, maker %0,04, spot taker %0,20 — v8 sert model).
"""
from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import ledger as lg  # noqa: E402
from radar.config import load_config  # noqa: E402
from radar.unified.costs import CostEngine, bp_to_r, gross_edge_from_expectancy  # noqa: E402
from radar.unified.models import Horizon, OrderType, Tradability  # noqa: E402

CFG = load_config()
FC = lg.fees_cfg(CFG)
TAKER_BP = FC["taker_yuzde"] * 100
MAKER_BP = FC["maker_yuzde"] * 100


def _tam(**kw):
    """Sentetik, bütün girdileri olan swing long: spread 2 bp, derinlik yeterli, ATR 3 (300 bp), funding 0,0001/8s, 72 saat."""
    a = dict(side="long", horizon=Horizon.SWING, entry_price=100.0, gross_edge_bp=100.0, notional_usd=1000.0,
             bid=99.99, ask=100.01, book_levels=[(100.01, 1000.0)], expected_holding_hours=72.0,
             funding_rate=0.0001, funding_interval_h=8.0, atr=3.0, timeframe="1D", coin_class="A",
             stop_probability=0.5)
    a.update(kw)
    return a


def test_sentetik_config_ucretleri_kullanilir():
    eng = CostEngine(CFG)
    assert math.isclose(eng.fee_bp(OrderType.MARKET), TAKER_BP)
    assert math.isclose(eng.fee_bp(OrderType.LIMIT), MAKER_BP)
    assert math.isclose(eng.fee_bp(OrderType.MARKET, "spot"), FC["spot_taker_yuzde"] * 100)


def test_sentetik_tam_girdi_hesap_ve_pass():
    e = CostEngine(CFG).estimate(**_tam())
    stop_raw = lg.stop_slip_bp({"atr": 3.0, "entry": 100.0, "tf": "1D", "coin_class": "A"}, FC)
    exp_total = TAKER_BP + TAKER_BP + 2.0 + 0.0 + 0.0 + 0.5 * stop_raw + 9.0
    assert math.isclose(e.entry_fee_bp, TAKER_BP) and math.isclose(e.exit_fee_bp, TAKER_BP)
    assert math.isclose(e.spread_bp, 2.0, rel_tol=1e-6)                 # iki taker geçişi × yarım spread
    assert e.entry_slippage_bp == 0.0 and e.exit_slippage_bp == 0.0
    assert math.isclose(e.stop_slippage_raw_bp, stop_raw) and math.isclose(e.stop_slippage_bp, 0.5 * stop_raw)
    assert math.isclose(e.funding_bp, 9.0, rel_tol=1e-6)                # 0,0001/8s × 72s = 9 bp, long öder
    assert math.isclose(e.total_cost_bp, exp_total, rel_tol=1e-6)
    assert math.isclose(e.edge_after_costs_bp, 100.0 - exp_total, rel_tol=1e-6)
    assert math.isclose(e.net_edge_bp, 100.0 - exp_total - 10.0, rel_tol=1e-6)
    assert e.tradability is Tradability.PASS and e.confidence == 1.0 and not e.missing_inputs


def test_sentetik_reject_ve_shadow_esikleri():
    base = CostEngine(CFG).estimate(**_tam())
    cost = base.total_cost_bp
    rej = CostEngine(CFG).estimate(**_tam(gross_edge_bp=cost - 1))
    assert rej.tradability is Tradability.REJECT and "MALIYET_SONRASI_AVANTAJ_YOK" in rej.reason_codes
    sh = CostEngine(CFG).estimate(**_tam(gross_edge_bp=cost + 5))      # pozitif ama tampondan (10 bp) küçük
    assert sh.tradability is Tradability.SHADOW and "AVANTAJ_TAMPONDAN_KUCUK" in sh.reason_codes
    assert CostEngine(CFG).estimate(**_tam(gross_edge_bp=cost + 11)).tradability is Tradability.PASS


def test_sentetik_gun_ici_esigi_swingden_siki():
    eng = CostEngine(CFG)
    cost = eng.estimate(**_tam()).total_cost_bp
    g = cost + 15                                                        # swing tamponu 10, gün içi 20
    assert eng.estimate(**_tam(gross_edge_bp=g)).tradability is Tradability.PASS
    intra = eng.estimate(**_tam(gross_edge_bp=g, horizon=Horizon.INTRADAY))
    assert intra.tradability is Tradability.SHADOW and intra.uncertainty_buffer_bp == 20.0
    micro = eng.estimate(**_tam(gross_edge_bp=g, horizon=Horizon.MICRO))
    assert micro.uncertainty_buffer_bp == 30.0


def test_sentetik_brut_avantaj_bilinmiyorsa_shadow():
    e = CostEngine(CFG).estimate(**_tam(gross_edge_bp=None))
    assert e.tradability is Tradability.SHADOW and e.net_edge_bp is None
    assert "BRUT_AVANTAJ_BILINMIYOR" in e.reason_codes


def test_sentetik_eksik_girdiler_guveni_dusurur_ve_tamponu_buyutur():
    e = CostEngine(CFG).estimate(side="long", horizon=Horizon.SWING, entry_price=100.0, gross_edge_bp=500.0)
    assert set(e.missing_inputs) == {"spread", "derinlik", "atr", "funding", "tutma_suresi"}
    assert math.isclose(e.confidence, 0.40, abs_tol=1e-9)
    assert e.uncertainty_buffer_bp == 10.0 + 5.0 * 5
    assert e.tradability is Tradability.SHADOW and any(c.startswith("MALIYET_GUVENI_DUSUK") for c in e.reason_codes)
    assert any("varsayıldı" in a for a in e.assumptions)


def test_sentetik_vwap_kaymasi():
    e = CostEngine(CFG).estimate(**_tam(book_levels=[(100.0, 5.0), (101.0, 5.0)], bid=99.99, ask=100.0))
    assert math.isclose(e.entry_slippage_bp, 50.0, rel_tol=1e-6)           # vwap 100,5 vs en iyi 100
    assert math.isclose(e.exit_slippage_bp, 50.0, rel_tol=1e-6)            # taker çıkış simetrik


def test_sentetik_market_derinlik_yetersiz_shadow():
    e = CostEngine(CFG).estimate(**_tam(gross_edge_bp=500.0, book_levels=[(100.01, 2.0)]))
    assert "DERINLIK_YETERSIZ" in e.reason_codes and e.tradability is Tradability.SHADOW


def test_sentetik_ioc_kismi_dolum():
    e = CostEngine(CFG).estimate(**_tam(gross_edge_bp=500.0, entry_order=OrderType.IOC_LIMIT,
                                        book_levels=[(100.01, 2.0)]))
    assert math.isclose(e.fill_ratio, 0.2, rel_tol=1e-3) and math.isclose(e.unfilled_ratio, 0.8, rel_tol=1e-3)
    assert "KISMI_DOLUM_YUKSEK" in e.reason_codes and e.tradability is Tradability.SHADOW
    assert e.missed_fill_buffer_bp == 5.0
    ok = CostEngine(CFG).estimate(**_tam(gross_edge_bp=500.0, entry_order=OrderType.IOC_LIMIT,
                                         book_levels=[(100.01, 8.0)]))
    assert ok.tradability is Tradability.PASS and any(c.startswith("KISMI_DOLUM:") for c in ok.reason_codes)


def test_sentetik_maker_giris():
    e = CostEngine(CFG).estimate(**_tam(entry_order=OrderType.LIMIT))
    assert math.isclose(e.entry_fee_bp, MAKER_BP) and e.entry_slippage_bp == 0.0
    assert e.missed_fill_buffer_bp == 5.0
    assert math.isclose(e.spread_bp, 1.0, rel_tol=1e-6)                   # yalnız taker çıkışta yarım spread


def test_sentetik_short_funding_geliri_varsayilan_sayilmaz():
    e = CostEngine(CFG).estimate(**_tam(side="short", book_levels=[(99.99, 1000.0)]))
    assert e.funding_bp == 0.0 and any("funding geliri" in a for a in e.assumptions)
    cfg2 = copy.deepcopy(CFG)
    cfg2["birlesik"] = {"maliyet": {"funding_gelir_kredisi": True}}
    e2 = CostEngine(cfg2).estimate(**_tam(side="short", book_levels=[(99.99, 1000.0)]))
    assert math.isclose(e2.funding_bp, -9.0, rel_tol=1e-6)
    assert e2.total_cost_bp < e.total_cost_bp


def test_sentetik_genc_coinde_stop_kaymasi_buyuk():
    a = CostEngine(CFG).estimate(**_tam(coin_class="A"))
    c = CostEngine(CFG).estimate(**_tam(coin_class="C"))
    assert math.isclose(c.stop_slippage_raw_bp, 1.5 * a.stop_slippage_raw_bp, rel_tol=1e-6)


def test_sentetik_carry_dort_bacak():
    eng = CostEngine(CFG)
    e = eng.estimate_carry(notional_usd=1000.0, expected_holding_hours=720.0, funding_ann_pct=20.0,
                           perp_bid=99.99, perp_ask=100.01, spot_bid=99.98, spot_ask=100.02)
    assert len(e.legs) == 4
    fees = sum(L.fee_bp for L in e.legs)
    assert math.isclose(fees, 2 * FC["spot_taker_yuzde"] * 100 + 2 * FC["taker_yuzde"] * 100)
    assert math.isclose(fees / 100, CFG["harvest"]["maliyet_yuzde"])        # config'teki 4 bacak maliyetiyle aynı
    assert math.isclose(e.gross_edge_bp, 20.0 / 100 * 720 / 8760 * 1e4, rel_tol=1e-9)
    assert e.tradability is Tradability.PASS
    kisa = eng.estimate_carry(notional_usd=1000.0, expected_holding_hours=72.0, funding_ann_pct=20.0,
                              perp_bid=99.99, perp_ask=100.01, spot_bid=99.98, spot_ask=100.02)
    assert kisa.tradability is Tradability.REJECT                           # 3 günlük funding 4 bacağı karşılamaz
    yok = eng.estimate_carry(notional_usd=1000.0, expected_holding_hours=720.0)
    assert yok.gross_edge_bp is None and yok.tradability is Tradability.SHADOW


def test_sentetik_r_donusumleri():
    assert gross_edge_from_expectancy(0.3, 400.0) == 120.0
    assert bp_to_r(40.0, 400.0) == 0.1 and bp_to_r(1.0, 0.0) is None
    e = CostEngine(CFG).estimate(**_tam())
    assert math.isclose(e.cost_r(400.0), e.total_cost_bp / 400.0)
