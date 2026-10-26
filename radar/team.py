"""Yönetici kadrosu: her biri farklı uzmanlık, zaman ufku ve işlem tarzı.

Her yönetici bir "fikir" (Idea) üretir:
  - zorunlu çekirdek koşul (tarzının özü: kırılım, Fibonacci bölgesi, squeeze...)
  - doğrulamalar (checks): bağımsız destekleyici kanıtlar. Güven = tutan doğrulama oranı.
  - risk = taban + (tavan − taban) × güven   → kanıt arttıkça büyür, kafaya göre büyüyemez
  - plan: giriş (piyasa / geri çekilmede limit), stop'un mantığı, hedefler, kısmi kâr ve iz süren stop tarzı

Yardımcı roller (her yöneticide):
  analist = analyze()           giriş uzmanı = entry_type/limit_px
  risk görevlisi = desk.size()  pozisyon yöneticisi = pp (kısmi kâr, başabaş, iz süren stop)
  günlükçü = checks + reasons   denetçi = haftalık karne + yedek kulübesi kuralı (desk)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass
class Manager:
    id: str
    name: str
    title: str
    emoji: str
    style: str
    tfs: list
    base_risk: float          # % (kendi sermayesinin)
    max_risk: float
    dd_bench: float           # % kendi zirvesinden bu kadar düşerse yedek kulübesi
    aggressive: bool = False
    analyze: Callable | None = None
    kind: str = "directional"  # directional | carry
    extra: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "title": self.title, "emoji": self.emoji, "style": self.style,
                "tfs": self.tfs, "base_risk": self.base_risk, "max_risk": self.max_risk, "dd_bench": self.dd_bench,
                "aggressive": self.aggressive, "kind": self.kind}


# ── ek göstergeler (yöneticilerin ortak veri masası) ──────────────────

def _swings(h: np.ndarray, l: np.ndarray, atr: float, k: int = 3, look: int = 60):
    """Son yükseliş ve düşüş dalgalarını bulur (Fibonacci için). Pivot ancak k mum sonra kesinleşir."""
    n = len(h)
    lo_idx = np.where(ind._pivots(l, k, "low"))[0]
    hi_idx = np.where(ind._pivots(h, k, "high"))[0]
    lo_idx = lo_idx[(lo_idx >= n - look) & (lo_idx <= n - 1 - k)]
    hi_idx = hi_idx[(hi_idx >= n - look) & (hi_idx <= n - 1 - k)]
    up = dn = None
    if len(hi_idx):
        iH = int(hi_idx[-1])
        prev_lows = lo_idx[lo_idx < iH]
        if len(prev_lows):
            iL = int(prev_lows[np.argmin(l[prev_lows])])
            H, L = float(h[iH]), float(l[iL])
            if H - L >= 3 * atr and float(np.min(l[iH:])) > L:
                up = {"H": H, "L": L, "iH": iH, "iL": iL, "since": n - 1 - iH, "size_atr": (H - L) / atr}
    if len(lo_idx):
        iLo = int(lo_idx[-1])
        prev_highs = hi_idx[hi_idx < iLo]
        if len(prev_highs):
            iHi = int(prev_highs[np.argmax(h[prev_highs])])
            Hh, Ll = float(h[iHi]), float(l[iLo])
            if Hh - Ll >= 3 * atr and float(np.max(h[iLo:])) < Hh:
                dn = {"H": Hh, "L": Ll, "iH": iHi, "iL": iLo, "since": n - 1 - iLo, "size_atr": (Hh - Ll) / atr}
    return up, dn


def extra_features(df: pd.DataFrame, f: pd.DataFrame) -> dict:
    """Son kapanmış mum için yöneticilerin ihtiyaç duyduğu ek veriler."""
    o, h, l, c, v = (df[x] for x in ("open", "high", "low", "close", "volume"))
    mid, up, lo = ind.bollinger(c, 20, 2.0)
    bbw = (up - lo) / mid.replace(0, np.nan)
    rank = bbw.rolling(120, min_periods=60).rank(pct=True)
    vol_ratio = v / v.rolling(20).mean().shift(1)
    rsi, adx = f["rsi"], f["adx"]
    atr = float(f["atr"].iloc[-1])
    rng = float(h.iloc[-1] - l.iloc[-1]) or 1e-12
    swing_up, swing_dn = _swings(h.to_numpy(float), l.to_numpy(float), atr) if atr == atr and atr > 0 else (None, None)

    def last(s, i=-1):
        x = s.iloc[i]
        return float(x) if x == x else float("nan")

    return {
        "open": last(o), "high": last(h), "low": last(l), "close": last(c),
        "prev_high": last(h, -2), "prev_low": last(l, -2),
        "low5": float(l.iloc[-5:].min()), "high5": float(h.iloc[-5:].max()),
        "ema20": last(f["ema_f"]), "ema50": last(f["ema_s"]), "atr": atr,
        "rsi": last(rsi), "rsi_prev": last(rsi, -2),
        "rsi_min3": float(rsi.iloc[-3:].min()), "rsi_max3": float(rsi.iloc[-3:].max()),
        "adx": last(adx), "adx_prev": last(adx, -2),
        "bb_mid": last(mid), "bb_up": last(up), "bb_lo": last(lo), "bb_pct": last(f["bb_pct"]),
        "bbw_rank": last(rank), "squeeze_prev": bool(rank.iloc[-2] <= 0.15) if rank.iloc[-2] == rank.iloc[-2] else False,
        "don_hi": float(h.iloc[-21:-1].max()), "don_lo": float(l.iloc[-21:-1].min()),
        "vol_ratio": last(vol_ratio), "close_pos": (float(c.iloc[-1]) - float(l.iloc[-1])) / rng,
        "st_dir": int(f["st_dir"].iloc[-1]), "div": int(f["div"].iloc[-1]),
        "swing_up": swing_up, "swing_dn": swing_dn,
    }


# ── fikir üretimi ──────────────────────────────────────────────────────

def _idea(ctx: dict, mgr: Manager, side: str, sl: float, tp: float, checks: list, core: str, plan: str,
          *, tp1_r: float | None = 1.0, pp: dict | None = None, max_saat: float, min_checks: int,
          entry_type: str = "piyasa", limit_px: float | None = None, limit_bars: int = 0,
          setup: str | None = None, kind: str = "trend", min_rr: float = 1.2, conv_bonus: float = 0.0) -> dict | None:
    px, atr = ctx["price"], ctx["atr"]
    if not (px and atr and atr > 0) or px != px:
        return None
    passed = sum(1 for _, ok in checks if ok)
    if passed < min_checks:
        return None
    sgn = 1 if side == "long" else -1
    ref = limit_px if entry_type == "limit" and limit_px else px
    dist = sgn * (ref - sl)
    if dist <= 0.25 * atr or dist > 5 * atr:
        return None
    if sgn * (tp - ref) < min_rr * dist * (1 - 1e-9):
        return None
    conviction = min(1.0, max(0.0, passed / max(len(checks), 1) * (1 - conv_bonus) + conv_bonus))
    tp1 = ref + sgn * tp1_r * dist if tp1_r else None
    if tp1 is not None and sgn * (tp - tp1) <= 0:
        tp1 = None
    reasons = [f"✓ {name}" for name, ok in checks if ok] + [f"✗ {name}" for name, ok in checks if not ok]
    return {
        "mgr": mgr.id, "inst": ctx["inst"], "sym": ctx["sym"], "tf": ctx["tf"], "side": side,
        "entry": ref, "market_px": px, "sl": sl, "tp": tp, "tp1": tp1, "atr": atr,
        "max_saat": max_saat, "pp": pp or {}, "conviction": round(conviction, 3),
        "checks": [[n, bool(ok)] for n, ok in checks], "core": core, "reasons": [core] + reasons, "plan": plan,
        "entry_type": entry_type, "limit_px": limit_px, "limit_valid_ms": int(limit_bars * ctx["bar_ms"]),
        "setup": setup or mgr.id, "kind": kind, "funding_rate": ctx.get("funding_rate", 0.0),
        "interval_h": ctx.get("interval_h", 8), "regime": ctx.get("regime", 0),
        "rr": round(sgn * (tp - ref) / dist, 2), "tags": [n for n, ok in checks if ok],
    }


def _clamp_stop(entry: float, raw_sl: float, atr: float, side: str, lo: float, hi: float) -> float:
    sgn = 1 if side == "long" else -1
    d = sgn * (entry - raw_sl)
    d = min(max(d, lo * atr), hi * atr)
    return entry - sgn * d


def _aligned(x, side):
    return (x == 1) if side == "long" else (x == -1)


def a_kemal(ctx, mgr, cfg):
    """Swing trader: 4S trend setup'ı + günlük trend; sabırlı, geri çekilmede girer."""
    sigs = [s for s in ctx.get("signals", []) if s["setup"] in ("trend_long", "trend_short") and s["score"] >= 65]
    if not sigs:
        return None
    s = max(sigs, key=lambda x: x["score"])
    side, sgn = s["side"], (1 if s["side"] == "long" else -1)
    px, atr, e20 = ctx["price"], ctx["atr"], ctx["ema20"]
    checks = [("ADX ≥ 25 (güçlü trend)", ctx["adx"] >= 25),
              ("7/14/28 günlük momentum tam uyumlu", abs(ctx.get("tsmom", 0)) >= 0.99 and np.sign(ctx["tsmom"]) == sgn),
              ("yeni para trend yönünde (OI artışı)", (ctx.get("oi_chg24") or 0) > 3 and sgn * (ctx.get("ret24") or 0) > 0),
              ("funding kalabalık değil", (-10 <= (ctx.get("funding_ann") or 0) <= 40) if side == "long" else ((ctx.get("funding_ann") or 0) >= -20)),
              ("BTC rejimi aynı yönde", _aligned(ctx.get("regime", 0), side)),
              ("hacim ortalama üstü", (ctx.get("vol_ratio") or 0) > 1.0)]
    extended = sgn * (px - e20) > 1.2 * atr
    entry_type, limit_px = ("limit", e20 + sgn * 0.3 * atr) if extended else ("piyasa", None)
    ref = limit_px or px
    sl = ref - sgn * 2.0 * atr
    tp = ref + sgn * 4.0 * atr
    plan = (f"Trend {'yukarı' if sgn > 0 else 'aşağı'}; fiyat EMA20'den uzak, geri çekilmede limit emirle gireceğim. " if extended
            else "Trend sağlam ve fiyat aşırı uzamamış, piyasadan giriyorum. ") + \
        "Stop 2 ATR, hedef 4 ATR; +1R'de yarısını alıp kalanı 2,5 ATR iz süren stopla taşıyacağım."
    return _idea(ctx, mgr, side, sl, tp, checks, f"4S {s['label']} {s['score']}/100", plan, tp1_r=1.0,
                 pp={"partial_frac": 0.5, "trail_atr": 2.5, "basabas": True}, max_saat=240, min_checks=2,
                 entry_type=entry_type, limit_px=limit_px, limit_bars=3, setup=s["setup"], conv_bonus=0.15)


