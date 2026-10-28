"""v10 politika testleri: oynaklık/kalabalık kapıları, yeniden giriş, kapanış stop'u (canlı defter + bt2)."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import bt2, policy, team  # noqa: E402
from radar import desk as dk  # noqa: E402
from radar import ledger as lg  # noqa: E402
from radar.config import load_config  # noqa: E402

CFG = load_config()
DAY = 86_400_000
NOW = 1_800_000_000_000


def _idea(**kw):
    i = {"mgr": "kaan", "inst": "X-USDT-SWAP", "sym": "X", "tf": "1D", "side": "long", "kind": "trend",
         "entry": 100.0, "market_px": 100.0, "sl": 96.0, "tp": 124.0, "tp1": 108.0, "atr": 3.0, "conviction": 0.5,
         "max_saat": 720, "pp": {"partial_frac": 0.3, "trail_atr": 2.5, "basabas": True}, "funding_rate": 0.0001,
         "interval_h": 8, "entry_type": "piyasa", "checks": [], "plan": "", "reasons": []}
    i.update(kw)
    return i


def test_turbo_duplicate_is_shadowed():
    """v10.1 kuralı: sanal portföy kapalıyken Turbo aynı bahsi ikinci kez gerçek parayla açmaz."""
    cfg = copy.deepcopy(CFG)
    cfg["ekip"]["sanal"] = []
    L = lg.empty()
    d = dk.Desk(cfg, {}, L)
    L["open"].append(lg.build_trade(_idea(mgr="bas", atr=2.0), 100.0, 10.0, 10.0, 1.0, cfg, NOW, 10_000))
    kind, det, _ = d.evaluate(_idea(mgr="turbo", atr=2.0), NOW)
    assert det and det["shadow"] and any("turbo tekrarı" in g for g in det.get("gm") or []), "aynı bahis ikinci kez gerçek olmaz"
    kind, det, _ = d.evaluate(_idea(mgr="turbo", inst="Y-USDT-SWAP", sym="Y", atr=2.0), NOW)
    assert det and not det["shadow"], "başka coinde turbo serbest"


def test_virtual_managers_always_shadow():
    """v10.2: Turbo ve Ortak Masa fikir üretir, kaydı tutulur, ama hep gölgede (gerçek sermaye yok)."""
    assert set(CFG["ekip"]["sanal"]) == {"turbo", "ortak"}
    d = dk.Desk(CFG, {}, lg.empty())
    kind, det, _ = d.evaluate(_idea(mgr="turbo", inst="Y-USDT-SWAP", sym="Y", atr=2.0), NOW)
    assert det and det["shadow"] and any("sanal portföy" in g for g in det.get("gm") or [])
    kind, det, _ = d.evaluate(_idea(mgr="kemal", inst="Z-USDT-SWAP", sym="Z", atr=2.0), NOW)
    assert det and not det["shadow"], "diğer ajanlar etkilenmez"


def test_gates():
    assert policy.gate(_idea(), {}, CFG) is None
    assert "oynaklık" in policy.gate(_idea(atr=6.0), {}, CFG)
    assert policy.gate(_idea(atr=6.0, mgr="ayse"), {}, CFG) is None, "Ayşe oynaklık kapısından muaf"
    assert "kalabalık" in policy.gate(_idea(), {"funding_ann": 45}, CFG)
    assert policy.gate(_idea(side="short", sl=104, tp=76), {"funding_ann": 45}, CFG) is None
    assert "kalabalık" in policy.gate(_idea(side="short", sl=104, tp=76), {"funding_ann": -30}, CFG)
    assert "Kaan" in policy.gate(_idea(), {"w_trend": "yatay"}, CFG), "Kaan'ın kendi kuralı önce gelir"
    assert "rejim" in policy.gate(_idea(mgr="selin"), {"w_trend": "yatay"}, CFG), "v10.2: yatayda hiçbir yön gerçek değil"
    assert policy.gate(_idea(tf="15m", atr=9.0), {}, CFG) is None, "yalnızca günlük fikirler"
    assert "stop kapısı" in policy.gate(_idea(sl=80.0, tp=130.0), {}, CFG), "giriş–stop %20 > %15 → gölge"
    assert policy.gate(_idea(sl=86.0, tp=120.0), {}, CFG) is None, "%14 stop serbest"
    off = copy.deepcopy(CFG)
    off["ekip"]["kurallar"]["aktif"] = False
    assert policy.gate(_idea(atr=9.0), {}, off) is None


def test_regime_gate():
    short = dict(side="short", sl=104.0, tp=76.0)
    assert policy.gate(_idea(mgr="selin"), {"w_trend": "boga"}, CFG) is None, "boğada long gerçek"
    assert "rejim" in policy.gate(_idea(mgr="selin"), {"w_trend": "ayi"}, CFG), "ayıda long gölge"
    assert policy.gate(_idea(mgr="selin", **short), {"w_trend": "ayi"}, CFG) is None, "ayıda short gerçek"
    assert "rejim" in policy.gate(_idea(mgr="selin", **short), {"w_trend": "boga"}, CFG), "boğada short gölge"
    assert "rejim" in policy.gate(_idea(mgr="bas", **short), {"w_trend": "yatay"}, CFG), "Baş Trader dahil"
    assert policy.gate(_idea(mgr="selin"), {}, CFG) is None, "hava bilinmiyorsa kapı yok"
    assert policy.gate(_idea(mgr="elif", kind="carry"), {"w_trend": "ayi"}, CFG) is None, "Elif (carry) muaf"
    assert policy.gate(_idea(mgr="selin", tf="1H"), {"w_trend": "ayi"}, CFG) is None, "yalnızca günlük fikirler"
    off = copy.deepcopy(CFG)
    off["ekip"]["kurallar"]["rejim_uyumu"] = False
    assert policy.gate(_idea(mgr="selin"), {"w_trend": "ayi"}, off) is None


def test_desk_regime_gate_and_policy_version():
    d = dk.Desk(CFG, {}, lg.empty())
    d.env = {"weather": {"trend": "ayi", "btc_bear": True, "btc_bull": False}}
    kind, det, _ = d.evaluate(_idea(mgr="kemal", atr=2.0), NOW)
    assert det and det["shadow"] and any("rejim kapısı" in g for g in det.get("gm") or []), "ayıda long gölgede ölçülür"
    kind, det, _ = d.evaluate(_idea(mgr="kemal", inst="Y-USDT-SWAP", sym="Y", side="short", sl=104.0, tp=76.0, atr=2.0), NOW)
    assert det and not det["shadow"], "ayıda short gerçek"
    t = lg.build_trade(_idea(), 100.0, 10.0, 10.0, 1.0, CFG, NOW, 10_000)
    assert t["politika"] == policy.VERSION == "v10.2"


def test_profit_lock():
    """v10.2 kâr kilidi: tepe kâr 0,5R'yi geçince stop tepe kârın %60'ına; altında dokunmaz; yalnız kâr yönüne."""
    t = lg.build_trade(_idea(tf="4H"), 100.0, 10.0, 10.0, 1.0, CFG, NOW, 10_000)     # stop 96 → 1R = 4
    assert not lg.trail(t, 101.5, CFG) and t["sl"] == 96.0, "tepe 0,375R: kilit yok"
    assert lg.trail(t, 103.0, CFG) and abs(t["sl"] - 101.8) < 1e-9, "tepe 0,75R: stop 100 + 0,6×3"
    assert not lg.trail(t, 102.0, CFG) and abs(t["sl"] - 101.8) < 1e-9, "fiyat geri gelince stop geri gitmez"
    s = lg.build_trade(_idea(tf="4H", side="short", sl=104.0, tp=76.0), 100.0, 10.0, 10.0, 1.0, CFG, NOW, 10_000)
    assert lg.trail(s, 97.0, CFG) and abs(s["sl"] - 98.2) < 1e-9, "short: 100 − 0,6×3"
    off = copy.deepcopy(CFG)
    off["risk"]["kar_kilidi"]["aktif"] = False
    u = lg.build_trade(_idea(tf="4H"), 100.0, 10.0, 10.0, 1.0, off, NOW, 10_000)
    assert not lg.trail(u, 103.0, off) and u["sl"] == 96.0


def test_reentry():
    loss = {"mgr": "kaan", "inst": "X-USDT-SWAP", "side": "long", "exit_ts": NOW - 2 * DAY, "r": -1.0}
    assert "zararla" in policy.reentry_block([loss], _idea(), NOW, CFG)
    assert policy.reentry_block([loss], _idea(side="short", sl=104, tp=76), NOW, CFG) is None, "ters yön serbest"
    assert policy.reentry_block([dict(loss, exit_ts=NOW - 6 * DAY)], _idea(), NOW, CFG) is None
    assert policy.reentry_block([loss], _idea(mgr="bas"), NOW, CFG) is None, "Baş Trader hariç"
    win = dict(loss, r=2.0)
    closes = [(NOW - 3 * DAY, 99.0), (NOW - 2 * DAY, 103.0), (NOW - DAY, 101.0)]
    assert "en iyi" in policy.reentry_block([win], _idea(entry=102.0), NOW, CFG, closes)
    assert policy.reentry_block([win], _idea(entry=104.0), NOW, CFG, closes) is None


def test_close_stop_trade_and_moves():
    t = lg.build_trade(_idea(), 100.0, 10.0, 10.0, 1.0, CFG, NOW, 10_000)
    assert t["sl_close"] == 96.0 and abs(t["sl"] - 92.0) < 1e-9, "gün içinde 2R felaket stop'u, kapanışta 1R"
    assert abs(lg.open_risk(t) - 40.0) < 1e-6 and abs(t["risk_usd"] - 40.0) < 1e-6
    assert not lg.set_sl(t, 94.0) and t["sl_close"] == 96.0, "kapanış stop'unun gerisine taşınmaz"
    assert lg.set_sl(t, 97.0) and "sl_close" not in t and t["sl"] == 97.0, "ötesine geçince normal stop"
    t2 = lg.build_trade(_idea(), 100.0, 10.0, 10.0, 1.0, CFG, NOW, 10_000)
    lg._breakeven(t2, CFG)
    assert "sl_close" not in t2 and t2["sl"] > 100.0
    t3 = lg.build_trade(_idea(tf="15m"), 100.0, 10.0, 10.0, 1.0, CFG, NOW, 10_000)
    assert "sl_close" not in t3 and t3["sl"] == 96.0
    t4 = lg.build_trade(_idea(kind="consensus"), 100.0, 10.0, 10.0, 1.0, CFG, NOW, 10_000)
    assert "sl_close" not in t4


def test_desk_shadows_gated_idea_even_for_bas():
    st, L = {}, lg.empty()
    d = dk.Desk(CFG, st, L)
    kind, det, _ = d.evaluate(_idea(mgr="bas", atr=8.0), NOW)
    assert det and det["shadow"] and any("oynaklık" in g for g in det.get("gm") or [])
    kind, det, _ = d.evaluate(_idea(mgr="bas", inst="Y-USDT-SWAP", sym="Y"), NOW)
    assert det and not det["shadow"]


def test_engine_close_stop_exit():
    import tempfile
    from tests.test_engine_live import _engine
    with tempfile.TemporaryDirectory() as dd:
        e = _engine(dd)
        t = lg.build_trade(_idea(), 100.0, 10.0, 10.0, 1.0, CFG, NOW, 10_000)
        e.ledger["open"].append(t)
        e.signal_exits = []
        e._queue_rule_exits({"inst": t["inst"], "tf": "1D", "close": 95.0, "live_bar": True})
        assert not e.signal_exits, "canlı (kapanmamış) mumla kapanış stop'u çalışmaz"
        e._queue_rule_exits({"inst": t["inst"], "tf": "1D", "close": 97.0})
        assert not e.signal_exits
        e._queue_rule_exits({"inst": t["inst"], "tf": "1D", "close": 95.0})
        assert e.signal_exits and "kapanış stop" in e.signal_exits[0][1]


def test_bt2_close_stop_vs_wick():
    idx = pd.date_range("2024-01-01", periods=6, freq="1D", tz="UTC")
    #   gün1 giriş 100; gün2 fitil 95 (stop 96 altında) ama kapanış 99; sonra yükseliş
    F = pd.DataFrame({"open": [100, 100, 99, 101, 104, 108], "high": [101, 101, 102, 105, 109, 112],
                      "low": [99, 99, 95, 100, 103, 107], "close": [100, 99.5, 99, 104, 108, 111]}, index=idx, dtype=float)
    base = {"mgr": "kaan", "sym": "X", "side": "long", "entry": 100.0, "sl": 96.0, "tp": 110.0, "tp1": None,
            "atr": 2.0, "max_saat": 24 * 30, "pp": {}, "exit": None}
    wick = bt2.simulate_trade(dict(base), F, 0, 24.0, lambda k: {})
    close = bt2.simulate_trade(dict(base, felaket_r=2.0), F, 0, 24.0, lambda k: {})
    assert "stop" in wick["why"] and wick["r"] < -0.9
    assert close["why"] == "hedef" and close["r"] > 2.0, "fitil kapanış stop'unu tetiklemez"


def test_turbo_risk_and_policy_config():
    m = {x.id: x for x in team.roster(CFG)}
    assert m["turbo"].max_risk <= 2.0 and not (m["turbo"].extra or {}).get("piramit_tam")
    assert m["kaan"].max_risk <= 2.0 and m["selin"].max_risk <= 3.0
    assert policy.cfg_of(CFG)["max_atr_yuzde"] == 5.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
