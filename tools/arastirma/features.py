"""Özellikler ve ileri getiriler (geniş tablolar: gün × coin). Bilgi yalnızca t günü kapanışına kadar.

İleri getiri F_h(t): t+1 açılışında gir, t+h kapanışında çık (log). Coin bu sürede listeden çıkarsa son işlem gününün
kapanışı kullanılır (çıkış fiyatı).
Evren U_t: t'de işlem görüyor, en az 60 aktif gün geçmişi var, son 30 günün ort. kote hacmine göre ilk N.
"""
import pickle
import sys

import numpy as np
import pandas as pd

TRAIN_START = pd.Timestamp("2020-10-28", tz="UTC")
SPLIT = pd.Timestamp("2025-01-07", tz="UTC")       # Binance yarışıyla aynı ayrım: sonrası SAKLI TEST


def load(path):
    return pickle.load(open(path, "rb"))


def build(P, top_n=100, horizons=(1, 7, 28)):
    o, h, l, c, qv = P["open"], P["high"], P["low"], P["close"], P["qv"]
    vol, taker, fund = P["vol"], P["taker"], P["fund_d"]
    lc = np.log(c)
    alive = c.notna()
    age = alive.cumsum().where(alive)                      # aktif gün sayısı
    tr30 = qv.rolling(30, min_periods=10).mean().where(alive)
    elig_base = alive & (age >= 60)
    rank = tr30.where(elig_base).rank(axis=1, ascending=False, method="first")
    U = (rank <= top_n) & elig_base

    F = {}
    for k in (1, 3, 7, 14, 28, 56, 112):
        F[f"mom_{k}"] = lc - lc.shift(k)
    # NaN-güvenli kayan ortalamalar (yalnız aktif günler)
    for n in (20, 50, 100, 200):
        ma = c.rolling(n, min_periods=int(n * 0.8)).mean()
        F[f"dma_{n}"] = np.log(c / ma)
        if n == 50:
            F["ma50_slope"] = np.log(ma / ma.shift(10))
    pc = c.shift(1)
    trr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()]).groupby(level=0).max() if False else None
    tr_ = np.maximum(h - l, np.maximum((h - pc).abs(), (l - pc).abs()))
    atr = tr_.rolling(14, min_periods=10).mean()
    F["atr_pct"] = atr / c
    r1 = lc.diff()
    F["rvol_30"] = r1.rolling(30, min_periods=20).std()
    hh20 = h.shift(1).rolling(20, min_periods=15).max()
    ll20 = l.shift(1).rolling(20, min_periods=15).min()
    F["brk_20"] = np.log(c / hh20)                         # >0 → 20 günlük zirve kırıldı
    F["brk_dn_20"] = np.log(c / ll20)
    F["range_pos_20"] = (c - ll20) / (hh20 - ll20)
    F["vol_ratio"] = qv / qv.shift(1).rolling(20, min_periods=15).mean()
    F["log_liq"] = np.log(tr30)
    F["taker_ratio_7"] = (taker.rolling(7, min_periods=5).sum() / vol.rolling(7, min_periods=5).sum()) - 0.5
    F["funding_7"] = fund.rolling(7, min_periods=5).sum() * (365 / 7)   # yıllık oran
    F["log_age"] = np.log(age)
    btc = lc["BTCUSDT"]
    for k in (7, 28, 56):
        F[f"rs_{k}"] = (lc - lc.shift(k)).sub(btc - btc.shift(k), axis=0)
    F["dd_90"] = lc - lc.rolling(90, min_periods=30).max()
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    F["rsi_14"] = 100 - 100 / (1 + up / dn)
    # BTC rejimi (zaman serisi; bütün coinlere aynı)
    bma50 = c["BTCUSDT"].rolling(50).mean()
    regime = pd.Series(np.where((c["BTCUSDT"] > bma50) & (bma50 > bma50.shift(10)), 1,
                                np.where((c["BTCUSDT"] < bma50) & (bma50 < bma50.shift(10)), -1, 0)), index=c.index)

    # İleri getiriler: t+1 açılış → t+h kapanış (çıkış olursa son kapanış)
    entry = o.shift(-1)
    FR = {}
    for hh in horizons:
        cf = c.ffill(limit=hh)                              # çıkış sonrası en fazla h gün son fiyatı taşı
        exitp = cf.shift(-hh)
        FR[hh] = np.log(exitp / entry).where(U)
    return {"F": F, "FR": FR, "U": U, "regime": regime, "atr": atr, "tr30": tr30}


def daily_ic(x, y, U):
    """Gün içi kesitsel Spearman: x, y geniş tablolar; yalnız U ve ikisi de dolu hücreler."""
    m = U & x.notna() & y.notna()
    xr = x.where(m).rank(axis=1)
    yr = y.where(m).rank(axis=1)
    xd = xr.sub(xr.mean(axis=1), axis=0)
    yd = yr.sub(yr.mean(axis=1), axis=0)
    num = (xd * yd).sum(axis=1)
    den = np.sqrt((xd ** 2).sum(axis=1) * (yd ** 2).sum(axis=1))
    n = m.sum(axis=1)
    ic = (num / den).where(n >= 20)
    return ic


if __name__ == "__main__":
    P = load(sys.argv[1])
    out = build(P)
    pickle.dump(out, open(sys.argv[2], "wb"))
    print("ok", {k: v.shape for k, v in list(out["F"].items())[:3]})