def a_deniz(ctx, mgr, cfg):
    """Gün içi trader: 15dk trend içinde RSI geri çekilmesinden dönüşü yakalar; dar stop, hızlı çıkış."""
    px, atr = ctx["price"], ctx["atr"]
    long_core = ctx["ema20"] > ctx["ema50"] and px > ctx["ema50"] and ctx["rsi_min3"] < 45 and ctx["rsi"] > ctx["rsi_prev"] and ctx["close"] > ctx["prev_high"]
    short_core = ctx["ema20"] < ctx["ema50"] and px < ctx["ema50"] and ctx["rsi_max3"] > 55 and ctx["rsi"] < ctx["rsi_prev"] and ctx["close"] < ctx["prev_low"]
    if not (long_core or short_core):
        return None
    side = "long" if long_core else "short"
    sgn = 1 if side == "long" else -1
    checks = [("günlük trend ters değil", ctx.get("d_trend", 0) != -sgn),
              ("ADX > 18", ctx["adx"] > 18),
              ("dönüş mumunda hacim", (ctx.get("vol_ratio") or 0) > 1.1),
              ("agresif alıcı/satıcı aynı yönde", sgn * (ctx.get("taker_imb") or 0) > 0),
              ("funding kalabalık değil", ((ctx.get("funding_ann") or 0) < 30) if side == "long" else ((ctx.get("funding_ann") or 0) > -10)),
              ("BTC rejimi aynı yönde", _aligned(ctx.get("regime", 0), side))]
    raw = ctx["low5"] - 0.3 * atr if side == "long" else ctx["high5"] + 0.3 * atr
    sl = _clamp_stop(px, raw, atr, side, 0.8, 2.0)
    tp = px + sgn * 1.6 * abs(px - sl)
    plan = ("Trend içinde RSI geri çekilmesi bitti, önceki mumun "
            f"{'tepesi' if side == 'long' else 'dibi'} kırıldı. Stop son 5 mumun "
            f"{'dibinin' if side == 'long' else 'tepesinin'} ötesinde; hedef 1,6R, +1R'de stop başabaşa. En fazla 8 saat.")
    return _idea(ctx, mgr, side, sl, tp, checks, "15dk trend içi geri çekilme dönüşü", plan, tp1_r=1.0,
                 pp={"partial_frac": 0.0, "trail_atr": 0.0, "basabas": True}, max_saat=8, min_checks=2, kind="scalp")


