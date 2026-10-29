"""Gün içi (saatlik) işlem araştırması — Binance Vision USDT-M 1 saatlik mumlar, batanlar dahil.

Evren: her gün, ÖNCEKİ günün bilgisiyle son 30 günlük hacme göre ilk 30 coin (gün gün; bugünün listesi değil).
Eğitim 2020-10-28 → 2025-01-06, saklı test 2025-01-07 → son. Bütün kurallar ve eşikler bu dosyada, veriye bakmadan
sabitlendi (9 kural). Seçim ölçütü yalnız eğitimle: işlem başına net ort. > 0, gün bazlı t > 2 ve 2021–2024'ün en az
3 yılında net ort. > 0. Saklı test sonuçları hepsi için yazılır ama seçim önceden yapılmıştır (raporda işaretli).

Saatler UTC. Saatlik mum t: [t, t+1s). Gün d'nin saatleri 0..23.
Kurallar:
  S0   her gün 20:00'de evrenin tamamı long, 24:00'te kapat (kıyas: saat etkisi)
  S1   gün içi momentum: 00→20 getirisinin yönünde 20→24 (long ya da short)
  S1L  yalnız long tarafı (00→20 artıdaysa)
  S2D  saatlik dip: son 1 saatte ≤ −%3 ve hacim son 24 saatin ort. ≥ 2 katı → long, 4 saat tut
  S2P  saatlik pompa: ≥ +%3 ve hacim ≥ 2 kat → short, 4 saat tut
  S3L  önceki gün zirvesi kırılımı: gün içinde (saat ≤ 20) kapanış önceki gün zirvesini ilk geçtiğinde long, gün sonu kapat
  S3S  önceki gün dibi kırılımı: short, gün sonu kapat
  S5   S3L + günlük araştırmanın filtreleri (önceki gün): BTC boğa, ATR% ≤ 5, yaş ≥ 365 gün, funding kalabalık değil
  S6   S1L + aynı günlük filtreler
Maliyet (gidiş-dönüş): 2 × (ücret 5 bp + kayma: likidite sırası ≤ 10 → 2 bp, diğer 5 bp); --stres ile ×2.
Günlük portföy: o gün açılan işlemlerin net getirilerinin eşit ağırlıklı ortalaması (kaldıraçsız), bileşik.
"""
import gzip
import json
import os
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from features import SPLIT, TRAIN_START, build  # noqa: E402

H1_DIR = Path(sys.argv[1])          # um_1h klasörü
PANEL = sys.argv[2]                 # panel.py çıktısı (günlük)
OUT = Path(sys.argv[3])             # sonuç klasörü
STRESS = 2.0 if "--stres" in sys.argv else 1.0
TOP = 30
FEE = 0.0005

P = pickle.load(open(PANEL, "rb"))
D = build(P, top_n=TOP, horizons=(1,))
days = P["close"].index                                  # günlük UTC
cols = [c for c in P["close"].columns if (H1_DIR / f"{c}.csv.gz").exists()]
nd, nh, nc = len(days), 24, len(cols)
t0 = days[0]

