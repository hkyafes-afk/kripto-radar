"""Karar mekanizması: Polat'ın öğrendiği HER okuma bir oy verir (+1 long yönü … −1 short yönü).

Kategoriler (okuma sırası: büyük zaman dilimi → seviye → hacim → türev → akış):
  trend      1G/4S/1S yapı, yapı bozulması, EMA20/50/200 ve eğimleri, ADX+DI, Parabolic SAR, SuperTrend, TRIX, Dow hacim teyidi
  seviye     destek/direnç konumu (önceki gün/hafta Y-D-K, açılışlar, VWAP, POC/VAH/VAL, 4S salınımları, yuvarlak seviyeler,
             trend çizgileri, test sayısı)
  formasyon  grafik formasyonları, harmonikler (4S/1S/15dk), mum formasyonları (yalnızca seviyede ve bağlamda güçlü)
  momentum   RSI, Stokastik, CCI, Williams %R, Ultimate, Momentum, MACD histogramı
  hacim      hacim oranı, climax, OBV eğimi ve kendi direnç kırılımı, MFI, PVI, Demand Index, hacim profili konumu
  uyusmazlik fiyat ile RSI / MACD / OBV / MFI / CCI uyuşmazlıkları (1S ve 15dk)
  turev      OI–fiyat, fonlama (kalabalık tersi), basis, long/short, likidasyon patlaması (tükenme), likidasyon kümesi mıknatısı
  akis       CVD, CVD ayrışması, defter dengesi, kalıcı duvarlar, büyük işlemler, tape hızı
  piyasa     BTC yönü (altcoinler için), BTC dominansı, stablecoin arzı, haber sonrası piyasa tepkisi
  tetik      zamanlama: kırılım, anlık kırılım, geri test, seviyede ret mumu, süpürme, sıkışmadan çıkış, yapı bozulması,
             trend çizgisi kırılımı, formasyon tamamlanması, uyuşmazlık + mum, SAR/SuperTrend dönüşü, OBV kırılımı,
             MACD ve Stokastik kesişimi, likidasyon tükenmesi

Giriş şartları (hepsi):
  1. Toplam puan |S| ≥ eşik (gece/hafta sonu eşik yükselir)
  2. Büyük zaman dilimi trendi ters değil
  3. Kitaptaki şablon kuralı: trend, hacim ve momentum ailelerinin üçü de aynı yönü gösteriyor
  4. En az bir tetik aynı yönde
  5. Sert vetolar yok (risk motoru ve veri/likidite kapıları ayrıca)
Her oy, öğrenme modülünün o okuma için ölçtüğü katsayıyla çarpılır (işe yarayan okumanın sesi yükselir).
"""
from __future__ import annotations

import math

from . import brain, patterns

CAT_W = {"trend": 2.0, "seviye": 1.2, "formasyon": 1.0, "momentum": 0.8, "hacim": 1.0, "uyusmazlik": 0.7,
         "turev": 1.0, "akis": 1.0, "piyasa": 0.8, "tetik": 1.0}
TF_W = {"1D": 1.5, "4H": 1.3, "1H": 1.0, "15m": 0.8, "5m": 0.6, "1m": 0.4}


def _clip(x, a=-1.0, b=1.0):
    return max(a, min(b, x))


def _sgn(x):
    return 0 if x is None or x == 0 else (1 if x > 0 else -1)


class Votes:
    def __init__(self, mult: dict | None = None):
        self.v: dict[str, tuple[str, float, float]] = {}     # ad → (kategori, oy, ağırlık)
        self.mult = mult or {}

    def add(self, cat: str, name: str, vote, w: float = 1.0):
        if vote is None or (isinstance(vote, float) and math.isnan(vote)):
            return
        self.v[name] = (cat, _clip(float(vote)), w * self.mult.get(name, 1.0))

    def cats(self) -> dict[str, float]:
        acc: dict[str, list] = {}
        for cat, vote, w in self.v.values():
            a = acc.setdefault(cat, [0.0, 0.0])
            a[0] += vote * w
            a[1] += w
        return {c: (a[0] / a[1] if a[1] else 0.0) for c, a in acc.items()}

    def score(self, cat_mult: dict | None = None) -> float:
        cs = self.cats()
        num = den = 0.0
        for c, s in cs.items():
            if c == "tetik":
                continue                                     # tetik zamanlamadır, yön puanına katılmaz
            w = CAT_W.get(c, 1.0) * (cat_mult or {}).get(c, 1.0)
            num += s * w
            den += w
        return num / den if den else 0.0