def a_ayse(ctx, mgr, cfg):
    """Kontrarian: funding uçta + fiyat aşırı uzamış → kalabalığın tersine, ortalamaya dönüş."""
    fa, px, atr = ctx.get("funding_ann") or 0, ctx["price"], ctx["atr"]
    short_core = fa > 40 and (ctx["bb_pct"] > 1.0 or ctx["rsi"] > 72)
    long_core = fa < -15 and (ctx["bb_pct"] < 0 or ctx["rsi"] < 28)
    if not (short_core or long_core):
        return None
    side = "short" if short_core else "long"
    sgn = 1 if side == "long" else -1
    checks = [("açık pozisyon şişti (OI > %5)", (ctx.get("oi_chg24") or 0) > 5),
              ("RSI uyumsuzluğu", ctx["div"] == sgn),
              ("agresif akış tükeniyor", sgn * (ctx.get("taker_imb") or 0) > 0),
              ("funding çok uçta", (fa > 80) if side == "short" else (fa < -40)),
              ("günlük trend karşı değil", ctx.get("d_trend", 0) != -sgn),
              ("tepe/dip hacmi (≥1,5x)", (ctx.get("vol_ratio") or 0) > 1.5)]
    raw = ctx["high5"] + 0.5 * atr if side == "short" else ctx["low5"] - 0.5 * atr
    sl = _clamp_stop(px, raw, atr, side, 1.0, 3.0)
    d = abs(px - sl)
    tp = ctx["bb_mid"]
    if sgn * (tp - px) < 1.25 * d:
        tp = px + sgn * 1.25 * d
    plan = (f"Funding yıllık %{fa:.0f}: {'longlar' if side == 'short' else 'shortlar'} çok kalabalık ve fiyat aşırı uzamış. "
            "Ortalamaya (Bollinger orta bandı) dönüş bekliyorum. Stop son tepenin/dibin ötesinde; +1R'de yarısını alırım. En fazla 36 saat.")
    return _idea(ctx, mgr, side, sl, tp, checks, f"kalabalık {'long' if side == 'short' else 'short'} + aşırı uzama", plan,
                 tp1_r=1.0, pp={"partial_frac": 0.5, "trail_atr": 1.5, "basabas": True}, max_saat=36, min_checks=2,
                 kind="donus", min_rr=1.2)


