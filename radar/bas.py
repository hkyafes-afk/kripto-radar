"""👑 Baş Trader — tam yetkili ana ajanın beyni (v9, kullanıcı kararı 27.10.2026).

Diğer yöneticiler tek bir dar kurala bakar (ortalama, zirve, sıkışma...). Baş Trader elindeki TÜM veriyi tek bir
puanda birleştirir ve her an (canlı mumla, her dakika) karar verir:

  trend       fiyat 50/200 günlük ortalamaya göre nerede, 50'lik ortalama yukarı mı aşağı mı, ADX trend gücü
  momentum    30 günlük getiri, 20 günlük zirve/dip kırılımı
  göreli güç  BTC'ye göre 30 günlük güç sırası (coinler arası)
  hacim       bugünkü hareket ortalamanın üstünde hacimle mi geliyor
  para akışı  açık pozisyon (OI) fiyatla aynı yönde mi artıyor (yeni para), alıcı/satıcı baskısı (taker)
  kalabalık   funding: herkes aynı taraftaysa ters risk; karşı taraf ödüyorsa sıkışma yakıtı
  piyasa      BTC havası (altcoinler için)
  ekip        diğer 7 yöneticinin bu coindeki gerçek pozisyonları (bütün kulvarları izler)
  balina      Hyperliquid'de kârlı büyük cüzdanların net yönü, büyük hesapların long/short oranı kalabalığa göre,
              son 1 saatin likidasyonları (kim sıkışıyor)

Puan −1 (güçlü short) … +1 (güçlü long). Eksik veri (ör. OI yoksa) o bileşeni dışarıda bırakır, ağırlıklar yeniden
dağıtılır. Kararlar:
  GİRİŞ   |puan| ≥ GIRIS_ESIK → puanın yönünde. Puan büyüdükçe risk büyür (sermayenin %0,5 → %2,5'i).
  ÇIKIŞ   puan pozisyonun tersine CIKIS_ESIK kadar dönerse (giriş eşiğiyle arada geniş boşluk: tek bir iğnede
          girip çıkmaz). Stop, ilk hedefte %30 kâr + başabaş, sonra iz süren stop her an canlı.
  KALDIRAÇ seçilmez, sonuçtur: risk ÷ stop mesafesi = pozisyon; kaldıraç teminatı küçültür, likidasyon her zaman
          stop'un 1,5 katı ötede (masa kuralı).
"""
from __future__ import annotations

import math

GIRIS_ESIK = 0.45
CIKIS_ESIK = 0.15          # pozisyonun tersine bu kadar dönerse çık (long'da puan ≤ −0,15)

W = {"trend": 0.22, "momentum": 0.18, "goreli": 0.10, "hacim": 0.10, "akis": 0.18, "kalabalik": 0.08,
     "piyasa": 0.06, "ekip": 0.08, "balina": 0.12}
W_LEARN: dict = {}         # kendi karnesinden öğrenilen çarpanlar (100+ kapanmış işlemden sonra; 0,5–1,5×)
LEARN_MIN = 100
KOVALAMA_ATR = 3.0         # fiyat 20 günlük ortalamadan bu kadar ATR uzaksa kovalamaz, geri çekilmede limitle girer

LABEL = {"trend": "Trend", "momentum": "Momentum", "goreli": "Göreli güç", "hacim": "Hacim", "akis": "Para akışı",
         "kalabalik": "Kalabalık", "piyasa": "Piyasa (BTC)", "ekip": "Ekip", "balina": "Balina"}


def _ok(x) -> bool:
    return isinstance(x, (int, float)) and x == x and not math.isinf(x)


def _sg(x: float) -> float:
    return 1.0 if x > 0 else -1.0 if x < 0 else 0.0


def _cl(x: float) -> float:
    return max(-1.0, min(1.0, x))