# ------------------------------------------------------------------------------------------------ okumaların hepsi
def full_read(m, c, now_ms: int) -> dict:
    """brain.read_all + bütün göstergeler, formasyonlar, trend çizgileri, likidite, makro."""
    rd = brain.read_all(m, c, now_ms)
    ext, pat = {}, {}
    for tf, s in c.series.items():
        if s.n >= 60:
            e = s.ext()
            ext[tf] = e
            if tf in ("4H", "1H", "15m", "5m"):
                pat[tf] = s.patterns()
    rd["ext"] = ext
    rd["formasyon"] = pat
    h1 = c.series.get("1H")
    rd["likidasyon_kumesi"] = patterns.liquidation_map(list(c.oi_h1), list(h1.bars)[-120:] if h1 else [], c.price())
    s15 = c.series.get("15m")
    atr15 = (ext.get("15m") or {}).get("atr")
    rd["stop_kumeleri"] = patterns.stop_pools(list(s15.bars), atr15) if s15 and atr15 else []
    rd["yuvarlak"] = patterns.round_levels(c.price())
    rd["tape_hizi"] = tape_speed(c, now_ms)
    rd["piyasa"] = dict(m.global_)
    return rd


def tape_speed(c, now_ms: int) -> float | None:
    """Son 1 dk işlem sayısı / son 15 dk ortalaması."""
    if len(c.tape) < 20:
        return None
    n1 = sum(1 for t in c.tape if t[0] >= now_ms - 60_000)
    n15 = sum(1 for t in c.tape if t[0] >= now_ms - 900_000)
    base = n15 / 15 if n15 else 0
    return round(n1 / base, 2) if base else None