def a_murat(ctx, mgr, cfg):
    """Teknik usta: son dalganın %50–%70,5 Fibonacci geri çekilmesinde dönüş onayıyla girer."""
    px, atr = ctx["price"], ctx["atr"]
    for side, sw in (("long", ctx.get("swing_up")), ("short", ctx.get("swing_dn"))):
        if not sw or sw["since"] > 30:
            continue
        H, L = sw["H"], sw["L"]
        rng = H - L
        sgn = 1 if side == "long" else -1
        retr = (H - px) / rng if side == "long" else (px - L) / rng
        trend_ok = (ctx["ema20"] > ctx["ema50"] or ctx.get("d_trend", 0) == 1) if side == "long" else \
                   (ctx["ema20"] < ctx["ema50"] or ctx.get("d_trend", 0) == -1)
        confirm = (ctx["close"] > ctx["open"] and ctx["rsi"] > ctx["rsi_prev"]) if side == "long" else \
                  (ctx["close"] < ctx["open"] and ctx["rsi"] < ctx["rsi_prev"])
        if not (0.5 <= retr <= 0.705 and trend_ok and confirm):
            continue
        lvl786 = H - 0.786 * rng if side == "long" else L + 0.786 * rng
        sl = _clamp_stop(px, lvl786 - sgn * 0.2 * atr, atr, side, 0.6, 3.5)
        tp = H + 0.272 * rng if side == "long" else L - 0.272 * rng
        target1 = H if side == "long" else L
        d = abs(px - sl)
        tp1_r = max(0.8, abs(target1 - px) / d) if d else 1.0
        checks = [("geri çekilmede hacim azaldı", (ctx.get("vol_ratio") or 9) < 1.0),
                  ("RSI aşırı bölgede değil (38–62)", 38 <= ctx["rsi"] <= 62),
                  ("BTC rejimi aynı yönde", _aligned(ctx.get("regime", 0), side)),
                  ("agresif akış aynı yönde", sgn * (ctx.get("taker_imb") or 0) > 0),
                  ("günlük trend aynı yönde", ctx.get("d_trend", 0) == sgn),
                  ("dalga güçlü (≥5 ATR)", sw["size_atr"] >= 5)]
        plan = (f"Son dalga {nfp(L)} → {nfp(H)}; fiyat %{retr * 100:.0f} geri çekildi (altın oran bölgesi) ve dönüş mumu geldi. "
                f"Stop %78,6 seviyesinin ötesinde; ilk hedef dalga {'tepesi' if side == 'long' else 'dibi'} (yarısını alırım), "
                f"ikinci hedef 1,272 uzantısı.")
        return _idea(ctx, mgr, side, sl, tp, checks, f"Fibonacci %{retr * 100:.0f} geri çekilme", plan, tp1_r=tp1_r,
                     pp={"partial_frac": 0.5, "trail_atr": 2.0, "basabas": True},
                     max_saat=96 if ctx["tf"] == "4H" else 48, min_checks=2, min_rr=1.5)
    return None