# saatlik diziler: gün × saat × coin
O = np.full((nd, nh, nc), np.nan, np.float32)
Hh, L, C, Q = (np.full_like(O, np.nan) for _ in range(4))
for j, s in enumerate(cols):
    with gzip.open(H1_DIR / f"{s}.csv.gz", "rt") as fh:
        df = pd.read_csv(fh)
    df = df[(df["quote_volume"] > 0) & (df["trades"] > 0)]          # zombi saatler
    k = ((df["ts_ms"].to_numpy() - int(t0.timestamp() * 1000)) // 3_600_000).astype(np.int64)
    ok = (k >= 0) & (k < nd * nh)
    k = k[ok]
    di, hi = k // nh, k % nh
    for arr, col in ((O, "open"), (Hh, "high"), (L, "low"), (C, "close"), (Q, "quote_volume")):
        arr[di, hi, j] = df[col].to_numpy(np.float32)[ok]
print(f"saatlik: {nc} coin, {nd} gün", flush=True)

# önceki günün bilgisiyle evren ve filtreler (bakış sızıntısı yok)
U = D["U"][cols].shift(1).fillna(False).to_numpy(bool)
liq = D["tr30"][cols].where(D["U"][cols]).rank(axis=1, ascending=False).shift(1).to_numpy()
slip = np.where(liq <= 10, 0.0002, 0.0005)
cost_rt = 2 * (FEE + slip) * STRESS                                  # gün × coin
F = D["F"]
reg_prev = D["regime"].shift(1).to_numpy()
atr_prev = F["atr_pct"][cols].shift(1).to_numpy()
age_prev = F["log_age"][cols].shift(1).to_numpy()
fund_pct = F["funding_7"][cols].where(D["U"][cols]).rank(axis=1, pct=True).shift(1).to_numpy()
FILT = (reg_prev[:, None] == 1) & (atr_prev <= 0.05) & (age_prev >= np.log(365)) & (fund_pct <= 0.8)

trades = {k: [] for k in ("S0", "S1", "S1L", "S2D", "S2P", "S3L", "S3S", "S5", "S6")}


def add(name, d, j, direction, entry, exitp):
    if not (np.isfinite(entry) and np.isfinite(exitp) and entry > 0 and exitp > 0):
        return
    g = direction * np.log(exitp / entry)
    trades[name].append((d, g, g - cost_rt[d, j]))


with np.errstate(invalid="ignore", divide="ignore"):
    for d in range(1, nd):
        js = np.where(U[d])[0]
        if len(js) == 0:
            continue
        # S0, S1, S1L, S6
        rf = np.log(C[d, 19] / O[d, 0])
        for j in js:
            e, x = O[d, 20, j], C[d, 23, j]
            add("S0", d, j, 1, e, x)
            if np.isfinite(rf[j]) and rf[j] != 0:
                add("S1", d, j, np.sign(rf[j]), e, x)
                if rf[j] > 0:
                    add("S1L", d, j, 1, e, x)
                    if FILT[d, j]:
                        add("S6", d, j, 1, e, x)
        # S3L / S3S / S5
        pdh, pdl = np.nanmax(Hh[d - 1], axis=0), np.nanmin(L[d - 1], axis=0)
        for j in js:
            cl = C[d, :21, j]
            up = np.where(cl > pdh[j])[0]
            if len(up):
                h0 = up[0]
                add("S3L", d, j, 1, O[d, h0 + 1, j], C[d, 23, j])
                if FILT[d, j]:
                    add("S5", d, j, 1, O[d, h0 + 1, j], C[d, 23, j])
            dn = np.where(cl < pdl[j])[0]
            if len(dn):
                h0 = dn[0]
                add("S3S", d, j, -1, O[d, h0 + 1, j], C[d, 23, j])
    # S2: saatlik dip / pompa (düz saat ekseni)
    Cf, Of, Qf = C.reshape(nd * nh, nc), O.reshape(nd * nh, nc), Q.reshape(nd * nh, nc)
    r1 = np.log(Cf[1:] / Cf[:-1])
    qm = pd.DataFrame(Qf).shift(1).rolling(24, min_periods=18).mean().to_numpy()
    vr = Qf / qm
    for t in range(25, nd * nh - 5):
        d = t // nh
        if d < 1:
            continue
        js = np.where(U[d])[0]
        rr, vv = r1[t - 1, js], vr[t, js]          # r1[t-1] = log(C[t]/C[t-1])
        for jj in np.where((rr <= -0.03) & (vv >= 2))[0]:
            j = js[jj]
            add("S2D", d, j, 1, Of[t + 1, j], Cf[t + 4, j])
        for jj in np.where((rr >= 0.03) & (vv >= 2))[0]:
            j = js[jj]
            add("S2P", d, j, -1, Of[t + 1, j], Cf[t + 4, j])

split_d = int(np.searchsorted(days, SPLIT))
train_d0 = int(np.searchsorted(days, TRAIN_START))


def summarize(name, lo, hi):
    T = [(d, g, n) for d, g, n in trades[name] if lo <= d < hi]
    if not T:
        return {"işlem": 0}
    arr = np.array(T)
    dd = pd.DataFrame(arr, columns=["d", "g", "n"])
    daily = dd.groupby("d")["n"].mean()
    tstat = daily.mean() / daily.std() * np.sqrt(len(daily)) if len(daily) > 2 and daily.std() > 0 else np.nan
    yrs = pd.Series(daily.values, index=days[daily.index.astype(int)]).groupby(lambda x: x.year).mean()
    eq = (1 + pd.Series(daily.values, index=days[daily.index.astype(int)])).cumprod()
    full = eq.reindex(days[lo:hi]).ffill().fillna(1.0)
    yrs_n = (days[hi - 1] - days[lo]).days / 365.25
    cagr = full.iloc[-1] ** (1 / yrs_n) - 1
    mdd = (full / full.cummax() - 1).min()
    dret = full.pct_change().fillna(0)
    sh = dret.mean() / dret.std() * np.sqrt(365) if dret.std() > 0 else np.nan
    return {"işlem": int(len(arr)), "brüt_bp": round(float(dd["g"].mean() * 1e4), 1),
            "net_bp": round(float(dd["n"].mean() * 1e4), 1), "kazanma%": round(float((dd["n"] > 0).mean() * 100), 1),
            "t_gün": round(float(tstat), 2), "yıllar_net_bp": {int(k): round(float(v * 1e4), 1) for k, v in yrs.items()},
            "yıllık%": round(float(cagr * 100), 1), "maksdüşüş%": round(float(mdd * 100), 1),
            "Sharpe": round(float(sh), 2), "işlem_günü%": round(100 * len(daily) / (hi - lo), 1)}


res = {}
for name in trades:
    tr = summarize(name, train_d0, split_d)
    ys = tr.get("yıllar_net_bp", {})
    passed = bool(tr.get("işlem", 0) and tr["net_bp"] > 0 and (tr["t_gün"] or 0) > 2
                  and sum(1 for y in (2021, 2022, 2023, 2024) if ys.get(y, -1) > 0) >= 3)
    te = summarize(name, split_d, nd)
    res[name] = {"eğitim": tr, "eğitimde_seçildi": passed, "saklı_test": te}
    print(name, "EĞİTİM", tr, "| seçildi:", passed, flush=True)
for name in trades:
    print(name, "TEST", res[name]["saklı_test"], flush=True)

OUT.mkdir(parents=True, exist_ok=True)
tag = "_stres" if STRESS > 1 else ""
(OUT / f"gun_ici{tag}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
L_ = [f"# Gün içi araştırma{' (2× maliyet)' if tag else ''}", "",
      f"Evren: her gün önceki günün bilgisiyle hacme göre ilk {TOP} coin (batanlar dahil). Eğitim "
      f"{TRAIN_START.date()} → {days[split_d - 1].date()}, saklı test {SPLIT.date()} → {days[-1].date()}. "
      "Seçim yalnız eğitimle (net > 0, t > 2, 2021–24'ün ≥3 yılında artı).", "",
      "| Kural | Eğitim işlem | Eğitim net bp/işlem | t | Eğitim yıllık % / düşüş % | Seçildi | Test işlem | Test net bp | Test yıllık % / düşüş % |",
      "|---|---|---|---|---|---|---|---|---|"]
for name, r in res.items():
    a, b = r["eğitim"], r["saklı_test"]
    L_.append(f"| {name} | {a.get('işlem')} | {a.get('net_bp')} | {a.get('t_gün')} | {a.get('yıllık%')} / {a.get('maksdüşüş%')} | "
              f"{'✅' if r['eğitimde_seçildi'] else '—'} | {b.get('işlem')} | {b.get('net_bp')} | {b.get('yıllık%')} / {b.get('maksdüşüş%')} |")
(OUT / f"GUN_ICI{tag.upper()}.md").write_text("\n".join(L_) + "\n", encoding="utf-8")
print("\n".join(L_))
