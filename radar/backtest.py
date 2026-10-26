"""Walk-forward backtest (fiyat bazlı parçalar).

Neden walk-forward? Parametreleri tüm veride optimize edip aynı veride test
etmek kendini kandırmaktır (overfitting). Burada parametreler ilk %70'lik
dönemde seçilir, son %30'da (modelin hiç görmediği veri) ölçülür.

Not: OKX funding geçmişi ~3 ay, OI/taker geçmişi daha kısa olduğu için
backtest yalnızca fiyat/hacim bazlı parçaları kullanır. Funding/OI'nin katkısı
canlı defterde ölçülür.
"""
from __future__ import annotations

import copy
import itertools
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import signals as sg
from . import store
from .config import ROOT, load_config

GRID = {
    "supertrend_carpan": [2.5, 3.0, 3.5],
    "esik": [55, 65, 75],
    "trend": [(1.5, 3.0), (2.0, 4.0), (1.5, 2.0)],
    "donus": [(1.2, 2.0), (1.0, 1.5), (1.5, 3.0)],
}


def _feature_arrays(df4, d_aligned, regime_aligned, cfg):
    f4 = sg.frame_features(df4, cfg)
    f = {k: f4[k].to_numpy(dtype=float) for k in f4.columns}
    f["d_trend"] = d_aligned["d_trend"].to_numpy(dtype=float)
    f["tsmom"] = d_aligned["tsmom"].to_numpy(dtype=float)
    nan = np.full(len(df4), np.nan)
    f["funding_ann"] = nan
    f["oi_chg24"] = nan
    return f, regime_aligned.to_numpy()


