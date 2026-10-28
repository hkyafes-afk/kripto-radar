"""Formasyonlar (kitap: fiyat formasyonları, Fibonacci/harmonik formasyonlar, trend çizgileri; txt: mum okumaları,
stop ve likidasyon kümeleri). Hepsi kapanmış mumlardan ve k mum sonra kesinleşen salınımlardan hesaplanır.

Her bulgu: {"ad", "yon": +1 boğa / −1 ayı, "durum": "tamam" (kırılım/teyit oldu) | "olusuyor", "seviye", "hedef", "guven"}
"""
from __future__ import annotations

import math

import numpy as np

from .indicators import pivots


def _arr(bars):
    a = np.array([b[:7] for b in bars], dtype=float)
    return a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 6]


def _line(i1, p1, i2, p2, at):
    return p1 + (p2 - p1) * (at - i1) / (i2 - i1) if i2 != i1 else p2


def _fit(idx, px):
    if len(idx) < 2:
        return None, None
    m, b = np.polyfit(np.array(idx, float), np.array(px, float), 1)
    return m, b


def _state(c, level, d, atr, k=3):
    """Kırılım tazeliği: "tamam" yalnızca fiyat seviyeyi son k mum içinde ≥0,25 ATR farkla geçtiyse.
    Daha önce geçildiyse "gecti" (artık oy vermez — eski haber), geçilmediyse "olusuyor"."""
    beyond = (c - level) * d > 0.25 * atr
    if not beyond[-1]:
        return "olusuyor"
    return "tamam" if not beyond[-k - 1:-1].all() else "gecti"