def a_kaan(ctx, mgr, cfg):
    """Kırılım (agresif): 20 mumluk aralığın hacimle kırılması; kanıt çoksa büyük oynar."""
    px, atr = ctx["price"], ctx["atr"]
    long_core = ctx["close"] > ctx["don_hi"] and (ctx.get("vol_ratio") or 0) > 1.8 and ctx["close"] > ctx["ema50"]
    short_core = ctx["close"] < ctx["don_lo"] and (ctx.get("vol_ratio") or 0) > 1.8 and ctx["close"] < ctx["ema50"]
    if not (long_core or short_core):
        return None
    side = "long" if long_core else "short"
    sgn = 1 if side == "long" else -1
    level = ctx["don_hi"] if side == "long" else ctx["don_lo"]
    fa = ctx.get("funding_ann") or 0
    checks = [("ADX yükseliyor (> 20)", ctx["adx"] > 20 and ctx["adx"] > ctx["adx_prev"]),
              ("yeni para giriyor (OI > %3)", (ctx.get("oi_chg24") or 0) > 3),
              ("funding kalabalık değil", (-5 < fa < 40) if side == "long" else (-30 < fa < 5)),
              ("BTC rejimi aynı yönde", _aligned(ctx.get("regime", 0), side)),
              ("agresif alıcı/satıcı baskın", sgn * (ctx.get("taker_imb") or 0) > 0.05),
              ("günlük trend aynı yönde", ctx.get("d_trend", 0) == sgn),
              ("sıkışmadan çıkış", (ctx.get("bbw_rank") or 1) < 0.6 or ctx.get("squeeze_prev"))]
    ext = sgn * (px - level) / atr
    entry_type, limit_px = ("limit", level + sgn * 0.25 * atr) if ext > 1.0 else ("piyasa", None)
    ref = limit_px or px
    sl = _clamp_stop(ref, level - sgn * 0.8 * atr, atr, side, 1.0, 3.0)
    tp = ref + sgn * 3.0 * abs(ref - sl)
    plan = (f"20 mumluk aralık {nfp(level)} {'yukarı' if sgn > 0 else 'aşağı'} {ctx.get('vol_ratio', 0):.1f}x hacimle kırıldı. "
            + ("Fiyat kırılımdan uzaklaştı, seviyeye geri testte limit emirle gireceğim. " if entry_type == "limit" else "Momentum taze, piyasadan giriyorum. ")
            + "Fiyat aralığa geri dönerse kırılım bozulmuş demektir, stop orada. Hedef 3R; 1,5R'de %30 alıp kalanı 2 ATR iz süren stopla taşırım.")
    return _idea(ctx, mgr, side, sl, tp, checks, f"{ctx['tf']} aralık kırılımı", plan, tp1_r=1.5,
                 pp={"partial_frac": 0.3, "trail_atr": 2.0, "basabas": True}, max_saat=120, min_checks=3,
                 entry_type=entry_type, limit_px=limit_px, limit_bars=4, min_rr=2.0)