# ------------------------------------------------------------------------------------------------ oylar
def votes(m, c, rd: dict, btc_rd: dict | None, mult: dict | None = None) -> Votes:
    V = Votes(mult)
    px = rd["fiyat"] or 0
    ext = rd["ext"]
    k = m.cfg["kurulum"]

    # ---------------- trend
    for tf in ("1D", "4H", "1H"):
        w = TF_W[tf]
        st = rd.get(f"yapi_{tf}") or {}
        y = {"yukari": 1, "asagi": -1}.get(st.get("yon"), 0)
        y += 0.5 * {"yukari": 1, "asagi": -1}.get(st.get("bos"), 0)
        V.add("trend", f"yapi_{tf}", y, w)
        e = ext.get(tf) or {}
        if e.get("ema50"):
            V.add("trend", f"ema_{tf}", (_sgn(px - e["ema20"]) + _sgn(e["ema20"] - e["ema50"]) + _sgn(e.get("ema50_egim"))) / 3, w)
        if e.get("ema200"):
            V.add("trend", f"ema200_{tf}", _sgn(px - e["ema200"]) * 0.7, w)
        if e.get("adx") is not None and e.get("di_spread") is not None:
            V.add("trend", f"adx_di_{tf}", _sgn(e["di_spread"]) * min(e["adx"] / 40, 1) if e["adx"] >= 20 else 0.0, w)
        if e.get("psar_yon") is not None:
            V.add("trend", f"psar_{tf}", e["psar_yon"] * 0.6, w)
        if e.get("supertrend_yon") is not None:
            V.add("trend", f"supertrend_{tf}", e["supertrend_yon"] * 0.7, w)
        if e.get("trix") is not None and e.get("trix_sig") is not None:
            V.add("trend", f"trix_{tf}", 0.5 * _sgn(e["trix"]) + 0.5 * _sgn(e["trix"] - e["trix_sig"]), w * 0.7)
        if e.get("dow_hacim") is not None and y:
            dh = e["dow_hacim"]
            V.add("trend", f"dow_{tf}", (0.5 if (y > 0 and dh > 1.1) else -0.5 if (y < 0 and dh < 0.9) else 0.0), w * 0.6)

    # ---------------- seviye
    e1 = ext.get("1H") or {}
    atr1 = e1.get("atr") or (px * 0.01)
    lv = list(rd.get("seviyeler") or [])
    lv += [{"ad": "yuvarlak", "px": p, "test": 0} for p in rd.get("yuvarlak") or []]
    for tf in ("1H", "15m"):
        tl = (rd["formasyon"].get(tf) or {}).get("cizgi") or {}
        if tl.get("destek"):
            lv.append({"ad": f"trend_destek_{tf}", "px": tl["destek"], "test": 2})
        if tl.get("direnc"):
            lv.append({"ad": f"trend_direnc_{tf}", "px": tl["direnc"], "test": 2})
    below = [x for x in lv if x["px"] < px]
    above = [x for x in lv if x["px"] > px]
    sup = max(below, key=lambda x: x["px"]) if below else None
    res = min(above, key=lambda x: x["px"]) if above else None
    if sup and res:
        ds, dr = (px - sup["px"]) / atr1, (res["px"] - px) / atr1
        # destek yakın, direnç uzak → long yeri; tersi short yeri. Test sayısı gücü artırır.
        st_s, st_r = 1 + min(sup.get("test", 0), 4) / 4, 1 + min(res.get("test", 0), 4) / 4
        V.add("seviye", "konum", _clip((dr * st_s - ds * st_r) / (dr + ds + 1e-9)), 1.5)
    named = {x["ad"]: x["px"] for x in rd.get("seviyeler") or []}
    if named.get("vwap"):
        V.add("seviye", "vwap", _clip((px - named["vwap"]) / atr1), 1.0)
    for nm, w in (("gun_acilis", 0.6), ("hafta_acilis", 0.6), ("dun_kapanis", 0.5), ("gecen_hafta_kapanis", 0.5), ("poc", 0.7)):
        if named.get(nm):
            V.add("seviye", f"ustunde_{nm}", _sgn(px - named[nm]) * 0.6, w)

    # ---------------- formasyon
    for tf in ("4H", "1H", "15m"):
        for p in (rd["formasyon"].get(tf) or {}).get("grafik", []) + (rd["formasyon"].get(tf) or {}).get("harmonik", []):
            V.add("formasyon", f"{p['ad']}_{tf}", p["yon"] * p.get("guven", 0.3) * (1.5 if p["durum"] == "tamam" else 1), TF_W[tf])
    trend_dir = _sgn((V.cats().get("trend") or 0))
    near = (sup and (px - sup["px"]) <= 0.5 * atr1) or (res and (res["px"] - px) <= 0.5 * atr1)
    for tf in ("15m", "5m"):
        for p in (rd["formasyon"].get(tf) or {}).get("mum", []):
            if not p["yon"]:
                continue
            ctx = (2.0 if near else 0.5) * (1.3 if p["yon"] == trend_dir else 0.7)
            V.add("formasyon", f"mum_{p['ad']}_{tf}", p["yon"] * 0.5 * ctx, TF_W[tf])

    # ---------------- momentum (15dk ve 1S; 5dk hafif)
    for tf in ("1H", "15m", "5m"):
        e = ext.get(tf) or {}
        w = TF_W[tf]
        if e.get("rsi") is not None:
            r = e["rsi"]
            ranging = (e.get("adx") or 0) < 20
            vote = (r - 50) / 25
            if ranging and (r > 75 or r < 25):
                vote = -_sgn(r - 50) * 0.5                       # yatayda aşırı bölge dönüş, trendde güç
            V.add("momentum", f"rsi_{tf}", vote, w)
        if e.get("stoch_k") is not None and e.get("stoch_k_onceki") is not None:
            kk, dd, kp, dp = e["stoch_k"], e["stoch_d"], e["stoch_k_onceki"], e["stoch_d_onceki"]
            cross = 1 if (kp <= dp and kk > dd and kk < 30) else (-1 if (kp >= dp and kk < dd and kk > 70) else 0)
            V.add("momentum", f"stoch_{tf}", cross or 0.3 * _sgn(kk - dd), w)
        if e.get("cci") is not None:
            V.add("momentum", f"cci_{tf}", _clip(e["cci"] / 200), w * 0.7)
        if e.get("willr") is not None:
            V.add("momentum", f"willr_{tf}", _clip((e["willr"] + 50) / 50) * 0.6, w * 0.6)
        if e.get("ultimate") is not None:
            V.add("momentum", f"ultimate_{tf}", _clip((e["ultimate"] - 50) / 25), w * 0.6)
        if e.get("momentum") is not None and e.get("atr") and px:
            V.add("momentum", f"momentum_{tf}", _clip(e["momentum"] / 100 * px / (2 * e["atr"])), w * 0.7)
        if e.get("macd_hist") is not None and e.get("macd_hist_onceki") is not None:
            V.add("momentum", f"macd_hist_{tf}", 0.6 * _sgn(e["macd_hist"]) + 0.4 * _sgn(e["macd_hist"] - e["macd_hist_onceki"]), w)

    # ---------------- hacim
    for tf in ("1H", "15m"):
        e = ext.get(tf) or {}
        s = c.series.get(tf)
        w = TF_W[tf]
        if e.get("hacim_oran") is not None and s and s.bars:
            b = s.bars[-1]
            V.add("hacim", f"hacim_oran_{tf}", _sgn(b[4] - b[1]) * _clip((e["hacim_oran"] - 1) / 2, 0, 1), w)
        if e.get("obv_egim") is not None:
            V.add("hacim", f"obv_egim_{tf}", _clip(e["obv_egim"]), w)
        if e.get("obv_kirilim"):
            V.add("hacim", f"obv_kirilim_{tf}", e["obv_kirilim"], w * 1.3)
        if e.get("mfi") is not None:
            mf = e["mfi"]
            V.add("hacim", f"mfi_{tf}", (-0.5 if mf > 85 else 0.5 if mf < 15 else (mf - 50) / 35), w * 0.8)
        if e.get("pvi_ustunde") is not None:
            V.add("hacim", f"pvi_{tf}", e["pvi_ustunde"] * 0.4, w * 0.5)
        if e.get("demand") is not None:
            V.add("hacim", f"demand_{tf}", _clip(e["demand"] * 3), w * 0.7)
    hv = rd.get("hacim_15m") or {}
    if hv.get("climax"):
        s15 = c.series["15m"].bars[-1]
        V.add("hacim", "climax_15m", -_sgn(s15[4] - s15[1]) * 0.8, 1.2)    # aşırı hacim trend sonunda dönüş

    # ---------------- uyuşmazlık
    for tf in ("1H", "15m"):
        u = (ext.get(tf) or {}).get("uyusmazlik") or {}
        for nm, val in u.items():
            if val:
                V.add("uyusmazlik", f"uyusmazlik_{nm}_{tf}", val, TF_W[tf])
    ak = rd["akis"]
    if ak.get("ayrisma"):
        V.add("uyusmazlik", "cvd_ayrisma", 1 if ak["ayrisma"] == "boga" else -1, 1.2)

    # ---------------- türev
    tr = rd["turev"]
    q = tr.get("oi_fiyat")
    if q:
        V.add("turev", "oi_fiyat", {"yeni_long": 0.6, "short_kapanisi": 0.3, "yeni_short": -0.6, "long_kapanisi": -0.3}[q], 1.2)
    fa = tr.get("fonlama_yillik")
    if fa is not None:
        V.add("turev", "fonlama", (-min(fa / 100, 1) if fa > k["fonlama_kalabalik_long"] else
                                    min(-fa / 60, 1) if fa < k["fonlama_kalabalik_short"] else 0.0), 1.0)
    b = tr.get("basis_yuzde")
    if b is not None:
        V.add("turev", "basis", (-0.5 if b > 0.15 else 0.5 if b < -0.15 else 0.0), 0.8)
    ls = tr.get("long_short")
    if ls is not None:
        V.add("turev", "long_short", (-0.5 if ls > 2.0 else 0.5 if ls < 0.8 else 0.0), 0.8)
    if tr.get("liq_patlama"):
        V.add("turev", "likidasyon_tukenme", 0.7 if tr["liq_patlama"] == "long" else -0.7, 1.2)
    lm = rd.get("likidasyon_kumesi") or {}
    up, dn = lm.get("ust"), lm.get("alt")
    if up or dn:
        su = (up["usd"] / max(abs(up["uzaklik_yuzde"]), 0.3)) if up else 0
        sd = (dn["usd"] / max(abs(dn["uzaklik_yuzde"]), 0.3)) if dn else 0
        V.add("turev", "likidasyon_miknatisi", _clip((su - sd) / (su + sd + 1e-9)) * 0.6, 0.7)

    # ---------------- akış
    vol = c.vol24_usd or 1
    for key, w in (("cvd_5dk", 1.0), ("cvd_15dk", 1.0)):
        if ak.get(key) is not None:
            V.add("akis", key, _clip(ak[key] / (vol / 2000)), w)
    if ak.get("defter_dengesi") is not None:
        V.add("akis", "defter_dengesi", _clip(ak["defter_dengesi"] * 3), 0.8)
    if ak.get("duvar_alt"):
        V.add("akis", "duvar_alt", 0.5, 0.8)
    if ak.get("duvar_ust"):
        V.add("akis", "duvar_ust", -0.5, 0.8)
    if ak.get("buyuk_islem_net_5dk"):
        V.add("akis", "buyuk_islemler", _clip(ak["buyuk_islem_net_5dk"] / (vol / 5000)), 1.0)
    sp = rd.get("tape_hizi")
    if sp is not None and ak.get("cvd_5dk") is not None:
        V.add("akis", "tape_hizi", _sgn(ak["cvd_5dk"]) * _clip((sp - 1) / 2, 0, 1), 0.7)

    # ---------------- piyasa
    if btc_rd and c.inst != "BTC-USDT-SWAP":
        for tf, w in (("4H", 1.0), ("1H", 0.7)):
            y = {"yukari": 1, "asagi": -1}.get((btc_rd.get(f"yapi_{tf}") or {}).get("yon"), 0)
            V.add("piyasa", f"btc_{tf}", y, w)
    g = rd.get("piyasa") or {}
    if g.get("btc_dom_chg") is not None:
        dv = _clip(g["btc_dom_chg"] / 0.5)
        V.add("piyasa", "btc_dominans", dv if c.inst == "BTC-USDT-SWAP" else -dv, 0.6)
    if g.get("stable_chg24") is not None:
        V.add("piyasa", "stablecoin", _clip(g["stable_chg24"] / 0.3), 0.5)
    if g.get("haber_tepki") is not None:
        V.add("piyasa", "haber_tepkisi", _clip(g["haber_tepki"]), 1.0)
    return V