# ------------------------------------------------------------------------------------------------ grafik formasyonları
def chart_patterns(bars: list, atr: float) -> list[dict]:
    if len(bars) < 40 or not atr:
        return []
    o, h, l, c, v = _arr(bars[-150:])
    n = len(c)
    hi, lo = pivots(h, l)
    out = []
    last = n - 1
    tol = max(0.6 * atr, c[-1] * 0.003)

    # Çift tepe / çift dip
    if len(hi) >= 2:
        a, b = hi[-2], hi[-1]
        mids = [i for i in lo if a < i < b]
        if abs(h[a] - h[b]) <= tol and mids and b - a >= 10 and b >= n - 25:
            neck = l[min(mids, key=lambda i: l[i])]
            if h[a] - neck >= 2 * atr:
                stt = _state(c, neck, -1, atr)
                out.append({"ad": "cift_tepe", "yon": -1, "durum": stt, "seviye": neck,
                            "hedef": neck - (h[b] - neck), "guven": 0.7 if stt == "tamam" else 0.3})
    if len(lo) >= 2:
        a, b = lo[-2], lo[-1]
        mids = [i for i in hi if a < i < b]
        if abs(l[a] - l[b]) <= tol and mids and b - a >= 10 and b >= n - 25:
            neck = h[max(mids, key=lambda i: h[i])]
            if neck - l[a] >= 2 * atr:
                stt = _state(c, neck, 1, atr)
                out.append({"ad": "cift_dip", "yon": 1, "durum": stt, "seviye": neck,
                            "hedef": neck + (neck - l[b]), "guven": 0.7 if stt == "tamam" else 0.3})

    # Omuz-baş-omuz (OBO) / ters OBO (TOBO)
    if len(hi) >= 3:
        s1, hd, s2 = hi[-3], hi[-2], hi[-1]
        if h[hd] > h[s1] + atr and h[hd] > h[s2] + atr and abs(h[s1] - h[s2]) <= 1.5 * tol:
            t1 = [i for i in lo if s1 < i < hd]
            t2 = [i for i in lo if hd < i < s2]
            if t1 and t2:
                i1, i2 = min(t1, key=lambda i: l[i]), min(t2, key=lambda i: l[i])
                neck = _line(i1, l[i1], i2, l[i2], last)
                stt = _state(c, neck, -1, atr)
                if s2 >= n - 30:
                    out.append({"ad": "obo", "yon": -1, "durum": stt, "seviye": neck,
                                "hedef": neck - (h[hd] - neck), "guven": 0.75 if stt == "tamam" else 0.3})
    if len(lo) >= 3:
        s1, hd, s2 = lo[-3], lo[-2], lo[-1]
        if l[hd] < l[s1] - atr and l[hd] < l[s2] - atr and abs(l[s1] - l[s2]) <= 1.5 * tol:
            t1 = [i for i in hi if s1 < i < hd]
            t2 = [i for i in hi if hd < i < s2]
            if t1 and t2:
                i1, i2 = max(t1, key=lambda i: h[i]), max(t2, key=lambda i: h[i])
                neck = _line(i1, h[i1], i2, h[i2], last)
                stt = _state(c, neck, 1, atr)
                if s2 >= n - 30:
                    out.append({"ad": "tobo", "yon": 1, "durum": stt, "seviye": neck,
                                "hedef": neck + (neck - l[hd]), "guven": 0.75 if stt == "tamam" else 0.3})

    # Üçgenler, takoz, kanal, elmas: son 3 tepe ve son 3 dibin eğimleri (ATR/mum biriminde)
    H3 = [i for i in hi if i >= n - 80][-3:]
    L3 = [i for i in lo if i >= n - 80][-3:]
    fit_ok = len(H3) == 3 and len(L3) == 3 and max(H3[-1], L3[-1]) >= n - 20
    if fit_ok:
        mh, bh = _fit(H3, [h[i] for i in H3])
        ml, bl = _fit(L3, [l[i] for i in L3])
        res_h = max(abs(h[i] - (mh * i + bh)) for i in H3)
        res_l = max(abs(l[i] - (ml * i + bl)) for i in L3)
        fit_ok = res_h <= 0.5 * atr and res_l <= 0.5 * atr      # üç dokunuş da çizgiye oturmalı
    if fit_ok:
        sh, sl = mh / atr, ml / atr
        up_line, lo_line = mh * last + bh, ml * last + bl
        width_now = up_line - lo_line
        start = min(H3[0], L3[0])
        width_then = (mh * start + bh) - (ml * start + bl)
        flat = 0.03
        brk = 1 if c[-1] > up_line else (-1 if c[-1] < lo_line else 0)
        name, yon = None, 0
        if abs(sh) < flat and sl > flat:
            name, yon = "yukselen_ucgen", 1
        elif abs(sl) < flat and sh < -flat:
            name, yon = "alcalan_ucgen", -1
        elif sh < -flat and sl > flat:
            name, yon = "simetrik_ucgen", brk
        elif sh > flat and sl > flat and sl > sh and width_now < width_then:
            name, yon = "yukselen_takoz", -1
        elif sh < -flat and sl < -flat and sh < sl and width_now < width_then:
            name, yon = "alcalan_takoz", 1
        elif sh * sl > 0 and abs(sh - sl) < 0.5 * max(abs(sh), abs(sl)) and width_now > 1.5 * atr:
            name, yon = ("yukselen_kanal" if sh > 0 else "alcalan_kanal"), (1 if sh > 0 else -1)
        if name and width_now > 0 and max(width_then, width_now) >= 2 * atr:
            d = brk if name == "simetrik_ucgen" else yon
            if d:
                lvl_ = up_line if d > 0 else lo_line
                stt = "olusuyor" if "kanal" in name else _state(c, lvl_, d, atr)
                done = stt == "tamam"
                pos = (c[-1] - lo_line) / width_now if width_now else 0.5
                out.append({"ad": name, "yon": d, "durum": stt,
                            "seviye": up_line if d > 0 else lo_line,
                            "hedef": (up_line if d > 0 else lo_line) + d * max(width_then, width_now),
                            "guven": 0.65 if done else 0.25, "kanal_konumu": round(pos, 2)})
        # Elmas: önce genişleyen, sonra daralan yapı → önceki trendin tersine dönüş
        if len(hi) >= 5 and len(lo) >= 5:
            Hb, Lb = hi[-5:-2], lo[-5:-2]
            mhb, _ = _fit(Hb, [h[i] for i in Hb])
            mlb, _ = _fit(Lb, [l[i] for i in Lb])
            if mhb / atr > flat and mlb / atr < -flat and sh < -flat and sl > flat:
                prior = 1 if c[max(0, Hb[0] - 20)] < c[Hb[0]] else -1
                d = -prior
                stt = _state(c, up_line if d > 0 else lo_line, d, atr)
                done = stt == "tamam"
                out.append({"ad": "elmas", "yon": d, "durum": stt,
                            "seviye": up_line if d > 0 else lo_line, "hedef": None, "guven": 0.6 if done else 0.2})

    # Bayrak / flama: güçlü direk + dar, karşı eğimli konsolidasyon + direk yönünde kırılım
    for pole in range(5, 9):
        seg_end = n - 1
        for cons in range(5, 21):
            p_end = seg_end - cons
            p_start = p_end - pole
            if p_start < 0:
                break
            move = c[p_end] - c[p_start]
            if abs(move) < 4 * atr:
                continue
            ch, cl = h[p_end + 1:seg_end].max(initial=-math.inf), l[p_end + 1:seg_end].min(initial=math.inf)
            if ch - cl > 0.5 * abs(move):
                continue
            d = 1 if move > 0 else -1
            stt = _state(c, ch if d > 0 else cl, d, atr)
            done = stt == "tamam"
            out.append({"ad": "bayrak" if cons >= 8 else "flama", "yon": d, "durum": stt,
                        "seviye": ch if d > 0 else cl, "hedef": (ch if d > 0 else cl) + move, "guven": 0.65 if done else 0.25})
            break
        else:
            continue
        break

    # Fincan (ve kulp): yuvarlak dip, sağ taraf sol kenara döner, kenarın kırılması
    if n >= 60:
        w = c[-60:]
        left = w[:10].max()
        bot_i = int(np.argmin(w))
        mid = w[15:45]
        round_ = len(mid) and (mid < w[bot_i] + 0.35 * (left - w[bot_i])).mean() >= 0.3   # V değil U: dipte zaman geçirmiş
        if 20 <= bot_i <= 40 and left - w[bot_i] > 4 * atr and round_:
            right = w[-10:].max()
            if right >= left - atr:
                stt = _state(c, left, 1, atr)
                done = stt == "tamam"
                out.append({"ad": "fincan", "yon": 1, "durum": stt, "seviye": left,
                            "hedef": left + (left - w[bot_i]), "guven": 0.6 if done else 0.2})
    return out


