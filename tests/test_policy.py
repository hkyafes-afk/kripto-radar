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


def test_gates():
    assert policy.gate(_idea(), {}, CFG) is None
    assert "oynaklık" in policy.gate(_idea(atr=6.0), {}, CFG)
    assert policy.gate(_idea(atr=6.0, mgr="ayse"), {}, CFG) is None, "Ayşe oynaklık kapısından muaf"
    assert "kalabalık" in policy.gate(_idea(), {"funding_ann": 45}, CFG)
    assert policy.gate(_idea(side="short", sl=104, tp=76), {"funding_ann": 45}, CFG) is None
    assert "kalabalık" in policy.gate(_idea(side="short", sl=104, tp=76), {"funding_ann": -30}, CFG)
    assert "Kaan" in policy.gate(_idea(), {"w_trend": "yatay"}, CFG)
    assert policy.gate(_idea(mgr="selin"), {"w_trend": "yatay"}, CFG) is None
    assert policy.gate(_idea(tf="15m", atr=9.0), {}, CFG) is None, "yalnızca günlük fikirler"
    off = copy.deepcopy(CFG)
    off["ekip"]["kurallar"]["aktif"] = False
    assert policy.gate(_idea(atr=9.0), {}, off) is None


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
