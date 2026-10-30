"""Tek ajan adaylarının günlük portföy simülasyonu (Binance günlük veri, batanlar dahil, gün gün evren).

Adaylar (eğitim bulgularından, SAKLI TESTE BAKMADAN tanımlandı; ağırlıklar optimize edilmedi):
  A  Yalnız long, BTC boğa rejiminde: bileşik puanı en yüksek K coin, eşit ağırlık, toplam %100.
  B  Piyasa nötr: en yüksek K long %50, en düşük K short %50, rejimden bağımsız.
  C  Karma: boğada A; boğa değilken en düşük K short (%50).
Bileşik puan = ortalama yüzdelik sıra (düşük ATR%, yaşlı coin, düşük funding, 20 günlük zirveye yakınlık).
Kıyaslar: BTC al-tut; EW (boğada evrenin tamamı eşit ağırlık long); A_rejimsiz (her zaman long).

Mekanik: sinyal t kapanışı, işlem t+1 açılışı. Haftalık yeniden dengeleme + rejim değişince ertesi açılışta.
Maliyet: işlem tutarı × (ücret 5 bp + likidite kayması: sıra ≤20 → 3 bp, ≤50 → 8 bp, diğer 15 bp); --stres maliyeti ×2.
Funding: gerçek günlük funding (long öder, short alır). Coin listeden çıkarsa son kapanıştan kapatılır.
Kaldıraç yok (brüt ≤ %100).
"""
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, sys.argv[1])
from features import SPLIT, TRAIN_START  # noqa: E402

P = pickle.load(open(sys.argv[2], "rb"))
D = pickle.load(open(sys.argv[3], "rb"))
SHOW_TEST = "--test" in sys.argv
STRESS = 2.0 if "--stres" in sys.argv else 1.0
import os
K = int(os.environ.get("K", 10))
REB = int(os.environ.get("REB", 7))
LAG = int(os.environ.get("LAG", 0))
PLACEBO = os.environ.get("PLACEBO") == "1"
KINDS = os.environ.get("KINDS", "BTC,EW,A_rejimsiz,A,B,C").split(",")

o, c = P["open"], P["close"]
fund = P["fund_d"].reindex_like(c).fillna(0.0)
F, U, reg = D["F"], D["U"], D["regime"]
cf = c.ffill()


def zr(x):
    return x.where(U).rank(axis=1, pct=True) - 0.5


score = (zr(-F["atr_pct"]) + zr(F["log_age"]) + zr(-F["funding_7"]) + zr(F["brk_20"])) / 4
if PLACEBO:                                   # plasebo: aynı evrende rastgele puan (sabit tohum)
    _rng = np.random.default_rng(int(os.environ.get("SEED", 12345)))
    score = pd.DataFrame(_rng.random(score.shape), index=score.index, columns=score.columns).where(U)
liq = D["tr30"].where(U).rank(axis=1, ascending=False)
FEE = 0.0005


def slip(rank):
    return np.where(rank <= 20, 0.0003, np.where(rank <= 50, 0.0008, 0.0015))


dates = c.index[c.index >= TRAIN_START]
cols = list(c.columns)
ci = {s: i for i, s in enumerate(cols)}
O, C, CF, FU = o.to_numpy(), c.to_numpy(), cf.to_numpy(), fund.to_numpy()
SC, UU, LQ = score.to_numpy(), U.to_numpy(), liq.to_numpy()
REG = reg.to_numpy()
pos0 = {d: i for i, d in enumerate(c.index)}


def targets(kind, i):
    """t (satır i) kapanışındaki hedef ağırlıklar: {sütun: ağırlık}."""
    s = SC[i]
    ok = UU[i] & ~np.isnan(s)
    js = np.where(ok)[0]
    if len(js) < 2 * K:
        return {}
    order = js[np.argsort(s[js])]
    top, bot = order[-K:], order[:K]
    bull = REG[i] == 1
    if kind == "A":
        return {j: 1.0 / K for j in top} if bull else {}
    if kind == "A_rejimsiz":
        return {j: 1.0 / K for j in top}
    if kind == "EW":
        return {j: 1.0 / len(js) for j in js} if bull else {}
    if kind == "B":
        w = {j: 0.5 / K for j in top}
        w.update({j: -0.5 / K for j in bot})
        return w
    if kind == "C":
        return {j: 1.0 / K for j in top} if bull else {j: -0.5 / K for j in bot}
    if kind == "BTC":
        return {ci["BTCUSDT"]: 1.0}
    raise ValueError(kind)