# ------------------------------------------------------------------------------------------------ harmonikler
def _alt(hi, lo):
    pts = sorted([(i, 1) for i in hi] + [(i, -1) for i in lo])
    out = []
    for p in pts:
        if out and out[-1][1] == p[1]:
            out[-1] = p          # aynı türden ardışık salınım: sonuncusu
        else:
            out.append(p)
    return out


def harmonics(bars: list, atr: float) -> list[dict]:
    if len(bars) < 60 or not atr:
        return []
    o, h, l, c, v = _arr(bars[-200:])
    hi, lo = pivots(h, l)
    pts = _alt(hi, lo)
    if len(pts) < 5:
        return []
    px = [(i, h[i] if t == 1 else l[i], t) for i, t in pts]
    out = []
    X, A, B, C, D = px[-5:]
    xa, ab, bc, cd = abs(A[1] - X[1]), abs(B[1] - A[1]), abs(C[1] - B[1]), abs(D[1] - C[1])
    ad_ = abs(A[1] - D[1])
    if xa >= 3 * atr and ab and bc:
        r_ab, r_bc, r_ad = ab / xa, bc / ab, ad_ / xa
        d = 1 if D[2] == -1 else -1                     # D dipse boğa formasyonu
        turned = (c[-1] - D[1]) * d > 0.3 * atr
        lst = len(c) - 1
        recent = D[0] >= lst - 12
        for name, abr, adr in (("gartley", (0.55, 0.68), (0.74, 0.83)), ("yarasa", (0.38, 0.52), (0.85, 0.92)),
                               ("yengec", (0.38, 0.62), (1.5, 1.7))):
            if abr[0] <= r_ab <= abr[1] and 0.38 <= r_bc <= 0.886 and adr[0] <= r_ad <= adr[1] and recent:
                out.append({"ad": name, "yon": d, "durum": "tamam" if turned else "olusuyor", "seviye": D[1],
                            "hedef": D[1] + d * 0.618 * ad_, "guven": 0.6 if turned else 0.25})
        if 0.38 <= r_bc <= 0.886 and ab and 0.9 <= cd / ab <= 1.1 and recent:
            out.append({"ad": "abcd", "yon": d, "durum": "tamam" if turned else "olusuyor", "seviye": D[1],
                        "hedef": D[1] + d * 0.618 * cd, "guven": 0.5 if turned else 0.2})
        # Wolfe dalgası: 1-3-5 ve 2-4 çizgileri yakınsar, 5 noktası 1-3 çizgisinin ötesine sarkar
        p1, p2, p3, p4, p5 = px[-5:]
        if p1[2] == p5[2] == p3[2]:
            s = -p5[2]                                  # 5 dip ise boğa
            t = p1[2]                                   # −1: dipler (boğa), +1: tepeler (ayı)
            l13 = _line(p1[0], p1[1], p3[0], p3[1], p5[0])
            beyond = (p5[1] - l13) * t > 0.1 * atr
            rule3 = (p3[1] - p1[1]) * t > 0             # 3, 1'in ötesinde
            rule4 = (p4[1] - p1[1]) * (-t) > 0 and (p2[1] - p4[1]) * (-t) > 0   # 4, 1 ile 2 arasında
            amp = min(abs(p2[1] - p1[1]), abs(p3[1] - p2[1]), abs(p4[1] - p3[1]), abs(p5[1] - p4[1])) >= 1.5 * atr
            span1, span2 = p3[0] - p1[0], p5[0] - p3[0]
            sym = span1 > 0 and 0.5 <= span2 / span1 <= 2.0
            conv = abs(p4[1] - p3[1]) < abs(p2[1] - p1[1])
            if beyond and rule3 and rule4 and amp and sym and conv and recent:
                tgt = _line(p1[0], p1[1], p4[0], p4[1], lst + 10)
                out.append({"ad": "wolfe", "yon": s, "durum": "tamam" if turned else "olusuyor", "seviye": p5[1],
                            "hedef": tgt, "guven": 0.5 if turned else 0.2})
    return out


