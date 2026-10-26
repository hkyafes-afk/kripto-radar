"""Teknik göstergeler. RSI/ATR/ADX Wilder yumuşatması ile, pandas ile vektörize.

SuperTrend doğası gereği özyinelemeli olduğu için numpy döngüsüyle hesaplanır
(2000 mumda milisaniyeler sürer).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder yumuşatması (alpha = 1/n)."""
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = rma(d.clip(lower=0), n)
    dn = rma(-d.clip(upper=0), n)
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.where(~((dn == 0) & up.notna()), 100.0)
    return out


def true_range(h: pd.Series, l: pd.Series, c: pd.Series) -> pd.Series:
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def atr(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    return rma(true_range(h, l, c), n)


def bollinger(c: pd.Series, n: int = 20, k: float = 2.0):
    mid = c.rolling(n).mean()
    sd = c.rolling(n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


def adx(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14):
    up = h.diff()
    dn = -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)
    tr = rma(true_range(h, l, c), n)
    pdi = 100 * rma(plus_dm, n) / tr
    mdi = 100 * rma(minus_dm, n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return rma(dx, n), pdi, mdi


def supertrend(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 10, mult: float = 3.0):
    """Döner: (yön Series: +1/-1/0, çizgi Series)."""
    a = atr(h, l, c, n).to_numpy()
    hl2 = ((h + l) / 2).to_numpy()
    cl = c.to_numpy()
    ub = hl2 + mult * a
    lb = hl2 - mult * a
    size = len(cl)
    fub = np.full(size, np.nan)
    flb = np.full(size, np.nan)
    dirn = np.zeros(size, dtype=int)
    for i in range(size):
        if np.isnan(a[i]):
            continue
        if i == 0 or np.isnan(fub[i - 1]):
            fub[i], flb[i] = ub[i], lb[i]
            dirn[i] = 1 if cl[i] >= hl2[i] else -1
            continue
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or cl[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or cl[i - 1] < flb[i - 1]) else flb[i - 1]
        if dirn[i - 1] == 1:
            dirn[i] = -1 if cl[i] < flb[i] else 1
        else:
            dirn[i] = 1 if cl[i] > fub[i] else -1
    line = np.where(dirn == 1, flb, np.where(dirn == -1, fub, np.nan))
    return pd.Series(dirn, index=c.index), pd.Series(line, index=c.index)


def _pivots(arr: np.ndarray, k: int, kind: str) -> np.ndarray:
    """arr[i], [i-k, i+k] penceresinin en düşük/en yüksek değeriyse True."""
    s = pd.Series(arr)
    win = 2 * k + 1
    if kind == "low":
        ext = s.rolling(win, center=True).min()
    else:
        ext = s.rolling(win, center=True).max()
    return (s == ext).to_numpy() & ~np.isnan(arr)


def divergence_series(low: pd.Series, high: pd.Series, rsi_s: pd.Series,
                      k: int = 3, lookback: int = 30, recent: int = 6) -> pd.Series:
    """Fiyat–RSI uyumsuzluğu, bakış hatası (look-ahead) olmadan.

    Bir pivot ancak k mum sonra kesinleşir. t anında yalnızca t-k'ye kadar
    kesinleşmiş pivotlar kullanılır.
      +1: boğa uyumsuzluğu (fiyat daha düşük dip, RSI daha yüksek dip)
      -1: ayı uyumsuzluğu (fiyat daha yüksek tepe, RSI daha düşük tepe)
    """
    lo, hi, r = low.to_numpy(float), high.to_numpy(float), rsi_s.to_numpy(float)
    n = len(lo)
    out = np.zeros(n, dtype=int)
    piv_lo = np.where(_pivots(lo, k, "low"))[0]
    piv_hi = np.where(_pivots(hi, k, "high"))[0]

    def scan(pivots, price, bullish):
        res = np.zeros(n, dtype=int)
        j = 0
        confirmed: list[int] = []
        for t in range(n):
            while j < len(pivots) and pivots[j] + k <= t:
                confirmed.append(pivots[j])
                j += 1
            if len(confirmed) < 2:
                continue
            p2, p1 = confirmed[-1], confirmed[-2]
            if t - p2 > k + recent or p2 - p1 > lookback or p2 - p1 < 4:
                continue
            if np.isnan(r[p1]) or np.isnan(r[p2]):
                continue
            if bullish and price[p2] < price[p1] and r[p2] > r[p1]:
                res[t] = 1
            if not bullish and price[p2] > price[p1] and r[p2] < r[p1]:
                res[t] = -1
        return res

    bull = scan(piv_lo, lo, True)
    bear = scan(piv_hi, hi, False)
    out = np.where(bull == 1, 1, np.where(bear == -1, -1, 0))
    return pd.Series(out, index=low.index)


def clv_delta(o: pd.Series, h: pd.Series, l: pd.Series, c: pd.Series, v: pd.Series) -> pd.Series:
    """Taker verisi yoksa alış/satış baskısı tahmini (Close Location Value x hacim)."""
    rng = (h - l).replace(0, np.nan)
    return (((c - l) - (h - c)) / rng).fillna(0) * v