def run(kind):
    units = {}                      # sütun → adet (short negatif)
    cash = 1.0
    eq_series, turn, cost_tot, fund_tot = [], 0.0, 0.0, 0.0
    last_reb, last_bull = -10 ** 9, None
    global PNL, EXPO
    PNL, EXPO = {}, []
    prevc = {}
    for k in range(len(dates) - 1):
        t, t1 = dates[k], dates[k + 1]
        i, i1 = pos0[t], pos0[t1]
        bull = REG[i] == 1
        need = (k - last_reb >= REB) or (kind in ("A", "C", "EW") and bull != last_bull)
        # açılışta fiyatlar (listeden çıkan: son kapanış)
        po = {j: (O[i1, j] if not np.isnan(O[i1, j]) else CF[i1, j]) for j in units}
        gone = [j for j in units if np.isnan(C[i1, j])]
        eq_open = cash + sum(u * po[j] for j, u in units.items())
        for j, u in units.items():
            if j in prevc:
                PNL[(t1, j)] = PNL.get((t1, j), 0.0) + u * (po[j] - prevc[j])
        trades = {}
        if need:
            tw = targets(kind, max(i - LAG, 0))
            last_reb, last_bull = k, bull
            for j in set(units) | set(tw):
                px = po.get(j, O[i1, j])
                if np.isnan(px):
                    continue
                cur = units.get(j, 0.0) * px
                tgt = tw.get(j, 0.0) * eq_open
                if j in gone:
                    tgt = 0.0
                if abs(tgt - cur) > 1e-12:
                    trades[j] = (tgt - cur, px)
        for j in gone:
            if j not in trades and units.get(j):
                trades[j] = (-units[j] * po[j], po[j])
        for j, (dn, px) in trades.items():
            r = LQ[i, j] if not np.isnan(LQ[i, j]) else 100
            cst = abs(dn) * (FEE + float(slip(r))) * STRESS
            cash -= dn + cst
            PNL[(t1, j)] = PNL.get((t1, j), 0.0) - cst
            units[j] = units.get(j, 0.0) + dn / px
            if abs(units[j] * px) < 1e-12:
                units.pop(j)
            turn += abs(dn)
            cost_tot += cst
        # gün içi funding (long öder)
        for j, u in units.items():
            px = C[i1, j] if not np.isnan(C[i1, j]) else CF[i1, j]
            f = FU[i1, j] * u * px
            cash -= f
            fund_tot += f
            PNL[(t1, j)] = PNL.get((t1, j), 0.0) - f + u * (px - po.get(j, O[i1, j] if not np.isnan(O[i1, j]) else px))
            prevc[j] = px
        for j in [j for j in prevc if j not in units]:
            prevc.pop(j)
        eq = cash + sum(u * (C[i1, j] if not np.isnan(C[i1, j]) else CF[i1, j]) for j, u in units.items())
        eq_series.append((t1, eq))
        EXPO.append((t1, sum(abs(u) * (C[i1, j] if not np.isnan(C[i1, j]) else CF[i1, j]) for j, u in units.items()) / eq if eq else 0))
    s = pd.Series(dict(eq_series))
    return s, turn, cost_tot, fund_tot


def metrics(eq, lo, hi):
    e = eq[(eq.index >= lo) & (eq.index < hi)]
    e = e / e.iloc[0]
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    cagr = e.iloc[-1] ** (1 / yrs) - 1
    dd = (e / e.cummax() - 1).min()
    r = e.pct_change().dropna()
    sh = r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else np.nan
    yr = e.resample("YE").last().pct_change()
    yr.iloc[0] = e.resample("YE").last().iloc[0] - 1
    return {"yıllık%": round(cagr * 100, 1), "maksdüşüş%": round(dd * 100, 1), "MAR": round(cagr / abs(dd), 2) if dd else None,
            "Sharpe": round(sh, 2), "yıllar%": {int(y.year): round(v * 100, 1) for y, v in yr.items()}}


if __name__ == "__main__":
    res = {}
    for kind in KINDS:
        eq, turn, cst, fnd = run(kind)
        res[kind] = eq
        m = metrics(eq, TRAIN_START, SPLIT)
        print(f"[EĞİTİM] {kind:10} {m} · ciro {turn:.0f}x · maliyet %{cst*100:.1f} · funding %{fnd*100:.1f}", flush=True)
        if SHOW_TEST:
            mt = metrics(eq, SPLIT, eq.index[-1] + pd.Timedelta(days=1))
            print(f"[TEST]   {kind:10} {mt}", flush=True)
    pickle.dump(res, open(sys.argv[4], "wb"))
