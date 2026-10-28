"""Analist-Hakem eğitimi (v9, her gece ayrı GitHub işi): "bu sinyal tutar mı?" (meta-labeling, López de Prado).

Veri: 5 yıllık testte yöneticilerin ürettiği binlerce işlem (canlı fonksiyonlarla, sert maliyetle) + canlı ve gölge
kapanmış işlemler (kalp dalındaki defter). Her işlemin giriş anındaki özellikleri (team.features_for) ve sonucu (R).

Model: yönetici başına ridge regresyon, hedef R (−1,5…+3'e kırpılır; tek bir uç işlem modeli çekmesin). Az veride
bütün yöneticiler birlikte. Zaman sıralı ayrım: ilk %70 eğitim, son %30 doğrulama (görülmemiş).

Söz hakkı şartı (validated): doğrulamada tahmin ile gerçek R arasında pozitif ilişki (≥ 0,05) VE modelin önerdiği
boyutla (0,5×–1,5×) işlem başına R, düz boyuta göre en az +0,02R iyi. Şart tutmazsa model tahminini yazar ama
boyuta dokunmaz. Sihirli değnek değildir: zayıf bir sinyali kurtarmaz, iyi sinyali seçici yapar.
Çıktı: data/hakem.json (canlı motor saatte bir okur) + data/HAKEM.md.
"""
from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import bt2, learn, quant, team
from .config import ROOT, load_config

MGRS = ("kemal", "kaan", "selin", "murat", "ayse")
LAMBDA = 5.0
MIN_GROUP = 300


def _xy(trs: list[dict], feats: list[str]):
    X = np.array([[np.nan if (t.get("feat") or {}).get(k) is None else float(t["feat"][k]) for k in feats] for t in trs])
    y = np.clip(np.array([float(t["r"]) for t in trs]), -1.5, 3.0)
    return X, y


def fit(trs: list[dict], feats: list[str]) -> dict | None:
    trs = sorted(trs, key=lambda t: t["entry_ts"])
    n = len(trs)
    if n < 120:
        return None
    k = int(n * 0.7)
    X, y = _xy(trs, feats)
    tr_X, tr_y, va_X, va_y = X[:k], y[:k], X[k:], y[k:]
    mean = np.nanmean(tr_X, axis=0)
    mean = np.where(np.isnan(mean), 0.0, mean)
    sd = np.nanstd(tr_X, axis=0)
    sd = np.where((sd > 1e-9) & ~np.isnan(sd), sd, 1.0)

    def z(A):
        A = np.where(np.isnan(A), mean, A)
        return (A - mean) / sd
    Zt, Zv = z(tr_X), z(va_X)
    b0 = float(tr_y.mean())
    A = Zt.T @ Zt + LAMBDA * np.eye(Zt.shape[1])
    coef = np.linalg.solve(A, Zt.T @ (tr_y - b0))
    pt, pv = b0 + Zt @ coef, b0 + Zv @ coef
    pm, ps = float(pt.mean()), float(pt.std()) or 1e-9
    corr = float(np.corrcoef(pv, va_y)[0, 1]) if len(va_y) > 10 and pv.std() > 0 else 0.0
    mult = np.clip(1.0 + np.clip(0.25 * (pv - pm) / ps, -0.5, 0.5), 0.5, 1.5)
    real_va = np.array([float(t["r"]) for t in trs[k:]])
    gain = float((mult * real_va).sum() / mult.sum() - real_va.mean()) if len(real_va) else 0.0
    validated = bool(corr >= 0.05 and gain >= 0.02)
    imp = sorted(zip(feats, coef), key=lambda x: -abs(x[1]))
    return {"b0": round(b0, 5), "coef": [round(float(c), 5) for c in coef], "mean": [round(float(x), 5) for x in mean],
            "sd": [round(float(x), 5) for x in sd], "pred_mean": round(pm, 5), "pred_sd": round(ps, 5), "n": n,
            "n_train": k, "n_val": n - k, "val_corr": round(corr, 3), "val_gain_r": round(gain, 4),
            "val_avg_r": round(float(real_va.mean()), 4) if len(real_va) else None, "validated": validated,
            "top": [[f, round(float(c), 4)] for f, c in imp[:6]]}


