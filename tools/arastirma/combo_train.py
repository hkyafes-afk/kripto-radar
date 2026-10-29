"""YALNIZ EĞİTİM: (a) coinin kendi trendi mutlak getiriyi açıklıyor mu (BTC rejimine göre)?
(b) Önceden sabitlenmiş eşit ağırlıklı bileşik puan: düşük oynaklık + yaş + düşük funding + 20g zirveye yakınlık.
    Ağırlıklar optimize EDİLMEDİ (eşit), yalnız eğitim IC'lerinin işaretleri kullanıldı.
"""
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, sys.argv[1])
from features import SPLIT, TRAIN_START, daily_ic  # noqa: E402

D = pickle.load(open(sys.argv[2], "rb"))
F, FR, U, reg = D["F"], D["FR"], D["U"], D["regime"]
idx = U.index
tm = pd.Series((idx >= TRAIN_START) & (idx < SPLIT), index=idx)

# (a) zaman serisi: coin MA50 üstünde mi? (Kemal'in temel varsayımı)
above = F["dma_50"] > 0
print("(a) Ort. 28 günlük getiri %, coin MA50 üstü/altı × BTC rejimi (eğitim, evren ilk 100):")
for r in (1, 0, -1):
    dm = tm & (reg == r)
    for a, lab in ((True, "üstü"), (False, "altı")):
        m = U & (above == a)
        v = FR[28].where(m)[dm].stack()
        print(f"  rejim {r:+d} · MA50 {lab}: ort %{v.mean()*100:6.2f} · medyan %{v.median()*100:6.2f} · n={len(v)}")


def zrank(x, Um):
    r = x.where(Um).rank(axis=1, pct=True)
    return r - 0.5


score = (zrank(-F["atr_pct"], U) + zrank(F["log_age"], U) + zrank(-F["funding_7"], U) + zrank(F["brk_20"], U)) / 4
ic = daily_ic(score, FR[28], U)[tm].dropna()
print(f"\n(b) Bileşik puan IC28 = {ic.mean():.3f} (t≈{ic.mean()/ic.std()*np.sqrt(len(ic)/28):.1f}), yıllara göre:",
      ic.groupby(ic.index.year).mean().round(3).to_dict())
pr = score.where(U).rank(axis=1, pct=True)
print("\nBileşik puan dilimlerine göre MUTLAK ort. 28 günlük getiri % (BTC rejimine göre):")
for r in (1, 0, -1):
    dm = tm & (reg == r)
    out = []
    for lo, hi in ((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)):
        v = FR[28].where(U & (pr > lo) & (pr <= hi))[dm].stack()
        out.append(f"{v.mean()*100:6.2f}")
    print(f"  rejim {r:+d}: Q1..Q5 = {' | '.join(out)}")

# funding maliyeti/geliri (28 gün) — long öder, short alır
fsum = D_f = None
