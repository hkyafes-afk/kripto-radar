"""Çevrimdışı testler: python -m pytest -q  (veya python tests/test_offline.py)"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import json

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import ledger as lg  # noqa: E402
from radar import signals as sg  # noqa: E402
from radar.config import load_config  # noqa: E402
from radar.demo import DemoClient  # noqa: E402

CFG = load_config()


def test_no_lookahead():
    """Son bardaki göstergeler, gelecekteki veriden etkilenmemeli."""
    df = DemoClient().candles("SOL-USDT-SWAP", "4H", 300)
    full = sg.frame_features(df, CFG)
    for t in (150, 200, 250, 299):
        part = sg.frame_features(df.iloc[: t + 1], CFG).iloc[-1]
        row = full.iloc[t]
        for col in ("st_dir", "rsi", "adx", "div", "bb_pct", "atr", "cvd_div"):
            a, b = part[col], row[col]
            assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-6, (t, col, a, b)


def test_daily_alignment_uses_closed_days():
    idx4 = pd.date_range("2026-01-01", periods=12, freq="4h", tz="UTC")
    f4 = pd.DataFrame({"x": range(12)}, index=idx4)
    fd = pd.DataFrame({"d_trend": [1, -1], "tsmom": [1.0, -1.0]},
                      index=pd.DatetimeIndex(["2026-01-01", "2026-01-02"], tz="UTC"))
    al = sg.align_daily(f4, fd)
    # 1 Ocak boyunca 1 Ocak günlük mumu henüz kapanmamış → NaN; son 4S mum (20:00) kapanınca kullanılabilir
    assert np.isnan(al["d_trend"].iloc[0])
    assert al["d_trend"].iloc[5] == 1          # 1 Ocak 20:00 mumu 2 Ocak 00:00'da kapanır
    assert al["d_trend"].iloc[11] == -1


def _bars(start_ms, prices):
    idx = pd.to_datetime([start_ms + i * 3600_000 for i in range(len(prices))], unit="ms", utc=True)
    rows = [{"open": p[0], "high": p[1], "low": p[2], "close": p[3]} for p in prices]
    return pd.DataFrame(rows, index=idx)


def _signal(side="long"):
    return {"inst": "X-USDT-SWAP", "sym": "X", "setup": f"trend_{side}", "side": side, "kind": "trend",
            "score": 70, "raw": 70, "entry": 100.0, "sl": 95.0 if side == "long" else 105.0,
            "tp": 110.0 if side == "long" else 90.0, "sl_pct": 5.0, "tp_pct": 10.0, "max_saat": 168,
            "funding_rate": 0.0001, "interval_h": 8, "tags": ["adx"], "reasons": []}


def test_ledger_tp_sl_time():
    t0 = 1_700_000_000_000
    # hedef
    L = lg.empty()
    lg.open_new(L, [_signal()], CFG, now_ms=t0)
    df = _bars(t0, [(100, 103, 99, 102), (102, 111, 101, 109)])
    closed = lg.update(L, lambda i: df, {}, CFG, now_ms=t0 + 3 * 3600_000)
    assert closed and closed[0]["outcome"] == "hedef" and closed[0]["r"] > 1.9
    # aynı mumda stop + hedef → muhafazakâr: stop
    L = lg.empty()
    lg.open_new(L, [_signal()], CFG, now_ms=t0)
    df = _bars(t0, [(100, 112, 94, 100)])
    closed = lg.update(L, lambda i: df, {}, CFG, now_ms=t0 + 2 * 3600_000)
    assert closed[0]["outcome"] == "stop" and closed[0]["r"] < -1.0
    # short hedef
    L = lg.empty()
    lg.open_new(L, [_signal("short")], CFG, now_ms=t0)
    df = _bars(t0, [(100, 101, 89, 90)])
    closed = lg.update(L, lambda i: df, {}, CFG, now_ms=t0 + 2 * 3600_000)
    assert closed[0]["outcome"] == "hedef" and closed[0]["r"] > 1.9
    # süre dolumu
    L = lg.empty()
    lg.open_new(L, [_signal()], CFG, now_ms=t0)
    df = _bars(t0, [(100, 101, 99, 101)] * 200)
    closed = lg.update(L, lambda i: df, {}, CFG, now_ms=t0 + 170 * 3600_000)
    assert closed[0]["outcome"] == "süre" and abs(closed[0]["pnl_pct"]) < 1.5
    # açık kalır + cooldown
    L = lg.empty()
    lg.open_new(L, [_signal()], CFG, now_ms=t0)
    lg.update(L, lambda i: _bars(t0, [(100, 101, 99, 100)]), {"X-USDT-SWAP": 102.0}, CFG, now_ms=t0 + 2 * 3600_000)
    assert len(L["open"]) == 1 and L["open"][0]["r_now"] == 0.4
    assert not lg.open_new(L, [_signal()], CFG, now_ms=t0 + 5 * 3600_000)   # zaten açık
    st = lg.stats(L, CFG)
    assert st["open_count"] == 1


def test_demo_dollars_and_limits():
    t0 = 1_700_000_000_000
    L = lg.empty()
    opened = lg.open_new(L, [_signal()], CFG, now_ms=t0)
    t = L["open"][0]
    assert t["margin_usd"] == 20 and t["notional_usd"] == 20 and abs(t["qty"] - 0.2) < 1e-9
    assert opened[0]["label"] if "label" in opened[0] else True
    # dakikalık mumla hedef: giriş anından önceki mum sayılmamalı
    idx_bars = _bars(t0 - 60_000, [(100, 120, 80, 100), (100, 101, 99, 100), (100, 110.5, 100, 110)])
    closed = lg.update(L, lambda i: idx_bars, {}, CFG, now_ms=t0 + 5 * 60_000, bar_ms=60_000)
    assert len(closed) == 1 and closed[0]["outcome"] == "hedef"
    # brüt 0.2 coin × 10$ = 2$ ; giriş taker %0.05 (0.01$) ; hedef limit → maker %0.02 (0.2×110×0.0002=0.0044$)
    assert abs(closed[0]["pnl_usd"] - 1.9856) < 1e-6, closed[0]["pnl_usd"]
    assert closed[0]["exit_liq"] == "maker"
    st = lg.stats(L, CFG)
    assert abs(st["demo"]["equity"] - 1001.99) < 0.006 and st["demo"]["return_pct"] > 0
    # limit: aynı periyotta en fazla max_acik_periyot
    L = lg.empty()
    sigs = []
    for i in range(30):
        s = _signal()
        s.update({"inst": f"C{i}-USDT-SWAP", "sym": f"C{i}", "tf": "15m"})
        sigs.append(s)
    lg.open_new(L, sigs, CFG, now_ms=t0)
    assert len(L["open"]) == CFG["demo"]["max_acik_periyot"]
    # eski kayıt göçü
    old = {"open": [{"inst": "X", "entry": 50.0}], "closed": [{"inst": "Y", "entry": 10.0, "pnl_pct": 5.0}]}
    lg.migrate(old, CFG)
    assert old["open"][0]["qty"] == 0.4 and old["closed"][0]["pnl_usd"] == 1.0


def test_realistic_execution():
    t0 = 1_700_000_000_000
    # 1) Kitaptan giriş: long ask'tan alır, stop/hedef mesafesi korunur
    L = lg.empty()
    op = lg.open_new(L, [_signal()], CFG, now_ms=t0, quote=lambda i: (100.2, 100.4, 100.3))
    t = L["open"][0]
    assert t["entry"] == 100.4 and t["entry_src"] == "kitap"
    assert abs(t["sl"] - 95.4) < 1e-9 and abs(t["tp"] - 110.4) < 1e-9
    # 2) OKX kontrat kuralları: BTC ctVal 0.01, lot 0.01 → 20$ / (0.01*80000=800$) = 0.025 → 0.02 kontrat
    L = lg.empty()
    sig = _signal(); sig.update({"entry": 80000.0, "sl": 78000.0, "tp": 84000.0})
    lg.open_new(L, [sig], CFG, now_ms=t0, meta=lambda i: {"ct_val": 0.01, "lot_sz": 0.01, "min_sz": 0.01})
    t = L["open"][0]
    assert t["contracts"] == 0.02 and abs(t["qty"] - 0.0002) < 1e-12 and abs(t["notional_usd"] - 16.0) < 1e-9
    # 3) En küçük lot çok büyükse işlem açılmaz, nedeni kaydedilir
    L, skipped = lg.empty(), []
    lg.open_new(L, [sig], CFG, now_ms=t0, meta=lambda i: {"ct_val": 1, "lot_sz": 1, "min_sz": 1}, skipped=skipped)
    assert not L["open"] and "en küçük lot" in skipped[0]["reason"]
    # 4) Gerçek funding: kesim anında long öder, short alır; aynı kesim iki kez uygulanmaz
    L = lg.empty()
    lg.open_new(L, [_signal()], CFG, now_ms=t0)
    pays = lg.apply_funding(L, "X-USDT-SWAP", 0.001, t0 + 3600_000, 100.0)
    assert len(pays) == 1 and abs(pays[0][1] - 0.02) < 1e-12        # 0.2 × 100 × 0.001
    assert not lg.apply_funding(L, "X-USDT-SWAP", 0.001, t0 + 3600_000, 100.0)
    assert not lg.apply_funding(L, "X-USDT-SWAP", 0.001, t0 - 1, 100.0)  # girişten önceki kesim sayılmaz
    c = lg.close_trade(L["open"].pop(), 100.0, t0 + 7200_000, "süre", CFG)
    assert abs(c["pnl_usd"] - (-0.01 - 0.01 - 0.02)) < 1e-9, c["pnl_usd"]   # 2 taker ücret + funding
    # 5) Mumdan tespit edilen stop'ta kayma eklenir (long: stop'un biraz altından dolum)
    L = lg.empty()
    lg.open_new(L, [_signal()], CFG, now_ms=t0)
    cl = lg.update(L, lambda i: _bars(t0, [(100, 100, 94, 95)]), {}, CFG, now_ms=t0 + 2 * 3600_000)
    assert cl[0]["outcome"] == "stop" and cl[0]["exit"] < 95.0
    # 6) Kaldıraç: 20x long'da likidasyon stop'tan önce gelir
    cfg = json.loads(json.dumps(CFG)); cfg["demo"]["kaldirac"] = 20
    L = lg.empty()
    lg.open_new(L, [_signal()], cfg, now_ms=t0)
    t = L["open"][0]
    assert t["liq_first"] and abs(t["liq"] - 100 * (1 - 1 / 20 + 0.005)) < 1e-9 and t["notional_usd"] == 400
    cl = lg.update(L, lambda i: _bars(t0, [(100, 100, 90, 92)]), {}, cfg, now_ms=t0 + 2 * 3600_000)
    assert cl[0]["outcome"] == "likidasyon" and abs(cl[0]["pnl_usd"] - (-20 - 0.2)) < 1e-9


def test_scoring_scalar_vs_vector():
    """Canlı (skaler) ve backtest (dizi) puanlaması aynı sonucu vermeli."""
    df = DemoClient().candles("ETH-USDT-SWAP", "4H", 300)
    f4 = sg.frame_features(df, CFG)
    n = len(f4)
    arrays = {k: f4[k].to_numpy(float) for k in f4.columns}
    arrays.update({"d_trend": np.ones(n), "tsmom": np.ones(n), "funding_ann": np.full(n, 10.0),
                   "oi_chg24": np.full(n, 5.0), "price": f4["close"].to_numpy()})
    for s in sg.build_setups(CFG):
        _, vec, _ = sg.score_setup(s, arrays, np.ones(n), CFG)
        for t in (n - 1, n - 20, n - 50):
            scal = {k: float(v[t]) for k, v in arrays.items()}
            _, sc, _ = sg.score_setup(s, scal, 1, CFG)
            a, b = float(sc), vec[t]
            assert (np.isnan(a) and np.isnan(b)) or a == b, (s.key, t, a, b)


def test_demo_scan_end_to_end():
    from radar import scan
    with tempfile.TemporaryDirectory() as d:
        p = scan.run(Path(d), client=DemoClient(), notify=False, demo=True)
        assert p["counts"]["scanned"] >= 30
        assert not any(c["sym"] == "CL" for c in p["coins"]), "emtia kontratı filtrelenmeli"
        assert (Path(d) / "index.html").read_text().count("window.__DATA__ = {") == 1
        # ikinci tarama: defter tekrar açmamalı
        p2 = scan.run(Path(d), client=DemoClient(), notify=False, demo=True)
        assert len(p2["ledger"]["open"]) == len(p["ledger"]["open"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
