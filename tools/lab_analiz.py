"""Laboratuvar işlem dökümünden kırılımlar: hangi koşulda hangi yönetici kazanıyor (eğitim ve doğrulama ayrı).

python tools/lab_analiz.py data/LAB_tekli.trades.json.gz --variant temel
"""
from __future__ import annotations

import argparse
import gzip
import json

import numpy as np
import pandas as pd

SPLIT = "2025-01-01"


def load(p, variant):
    with gzip.open(p, "rt") as f:
        T = pd.DataFrame(json.load(f))
    T = T[T["variant"] == variant].copy()
    T["is"] = T["entry_ts"] < SPLIT
    return T


def agg(g):
    r = g["r"].to_numpy(float)
    gl = -r[r <= 0].sum()
    return pd.Series({"n": len(r), "win": (r > 0).mean() * 100 if len(r) else np.nan, "avgR": r.mean() if len(r) else np.nan,
                      "pf": r[r > 0].sum() / gl if gl > 0 else np.nan})


def table(T, by, title):
    out = []
    for key, g in T.groupby(by, observed=True):
        a, b = agg(g[g["is"]]), agg(g[~g["is"]])
        out.append([key, int(a["n"]), a["win"], a["avgR"], a["pf"], int(b["n"]), b["win"], b["avgR"], b["pf"]])
    df = pd.DataFrame(out, columns=[str(by), "IS n", "IS win", "IS R", "IS PF", "OOS n", "OOS win", "OOS R", "OOS PF"])
    print(f"\n## {title}\n")
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--variant", default="temel")
    a = ap.parse_args()
    T = load(a.path, a.variant)
    T["yas"] = pd.cut(T["age_d"], [-1, 90, 180, 365, 730, 1095, 99999], labels=["<3ay", "3-6ay", "6-12ay", "1-2y", "2-3y", "3y+"])
    T["stop%"] = pd.cut(T["stop_pct"], [0, 5, 10, 15, 20, 30, 1000], labels=["<5", "5-10", "10-15", "15-20", "20-30", "30+"])
    T["atr%"] = pd.cut(T["atr_pct"], [0, 3, 5, 8, 12, 1000], labels=["<3", "3-5", "5-8", "8-12", "12+"])
    T["funding"] = pd.cut(T["fa"].astype(float), [-1e9, -20, 0, 10, 30, 60, 1e9], labels=["<-20", "-20..0", "0..10", "10..30", "30..60", "60+"])
    for col, title in (("mgr", "Yönetici"), (["mgr", "side"], "Yönetici × yön"), (["mgr", "yas"], "Yönetici × coin yaşı"),
                       (["mgr", "stop%"], "Yönetici × stop mesafesi (%)"), (["mgr", "atr%"], "Yönetici × günlük ATR (%)"),
                       (["side", "funding"], "Yön × funding (yıllık %)"), (["mgr", "w_trend"], "Yönetici × BTC havası"),
                       ("why", "Çıkış nedeni")):
        table(T, col, title)