# ------------------------------------------------------------------------------------------------ tetikler
def triggers(m, c, rd: dict) -> list[tuple[str, int]]:
    """(ad, yön) zamanlama olayları. Kapanmış 15dk/5dk mumlar + anlık (canlı 1dk mum, tape)."""
    out = []
    ext = rd["ext"]
    k = m.cfg["kurulum"]
    s15, s5 = c.series.get("15m"), c.series.get("5m")
    e15 = ext.get("15m") or {}
    atr = e15.get("atr")
    if not s15 or len(s15.bars) < 3 or not atr:
        return out
    px = rd["fiyat"]
    lv = rd.get("seviyeler") or []
    for side in ("long", "short"):
        for st in brain.setups(m, c, rd, side):
            out.append((st["ad"], 1 if side == "long" else -1))
    b, pb = s15.bars[-1], s15.bars[-2]
    if e15.get("sikisma_bitti") and e15.get("sikisma_yon"):
        out.append(("sikisma_cikisi", e15["sikisma_yon"]))
    st = rd.get("yapi_15m") or {}
    if st.get("bos"):
        out.append(("yapi_bozulmasi_15m", 1 if st["bos"] == "yukari" else -1))
    for tf in ("15m", "1H"):
        f = rd["formasyon"].get(tf) or {}
        tl = f.get("cizgi") or {}
        if tl.get("kirilim"):
            out.append((f"trend_cizgisi_kirilim_{tf}", tl["kirilim"]))
        if tl.get("geri_test"):
            out.append((f"geri_test_{tf}", tl["geri_test"]))
        for p in f.get("grafik", []) + f.get("harmonik", []):
            if p["durum"] == "tamam":
                out.append((f"formasyon_{p['ad']}_{tf}", p["yon"]))
    u = e15.get("uyusmazlik") or {}
    cd = brain.candle(b, pb)
    for nm, val in u.items():
        if val and ((val > 0 and cd["tip"] in ("pin_alt", "yutan_yukari", "guclu_yukari")) or
                    (val < 0 and cd["tip"] in ("pin_ust", "yutan_asagi", "guclu_asagi"))):
            out.append((f"uyusmazlik_donus_{nm}", val))
            break
    for nm in ("psar_donus", "supertrend_donus"):
        if e15.get(nm):
            out.append((f"{nm}_15m", e15[nm]))
    if e15.get("obv_kirilim"):
        out.append(("obv_kirilim_15m", e15["obv_kirilim"]))
    if e15.get("macd_kesisim"):
        out.append(("macd_kesisim_15m", e15["macd_kesisim"]))
    kk, dd, kp, dp = (e15.get(x) for x in ("stoch_k", "stoch_d", "stoch_k_onceki", "stoch_d_onceki"))
    if None not in (kk, dd, kp, dp):
        if kp <= dp and kk > dd and kk < 25:
            out.append(("stoch_kesisim_15m", 1))
        elif kp >= dp and kk < dd and kk > 75:
            out.append(("stoch_kesisim_15m", -1))
    tr = rd["turev"]
    if tr.get("liq_patlama"):
        out.append(("likidasyon_tukenmesi", 1 if tr["liq_patlama"] == "long" else -1))
    # anlık kırılım: canlı 1dk mumda önemli seviye geçildi, hacim 2 kat, CVD aynı yönde
    s1 = c.series.get("1m")
    if s1 and s1.live and s1.ind.get("vol_avg"):
        lb = s1.live
        vr = lb[6] / s1.ind["vol_avg"] if s1.ind["vol_avg"] else 0
        cvd5 = rd["akis"].get("cvd_5dk") or 0
        for L in lv:
            p = L["px"]
            if lb[1] <= p < px and vr >= 2 and cvd5 > 0:
                out.append((f"anlik_kirilim_{L['ad']}", 1))
                break
            if lb[1] >= p > px and vr >= 2 and cvd5 < 0:
                out.append((f"anlik_kirilim_{L['ad']}", -1))
                break
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