# ------------------------------------------------------------------------------------------------ trend çizgileri
def trendlines(bars: list, atr: float) -> dict:
    """Son iki salınım dibinden destek, son iki tepeden direnç çizgisi; kırılım ve rol değişimi (geri test)."""
    out = {"destek": None, "direnc": None, "kirilim": 0, "geri_test": 0}
    if len(bars) < 40 or not atr:
        return out
    o, h, l, c, v = _arr(bars[-150:])
    hi, lo = pivots(h, l)
    n = len(c) - 1
    if len(lo) >= 2 and l[lo[-1]] > l[lo[-2]]:
        a, b = lo[-2], lo[-1]
        out["destek"] = _line(a, l[a], b, l[b], n)
        prev = _line(a, l[a], b, l[b], n - 1)
        if c[-1] < out["destek"] and c[-2] >= prev:
            out["kirilim"] = -1
    if len(hi) >= 2 and h[hi[-1]] < h[hi[-2]]:
        a, b = hi[-2], hi[-1]
        out["direnc"] = _line(a, h[a], b, h[b], n)
        prev = _line(a, h[a], b, h[b], n - 1)
        if c[-1] > out["direnc"] and c[-2] <= prev:
            out["kirilim"] = 1
    # rol değişimi: son 10 mumda kırılan bir tepe (direnç) şimdi destek olarak test edilip tutuldu
    for i in reversed(hi[-4:]):
        lvl = h[i]
        if i < n - 3 and c[i + 1:].max() > lvl + 0.5 * atr and abs(l[-1] - lvl) <= 0.3 * atr and c[-1] > lvl:
            out["geri_test"] = 1
            out["geri_test_seviye"] = lvl
            break
    for i in reversed(lo[-4:]):
        lvl = l[i]
        if i < n - 3 and c[i + 1:].min() < lvl - 0.5 * atr and abs(h[-1] - lvl) <= 0.3 * atr and c[-1] < lvl:
            out["geri_test"] = -1
            out["geri_test_seviye"] = lvl
            break
    return out