def parts(ctx: dict) -> dict:
    """Bileşen puanları (−1…+1). Hesaplanamayan bileşen sözlükte yer almaz."""
    p: dict = {}
    c = ctx.get("close")
    if not _ok(c) or c <= 0:
        return p
    s50, s200, s50p, adx = ctx.get("sma50"), ctx.get("sma200"), ctx.get("sma50_prev5"), ctx.get("adx")
    tv = []
    if _ok(s50):
        tv.append(_sg(c - s50))
    if _ok(s200):
        tv.append(_sg(c - s200))
    if _ok(s50) and _ok(s50p) and s50p > 0:
        tv.append(_cl((s50 / s50p - 1) * 50))           # 5 günde %2 eğim = tam puan
    if tv:
        t = sum(tv) / len(tv)
        if _ok(adx):
            t *= 0.5 if adx < 15 else 1.0 if adx < 25 else 1.2
        p["trend"] = _cl(t)
    mv = []
    r30 = ctx.get("ret30")
    if _ok(r30):
        mv.append(math.tanh(r30 / 0.30))
    hh, ll = ctx.get("hh20"), ctx.get("ll20")
    if _ok(hh) and _ok(ll):
        mv.append(1.0 if c > hh else -1.0 if c < ll else 0.0)
    if mv:
        p["momentum"] = _cl(sum(mv) / len(mv))
    rr = ctx.get("rs_rank")
    if _ok(rr):
        p["goreli"] = _cl((rr - 0.5) * 2)
    vr, op = ctx.get("vol_ratio"), ctx.get("open")
    if _ok(vr) and _ok(op) and op > 0:
        day = c / op - 1
        p["hacim"] = _cl(_sg(day) * max(0.0, min(1.0, (vr - 1.0) / 1.5))) if vr > 1.0 else 0.0
    av = []
    oi, ch = ctx.get("oi_chg24"), ctx.get("chg24")
    if _ok(oi) and _ok(ch):
        if oi > 2:                                       # yeni para giriyor: fiyat hangi yöne gidiyorsa onu güçlendirir
            av.append(_sg(ch) * min(1.0, oi / 15))
        elif oi < -2:                                    # pozisyon kapanıyor: hareket zayıf (short kapatma / long kaçışı)
            av.append(_sg(ch) * 0.25)
        else:
            av.append(0.0)
    tk = ctx.get("taker_imb")
    if _ok(tk):
        av.append(math.tanh(tk * 8))
    if av:
        p["akis"] = _cl(sum(av) / len(av))
    fa = ctx.get("funding_ann")
    if _ok(fa):
        p["kalabalik"] = _cl(-math.tanh(fa / 60))           # longlar çok ödüyorsa (−), shortlar ödüyorsa (+ sıkışma)
    if ctx.get("sym") not in ("BTC",):
        if ctx.get("btc_bull"):
            p["piyasa"] = 1.0
        elif ctx.get("btc_bear"):
            p["piyasa"] = -1.0
    cr = ctx.get("crowd") or {}
    bv = []
    hl = cr.get("hl") or {}
    if (hl.get("n_long") or 0) + (hl.get("n_short") or 0) >= 3 and _ok(hl.get("net")):
        bv.append(_cl(hl["net"]))                         # kârlı büyük cüzdanlar net long (+) / short (−)
    la, lt = cr.get("ls_acct"), cr.get("ls_top")
    if _ok(la) and _ok(lt) and la > 0 and lt > 0:
        bv.append(_cl(math.tanh(math.log(lt / la) * 2)))  # büyük hesaplar kalabalıktan daha long mu?
    lq = cr.get("liq_1h") or {}
    ls_, ll_ = float(lq.get("short_usd") or 0), float(lq.get("long_usd") or 0)
    if ls_ + ll_ >= 50_000:
        bv.append(_cl((ls_ - ll_) / (ls_ + ll_)))         # shortlar likide oluyorsa yukarı sıkışma (+)
    if bv:
        p["balina"] = _cl(sum(bv) / len(bv))
    pe = ctx.get("peers") or {}
    nl, ns = int(pe.get("long") or 0), int(pe.get("short") or 0)
    if nl + ns:
        p["ekip"] = _cl((nl - ns) / (nl + ns) * min(1.0, (nl + ns) / 2))
    return p


def weights() -> dict:
    return {k: v * float(W_LEARN.get(k, 1.0)) for k, v in W.items()}


def score(ctx: dict) -> tuple[float, dict]:
    p = parts(ctx)
    w = weights()
    if sum(W[k] for k in p) < 0.4:                       # verinin yarısından azı varsa karar verme
        return 0.0, p
    wsum = sum(w[k] for k in p)
    return _cl(sum(w[k] * v for k, v in p.items()) / wsum), p


def extension(ctx: dict, side: str) -> float | None:
    """Fiyat hareketin ne kadar ilerisinde: 20 günlük ortalamaya ATR cinsinden uzaklık (pozisyon yönünde)."""
    c, e, a = ctx.get("close"), ctx.get("ema20"), ctx.get("atr")
    if not (_ok(c) and _ok(e) and _ok(a) and a > 0):
        return None
    return (1 if side == "long" else -1) * (c - e) / a


def learn(trades: list[dict]) -> tuple[dict, dict]:
    """Kendi karnesi: kapanmış işlemlerde her bileşenin (işlem yönünde) değeri ile sonucu (R) arasındaki ilişki.
    Haklı çıkan bileşenin ağırlığı artar, yanılanınki azalır (0,5–1,5×). LEARN_MIN işlemden önce dokunmaz."""
    rows = [t for t in trades if t.get("bas_parts") and t.get("r") is not None and not t.get("shadow")]
    rep = {"n": len(rows), "min": LEARN_MIN, "corr": {}, "mult": {}}
    if len(rows) < LEARN_MIN:
        return {}, rep
    out = {}
    for k in W:
        xs, ys = [], []
        for t in rows:
            v = (t["bas_parts"] or {}).get(k)
            if v is None:
                continue
            xs.append((1 if t["side"] == "long" else -1) * float(v))
            ys.append(float(t["r"]))
        if len(xs) < 30:
            continue
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        sy = math.sqrt(sum((y - my) ** 2 for y in ys))
        if sx == 0 or sy == 0:
            continue
        corr = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)
        rep["corr"][k] = round(corr, 3)
        out[k] = round(max(0.5, min(1.5, 1 + 2.5 * corr)), 3)
    rep["mult"] = out
    return out, rep


def reasons(p: dict, side: str) -> list[str]:
    sgn = 1 if side == "long" else -1
    out = []
    for k, v in sorted(p.items(), key=lambda kv: -abs(W[kv[0]] * kv[1])):
        if abs(v) < 0.05:
            continue
        out.append(f"{'✓' if sgn * v > 0 else '✗'} {LABEL[k]} {v:+.2f}")
    return out


def exit_reason(ctx: dict, side: str) -> str | None:
    s, _ = score(ctx)
    sgn = 1 if side == "long" else -1
    if sgn * s <= -CIKIS_ESIK:
        return f"Baş Trader: tablo tersine döndü (puan {s:+.2f})"
    return None


def watch(ctxs: list[dict], n: int = 14) -> list[dict]:
    """Panel: Baş Trader'ın şu an en çok dikkatini çeken coinler ve neden."""
    rows = []
    for c in ctxs:
        s, p = score(c)
        rows.append({"sym": c.get("sym"), "score": round(s, 3), "price": c.get("close"),
                     "chg24": c.get("chg24"), "parts": {k: round(v, 2) for k, v in p.items()},
                     "peers": c.get("peers") or {}})
    rows.sort(key=lambda r: -abs(r["score"]))
    return rows[:n]