# ------------------------------------------------------------------------------------------------ stop yeri
def structural_stop(c, rd: dict, d: int, entry: float, min_atr: float) -> tuple[float | None, str]:
    """Yapıya dayalı stop: girişin aleyhindeki en yakın anlamlı salınım/seviye/stop kümesinin ötesi (+0,2 ATR tampon).
    Stop kümesinin hemen dibine değil ötesine konur (stop avına yem olmamak için)."""
    e15 = rd["ext"].get("15m") or {}
    atr = e15.get("atr")
    if not atr:
        return None, ""
    cands = []
    for tf in ("15m", "1H"):
        s = c.series.get(tf)
        if not s:
            continue
        piv = s.pivots_lo if d > 0 else s.pivots_hi
        for _, p, _ in list(piv)[-6:]:
            cands.append((p, f"{tf} {'dip' if d > 0 else 'tepe'}"))
    for x in rd.get("stop_kumeleri") or []:
        if (d > 0 and x["taraf"] == "alt") or (d < 0 and x["taraf"] == "ust"):
            cands.append((x["px"], "stop kümesi"))
    for L in rd.get("seviyeler") or []:
        cands.append((L["px"], L["ad"]))
    best = None
    for p, why in cands:
        dist = (entry - p) * d
        if dist >= min_atr * atr and (best is None or dist < (entry - best[0]) * d):
            best = (p, why)
    if best is None:
        e1 = rd["ext"].get("1H") or {}
        a1 = e1.get("atr") or 2 * atr
        return entry - d * 1.5 * a1, "1,5 × 1S ATR (yakında yapı yok)"
    return best[0] - d * 0.2 * atr, f"{best[1]} ötesi"