# ------------------------------------------------------------------------------------------------ mum formasyonları
def candle_patterns(bars: list, atr: float) -> list[dict]:
    """Çok mumlu formasyonlar. Bağlam (önceki 5 mumun yönü) çekiç/asılı adam gibi ayrımlar için kullanılır."""
    if len(bars) < 8 or not atr:
        return []
    out = []
    b3, b2, b1 = bars[-3], bars[-2], bars[-1]
    O, Hh, L, C = b1[1], b1[2], b1[3], b1[4]
    rng = Hh - L
    body = abs(C - O)
    ctx = bars[-6][4] - bars[-2][4]                     # >0: önceden düşüş
    if rng > 0:
        lw, uw = min(O, C) - L, Hh - max(O, C)
        if lw >= 2 * body and uw <= 0.3 * rng:
            out.append({"ad": "cekic" if ctx > 0 else "asili_adam", "yon": 1 if ctx > 0 else -1})
        if uw >= 2 * body and lw <= 0.3 * rng:
            out.append({"ad": "kayan_yildiz" if ctx < 0 else "ters_cekic", "yon": -1 if ctx < 0 else 1})
    # iç mum / dış mum
    if b1[2] < b2[2] and b1[3] > b2[3]:
        out.append({"ad": "ic_mum", "yon": 0})
    if b1[2] > b2[2] and b1[3] < b2[3]:
        out.append({"ad": "dis_mum", "yon": 1 if C > O else -1})
    # harami
    if abs(b2[4] - b2[1]) > atr * 0.8 and max(O, C) < max(b2[1], b2[4]) and min(O, C) > min(b2[1], b2[4]):
        out.append({"ad": "harami", "yon": 1 if b2[4] < b2[1] else -1})
    # sabah / akşam yıldızı
    big = lambda b: abs(b[4] - b[1]) > 0.8 * atr
    small = lambda b: abs(b[4] - b[1]) < 0.35 * atr
    if big(b3) and small(b2) and big(b1):
        if b3[4] < b3[1] and C > O and C > (b3[1] + b3[4]) / 2:
            out.append({"ad": "sabah_yildizi", "yon": 1})
        if b3[4] > b3[1] and C < O and C < (b3[1] + b3[4]) / 2:
            out.append({"ad": "aksam_yildizi", "yon": -1})
    # üç beyaz asker / üç kara karga
    if all(b[4] > b[1] and big(b) for b in (b3, b2, b1)) and b1[4] > b2[4] > b3[4]:
        out.append({"ad": "uc_beyaz_asker", "yon": 1})
    if all(b[4] < b[1] and big(b) for b in (b3, b2, b1)) and b1[4] < b2[4] < b3[4]:
        out.append({"ad": "uc_kara_karga", "yon": -1})
    # cımbız dip / tepe
    tol = 0.1 * atr
    if abs(b1[3] - b2[3]) <= tol and b2[4] < b2[1] and C > O:
        out.append({"ad": "cimbiz_dip", "yon": 1})
    if abs(b1[2] - b2[2]) <= tol and b2[4] > b2[1] and C < O:
        out.append({"ad": "cimbiz_tepe", "yon": -1})
    return out