def a_selin(ctx, mgr, cfg):
    """Squeeze (agresif): volatilite en dar seviyeden patlarken, yanlış taraftaki kalabalığın sıkışmasını oynar."""
    px, atr = ctx["price"], ctx["atr"]
    if not ctx.get("squeeze_prev") or (ctx.get("vol_ratio") or 0) <= 1.5:
        return None
    if ctx["bb_pct"] > 1:
        side = "long"
    elif ctx["bb_pct"] < 0:
        side = "short"
    else:
        return None
    sgn = 1 if side == "long" else -1
    fa, oi = ctx.get("funding_ann") or 0, ctx.get("oi_chg24") or 0
    checks = [("yanlış tarafta kalabalık var (sıkışma yakıtı)", (fa < 5 and oi > 0) if side == "long" else (fa > 30 and oi > 0)),
              ("agresif akış aynı yönde", sgn * (ctx.get("taker_imb") or 0) > 0),
              ("günlük trend ters değil", ctx.get("d_trend", 0) != -sgn),
              ("BTC rejimi aynı yönde", _aligned(ctx.get("regime", 0), side)),
              ("ADX yükseliyor", ctx["adx"] > ctx["adx_prev"]),
              ("güçlü kapanış", ctx["close_pos"] > 0.7 if side == "long" else ctx["close_pos"] < 0.3)]
    sl = _clamp_stop(px, ctx["bb_mid"] - sgn * 0.2 * atr, atr, side, 0.8, 2.5)
    tp = px + sgn * 2.5 * abs(px - sl)
    plan = ("Bollinger bantları son 120 mumun en dar %15'indeydi ve şimdi "
            f"{ctx.get('vol_ratio', 0):.1f}x hacimle {'yukarı' if sgn > 0 else 'aşağı'} patladı. "
            "Stop orta bandın ötesinde; hedef 2,5R, +1R'de yarısını alıp kalanı 1,5 ATR iz süren stopla taşırım.")
    return _idea(ctx, mgr, side, sl, tp, checks, "volatilite sıkışmasından patlama", plan, tp1_r=1.0,
                 pp={"partial_frac": 0.5, "trail_atr": 1.5, "basabas": True},
                 max_saat=24 if ctx["tf"] == "15m" else 48, min_checks=2, min_rr=2.0, kind="squeeze")


