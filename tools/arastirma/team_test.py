"""Mevcut ekip (Kemal, Kaan, Selin, Murat) — aynı Binance verisi (zombi düzeltmeli, hacme göre ilk 100, batanlar dahil),
yarışın kendi kuralları ve risk ayarlarıyla. Özsermaye EĞİTİM (2020-10-28 → 2025-01-06) ve SAKLI TEST (2025-01-07 →)
pencerelerinde ayrı ayrı (her pencere 1000$ ile başlar; işlem giriş tarihi pencere içinde olanlar).
"""
import copy
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, sys.argv[1])          # repo
from radar import binance_data, bt2, learn, quant, team  # noqa: E402
from radar.config import load_config  # noqa: E402

VERI = sys.argv[2]
OUT = sys.argv[3]
TRAIN_START = pd.Timestamp("2020-10-28", tz="UTC")
SPLIT = pd.Timestamp("2025-01-07", tz="UTC")
LONG_ONLY = {"kaan", "selin"}

base = load_config(None)
base["_learned"] = learn.load_learned()
base.setdefault("ekip", {})["varyant"] = {}
daily, elig, ages, info = binance_data.load(VERI, top_n=100)
print(info, flush=True)
end = max(d[1].index[-1] for d in daily.values())
BF = quant.features(daily["BTC"][1])
wx = {BF.index[k]: quant.btc_weather(BF, k) for k in range(len(BF))}
feats = {s: quant.features(df) for s, (inst, df, _) in daily.items()}
mgrs = {m.id: m for m in team.roster(base) if m.analyze}


def trades_for(mid):
    cfg = copy.deepcopy(base)
    cfg["_variant"] = {mid: []}
    out = []
    for sym, (inst, df, vol) in daily.items():
        F = feats[sym]
        idx = F.index
        warm = max(int(np.searchsorted(idx, TRAIN_START)), 210)
        va = vol.to_numpy(float)

        def extra(k, idx=idx, va=va):
            w = wx.get(idx[k]) or {}
            v = None if va[k] != va[k] else float(va[k])
            return {"btc_bear": w.get("btc_bear", False), "btc_bull": w.get("btc_bull", False),
                    "w_trend": w.get("trend"), "high_vol": w.get("high_vol"), "vol24_usd": v}
        out += bt2.run_manager(mgrs[mid], F, sym, inst, 24.0, warm, extra, cfg, first_ts=ages.get(sym),
                               eligible=elig[sym].to_numpy(bool))
    return out


def wmult(t):
    return quant.weather_mult({"trend": t.get("weather_trend") or "yatay", "high_vol": t.get("high_vol")},
                              t["mgr"], t["side"], bear_long=0.5)


def window(trs, risk, lo, hi):
    w = [t for t in trs if lo <= t["entry_ts"] < hi]
    days = pd.date_range(lo.normalize(), min(hi, end).normalize(), freq="1D", tz="UTC")
    ec = bt2.equity_curve(w, risk, days=days, mult_fn=wmult)
    p = bt2.perf(ec["curve"]) if ec else {}
    rs = [t["r"] for t in w]
    return {"işlem": len(w), "ort_R": round(float(np.mean(rs)), 3) if rs else None,
            "kazanma%": round(100 * float(np.mean([r > 0 for r in rs])), 1) if rs else None,
            "yıllık%": p.get("cagr_pct"), "maksdüşüş%": p.get("max_dd_pct"), "Sharpe": p.get("sharpe")}, ec


res, pool, risks = {}, [], {}
for mid in ("kemal", "kaan", "selin", "murat"):
    trs = trades_for(mid)
    eqtr = [t for t in trs if t["side"] == "long"] if mid in LONG_ONLY else trs
    risks[mid] = mgrs[mid].base_risk
    pool += eqtr
    for lab, lo, hi in (("EĞİTİM", TRAIN_START, SPLIT), ("TEST", SPLIT, end + pd.Timedelta(days=1))):
        m, _ = window(eqtr, mgrs[mid].base_risk, lo, hi)
        res[(mid, lab)] = m
        print(f"[{lab:6}] {mid:6} {m}", flush=True)
for lab, lo, hi in (("EĞİTİM", TRAIN_START, SPLIT), ("TEST", SPLIT, end + pd.Timedelta(days=1))):
    m, ec = window(pool, risks, lo, hi)
    res[("ekip", lab)] = m
    print(f"[{lab:6}] ekip   {m}", flush=True)
pickle.dump(res, open(OUT, "wb"))
