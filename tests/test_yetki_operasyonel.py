"""YETKI_GOLGE operasyonel shadow: yalnız gölge sanal portföy; hedef doğrulama (ZAMA); MFE/MAE/giveback; karne.
Gerçek emir, private endpoint ve PAPER yetki YOK. Veriler SENTETİKTİR."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from radar import karne  # noqa: E402
from radar.unified.decision import UnifiedDecisionEngine  # noqa: E402
from radar.unified.models import Decision, Side, Tradability  # noqa: E402
from radar.unified.targets import classify  # noqa: E402
from radar.unified.trader import UnifiedTrader  # noqa: E402
from test_golge_portfoy import fake_engine, golge, tick  # noqa: E402
from test_unified_strategies import kemal_ctx  # noqa: E402

ZAMA = dict(entry=0.06483, stop=0.08404, targets=(0.0264135, -0.0504194))


def test_zama_regresyonu_mekanik_ve_negatif():
    c = classify(Side.SHORT, ZAMA["entry"], ZAMA["stop"], ZAMA["targets"])
    assert c["hedef"] == []                       # mekanik 2R primary olamaz
    assert c["mekanik"] == [0.0264135]            # ≈ 2R referansı
    assert c["reddedilen"] == [-0.0504194]        # negatif (≈ 6R) reddedilir


def test_hedef_gecerlilik_kurallari():
    assert classify(Side.LONG, 100, 95, [float("nan"), 0, -1, 90, 120, float("inf")])["hedef"] == [120]
    assert classify(Side.SHORT, 100, 105, [80, 110])["hedef"] == [80]          # |80−100|/5 = 4R: yapısal
    assert classify(Side.SHORT, 100, 105, [90, 110])["mekanik"] == [90]        # 2R: yalnız referans
    assert classify(Side.SHORT, 100, 105, [80, 110])["reddedilen"] == [110]


def _opp(**kw):
    base = dict(side=Side.SHORT, entry=ZAMA["entry"], stop=ZAMA["stop"], targets=ZAMA["targets"], max_holding_hours=48)
    base.update(kw)
    return SimpleNamespace(**base)


COST = SimpleNamespace(missing_inputs=(), tradability=Tradability.PASS, net_edge_bp=12.0)


def test_operasyonel_uygunluk_tek_engel_ve_zama():
    op = UnifiedDecisionEngine._operational(_opp(), COST, Decision.SHADOW, ("YETKI_GOLGE",))
    assert op["uygun"] is True and op["hedef_tipi"] == "yok" and op["cikis_politikasi"] == "stop + iz + zaman"
    assert op["reddedilen"] == [-0.0504194] and op["mekanik"] == [0.0264135]


def test_operasyonel_engeller_ve_spread_eksikliği_acmaz():
    o = _opp()
    assert UnifiedDecisionEngine._operational(o, COST, Decision.SHADOW, ("YETKI_GOLGE", "POLITIKA:ATR"))["uygun"] is False
    assert UnifiedDecisionEngine._operational(o, COST, Decision.SHADOW, ("VERI_SORUNU:x",))["uygun"] is False
    spread_yok = SimpleNamespace(**{**COST.__dict__, "missing_inputs": ("spread",)})
    assert UnifiedDecisionEngine._operational(o, spread_yok, Decision.SHADOW, ("YETKI_GOLGE",))["uygun"] is False
    assert UnifiedDecisionEngine._operational(_opp(max_holding_hours=None), COST, Decision.SHADOW, ("YETKI_GOLGE",))["uygun"] is False
    assert UnifiedDecisionEngine._operational(_opp(stop=0.05), COST, Decision.SHADOW, ("YETKI_GOLGE",))["uygun"] is False


def _pos(side, entry, qty, risk, fees=0.0, funding=0.0):
    return {"symbol": "A", "inst": "A-USDT-SWAP", "side": side, "entry": entry, "qty": qty, "risk_usd": risk,
            "fees_usd": fees, "funding_usd": funding, "realized_usd": 0.0, "stop": 0, "targets": [], "hist": []}


def test_mfe_mae_giveback_guncellemesi():
    p = _pos("long", 100.0, 1.0, 10.0)
    for px in (110.0, 95.0, 105.0):
        UnifiedTrader._mark(None, p, px, 1)
    assert p["mfe_r"] == 1.0 and p["mae_r"] == -0.5
    assert p["peak_r"] == 1.0 and abs(p["giveback_r"] - 0.5) < 1e-9       # giveback = peak − şimdiki net R
    assert p["giveback_r"] >= 0


def test_fee_funding_yalniz_bir_kez_ve_short_pnl():
    p = _pos("short", 100.0, 2.0, 20.0, fees=1.0, funding=-0.5)
    UnifiedTrader._mark(None, p, 95.0, 1)
    assert abs(p["unrealized_usd"] - (2 * 5 - 1.0 - 0.5)) < 1e-9
    assert abs(p["unrealized_r"] - p["unrealized_usd"] / 20.0) < 1e-9


def test_karne_operasyonel_guven_etiketi():
    assert karne.guven(3) == "kanıt yok" and karne.guven(7) == "çok düşük" and karne.guven(15) == "ön eğilim"
    assert karne.guven(25) == "orta" and karne.guven(30) == "kullanılabilir örnek"
    k = karne.build([], [], golge_closed=[{"operational": True, "r": -0.5, "net_usd": -5.0, "strategies": ["x"]}])
    assert k["operasyonel_shadow"]["n"] == 1 and k["operasyonel_shadow"]["guven"] == "kanıt yok"


def test_operasyonel_golge_acilir_ve_tekrar_acmaz(tmp_path, monkeypatch):
    orig = UnifiedDecisionEngine.decide

    def decide(self, ctxs, now_ms=None):
        out = orig(self, ctxs, now_ms)
        conv = []
        for d in out:
            plan = {**(d.plan or {}), "operasyonel": {"uygun": True, "hedef": [], "mekanik": [], "reddedilen": [],
                                                       "hedef_tipi": "yok", "cikis_politikasi": "stop + iz + zaman",
                                                       "neden": []}}
            conv.append(replace(d, decision=Decision.SHADOW, plan=plan))
        return conv
    monkeypatch.setattr(UnifiedDecisionEngine, "decide", decide)
    fe = fake_engine(tmp_path)
    tick(fe, 101.0)
    fe._unified_shadow([kemal_ctx()])
    d = golge(tmp_path)
    assert len(d["pozisyonlar"]) == 1
    p = d["pozisyonlar"][0]
    assert p["operational"] is True and p["karar"] == "YETKI_GOLGE"
    assert p["target_type"] == "yok" and not [t for t in (p.get("targets") or []) if not t > 0]
    tick(fe, 103.0)
    fe._unified_shadow([kemal_ctx()])
    assert len(golge(tmp_path)["pozisyonlar"]) == 1                                  # aynı fırsat tekrar açılmaz