def nfp(p: float) -> str:
    if p is None or p != p:
        return "-"
    if p >= 1000:
        return f"{p:,.0f}".replace(",", ".")
    if p >= 1:
        return f"{p:.3f}".rstrip("0").rstrip(".")
    return f"{p:.6f}".rstrip("0")


ROSTER = [
    Manager("kemal", "Kemal", "Swing Trader", "🧭", "4 saatlik ve günlük trendleri takip eder, günlerce tutar, geri çekilmede girer.",
            ["4H"], 1.5, 2.5, 20, analyze=a_kemal),
    Manager("deniz", "Deniz", "Gün İçi Trader", "⚡", "15 dakikalıkta trend içi geri çekilmeleri yakalar; dar stop, hızlı çıkış, en fazla 8 saat.",
            ["15m"], 1.5, 2.5, 20, analyze=a_deniz),
    Manager("ayse", "Ayşe", "Kontrarian", "🔄", "Funding uçtayken ve fiyat aşırı uzamışken kalabalığın tersine oynar.",
            ["1H"], 1.5, 2.5, 20, analyze=a_ayse),
    Manager("murat", "Murat", "Teknik Analist", "📐", "Son dalganın Fibonacci altın oran bölgesindeki geri çekilmesinde dönüş onayıyla girer.",
            ["1H", "4H"], 1.5, 2.5, 20, analyze=a_murat),
    Manager("elif", "Elif", "Piyasa-Nötr", "⚖️", "Yön tahmini yapmaz: spot alıp vadelide short açar, istikrarlı funding'i toplar.",
            [], 0.0, 0.0, 10, analyze=None, kind="carry"),
    Manager("kaan", "Kaan", "Kırılım Avcısı (agresif)", "🚀", "Aralık kırılımlarını hacim ve yeni para girişiyle teyit eder; kanıt çoksa büyük oynar.",
            ["1H", "4H"], 3.0, 5.0, 30, aggressive=True, analyze=a_kaan),
    Manager("selin", "Selin", "Squeeze Avcısı (agresif)", "🌪", "Volatilite aylardır en dar seviyeden patlarken yanlış taraftaki kalabalığın sıkışmasını oynar.",
            ["15m", "1H"], 3.0, 5.0, 30, aggressive=True, analyze=a_selin),
]
BY_ID = {m.id: m for m in ROSTER}


def roster(cfg: dict) -> list[Manager]:
    """config.yaml 'ekip' bölümündeki değişikliklerle (aktif/pasif, risk) kadroyu döner."""
    over = (cfg.get("ekip") or {}).get("yoneticiler") or {}
    out = []
    for m in ROSTER:
        o = over.get(m.id) or {}
        if o.get("aktif", True) is False:
            continue
        mm = Manager(**{**m.__dict__})
        for k_src, k_dst in (("taban_risk", "base_risk"), ("tavan_risk", "max_risk"), ("yedek_dusus", "dd_bench")):
            if o.get(k_src) is not None:
                setattr(mm, k_dst, float(o[k_src]))
        if o.get("periyotlar"):
            mm.tfs = list(o["periyotlar"])
        out.append(mm)
    return out


def ideas_for(ctx: dict, managers: list[Manager], cfg: dict) -> list[dict]:
    out = []
    for m in managers:
        if m.analyze is None or ctx["tf"] not in m.tfs:
            continue
        try:
            idea = m.analyze(ctx, m, cfg)
        except Exception as e:  # noqa: BLE001 — bir yöneticinin hatası diğerlerini durdurmasın
            print(f"[ekip] {m.id} {ctx.get('sym')} {ctx.get('tf')}: {e}")
            idea = None
        if idea:
            out.append(idea)
    return out
