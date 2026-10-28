"""Polat'ın beyni: grafiği ve veriyi okuyan fonksiyonlar + tanıdığı üç kurulum.

Okuma sırası (kullanıcının notundaki gibi): büyük zaman diliminde yapı ve trend (1G → 4S) → önemli seviyeler →
hacim onayı → türev verileri (OI, fonlama, likidasyon, long/short, basis) → emir akışı (defter, duvar, tape, CVD).

Göstergeler yardımcıdır, tek başına sinyal değildir: her kurulumun ZORUNLU koşulları yapı + seviye + mumdur;
göstergeler ve türev/akış verileri yalnızca TEYİT ya da VETO olarak sayılır.
Tanımadığı (bu üç kurulumdan biri olmayan) hiçbir harekette işlem açmaz.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .market import Coin, Market
from .series import Series


def _r(x, d=4):
    return None if x is None else round(x, d)


# ------------------------------------------------------------------------------------------------
# 1. Yapı
# ------------------------------------------------------------------------------------------------
def structure(s: Series) -> dict:
    out = {"yon": "belirsiz", "hh": None, "hl": None, "bos": None, "son_tepe": None, "son_dip": None}
    if not s.ready or len(s.pivots_hi) < 2 or len(s.pivots_lo) < 2:
        return out
    ph, pl = s.pivots_hi, s.pivots_lo
    hh, hl = ph[-1][1] > ph[-2][1], pl[-1][1] > pl[-2][1]
    c = s.ind["close"]
    e50 = s.ind.get("ema50")
    out.update(hh=hh, hl=hl, son_tepe=ph[-1][1], son_dip=pl[-1][1])
    if hh and hl and (e50 is None or c > e50):
        out["yon"] = "yukari"
    elif not hh and not hl and (e50 is None or c < e50):
        out["yon"] = "asagi"
    else:
        out["yon"] = "yatay"
        out["aralik"] = (min(pl[-1][1], pl[-2][1]), max(ph[-1][1], ph[-2][1]))
    if c > ph[-1][1]:
        out["bos"] = "yukari"                 # son önemli tepe kırıldı
    elif c < pl[-1][1]:
        out["bos"] = "asagi"
    return out


# ------------------------------------------------------------------------------------------------
# 2. Mum
# ------------------------------------------------------------------------------------------------
def candle(b: list, prev: list | None = None) -> dict:
    ts, o, h, l, c = b[:5]
    rng = h - l
    if rng <= 0:
        return {"tip": "duz", "govde": 0, "ust_fitil": 0, "alt_fitil": 0, "kapanis_konumu": 0.5}
    body = abs(c - o)
    up_w = h - max(o, c)
    lo_w = min(o, c) - l
    pos = (c - l) / rng
    tip = "normal"
    if body < 0.1 * rng:
        tip = "doji"
    if lo_w >= 2 * body and lo_w >= 0.5 * rng and pos >= 0.6:
        tip = "pin_alt"                       # alıcı aşağıda savundu
    elif up_w >= 2 * body and up_w >= 0.5 * rng and pos <= 0.4:
        tip = "pin_ust"                       # satıcı yukarıyı reddetti
    elif body >= 0.5 * rng and pos >= 0.7 and c > o:
        tip = "guclu_yukari"
    elif body >= 0.5 * rng and pos <= 0.3 and c < o:
        tip = "guclu_asagi"
    if prev is not None:
        po, pc = prev[1], prev[4]
        if c > o and pc < po and c >= po and o <= pc and body > abs(pc - po):
            tip = "yutan_yukari"
        elif c < o and pc > po and c <= po and o >= pc and body > abs(pc - po):
            tip = "yutan_asagi"
    return {"tip": tip, "govde": _r(body / rng, 3), "ust_fitil": _r(up_w / rng, 3),
            "alt_fitil": _r(lo_w / rng, 3), "kapanis_konumu": _r(pos, 3)}


# ------------------------------------------------------------------------------------------------
# 3. Hacim
# ------------------------------------------------------------------------------------------------
def volume(s: Series, climax_k: float) -> dict:
    if not s.bars or not s.ind.get("vol_avg"):
        return {"oran": None, "climax": False}
    b = s.bars[-1]
    ratio = b[6] / s.ind["vol_avg"] if s.ind["vol_avg"] else None
    atr, e50 = s.ind.get("atr"), s.ind.get("ema50")
    extended = bool(atr and e50 and abs(b[4] - e50) > 3 * atr)
    return {"oran": _r(ratio, 2), "climax": bool(ratio and ratio >= climax_k and extended)}


def volume_profile(bars: list, bins: int = 60) -> dict | None:
    """Hacim profili: her mumun hacmi kendi aralığına eşit dağıtılır. POC + %70 değer alanı."""
    if len(bars) < 20:
        return None
    lo = min(b[3] for b in bars)
    hi = max(b[2] for b in bars)
    if hi <= lo:
        return None
    w = (hi - lo) / bins
    hist = [0.0] * bins
    for b in bars:
        a = int((b[3] - lo) / w)
        z = min(int((b[2] - lo) / w), bins - 1)
        a = min(a, z)
        share = b[6] / (z - a + 1)
        for i in range(a, z + 1):
            hist[i] += share
    poc_i = max(range(bins), key=lambda i: hist[i])
    total, acc = sum(hist), hist[poc_i]
    lo_i = hi_i = poc_i
    while acc < 0.7 * total and (lo_i > 0 or hi_i < bins - 1):
        down = hist[lo_i - 1] if lo_i > 0 else -1
        up = hist[hi_i + 1] if hi_i < bins - 1 else -1
        if up >= down:
            hi_i += 1
            acc += up
        else:
            lo_i -= 1
            acc += down
    return {"poc": lo + (poc_i + 0.5) * w, "vah": lo + (hi_i + 1) * w, "val": lo + lo_i * w}


# ------------------------------------------------------------------------------------------------
# 4. Seviyeler
# ------------------------------------------------------------------------------------------------
def vwap(bars: list, since_ms: int) -> float | None:
    pv = v = 0.0
    for b in bars:
        if b[0] >= since_ms:
            tp = (b[2] + b[3] + b[4]) / 3
            pv += tp * b[5]
            v += b[5]
    return pv / v if v else None


def levels(c: Coin) -> list[dict]:
    """Önemli seviyeler: önceki gün/hafta Y/D/K, gün ve hafta açılışı, günlük VWAP, 4S salınımları,
    hacim profili (POC/VAH/VAL, son 7 gün 1S), her biri test sayısıyla."""
    out = []
    d1, h4, h1, m5 = (c.series.get(k) for k in ("1D", "4H", "1H", "5m"))
    now_ms = (m5.bars[-1][0] + 300_000) if m5 and m5.bars else 0
    if d1 and len(d1.bars) >= 2:
        pd_ = d1.bars[-1]
        out += [{"ad": "dun_yuksek", "px": pd_[2]}, {"ad": "dun_dusuk", "px": pd_[3]}, {"ad": "dun_kapanis", "px": pd_[4]}]
        day_open = d1.live[1] if d1.live else pd_[4]
        out.append({"ad": "gun_acilis", "px": day_open})
        # hafta: ISO haftası (Pazartesi 00:00 UTC)
        weeks: dict = {}
        for b in list(d1.bars)[-21:]:
            k = datetime.fromtimestamp(b[0] / 1000, timezone.utc).isocalendar()[:2]
            w = weeks.setdefault(k, [b[1], b[2], b[3], b[4]])
            w[1], w[2], w[3] = max(w[1], b[2]), min(w[2], b[3]), b[4]
        keys = sorted(weeks)
        cur_week = datetime.fromtimestamp(now_ms / 1000, timezone.utc).isocalendar()[:2] if now_ms else None
        prev_keys = [k for k in keys if k != cur_week]
        if prev_keys:
            pw = weeks[prev_keys[-1]]
            out += [{"ad": "gecen_hafta_yuksek", "px": pw[1]}, {"ad": "gecen_hafta_dusuk", "px": pw[2]},
                    {"ad": "gecen_hafta_kapanis", "px": pw[3]}]
        if cur_week in weeks:
            out.append({"ad": "hafta_acilis", "px": weeks[cur_week][0]})
    if m5 and m5.bars:
        day0 = now_ms - now_ms % 86_400_000
        vw = vwap(list(m5.bars) + ([m5.live] if m5.live else []), day0)
        if vw:
            out.append({"ad": "vwap", "px": vw})
    if h4:
        for p in list(h4.pivots_hi)[-4:]:
            out.append({"ad": "4s_tepe", "px": p[1]})
        for p in list(h4.pivots_lo)[-4:]:
            out.append({"ad": "4s_dip", "px": p[1]})
    if h1 and len(h1.bars) >= 50:
        vp = volume_profile(list(h1.bars)[-168:])
        if vp:
            out += [{"ad": "poc", "px": vp["poc"]}, {"ad": "vah", "px": vp["vah"]}, {"ad": "val", "px": vp["val"]}]
    # test sayısı: son 200 1S mumda seviyeye 0,25 ATR yaklaşıp dönen mum sayısı
    if h1 and h1.ind.get("atr"):
        tol = 0.25 * h1.ind["atr"]
        bars = list(h1.bars)[-200:]
        for lv in out:
            p = lv["px"]
            lv["test"] = sum(1 for b in bars if (abs(b[2] - p) <= tol and b[4] < p) or (abs(b[3] - p) <= tol and b[4] > p))
    return out


def fib_zone(s: Series, side: str) -> tuple[float, float] | None:
    """Son salınımın 0,5–0,618 düzeltme bölgesi (tek başına anlamsız; yapı ile birlikte kullanılır)."""
    if len(s.pivots_hi) < 1 or len(s.pivots_lo) < 1:
        return None
    hi, lo = s.pivots_hi[-1], s.pivots_lo[-1]
    if side == "long" and lo[0] < hi[0]:          # dip → tepe yükselişi
        r = hi[1] - lo[1]
        return hi[1] - 0.618 * r, hi[1] - 0.5 * r
    if side == "short" and hi[0] < lo[0]:
        r = hi[1] - lo[1]
        return lo[1] + 0.5 * r, lo[1] + 0.618 * r
    return None


# ------------------------------------------------------------------------------------------------
# 5. Türev verileri
# ------------------------------------------------------------------------------------------------
def _pct_change_hist(hist, now_ms: int, window_ms: int):
    if not hist:
        return None
    cur = hist[-1][1]
    old = None
    for t, v, *_ in hist:
        if t >= now_ms - window_ms:
            old = v
            break
    if old in (None, 0):
        return None
    return (cur / old - 1) * 100


def derivatives(c: Coin, now_ms: int, fund_interval_h: float = 8.0) -> dict:
    s1 = c.series.get("1H")
    oi1 = _pct_change_hist(c.oi_hist, now_ms, 3_600_000)
    px1 = None
    if s1 and len(s1.bars) >= 2:
        ref = s1.live[4] if s1.live else s1.bars[-1][4]
        px1 = (ref / s1.bars[-1][1] - 1) * 100 if s1.live else (s1.bars[-1][4] / s1.bars[-2][4] - 1) * 100
    quad = None
    if oi1 is not None and px1 is not None:
        if px1 > 0 and oi1 > 0:
            quad = "yeni_long"
        elif px1 > 0 and oi1 <= 0:
            quad = "short_kapanisi"
        elif px1 <= 0 and oi1 > 0:
            quad = "yeni_short"
        else:
            quad = "long_kapanisi"
    liq_long = sum(u for t, s, u in c.liqs if s == "long" and t >= now_ms - 900_000)
    liq_short = sum(u for t, s, u in c.liqs if s == "short" and t >= now_ms - 900_000)
    hour = [(t, s, u) for t, s, u in c.liqs if t >= now_ms - 3_600_000]
    base = max(sum(u for *_, u in hour) / 4, c.vol24_usd / 5000 if c.vol24_usd else 0, 50_000)
    basis = None
    if c.mark and c.index:
        basis = (c.mark / c.index - 1) * 100
    fann = c.funding * (8760 / fund_interval_h) * 100 if c.funding is not None else None
    return {"fonlama_yillik": _r(fann, 2), "oi_1s": _r(oi1, 2), "fiyat_1s": _r(px1, 2), "oi_fiyat": quad,
            "liq_long_15dk": round(liq_long), "liq_short_15dk": round(liq_short),
            "liq_patlama": "long" if liq_long >= 3 * base else ("short" if liq_short >= 3 * base else None),
            "long_short": _r(c.ls_ratio, 3), "basis_yuzde": _r(basis, 4)}


# ------------------------------------------------------------------------------------------------
# 6. Emir akışı
# ------------------------------------------------------------------------------------------------
def flow(c: Coin, now_ms: int, atr: float | None, persist_s: float) -> dict:
    out = {"cvd_5dk": None, "cvd_15dk": None, "ayrisma": None, "defter_dengesi": None,
           "duvar_ust": None, "duvar_alt": None, "buyuk_islem_net_5dk": 0}
    h = c.cvd_hist
    if len(h) >= 2:
        def delta(win):
            old = next((x for x in h if x[0] >= now_ms - win), None)
            return (h[-1][1] - old[1]) if old else None
        out["cvd_5dk"], out["cvd_15dk"] = _r(delta(300_000), 0), _r(delta(900_000), 0)
        # ayrışma: son 15 dk'nın ikinci yarısında fiyat yeni uç yaptı ama CVD yapmadı
        seg = [x for x in h if x[0] >= now_ms - 900_000]
        if len(seg) >= 20:
            m = len(seg) // 2
            a, b = seg[:m], seg[m:]
            if max(x[2] for x in b) > max(x[2] for x in a) and max(x[1] for x in b) < max(x[1] for x in a):
                out["ayrisma"] = "ayi"       # fiyat yükseliyor, agresif alım zayıflıyor
            elif min(x[2] for x in b) < min(x[2] for x in a) and min(x[1] for x in b) > min(x[1] for x in a):
                out["ayrisma"] = "boga"
    if c.book.ok:
        bids, asks = c.book.levels("bids", 10, c.ct), c.book.levels("asks", 10, c.ct)
        bu, au = sum(p * s for p, s in bids), sum(p * s for p, s in asks)
        if bu + au:
            out["defter_dengesi"] = _r((bu - au) / (bu + au), 3)
        px = c.price()
        lim = (atr or px * 0.01) * 2
        for (side, wp), (t0, t1, sz) in c.walls.items():
            if (t1 - t0) / 1000 < persist_s or abs(wp - px) > lim:
                continue
            rec = {"px": wp, "usd": round(wp * sz), "sure_sn": round((t1 - t0) / 1000)}
            if side == "asks" and wp > px and (out["duvar_ust"] is None or wp < out["duvar_ust"]["px"]):
                out["duvar_ust"] = rec
            if side == "bids" and wp < px and (out["duvar_alt"] is None or wp > out["duvar_alt"]["px"]):
                out["duvar_alt"] = rec
    out["buyuk_islem_net_5dk"] = round(sum(u * s for t, _, u, s in c.big_trades if t >= now_ms - 300_000))
    return out


def session(ts_ms: int) -> str:
    h = datetime.fromtimestamp(ts_ms / 1000, timezone.utc).hour
    if 13 <= h < 21:
        return "ABD"
    if 7 <= h < 13:
        return "Avrupa"
    if 0 <= h < 7:
        return "Asya"
    return "gece"


# ------------------------------------------------------------------------------------------------
# 7. Bütün okuma + kurulumlar
# ------------------------------------------------------------------------------------------------
def read_all(m: Market, c: Coin, now_ms: int) -> dict:
    k = m.cfg["kurulum"]
    s = c.series
    rd = {"inst": c.inst, "ts": now_ms, "fiyat": c.price(), "seans": session(now_ms), "spread_bp": _r(c.spread_bp(), 2)}
    for tf in ("1D", "4H", "1H", "15m"):
        if tf in s:
            st = structure(s[tf])
            rd[f"yapi_{tf}"] = st
            ind = s[tf].ind
            rd[f"gosterge_{tf}"] = {x: _r(ind.get(x), 6) for x in ("ema20", "ema50", "ema200", "rsi", "atr", "macd", "macd_sig", "bbw")}
            rd[f"gosterge_{tf}"]["bb_yuzdelik"] = _r(s[tf].bbw_pct(), 2)
    m15 = s.get("15m")
    if m15 and len(m15.bars) >= 2:
        rd["mum_15m"] = candle(m15.bars[-1], m15.bars[-2])
        rd["hacim_15m"] = volume(m15, k["climax_kat"])
    m5 = s.get("5m")
    if m5 and len(m5.bars) >= 2:
        rd["mum_5m"] = candle(m5.bars[-1], m5.bars[-2])
    rd["seviyeler"] = sorted(levels(c), key=lambda x: x["px"])
    rd["turev"] = derivatives(c, now_ms)
    atr15 = m15.ind.get("atr") if m15 else None
    rd["akis"] = flow(c, now_ms, atr15, k["duvar_kalici_sn"])
    return rd


def _near(levels_: list[dict], px: float, tol: float, names=None):
    best = None
    for lv in levels_:
        if names and lv["ad"] not in names:
            continue
        d = abs(lv["px"] - px)
        if d <= tol and (best is None or d < abs(best["px"] - px)):
            best = lv
    return best


def bias(rd: dict, btc_rd: dict | None, is_btc: bool) -> str | None:
    d, h4 = rd.get("yapi_1D", {}).get("yon"), rd.get("yapi_4H", {}).get("yon")
    b = None
    if d != "asagi" and h4 == "yukari":
        b = "long"
    elif d != "yukari" and h4 == "asagi":
        b = "short"
    if b and not is_btc and btc_rd:
        bh4 = btc_rd.get("yapi_4H", {}).get("yon")
        if b == "long" and bh4 == "asagi":
            return None                         # BTC 4S düşerken altcoin long yok
        if b == "short" and bh4 == "yukari":
            return None
    return b


def setups(m: Market, c: Coin, rd: dict, side: str) -> list[dict]:
    """Zorunlu koşulları tutan kurulumlar. Her biri: ad, gerekçeler, stop referansı."""
    k = m.cfg["kurulum"]
    s15 = c.series["15m"]
    if len(s15.bars) < 3 or not s15.ind.get("atr"):
        return []
    atr = s15.ind["atr"]
    b, pb = s15.bars[-1], s15.bars[-2]
    cd = rd.get("mum_15m") or candle(b, pb)
    vol = rd.get("hacim_15m") or {}
    lv = rd["seviyeler"]
    out = []
    sgn = 1 if side == "long" else -1
    aktif = set(k["aktif"])

    # A) Kırılım: önemli seviye bu mumda hacimle ve güçlü kapanışla kırıldı
    if "kirilim" in aktif:
        names = {"dun_yuksek", "gecen_hafta_yuksek", "4s_tepe", "vah", "hafta_acilis", "gun_acilis"} if side == "long" \
            else {"dun_dusuk", "gecen_hafta_dusuk", "4s_dip", "val", "hafta_acilis", "gun_acilis"}
        for L in lv:
            if L["ad"] not in names:
                continue
            p = L["px"]
            crossed = (b[4] > p >= pb[4]) if side == "long" else (b[4] < p <= pb[4])
            strong = cd["tip"] in (("guclu_yukari", "yutan_yukari") if side == "long" else ("guclu_asagi", "yutan_asagi"))
            if crossed and strong and (vol.get("oran") or 0) >= k["hacim_kirilim_kat"] and not vol.get("climax"):
                sl = min(b[3], p - 0.5 * atr) if side == "long" else max(b[2], p + 0.5 * atr)
                out.append({"ad": "kirilim", "seviye": L, "sl": sl,
                            "gerekce": [f"{L['ad']} {p:.6g} kırıldı", f"mum {cd['tip']}", f"hacim {vol.get('oran')}x"]})
                break

    # B) Trend içi geri çekilme: destek bölgesinde ret mumu
    if "geri_cekilme" in aktif:
        h4 = c.series.get("4H")
        h1 = c.series.get("1H")
        zones = []
        if h4 and h4.ind.get("ema20"):
            zones.append(("4s_ema20", h4.ind["ema20"]))
        fz = fib_zone(h4, side) if h4 else None
        if fz:
            zones.append(("fib_0.5-0.618", (fz[0] + fz[1]) / 2))
        for L in lv:
            if L["ad"] in ("poc", "vwap", "val" if side == "long" else "vah", "dun_dusuk" if side == "long" else "dun_yuksek"):
                zones.append((L["ad"], L["px"]))
        rsi1 = h1.ind.get("rsi") if h1 else None
        rej = cd["tip"] in (("pin_alt", "yutan_yukari") if side == "long" else ("pin_ust", "yutan_asagi"))
        rsi_ok = rsi1 is not None and (35 <= rsi1 <= 60 if side == "long" else 40 <= rsi1 <= 65)
        if rej and rsi_ok:
            for name, z in zones:
                touched = (b[3] <= z + 0.3 * atr and b[4] > z) if side == "long" else (b[2] >= z - 0.3 * atr and b[4] < z)
                if touched:
                    sl = min(b[3], z - 0.3 * atr) if side == "long" else max(b[2], z + 0.3 * atr)
                    out.append({"ad": "geri_cekilme", "seviye": {"ad": name, "px": z}, "sl": sl,
                                "gerekce": [f"{name} {z:.6g} bölgesinde ret", f"mum {cd['tip']}", f"1S RSI {rsi1:.0f}"]})
                    break

    # C) Süpürme (başarısız kırılım): stop kümesinin altı/üstü iğneyle alındı, mum seviyenin içinde kapandı
    if "supurme" in aktif:
        names = {"dun_dusuk", "gecen_hafta_dusuk", "4s_dip", "val"} if side == "long" else {"dun_yuksek", "gecen_hafta_yuksek", "4s_tepe", "vah"}
        tr = rd["turev"]
        for L in lv:
            if L["ad"] not in names:
                continue
            p = L["px"]
            swept = (b[3] < p - 0.1 * atr and b[4] > p) if side == "long" else (b[2] > p + 0.1 * atr and b[4] < p)
            wick = (cd["alt_fitil"] or 0) >= 0.5 if side == "long" else (cd["ust_fitil"] or 0) >= 0.5
            fuel = tr.get("liq_patlama") == ("long" if side == "long" else "short") or (vol.get("oran") or 0) >= 1.5
            if swept and wick and fuel:
                sl = (b[3] - 0.2 * atr) if side == "long" else (b[2] + 0.2 * atr)
                out.append({"ad": "supurme", "seviye": L, "sl": sl,
                            "gerekce": [f"{L['ad']} {p:.6g} altı/üstü süpürüldü, içeride kapandı",
                                        f"fitil {cd['alt_fitil'] if side == 'long' else cd['ust_fitil']}",
                                        f"likidasyon {tr.get('liq_patlama')} / hacim {vol.get('oran')}x"]})
                break
    for o in out:
        o["side"] = side
        o["sgn"] = sgn
    return out


def confirmations(m: Market, c: Coin, rd: dict, st: dict, btc_rd: dict | None) -> tuple[list[str], list[str]]:
    """(teyitler, vetolar)"""
    k = m.cfg["kurulum"]
    side = st["side"]
    L = side == "long"
    t, v = [], []
    vol = rd.get("hacim_15m") or {}
    tr, ak = rd["turev"], rd["akis"]
    g15, g1 = rd.get("gosterge_15m", {}), rd.get("gosterge_1H", {})
    if (vol.get("oran") or 0) >= k["hacim_kirilim_kat"]:
        t.append("hacim onayı")
    if ak["cvd_15dk"] is not None and (ak["cvd_15dk"] > 0) == L:
        t.append("CVD yönle uyumlu")
    if ak["ayrisma"] == ("ayi" if L else "boga"):
        v.append(f"CVD ayrışması ({ak['ayrisma']})")
    if ak["defter_dengesi"] is not None and (ak["defter_dengesi"] > 0.1 if L else ak["defter_dengesi"] < -0.1):
        t.append(f"defter dengesi {ak['defter_dengesi']}")
    wall = ak["duvar_ust"] if L else ak["duvar_alt"]
    if wall:
        risk = abs(rd["fiyat"] - st["sl"])
        if abs(wall["px"] - rd["fiyat"]) < risk:
            v.append(f"karşıda kalıcı duvar {wall['px']:.6g} ({wall['sure_sn']} sn)")
    own = ak["duvar_alt"] if L else ak["duvar_ust"]
    if own:
        t.append(f"arkada kalıcı duvar {own['px']:.6g}")
    q = tr["oi_fiyat"]
    if q and ((L and q in ("yeni_long", "short_kapanisi")) or (not L and q in ("yeni_short", "long_kapanisi"))):
        t.append(f"OI-fiyat: {q}")
    fa = tr["fonlama_yillik"]
    if fa is not None:
        if L and fa > k["fonlama_kalabalik_long"]:
            v.append(f"fonlama kalabalık long (%{fa})")
        elif not L and fa < k["fonlama_kalabalik_short"]:
            v.append(f"fonlama kalabalık short (%{fa})")
    ls = tr["long_short"]
    if ls is not None and ((L and ls < 1.0) or (not L and ls > 2.0)):
        t.append(f"kalabalık karşı tarafta (L/S {ls})")
    if g15.get("macd") is not None and g15.get("macd_sig") is not None and ((g15["macd"] > g15["macd_sig"]) == L):
        t.append("MACD 15dk yönde")
    if g1.get("ema20") and g1.get("ema50") and ((g1["ema20"] > g1["ema50"]) == L):
        t.append("1S EMA20/50 dizilimi")
    if st["ad"] == "kirilim" and (rd.get("gosterge_15m", {}).get("bb_yuzdelik") or 1) <= 0.3:
        t.append("Bollinger sıkışmasından çıkış")
    if btc_rd and c.inst != "BTC-USDT-SWAP":
        by = btc_rd.get("yapi_1H", {}).get("yon")
        if by == ("yukari" if L else "asagi"):
            t.append("BTC 1S aynı yönde")
    if (rd.get("hacim_15m") or {}).get("climax") and st["ad"] == "kirilim":
        v.append("hacim patlaması (tükenme riski)")
    tst = (st.get("seviye") or {}).get("test")
    if tst and tst >= 2:
        t.append(f"seviye {tst} kez test edilmiş")
    return t, v