# ------------------------------------------------------------------------------------------------ karar
def decide(m, c, rd: dict, btc_rd: dict | None, mult: dict | None, cat_mult: dict | None, cfg: dict) -> dict:
    K = cfg["karar"]
    V = votes(m, c, rd, btc_rd, mult)
    cats = V.cats()
    S = V.score(cat_mult)
    d = _sgn(S)
    trig = triggers(m, c, rd)
    for nm, td in trig:
        V.add("tetik", f"tetik_{nm}", td, 1.0)
    esik = K["esik"] + (K["gece_ek_esik"] if rd["seans"] == "gece" or _weekend(rd["ts"]) else 0.0)
    out = {"S": round(S, 4), "yon": d, "kategoriler": {k: round(v, 3) for k, v in cats.items()},
           "oylar": {n: round(v[1], 3) for n, v in V.v.items()}, "tetikler": trig, "esik": esik, "engel": None}
    if d == 0:
        out["engel"] = "yön yok"
        return out
    if abs(S) < esik:
        out["engel"] = f"puan {S:+.3f} eşiğin ({esik}) altında"
    elif cats.get("trend", 0) * d < -K["trend_tolerans"]:
        out["engel"] = "büyük zaman dilimi trendi ters"
    elif any(cats.get(f, 0) * d <= 0 for f in K["aileler"]):
        bad = [f for f in K["aileler"] if cats.get(f, 0) * d <= 0]
        out["engel"] = "gösterge aileleri aynı yönde değil: " + ", ".join(bad)
    elif not any(td == d for _, td in trig):
        out["engel"] = "tetik yok (zamanlama)"
    out["tetik_yonde"] = [nm for nm, td in trig if td == d]
    return out


def _weekend(ts_ms: int) -> bool:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).weekday() >= 5
