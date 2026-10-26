"""Funding harvest (delta-nötr carry) aday listesi.

Mantık: spot'tan al + perp'te aynı miktarda short aç → fiyat riskin ~0, pozitif
funding'i toplarsın. Ama araştırma bu getirinin 2024'ten beri ciddi daraldığını
gösteriyor; o yüzden sadece STABİL ve maliyeti hızlı çıkaran adaylar listelenir.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def funding_stats(hist: pd.DataFrame, interval_h: float) -> dict | None:
    if hist is None or hist.empty or len(hist) < 6:
        return None
    r = hist["rate"].dropna()
    if r.empty:
        return None
    per_year = 8760.0 / interval_h
    mean, std = float(r.mean()), float(r.std(ddof=0))
    last7 = r[r.index >= r.index.max() - pd.Timedelta(days=7)]
    pos = float((r > 0).mean())
    cv = std / abs(mean) if mean != 0 else np.inf
    return {
        "ann_30d": mean * per_year * 100,
        "ann_7d": float(last7.mean()) * per_year * 100 if len(last7) else np.nan,
        "pos_pct": pos,
        "std_ann": std * per_year * 100,
        "stability": pos / (1.0 + min(cv, 10.0)),     # 0..1, yüksek = istikrarlı
        "n": int(len(r)),
    }


def rank_candidates(rows: list[dict], cfg: dict) -> list[dict]:
    """rows: her coin için {sym, inst, vol24_usd, spot_vol_usd, funding_ann, interval_h, stats, other}."""
    h = cfg["harvest"]
    out = []
    for x in rows:
        st = x.get("stats")
        if not st:
            continue
        if x.get("spot_vol_usd", 0) < h["min_spot_hacim_usd"]:
            continue
        if x.get("vol24_usd", 0) < h["min_perp_hacim_usd"]:
            continue
        if st["ann_30d"] < h["min_yillik"] or st["pos_pct"] < h["min_pozitif_oran"]:
            continue
        if x.get("funding_ann", 0) <= 0:   # şu an negatifse harvest değil
            continue
        daily = st["ann_30d"] / 365.0
        breakeven = h["maliyet_yuzde"] / daily if daily > 0 else np.inf
        score = st["ann_30d"] * st["stability"]
        # Trend: son 7g ortalaması 30g'nin yarısının altına düştüyse soğuyor demektir
        cooling = (st["ann_7d"] == st["ann_7d"]) and st["ann_7d"] < 0.5 * st["ann_30d"]
        out.append({
            "sym": x["sym"], "inst": x["inst"],
            "ann_now": round(x["funding_ann"], 1),
            "ann_7d": round(st["ann_7d"], 1) if st["ann_7d"] == st["ann_7d"] else None,
            "ann_30d": round(st["ann_30d"], 1),
            "pos_pct": round(st["pos_pct"] * 100),
            "stability": round(st["stability"] * 100),
            "breakeven_days": round(breakeven, 1) if np.isfinite(breakeven) else None,
            "interval_h": x["interval_h"],
            "vol24_usd": x["vol24_usd"], "spot_vol_usd": x["spot_vol_usd"],
            "score": round(score, 1), "cooling": bool(cooling),
            "other": x.get("other") or {},
        })
    out.sort(key=lambda r: r["score"], reverse=True)
    return out[: h["liste"]]