# ------------------------------------------------------------------------------------------------ likidite: stop/likidasyon kümeleri
def round_levels(px: float) -> list[float]:
    if px <= 0:
        return []
    step = 10 ** (math.floor(math.log10(px)) - 1)
    base = math.floor(px / step) * step
    return [base, base + step, base - step, base + 2 * step]


def stop_pools(bars: list, atr: float) -> list[dict]:
    """Stop kümeleri: salınım tepelerinin hemen üstü ve diplerinin hemen altı; eşit tepe/dip daha güçlü."""
    if len(bars) < 30 or not atr:
        return []
    o, h, l, c, v = _arr(bars[-150:])
    hi, lo = pivots(h, l)
    out = []
    for idxs, arr, side in ((hi[-6:], h, 1), (lo[-6:], l, -1)):
        for i in idxs:
            lvl = arr[i]
            if (side == 1 and c[i + 1:].max(initial=-math.inf) > lvl) or (side == -1 and c[i + 1:].min(initial=math.inf) < lvl):
                continue                                 # zaten alınmış
            eq = sum(1 for j in idxs if j != i and abs(arr[j] - lvl) <= 0.25 * atr)
            out.append({"px": lvl + side * 0.1 * atr, "taraf": "ust" if side == 1 else "alt", "esit": eq,
                        "guc": 1 + eq})
    return out


def liquidation_map(oi_h1: list, bars_h1: list, px: float, levs=(10, 25, 50, 100), lev_w: dict | None = None) -> dict:
    """Tahmini likidasyon kümeleri. Kimin hangi fiyatta likide olacağını yalnızca borsa bilir; bu bir TAHMİNDİR:
    1S mumlarda OI arttıysa, o mumun yönüne göre açılan pozisyonların kaldıraç dağılımına göre likidasyon fiyatına
    eklenir. Kaldıraç dağılımı (lev_w) OKX'in gerçek likidasyon akışıyla sürekli kalibre edilir (agent.on_liq).
    Fiyatın o mumdan SONRA ulaştığı kümeler silinir."""
    out = {"ust": None, "alt": None, "kumeler": []}
    if len(oi_h1) < 3 or not bars_h1 or px <= 0:
        return out
    w = lev_w or {L: 1 / len(levs) for L in levs}
    tot = sum(w.get(L, 0) for L in levs) or 1
    idx = {b[0]: i for i, b in enumerate(bars_h1)}
    clusters: dict[float, list] = {}
    for (t0, oi0), (t1, oi1) in zip(oi_h1[:-1], oi_h1[1:]):
        d = oi1 - oi0
        i = idx.get(t1, idx.get(t0))
        if d <= 0 or i is None:
            continue
        b = bars_h1[i]
        entry = b[4]
        side_long = b[4] >= b[1]
        later = bars_h1[i + 1:]
        hi_after = max((x[2] for x in later), default=-math.inf)
        lo_after = min((x[3] for x in later), default=math.inf)
        for L in levs:
            lp = entry * (1 - 1 / L) if side_long else entry * (1 + 1 / L)
            if (side_long and lo_after <= lp) or (not side_long and hi_after >= lp):
                continue                                  # o seviyeye sonradan gelindi: küme tükendi
            key = round(lp / px, 3)
            e = clusters.setdefault(key, [0.0, {}])
            e[0] += d * entry * w.get(L, 0) / tot
            e[1][L] = e[1].get(L, 0) + 1
    above = {k: v for k, v in clusters.items() if k > 1}
    below = {k: v for k, v in clusters.items() if k < 1}

    def top(dd):
        if not dd:
            return None
        k = max(dd, key=lambda x: dd[x][0] / max(abs(x - 1), 0.002))
        return {"px": k * px, "usd": round(dd[k][0]), "uzaklik_yuzde": round((k - 1) * 100, 2)}
    out["ust"], out["alt"] = top(above), top(below)
    out["kumeler"] = sorted(((k * px, round(v[0]), max(v[1], key=v[1].get)) for k, v in clusters.items()),
                            key=lambda x: -x[1])[:40]
    return out