def live_trades(ledger_path: Path | None) -> list[dict]:
    if not ledger_path or not ledger_path.exists():
        return []
    try:
        L = json.loads(ledger_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for t in L.get("closed") or []:
        if t.get("feat") and t.get("mgr") in MGRS and t.get("r") is not None and not t.get("carry"):
            out.append({"mgr": t["mgr"], "r": float(t["r"]), "feat": t["feat"],
                        "entry_ts": pd.Timestamp(int(t["entry_ts"]), unit="ms", tz="UTC"), "live": True})
    return out


def run(client=None, cfg_path=None, n_coins=30, years=5.0, ledger_path: Path | None = None,
        out: Path | None = None) -> dict:
    t0 = time.time()
    base = load_config(cfg_path)
    base["_learned"] = learn.load_learned()
    if client is None:
        from .okx import OKXClient
        client = OKXClient()
    daily, _, now_ms = bt2.load_data(client, base, n_coins, years, deniz_days=0)
    test_start = pd.Timestamp(now_ms - int(years * 365 * 86400_000), unit="ms", tz="UTC")
    BF = quant.features(daily["BTC"][1])
    wx = {BF.index[k]: quant.btc_weather(BF, k) for k in range(len(BF))}
    feats = {sym: quant.features(df) for sym, (inst, df, _) in daily.items()}
    # göreli güç: her gün coin 30 günlük getirisi − BTC'ninki, kesitsel yüzdelik sıra
    r30 = pd.DataFrame({sym: F["ret30"] for sym, F in feats.items()})
    rel = r30.sub(r30["BTC"], axis=0) if "BTC" in r30 else r30
    rank = rel.rank(axis=1, pct=True)
    ages = {sym: bt2.coin_first_ts(client, sym, inst) for sym, (inst, df, _) in daily.items()}
    mgrs = {m.id: m for m in team.roster(base) if m.id in MGRS and m.analyze}
    trades = []
    for sym, (inst, df, vol) in daily.items():
        F = feats[sym]
        idx = F.index
        warm = max(int(np.searchsorted(idx, test_start)), 210)

        def extra(k, idx=idx, sym=sym):
            w = wx.get(idx[k]) or {}
            ts = idx[k]
            return {"btc_bear": w.get("btc_bear", False), "btc_bull": w.get("btc_bull", False),
                    "w_trend": w.get("trend"), "high_vol": w.get("high_vol"), "vol24_usd": vol,
                    "rs_rel": float(rel.at[ts, sym]) if ts in rel.index and rel.at[ts, sym] == rel.at[ts, sym] else None,
                    "rs_rank": float(rank.at[ts, sym]) if ts in rank.index and rank.at[ts, sym] == rank.at[ts, sym] else None}
        cfg = copy.deepcopy(base)
        for mid, m in mgrs.items():
            trades += bt2.run_manager(m, F, sym, inst, 24.0, warm, extra, cfg, first_ts=ages.get(sym))
        print(f"  {sym}: {len(trades)} işlem (toplam)")
    lv = live_trades(ledger_path)
    trades += lv
    feats_list = list(team.FEAT_KEYS)
    groups = {}
    allfit = fit(trades, feats_list)
    if allfit:
        groups["_hepsi"] = allfit
    for mid in MGRS:
        trs = [t for t in trades if t["mgr"] == mid]
        if len(trs) >= MIN_GROUP:
            g = fit(trs, feats_list)
            if g:
                groups[mid] = g
    # Gözcü'nün göreli güç kuralı kanıtla: trend tarzında girişteki göreli güç sırasına (yön ayarlı) göre ort. R
    tr_style = [t for t in trades if t["mgr"] in ("kemal", "kaan", "selin", "murat")
                and (t.get("feat") or {}).get("rs_rank") is not None]
    gz = {"n": len(tr_style), "mode": "nötr"}
    if len(tr_style) >= 300:
        rr = np.array([t["feat"]["rs_rank"] for t in tr_style])
        R = np.clip(np.array([float(t["r"]) for t in tr_style]), -1.5, 3.0)
        top, bot, mid = R[rr >= 0.8], R[rr < 0.4], R[(rr >= 0.4) & (rr < 0.8)]
        gz.update({"top_r": round(float(top.mean()), 3) if len(top) else None, "n_top": int(len(top)),
                   "mid_r": round(float(mid.mean()), 3) if len(mid) else None,
                   "bot_r": round(float(bot.mean()), 3) if len(bot) else None, "n_bot": int(len(bot))})
        if len(top) >= 60 and len(bot) >= 60:
            d = float(top.mean() - bot.mean())
            gz["mode"] = "momentum" if d >= 0.05 else "ters" if d <= -0.05 else "nötr"
            gz["diff_r"] = round(d, 3)
    res = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "features": feats_list, "gozcu": gz,
           "groups": groups, "n_backtest": len(trades) - len(lv), "n_live": len(lv), "years": years,
           "n_coins": len(daily), "duration_s": round(time.time() - t0, 1)}
    p = out or (ROOT / "data" / "hakem.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# Analist-Hakem modeli — {res['generated']}", "",
          f"{res['n_backtest']} test işlemi + {res['n_live']} canlı/gölge işlem · {res['n_coins']} coin · ridge regresyon (hedef R), "
          "zaman sıralı %70/%30", "", "| Grup | İşlem | Doğrulama ilişkisi | Boyutla kazanç (R/işlem) | Söz hakkı | En etkili özellikler |",
          "|---|---|---|---|---|---|"]
    for k, g in groups.items():
        md.append(f"| {k} | {g['n']} | {g['val_corr']} | {g['val_gain_r']:+.3f} | {'evet' if g['validated'] else 'hayır'} | "
                  + ", ".join(f"{f} {c:+.3f}" for f, c in g["top"][:4]) + " |")
    md += ["", "Söz hakkı: doğrulamada ilişki ≥ 0,05 ve önerilen boyutla işlem başına ≥ +0,02R. Yoksa model yalnızca tahminini yazar.",
           "", f"**Gözcü kontrolü (göreli güç, trend tarzı, {gz['n']} işlem):** en güçlü %20 → {gz.get('top_r')}R ({gz.get('n_top')}), "
           f"orta → {gz.get('mid_r')}R, en zayıf %40 → {gz.get('bot_r')}R ({gz.get('n_bot')}). Kural: **{gz['mode']}** "
           "(momentum = güçlüde büyüt, ters = güçlüde küçült, nötr = göreli güç boyuta dokunmaz)."]
    (p.parent / "HAKEM.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return res


def load(path: Path | None = None) -> dict:
    p = path or (ROOT / "data" / "hakem.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
