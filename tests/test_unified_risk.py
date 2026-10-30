"""RiskGate (radar/unified/risk.py). Kararlar, portföy ve piyasa değerleri SENTETİKTİR (elle yazılmış sayılar)."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.config import load_config  # noqa: E402
from radar.unified.models import Decision, DecisionResult  # noqa: E402
from radar.unified.risk import MarketRisk, OpenPosition, PortfolioState, RiskGate, risk_settings  # noqa: E402

CFG = load_config()
NOW = 1_800_000_000_000


def dec(decision=Decision.OPEN, side="long", entry=100.0, stop=95.0, key="k1", mult=None):
    rp = {"boyut_carpani": mult if mult is not None else (1.0 if decision is Decision.OPEN else 0.5)}
    return DecisionResult(decision=decision, symbol="X", side=side, strategy="swing_trend", timeframe="1D",
                          confidence=0.6, gross_edge_bp=300.0, costs=None, net_edge_bp=200.0, entry=entry, stop=stop,
                          risk_proposal=rp, ts_ms=NOW, idempotency_key=key, plan={"inst": "X-USDT-SWAP"})


def pf(equity=1000.0, peak=None, day=None, positions=(), streak=0, halted=None, issue=None):
    return PortfolioState(equity=equity, peak_equity=peak or equity, day_start_equity=day or equity,
                          positions=tuple(positions), loss_streak=streak, halted=halted, data_issue=issue)


def mk(vol=100e6, high_vol=False, cluster=None, meta=None):
    return MarketRisk(inst="X-USDT-SWAP", price=100.0, vol24_usd=vol, high_vol=high_vol, cluster=cluster, meta=meta)


G = RiskGate(CFG)


def test_sentetik_temel_boyutlandirma_yuzde_yarim_risk():
    r = G.evaluate(dec(), pf(), mk())
    assert r.decision is Decision.OPEN and r.approved
    assert abs(r.risk_usd - 5.0) < 1e-6 and abs(r.risk_pct - 0.5) < 1e-6        # %0,5 × 1000$
    assert abs(r.notional_usd - 100.0) < 1e-6                                    # 5$ / %5 stop
    assert r.leverage == 1.0 and r.liq_price is None                             # en düşük yeterli kaldıraç
    assert "toplam_risk" in r.limits_checked and not r.limits_hit


def test_sentetik_yarim_boyut_karari():
    r = G.evaluate(dec(Decision.REDUCE_SIZE), pf(), mk())
    assert r.decision is Decision.REDUCE_SIZE and abs(r.risk_usd - 2.5) < 1e-6


def test_sentetik_acma_karari_degilse_gecer():
    r = G.evaluate(dec(Decision.SHADOW), pf(), mk())
    assert r.decision is Decision.SHADOW and not r.approved


def test_sentetik_devre_kesiciler():
    assert G.evaluate(dec(), pf(halted="elle"), mk()).reason_codes == ("DEVRE_DURDURULDU",)
    r = G.evaluate(dec(), pf(equity=890.0, peak=1000.0), mk())
    assert r.decision is Decision.REJECT and r.reason_codes[0].startswith("DEVRE_MAX_DUSUS")
    r = G.evaluate(dec(), pf(equity=975.0, peak=1000.0, day=1000.0), mk())
    assert r.decision is Decision.REJECT and r.reason_codes[0].startswith("DEVRE_GUNLUK_KAYIP")
    r = G.evaluate(dec(), pf(issue="WS kopuk"), mk())
    assert r.decision is Decision.SHADOW and r.reason_codes == ("DEVRE_VERI",)


def test_sentetik_coin_basina_tek_pozisyon_ve_tekrar():
    p = OpenPosition(inst="X-USDT-SWAP", side="long", risk_usd=5.0)
    assert G.evaluate(dec(), pf(positions=[p]), mk()).reason_codes == ("COIN_ACIK",)
    q = OpenPosition(inst="Y-USDT-SWAP", side="long", risk_usd=5.0, idempotency_key="k1")
    assert G.evaluate(dec(), pf(positions=[q]), mk()).reason_codes == ("TEKRAR_FIRSAT",)


def test_sentetik_toplam_risk_siniri_kucultur_ya_da_reddeder():
    full = [OpenPosition(inst=f"C{i}", side="short" if i % 2 else "long", risk_usd=5.0) for i in range(5)]
    r = G.evaluate(dec(), pf(positions=full), mk())                     # 25$ dolu, sınır 30$ → 5$ kaldı → tam
    assert r.decision is Decision.OPEN
    six = full + [OpenPosition(inst="C9", side="short", risk_usd=3.5)]  # 28,5$ → 1,5$ kaldı (istenen 5$'ın %30'u)
    r = G.evaluate(dec(), pf(positions=six), mk())
    assert r.decision is Decision.REDUCE_SIZE and "toplam_risk" in r.limits_hit and r.risk_usd <= 1.5 + 1e-9
    seven = six + [OpenPosition(inst="C8", side="short", risk_usd=1.0)]
    r = G.evaluate(dec(), pf(positions=seven), mk())                    # 0,5$ kaldı → %10 < %25 → red
    assert r.decision is Decision.REJECT and "RISK_COK_KUCULDU" in r.reason_codes


def test_sentetik_ayni_yon_ve_kume():
    same = [OpenPosition(inst=f"L{i}", side="long", risk_usd=0.1) for i in range(5)]
    r = G.evaluate(dec(), pf(positions=same), mk())
    assert r.decision is Decision.REJECT and r.reason_codes[0].startswith("AYNI_YON_ADET")
    cl = [OpenPosition(inst=f"M{i}", side="short", risk_usd=1.0, cluster="meme") for i in range(2)]
    r = G.evaluate(dec(), pf(positions=cl), mk(cluster="meme"))
    assert r.decision is Decision.REJECT and r.reason_codes == ("KUME_DOLU:meme",)


def test_sentetik_kaldirac_en_dusuk_yeterli_ve_tavan():
    # dar stop (%0,5) → nominal 1000$ isterdi; tek pozisyon tavanı bakiyenin %35'i = 350$ → küçülür
    r = G.evaluate(dec(stop=99.5), pf(), mk())
    assert r.decision is Decision.REDUCE_SIZE and "nominal_tavan" in r.limits_hit
    assert abs(r.notional_usd - 350.0) < 1e-6 and r.leverage == 1.0
    # teminatın çoğu kullanımda → kaldıraç artar ama likidite/oynaklık tavanını aşamaz
    used = [OpenPosition(inst="Z", side="short", risk_usd=0.0, margin_usd=900.0)]
    r = G.evaluate(dec(stop=99.5), pf(positions=used), mk())
    assert r.leverage == 5.0 and abs(r.margin_usd - r.notional_usd / 5) < 1e-9
    r2 = G.evaluate(dec(stop=99.5), pf(positions=used), mk(high_vol=True))
    assert r2.leverage <= 2.0
    r3 = G.evaluate(dec(stop=99.5), pf(positions=used), mk(vol=None))   # likidite bilinmiyor → düşük sayılır
    assert r3.leverage <= 2.0


def test_sentetik_likidasyon_tamponu():
    used = [OpenPosition(inst="Z", side="short", risk_usd=0.0, margin_usd=950.0)]
    r = G.evaluate(dec(stop=80.0), pf(positions=used), mk())            # geniş stop: yüksek kaldıraçta likidasyon stop'tan önce
    assert r.approved
    if r.liq_price is not None:
        assert r.liq_price < 80.0 and (100 - r.liq_price) >= 2 * 20 - 1e-9


def test_sentetik_kayip_serisi_riski_yariya_indirir():
    n = int(risk_settings(CFG)["kayip_serisi"])
    r = G.evaluate(dec(), pf(streak=n), mk())
    assert abs(r.risk_usd - 2.5) < 1e-6 and any(c.startswith("KAYIP_SERISI") for c in r.reason_codes)


def test_sentetik_lot_yuvarlama():
    r = G.evaluate(dec(), pf(), mk(meta={"ct_val": 0.1, "lot_sz": 1, "min_sz": 1}))
    assert r.approved and abs(r.qty - 1.0) < 1e-9                     # 100$/100 = 1 coin = 10 kontrat
    r = G.evaluate(dec(), pf(), mk(meta={"ct_val": 10, "lot_sz": 1, "min_sz": 1}))   # en küçük lot 1000$
    assert r.decision is Decision.REJECT and r.reason_codes[0].startswith("LOT")


def test_sentetik_gecersiz_stop():
    assert G.evaluate(dec(stop=105.0), pf(), mk()).reason_codes == ("GIRIS_STOP_GECERSIZ",)


def test_birlesik_risk_config_ile_degisir():
    cfg = copy.deepcopy(CFG)
    cfg.setdefault("birlesik", {})["risk"] = {"max_kume": 5}
    assert risk_settings(cfg)["max_kume"] == 5 and risk_settings(CFG)["max_kume"] == 2