def simulate(df4: pd.DataFrame, score: np.ndarray, atr: np.ndarray, side: str,
             thr: float, sl_k: float, tp_k: float, max_bars: int, cost_pct: float,
             start: int, end: int) -> list[dict]:
    o, h, l, c = (df4[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    n = len(c)
    sgn = 1 if side == "long" else -1
    trades = []
    i = max(start, 1)
    while i < min(end, n - 1):
        s = score[i]
        if not (s == s and s >= thr and atr[i] == atr[i] and atr[i] > 0):
            i += 1
            continue
        entry = o[i + 1]
        sl = entry - sgn * sl_k * atr[i]
        tp = entry + sgn * tp_k * atr[i]
        sl_pct = abs(entry - sl) / entry * 100
        exit_px, j, why = None, i + 1, "süre"
        last = min(n - 1, i + max_bars)
        for j in range(i + 1, last + 1):
            hit_sl = l[j] <= sl if sgn > 0 else h[j] >= sl
            hit_tp = h[j] >= tp if sgn > 0 else l[j] <= tp
            if hit_sl:
                exit_px, why = sl, "stop"
                break
            if hit_tp:
                exit_px, why = tp, "hedef"
                break
        if exit_px is None:
            exit_px = c[last]
            j = last
        net = sgn * (exit_px / entry - 1) * 100 - cost_pct
        trades.append({"i": i, "r": net / sl_pct, "pnl": net, "why": why})
        i = j + 1
    return trades


def _summ(trs):
    if not trs:
        return {"n": 0}
    r = np.array([t["r"] for t in trs])
    gl = -r[r <= 0].sum()
    return {"n": int(len(r)), "avg_r": round(float(r.mean()), 3), "sum_r": round(float(r.sum()), 1),
            "win_rate": round(float((r > 0).mean()) * 100, 1),
            "pf": round(float(r[r > 0].sum() / gl), 2) if gl > 0 else None}


def run(n_coins: int = 30, days: int = 365, client=None, cfg_path=None, out: Path | None = None) -> dict:
    t0 = time.time()
    base_cfg = load_config(cfg_path)
    if client is None:
        from .okx import OKXClient
        client = OKXClient()
    from .scan import build_universe
    cfg_u = copy.deepcopy(base_cfg)
    cfg_u["tarama"]["coin_sayisi"] = n_coins
    universe, _ = build_universe(client, cfg_u)
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days + 60) * 86400 * 1000
    btc_d = client.history_candles("BTC-USDT-SWAP", "1D", now_ms - (days + 300) * 86400 * 1000)
    reg_d = sg.btc_regime_series(btc_d)

    data = {}
    for r in universe:
        try:
            df4 = client.history_candles(r["inst"], "4H", start_ms)
            if len(df4) < 400:
                continue
            data[r["sym"]] = df4
            print(f"  {r['sym']}: {len(df4)} mum")
        except Exception as e:  # noqa: BLE001
            print(f"  {r['sym']} atlandı: {e}")
    if not data:
        raise RuntimeError("Backtest için veri alınamadı.")

    # Her coin için hizalanmış günlük özellik + rejim (parametreden bağımsız)
    prep = {}
    for sym, df4 in data.items():
        d1 = df4.resample("1D").agg({"open": "first", "high": "max", "low": "min",
                                     "close": "last", "volume": "sum"}).dropna()
        da = sg.align_daily(df4, sg.daily_features(d1, base_cfg))
        ra = sg.align_daily(df4, pd.DataFrame({"d_trend": reg_d, "tsmom": reg_d}))["d_trend"].fillna(0)
        warm = 60 * 6
        split = warm + int((len(df4) - warm) * 0.7)
        prep[sym] = (df4, da, ra, warm, split)

    cost = base_cfg["defter"]["komisyon_gidis_donus"] + base_cfg["defter"]["kayma"]
    results: dict[str, list] = {k: [] for k in sg.SETUP_LABELS}
    default_combo = {}
    for mult in GRID["supertrend_carpan"]:
        cfg = copy.deepcopy(base_cfg)
        cfg["gostergeler"]["supertrend_carpan"] = mult
        setups = sg.build_setups(cfg)
        cache = {}
        for sym, (df4, da, ra, warm, split) in prep.items():
            f, reg = _feature_arrays(df4, da, ra, cfg)
            cache[sym] = (f, reg, {s.key: sg.score_setup(s, f, reg, cfg)[1] for s in setups})
        for s in setups:
            sl_tp_list = GRID[s.kind]
            mh = int(cfg["sinyal"][s.kind]["max_saat"] / 4)
            for thr, (slk, tpk) in itertools.product(GRID["esik"], sl_tp_list):
                is_tr, oos_tr = [], []
                for sym, (df4, da, ra, warm, split) in prep.items():
                    f, reg, scores = cache[sym]
                    sc = scores[s.key]
                    is_tr += simulate(df4, sc, f["atr"], s.side, thr, slk, tpk, mh, cost, warm, split)
                    oos_tr += simulate(df4, sc, f["atr"], s.side, thr, slk, tpk, mh, cost, split, len(df4))
                params = {"st": mult, "esik": thr, "stop": slk, "hedef": tpk}
                results[s.key].append({"params": params, "is": _summ(is_tr), "oos": _summ(oos_tr)})
                dflt = base_cfg["sinyal"][s.kind]
                if (mult == base_cfg["gostergeler"]["supertrend_carpan"]
                        and thr == base_cfg["sinyal"]["guclu_esik"]
                        and (slk, tpk) == (dflt["stop_atr"], dflt["hedef_atr"])):
                    default_combo[s.key] = _summ(oos_tr)

    out_setups = {}
    for key, rows in results.items():
        ok = [r for r in rows if r["is"].get("n", 0) >= 30]
        best = max(ok, key=lambda r: r["is"]["avg_r"]) if ok else None
        out_setups[key] = {
            "best_params": best["params"] if best else None,
            "is_best": best["is"] if best else None,
            "oos_best": best["oos"] if best else None,
            "oos_default": default_combo.get(key),
            "top": sorted(ok, key=lambda r: r["is"]["avg_r"], reverse=True)[:8],
        }
    tz = ZoneInfo(base_cfg["bildirim"]["saat_dilimi"])
    report = {
        "generated_at": now_ms,
        "generated_local": datetime.now(tz).strftime("%d.%m.%Y %H:%M"),
        "coins": len(prep), "days": days, "duration_s": round(time.time() - t0, 1),
        "setups": out_setups,
        "note": ("Varsayılan ayarlar yalnızca 'Varsayılan' sütunu da pozitifse anlamlıdır. "
                 "Örneklem içi en iyi parametre, örneklem dışında çoğu zaman zayıflar; config'i "
                 "değiştirmeden önce OOS sonucunun pozitif ve işlem sayısının 30+ olduğundan emin ol."),
    }
    out = out or (ROOT / "data" / "backtest.json")
    store.dump(out, report)
    print(f"✓ Backtest bitti: {len(prep)} coin, {round(time.time() - t0)} sn → {out}")
    for k, v in out_setups.items():
        print(f"  {k}: en iyi {v['best_params']} OOS={v['oos_best']} | varsayılan OOS={v['oos_default']}")
    return report
