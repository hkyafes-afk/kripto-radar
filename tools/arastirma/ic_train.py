"""YALNIZ EĞİTİM dönemi (2020-10-28 → 2025-01-06): her etkenin sonraki getiriyle ilişkisi.

Ölçüler (h = 7 ve 28 gün):
  IC      gün içi kesitsel Spearman ortalaması (hangi coin diğerlerinden iyi gider?)
  t       IC / std * sqrt(gün / h)  (örtüşen pencereler için kaba düzeltme)
  yıl+    IC'nin pozitif olduğu yıl sayısı / yıl sayısı (2021–2024; tutarlılık)
  Q5-ev   en üst beşte birlik dilimin ortalama getirisi − evren ortalaması (%, h gün)
  Q1-ev   en alt dilim − evren
"""
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, sys.argv[1])
from features import SPLIT, TRAIN_START, daily_ic  # noqa: E402

D = pickle.load(open(sys.argv[2], "rb"))
F, FR, U = D["F"], D["FR"], D["U"]
idx = U.index
tmask = (idx >= TRAIN_START) & (idx < SPLIT)


def quint(x, y, Um):
    m = Um & x.notna() & y.notna()
    xr = x.where(m).rank(axis=1, pct=True)
    yv = y.where(m)
    ev = yv.mean(axis=1)
    q5 = yv.where(xr > 0.8).mean(axis=1)
    q1 = yv.where(xr <= 0.2).mean(axis=1)
    return (q5 - ev), (q1 - ev), q5, q1, ev


rows = []
for name, x in F.items():
    rec = {"etken": name}
    for h in (7, 28):
        y = FR[h]
        ic = daily_ic(x, y, U)[tmask]
        ic = ic.dropna()
        rec[f"IC{h}"] = ic.mean()
        rec[f"t{h}"] = ic.mean() / ic.std() * np.sqrt(len(ic) / h)
        yrs = ic.groupby(ic.index.year).mean()
        rec[f"yil+{h}"] = f"{(yrs > 0).sum()}/{len(yrs)}"
        q5e, q1e, q5, q1, ev = quint(x, y, U)
        rec[f"Q5-ev{h}"] = q5e[tmask].mean() * 100
        rec[f"Q1-ev{h}"] = q1e[tmask].mean() * 100
    rows.append(rec)

T = pd.DataFrame(rows).set_index("etken")
T = T.reindex(T["IC28"].abs().sort_values(ascending=False).index)
pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 20)
print(T.round(3).to_string())

# Evrenin ortalama getirisi BTC rejimine göre (zaman serisi)
reg = D["regime"]
for h in (7, 28):
    ev = FR[h].where(U).mean(axis=1)[tmask]
    g = ev.groupby(reg[tmask]).agg(["mean", "count"])
    g["mean"] *= 100
    print(f"\nEvren ort. {h} günlük getiri (%), BTC rejimine göre (1 boğa, 0 yatay, -1 ayı):\n", g.round(2).to_string())
