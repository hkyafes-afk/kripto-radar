"""Göstergeler (kitaptaki bütün aileler + txt): her mum kapanışında son 300 mum üzerinden hesaplanır.

Trend:      SMA/EMA ve eğimleri, ADX, +DI/−DI, DI spread, Parabolic SAR, SuperTrend, TRIX, MACD (+histogram)
Momentum:   RSI, Stokastik, CCI, Williams %R, Momentum/ROC, Ultimate Oscillator
Hacim:      OBV (+ kendi direnç/destek kırılımı), MFI, PVI, Demand Index (basitleştirilmiş), hacim oranı
Oynaklık:   ATR, Bollinger, Keltner, sıkışma (Bollinger Keltner'in içinde) ve sıkışmada yön tayini
Uyuşmazlık: fiyat ile RSI / MACD histogramı / OBV / MFI / CCI arasında normal (dönüş) uyuşmazlığı
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

try:
    from scipy.signal import lfilter as _lfilter
except ImportError:  # pragma: no cover
    _lfilter = None

K_PIV = 3


def _nan(n):
    return np.full(n, np.nan)


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = _nan(len(x))
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def _recur(x: np.ndarray, start: int, seed: float, a: float, out: np.ndarray):
    """y[i] = y[i−1] + a·(x[i] − y[i−1]) — C hızında (scipy lfilter), yoksa düz döngü."""
    seg = x[start:]
    if not len(seg):
        return out
    if _lfilter is not None:
        y, _ = _lfilter([a], [1.0, -(1.0 - a)], seg, zi=[(1.0 - a) * seed])
        out[start:] = y
    else:  # pragma: no cover
        v = seed
        for k, xv in enumerate(seg):
            v += a * (xv - v)
            out[start + k] = v
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = _nan(len(x))
    valid = ~np.isnan(x)
    if not valid.any():
        return out
    first = int(np.argmax(valid))
    if len(x) - first < n:
        return out
    v = np.where(valid, x, 0.0)
    s = v[first:first + n].mean()
    out[first + n - 1] = s
    return _recur(v, first + n, s, 2 / (n + 1), out)


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    out = _nan(len(x))
    if len(x) < n:
        return out
    s = np.nanmean(x[:n])
    out[n - 1] = s
    return _recur(np.nan_to_num(np.asarray(x, dtype=float)), n, s, 1.0 / n, out)


def true_range(h, l, c):
    pc = np.roll(c, 1)
    pc[0] = c[0]
    return np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up, dn = wilder(np.maximum(d, 0)[1:], n), wilder(np.maximum(-d, 0)[1:], n)
    r = 100 - 100 / (1 + up / np.where(dn == 0, np.nan, dn))
    r = np.where(dn == 0, 100.0, r)
    return np.insert(r, 0, np.nan)


def macd(c, f=12, s=26, sig=9):
    m = ema(c, f) - ema(c, s)
    sg = ema(m, sig)
    return m, sg, m - sg


def adx(h, l, c, n=14):
    up = np.diff(h, prepend=h[0])
    dn = -np.diff(l, prepend=l[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = wilder(true_range(h, l, c)[1:], n)
    pdi = 100 * wilder(pdm[1:], n) / tr
    mdi = 100 * wilder(mdm[1:], n) / tr
    dx = 100 * abs(pdi - mdi) / np.where(pdi + mdi == 0, np.nan, pdi + mdi)
    a = wilder(np.nan_to_num(dx, nan=0.0)[n - 1:], n)
    adx_ = np.concatenate([_nan(n - 1), a])
    return np.insert(adx_, 0, np.nan), np.insert(pdi, 0, np.nan), np.insert(mdi, 0, np.nan)


def _roll(x, n, fn):
    out = _nan(len(x))
    if len(x) >= n:
        out[n - 1:] = fn(sliding_window_view(x, n), axis=1)
    return out


def _rsum(x, n):
    out = _nan(len(x))
    if len(x) >= n:
        cs = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = cs[n:] - cs[:-n]
    return out


def stoch(h, l, c, n=14, ks=3, ds=3):
    hh, ll = _roll(h, n, np.max), _roll(l, n, np.min)
    k = np.where(hh > ll, 100 * (c - ll) / np.where(hh > ll, hh - ll, 1), 50.0)
    k = sma(np.nan_to_num(k, nan=50.0), ks)
    return k, sma(np.nan_to_num(k, nan=50.0), ds)


def cci(h, l, c, n=20):
    tp = (h + l + c) / 3
    out = _nan(len(c))
    if len(c) >= n:
        w = sliding_window_view(tp, n)
        m = w.mean(axis=1)
        md = np.abs(w - m[:, None]).mean(axis=1)
        out[n - 1:] = np.where(md > 0, (tp[n - 1:] - m) / (0.015 * np.where(md > 0, md, 1)), 0.0)
    return out


def willr(h, l, c, n=14):
    hh, ll = _roll(h, n, np.max), _roll(l, n, np.min)
    return np.where(hh > ll, -100 * (hh - c) / np.where(hh > ll, hh - ll, 1), -50.0)


def mfi(h, l, c, v, n=14):
    tp = (h + l + c) / 3
    rmf = tp * v
    d = np.diff(tp, prepend=tp[0])
    pos, neg = _rsum(np.where(d > 0, rmf, 0.0), n), _rsum(np.where(d < 0, rmf, 0.0), n)
    return np.where(neg > 0, 100 - 100 / (1 + pos / np.where(neg > 0, neg, 1)), 100.0)


def obv(c, v):
    d = np.sign(np.diff(c, prepend=c[0]))
    return np.cumsum(d * v)


def pvi(c, v):
    out = np.zeros(len(c))
    out[0] = 1000.0
    for i in range(1, len(c)):
        out[i] = out[i - 1] * (c[i] / c[i - 1]) if v[i] > v[i - 1] and c[i - 1] else out[i - 1]
    return out


def demand_index(h, l, c, v, n=19):
    """James Sibbet Demand Index — MetaStock dönüşümü (wabbit, MetaStock forumu, 2006):
      wCl = (H+L+2C)/4; AvgTR = SMA(HHV(H,2) − LLV(L,2), n); VolRatio = V / SMA(V, n)
      WtCRatio = (wCl − wCl[−1]) / min(wCl, wCl[−1]); Const = (3·wCl / AvgTR) · |WtCRatio|
      BuyPr = VolRatio (WtCRatio ≥ 0) yoksa VolRatio / e^Const; SellPr = VolRatio (WtCRatio ≤ 0) yoksa VolRatio / e^Const
      BuyPres = EMA(BuyPr, n); SellPres = EMA(SellPr, n); ratio = min/max
      DI = (BuyPres > SellPres ? 2 − ratio : ratio) − 1, ×100  → −100…+100"""
    w = (h + l + 2 * c) / 4
    hh2 = np.maximum(h, np.roll(h, 1))
    ll2 = np.minimum(l, np.roll(l, 1))
    hh2[0], ll2[0] = h[0], l[0]
    avg_tr = sma(hh2 - ll2, n)
    vr = v / sma(v, n)
    pw = np.roll(w, 1)
    pw[0] = w[0]
    wcr = (w - pw) / np.minimum(w, pw)
    const = np.where(avg_tr > 0, (w * 3) / np.where(avg_tr > 0, avg_tr, 1), 0) * np.abs(wcr)
    damp = vr / np.exp(np.minimum(np.nan_to_num(const), 50))
    bp = np.where(wcr >= 0, vr, damp)
    sp = np.where(wcr <= 0, vr, damp)
    bpr, spr = ema(bp, n), ema(sp, n)
    ratio = np.minimum(bpr, spr) / np.maximum(bpr, spr)
    return (np.where(bpr > spr, 2 - ratio, ratio) - 1) * 100


def ultimate(h, l, c, a=7, b=14, d=28):
    pc = np.roll(c, 1)
    pc[0] = c[0]
    bp = c - np.minimum(l, pc)
    tr = np.maximum(h, pc) - np.minimum(l, pc)

    def avg(n):
        t = _rsum(tr, n)
        return np.where(t > 0, _rsum(bp, n) / np.where(t > 0, t, 1), 0.5)
    return 100 * (4 * avg(a) + 2 * avg(b) + avg(d)) / 7


def trix(c, n=15, sig=9):
    e3 = ema(ema(ema(c, n), n), n)
    t = np.full(len(c), np.nan)
    t[1:] = (e3[1:] / e3[:-1] - 1) * 1e4
    return t, ema(t, sig)


def psar(h, l, step=0.02, mx=0.2):
    n = len(h)
    s = np.zeros(n)
    trend = np.ones(n)
    if n < 3:
        return s, trend
    up = h[1] > h[0]
    af, ep = step, (h[1] if up else l[1])
    s[0] = s[1] = l[0] if up else h[0]
    trend[:2] = 1 if up else -1
    for i in range(2, n):
        prev = s[i - 1]
        cur = prev + af * (ep - prev)
        if up:
            cur = min(cur, l[i - 1], l[i - 2])
            if l[i] < cur:
                up, cur, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, mx)
        else:
            cur = max(cur, h[i - 1], h[i - 2])
            if h[i] > cur:
                up, cur, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, mx)
        s[i] = cur
        trend[i] = 1 if up else -1
    return s, trend


def supertrend(h, l, c, n=10, mult=3.0):
    atr = wilder(true_range(h, l, c), n)
    mid = (h + l) / 2
    ub, lb = mid + mult * atr, mid - mult * atr
    fub, flb = ub.copy(), lb.copy()
    st = np.full(len(c), np.nan)
    d = np.ones(len(c))
    for i in range(1, len(c)):
        if np.isnan(atr[i]):
            continue
        if not np.isnan(fub[i - 1]):
            fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
            flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
            d[i] = (-1 if c[i] < flb[i] else 1) if d[i - 1] == 1 else (1 if c[i] > fub[i] else -1)
        st[i] = flb[i] if d[i] > 0 else fub[i]
    return st, d


def pivots(x_hi, x_lo, k=K_PIV):
    """(tepe indeksleri, dip indeksleri), k mum sonra kesinleşen (vektörel)."""
    n = len(x_hi)
    if n < 2 * k + 1:
        return [], []
    wh, wl = sliding_window_view(x_hi, 2 * k + 1), sliding_window_view(x_lo, 2 * k + 1)
    mh, ml = wh[:, k], wl[:, k]
    hi = np.nonzero((mh == wh.max(axis=1)) & (mh > wh[:, :k].max(axis=1)))[0] + k
    lo = np.nonzero((ml == wl.min(axis=1)) & (ml < wl[:, :k].min(axis=1)))[0] + k
    return hi.tolist(), lo.tolist()


def divergence(c, h, l, ind, hi, lo, lookback=60):
    """Normal uyuşmazlık: fiyat daha düşük dip / gösterge daha yüksek dip → +1 (boğa);
    fiyat daha yüksek tepe / gösterge daha düşük tepe → −1 (ayı). Son iki salınım, son 'lookback' mum içinde."""
    n = len(c)
    lo = [i for i in lo if i >= n - lookback and not np.isnan(ind[i])]
    hi = [i for i in hi if i >= n - lookback and not np.isnan(ind[i])]
    out = 0
    if len(lo) >= 2 and l[lo[-1]] < l[lo[-2]] and ind[lo[-1]] > ind[lo[-2]]:
        out = 1
    if len(hi) >= 2 and h[hi[-1]] > h[hi[-2]] and ind[hi[-1]] < ind[hi[-2]]:
        if out == 0 or hi[-1] > lo[-1]:
            out = -1
    return out


def own_break(ind: np.ndarray, k=K_PIV) -> int:
    """Göstergenin kendi direncini/desteğini kırması (kitap: OBV'nin önceki zirvesini geçmesi)."""
    v = ind[~np.isnan(ind)]
    if len(v) < 2 * k + 5:
        return 0
    hi, lo = pivots(v, v, k)
    cur, prev = v[-1], v[-2]
    if hi and cur > v[hi[-1]] >= prev:
        return 1
    if lo and cur < v[lo[-1]] <= prev:
        return -1
    return 0


def _f(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


def compute(bars: list) -> dict:
    """Son kapanmış mumlara göre bütün göstergeler + türetilmiş sinyaller."""
    if len(bars) < 60:
        return {}
    a = np.array([b[:7] for b in bars[-300:]], dtype=float)
    o, h, l, c, v = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]
    vq = a[:, 6]
    out: dict = {}
    for n in (20, 50, 200):
        e = ema(c, n)
        out[f"ema{n}"] = _f(e[-1])
        out[f"ema{n}_egim"] = _f((e[-1] / e[-6] - 1) * 100) if len(e) > 6 and not np.isnan(e[-6]) else None
    s20 = sma(c, 20)
    out["sma20"], out["sma50"] = _f(s20[-1]), _f(sma(c, 50)[-1])
    r = rsi(c)
    out["rsi"] = _f(r[-1])
    m, sg, hist = macd(c)
    out.update(macd=_f(m[-1]), macd_sig=_f(sg[-1]), macd_hist=_f(hist[-1]), macd_hist_onceki=_f(hist[-2]))
    out["macd_kesisim"] = int(np.sign(hist[-1]) != np.sign(hist[-2]) and not np.isnan(hist[-2])) * int(np.sign(hist[-1]))
    ad, pdi, mdi = adx(h, l, c)
    out.update(adx=_f(ad[-1]), pdi=_f(pdi[-1]), mdi=_f(mdi[-1]), di_spread=_f(pdi[-1] - mdi[-1]))
    k, d = stoch(h, l, c)
    out.update(stoch_k=_f(k[-1]), stoch_d=_f(d[-1]), stoch_k_onceki=_f(k[-2]), stoch_d_onceki=_f(d[-2]))
    cc = cci(h, l, c)
    out["cci"] = _f(cc[-1])
    out["willr"] = _f(willr(h, l, c)[-1])
    mf = mfi(h, l, c, v)
    out["mfi"] = _f(mf[-1])
    ob = obv(c, v)
    out["obv_egim"] = _f((ob[-1] - ob[-11]) / (np.mean(abs(np.diff(ob[-60:]))) * 10 + 1e-12))
    out["obv_kirilim"] = own_break(ob)
    out["momentum"] = _f((c[-1] / c[-11] - 1) * 100)
    pv = pvi(c, v)
    pe = ema(pv, 50)
    out["pvi_ustunde"] = int(pv[-1] > pe[-1]) * 2 - 1 if not np.isnan(pe[-1]) else 0
    out["demand"] = _f(demand_index(h, l, c, v)[-1])     # −100…+100
    out["ultimate"] = _f(ultimate(h, l, c)[-1])
    t, ts = trix(c)
    out.update(trix=_f(t[-1]), trix_sig=_f(ts[-1]))
    ps, ptr = psar(h, l)
    out.update(psar=_f(ps[-1]), psar_yon=int(ptr[-1]), psar_donus=int(ptr[-1] != ptr[-2]) * int(ptr[-1]))
    st, sd = supertrend(h, l, c)
    out.update(supertrend=_f(st[-1]), supertrend_yon=int(sd[-1]), supertrend_donus=int(sd[-1] != sd[-2]) * int(sd[-1]))
    atr = wilder(true_range(h, l, c), 14)
    out["atr"] = _f(atr[-1])
    sd20 = _roll(c, 20, np.std)
    bu, bl = s20 + 2 * sd20, s20 - 2 * sd20
    e20 = ema(c, 20)
    ku, kl = e20 + 1.5 * atr, e20 - 1.5 * atr
    out.update(bb_up=_f(bu[-1]), bb_lo=_f(bl[-1]), bb_mid=_f(s20[-1]), kc_up=_f(ku[-1]), kc_lo=_f(kl[-1]))
    sq = (bu < ku) & (bl > kl)
    out["sikisma"] = bool(sq[-1])
    out["sikisma_bitti"] = bool(sq[-2] and not sq[-1])
    # sıkışmada yön tayini (kitap): momentum + kapanışın orta banda göre konumu
    out["sikisma_yon"] = int(np.sign((c[-1] - s20[-1]) + (m[-1] - m[-4] if not np.isnan(m[-4]) else 0)))
    bw = (bu - bl) / s20
    valid = bw[~np.isnan(bw)][-120:]
    out["bb_genislik_yuzdelik"] = _f((valid <= bw[-1]).mean()) if len(valid) > 30 else None
    out["hacim_oran"] = _f(vq[-1] / vq[-21:-1].mean()) if vq[-21:-1].mean() else None
    # Dow: trend yönündeki mumlarda hacim daha mı yüksek
    up, dn = vq[-20:][c[-20:] > o[-20:]], vq[-20:][c[-20:] < o[-20:]]
    out["dow_hacim"] = _f((up.mean() if len(up) else 0) / (dn.mean() if len(dn) and dn.mean() else 1))
    hi, lo = pivots(h, l)
    out["uyusmazlik"] = {"rsi": divergence(c, h, l, r, hi, lo), "macd": divergence(c, h, l, hist, hi, lo),
                         "obv": divergence(c, h, l, ob, hi, lo), "mfi": divergence(c, h, l, mf, hi, lo),
                         "cci": divergence(c, h, l, cc, hi, lo)}
    return out
