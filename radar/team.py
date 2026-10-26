"""Yönetici kadrosu (v8): her yöntem araştırma bulgularına dayanır, parametreler ÖNCEDEN sabitlenmiştir
(geçmiş veride en iyi ayar aranmaz → aşırı uydurma yok). Canlı ve 5 yıllık test aynı fonksiyonları çağırır.
Kanıt özeti ve kaynaklar: PLAN.md.

  Kemal  günlük trend takibi (50 günlük ortalama + 20/100 trend filtresi, 3 ATR iz süren stop)
  Kaan   günlük 20 günlük zirve kırılımı + hacim (Turtle tarzı, 10 günlük dip çıkışı) — agresif
  Selin  günlük volatilite sıkışmasından patlama — agresif
  Murat  baş analist: herkese coin karnesi + hacim profili desteğinde trend içi geri çekilme
  Ayşe   sadece gerçek aşırılık: uzun vadeli trend içinde kapitülasyon mumu (+ canlıda funding/OI teyidi)
  Deniz  BTC/ETH 15dk, yoğun saatlerde limit emirle (maker) trend içi geri çekilme
  Elif   piyasa-nötr funding (spot + vadeli short), sadece likit coinler
  Short işlemler yalnızca BTC 50 günlük ortalamanın altındayken (araştırma: short sinyalleri tarihsel olarak zayıf).

Eski (v7) açıklama:

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
                "aggressive": self.aggressive, "kind": self.kind, "lev_cap": (self.extra or {}).get("lev_cap", 10)}


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
          setup: str | None = None, kind: str = "trend", min_rr: float = 1.2, conv_bonus: float = 0.0,
          exit_rule: dict | None = None) -> dict | None:
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
        "exit": exit_rule, "signal_close": ctx.get("close"),
    }


def _clamp_stop(entry: float, raw_sl: float, atr: float, side: str, lo: float, hi: float) -> float:
    sgn = 1 if side == "long" else -1
    d = sgn * (entry - raw_sl)
    d = min(max(d, lo * atr), hi * atr)
    return entry - sgn * d


def _aligned(x, side):
    return (x == 1) if side == "long" else (x == -1)


def _ok(x) -> bool:
    return x == x and x is not None


def _live(ctx, key, cond) -> bool:
    """Sadece canlıda bilinen veri (funding, OI, alıcı/satıcı baskısı). Yoksa doğrulama 'tutmadı' sayılır."""
    v = ctx.get(key)
    if v is None or v != v:
        return False
    try:
        return bool(cond(v))
    except Exception:  # noqa: BLE001
        return False


def _shorts_allowed(ctx) -> bool:
    return bool(ctx.get("btc_bear"))


DAY_H = 24


def _short_shadow(idea, who):
    """5 yıllık test: Kaan ve Selin'in short'ları masraf sonrası ~0R (long'lar +0,28R / +0,20R). Short'lar risk bütçesini
    boşa harcamasın diye gölgede (sanal) açılır ve ölçülmeye devam eder."""
    if idea and idea["side"] == "short":
        idea["shadow_only"] = True
        idea["plan"] += f" (Not: {who}'ın short'ları testte masraf sonrası kazandırmadığı için gölgede ölçülüyor.)"
    return idea


def a_kemal(ctx, mgr, cfg):
    """Günlük trend takibi. Kanıt: BTC 50 günlük ortalama Sharpe 1,9 (Grayscale 2012–2023); trend
    kuralları 2022–2024 canlı testinde de çalıştı (Quantpedia). Trend bitince (kapanış 50 günlüğün altı) çıkar."""
    c, atr = ctx["close"], ctx["atr"]
    if not (_ok(ctx["sma100"]) and _ok(atr) and atr > 0):
        return None
    up = c > ctx["sma50"] and ctx["sma20"] > ctx["sma100"]
    dn = c < ctx["sma50"] and ctx["sma20"] < ctx["sma100"]
    long_t = up and (not ctx.get("above50_prev") or c > ctx["hc20"])
    short_t = dn and _shorts_allowed(ctx) and (not ctx.get("below50_prev") or c < ctx["lc20"])
    if not (long_t or short_t):
        return None
    side = "long" if long_t else "short"
    sgn = 1 if side == "long" else -1
    px = ctx["price"]
    sl = px - sgn * 3.0 * atr
    tp = px + 20.0 * atr if side == "long" else max(px - 20.0 * atr, px * 0.15)   # "hedefsiz": trend bitince çıkılır
    fa = ctx.get("funding_ann")
    checks = [("200 günlük ortalamanın doğru tarafında", _ok(ctx["sma200"]) and sgn * (c - ctx["sma200"]) > 0),
              ("50 günlük ortalamanın eğimi trend yönünde", sgn * (ctx["sma50"] - ctx["sma50_prev5"]) > 0),
              ("BTC havası aynı yönde", bool(ctx.get("btc_bull")) if side == "long" else bool(ctx.get("btc_bear"))),
              ("hacim ortalamanın üstünde", (ctx.get("vol_ratio") or 0) > 1.0),
              ("funding kalabalık değil", _live(ctx, "funding_ann", lambda x: x < 40 if side == "long" else x > -20)),
              ("yeni para trend yönünde (OI)", _live(ctx, "oi_chg24", lambda x: x > 0))]
    trig = "trend yeni başladı" if not (ctx.get("above50_prev") if side == "long" else ctx.get("below50_prev")) \
        else "trend içinde 20 günlük yeni kapanış zirvesi" if side == "long" else "trend içinde 20 günlük yeni kapanış dibi"
    plan = (f"Günlük trend {'yukarı' if sgn > 0 else 'aşağı'} ({trig}). Stop 3 ATR; stop fiyatla birlikte en iyi "
            f"seviyenin 3 ATR gerisinden taşınır. Hedef yok: trend bitene kadar (günlük kapanış 50 günlük ortalamanın "
            f"{'altına' if sgn > 0 else 'üstüne'} geçene kadar) tutarım. En fazla 120 gün.")
    return _idea(ctx, mgr, side, sl, tp, checks, "günlük trend (50g ortalama + 20/100 filtresi)", plan, tp1_r=None,
                 pp={"partial_frac": 0.0, "trail_atr": 3.0, "basabas": False, "trail_now": True},
                 max_saat=120 * DAY_H, min_checks=0, setup="kemal", kind="trend", min_rr=1.2,
                 exit_rule={"type": "ma", "n": 50})


def a_kaan(ctx, mgr, cfg):
    """Günlük 20 günlük zirve kırılımı + hacim (Turtle tarzı). Kanıt: kanal kırılımı en iyi teknik kural ailesi
    (Springer 2019), 10–50 günlük zirve kuralı 2022–2024'te çalıştı. 10 günlük dip kırılınca çıkar."""
    c, atr, px = ctx["close"], ctx["atr"], ctx["price"]
    vr = ctx.get("vol_ratio") or 0
    if not (_ok(ctx["hh20"]) and _ok(ctx["sma50"]) and _ok(atr) and atr > 0):
        return None
    long_core = c > ctx["hh20"] and vr >= 1.5 and c > ctx["sma50"]
    short_core = c < ctx["ll20"] and vr >= 1.5 and c < ctx["sma50"] and _shorts_allowed(ctx)
    if not (long_core or short_core):
        return None
    side = "long" if long_core else "short"
    sgn = 1 if side == "long" else -1
    raw = max(ctx["ll10"], px - 2.5 * atr) if side == "long" else min(ctx["hh10"], px + 2.5 * atr)
    sl = _clamp_stop(px, raw, atr, side, 1.0, 2.5)
    tp = px + sgn * 6.0 * abs(px - sl)
    checks = [("100 günlük trend aynı yönde", _ok(ctx["sma100"]) and sgn * (c - ctx["sma100"]) > 0),
              ("güçlü kapanış", ctx["close_pos"] >= 0.7 if side == "long" else ctx["close_pos"] <= 0.3),
              ("BTC havası aynı yönde", bool(ctx.get("btc_bull")) if side == "long" else bool(ctx.get("btc_bear"))),
              ("hacim 2 katın üstünde", vr >= 2.0),
              ("yeni para giriyor (OI)", _live(ctx, "oi_chg24", lambda x: x > 3)),
              ("funding kalabalık değil", _live(ctx, "funding_ann", lambda x: x < 40 if side == "long" else x > -30))]
    plan = (f"20 günlük {'tepe' if sgn > 0 else 'dip'} {nfp(ctx['hh20'] if sgn > 0 else ctx['ll20'])} {vr:.1f}x hacimle kırıldı. "
            f"Stop 10 günlük {'dibin' if sgn > 0 else 'tepenin'} ötesinde (1–2,5 ATR). 2R'de %30 alırım, kalanı 2,5 ATR iz "
            f"süren stopla ve 10 günlük {'dip' if sgn > 0 else 'tepe'} kırılınca çıkarak taşırım. En fazla 30 gün.")
    idea = _idea(ctx, mgr, side, sl, tp, checks, "günlük 20 günlük kanal kırılımı + hacim", plan, tp1_r=2.0,
                 pp={"partial_frac": 0.3, "trail_atr": 2.5, "basabas": True}, max_saat=30 * DAY_H, min_checks=0,
                 setup="kaan", kind="trend", min_rr=2.0, exit_rule={"type": "chan", "n": 10})
    return _short_shadow(idea, "Kaan")


def a_selin(ctx, mgr, cfg):
    """Günlük volatilite sıkışmasından patlama: bantlar 120 günün en dar %20'sinden çıkarken trend yönünde kırılım."""
    c, atr, px = ctx["close"], ctx["atr"], ctx["price"]
    vr = ctx.get("vol_ratio") or 0
    if not (ctx.get("squeeze3") and _ok(ctx["bb_up"]) and _ok(ctx["sma50"]) and _ok(atr) and atr > 0):
        return None
    long_core = c > ctx["bb_up"] and c > ctx["sma50"] and vr >= 1.3
    short_core = c < ctx["bb_lo"] and c < ctx["sma50"] and vr >= 1.3 and _shorts_allowed(ctx)
    if not (long_core or short_core):
        return None
    side = "long" if long_core else "short"
    sgn = 1 if side == "long" else -1
    sl = _clamp_stop(px, ctx["bb_mid"] - sgn * 0.5 * atr, atr, side, 1.0, 2.5)
    tp = px + sgn * 4.0 * abs(px - sl)
    checks = [("20 günlük kanal da kırıldı", sgn * (c - (ctx["hh20"] if sgn > 0 else ctx["ll20"])) > 0),
              ("güçlü kapanış", ctx["close_pos"] >= 0.7 if side == "long" else ctx["close_pos"] <= 0.3),
              ("BTC havası aynı yönde", bool(ctx.get("btc_bull")) if side == "long" else bool(ctx.get("btc_bear"))),
              ("hacim 2 katın üstünde", vr >= 2.0),
              ("yanlış tarafta kalabalık (sıkışma yakıtı)", _live(ctx, "funding_ann", lambda x: x < 5 if side == "long" else x > 30)),
              ("yeni para giriyor (OI)", _live(ctx, "oi_chg24", lambda x: x > 0))]
    plan = ("Bollinger bantları 120 günün en dar %20'sindeydi; fiyat şimdi "
            f"{vr:.1f}x hacimle bandın {'üstünde' if sgn > 0 else 'altında'} kapandı. Stop orta bandın ötesinde; "
            "1,5R'de yarısını alıp kalanı 2 ATR iz süren stopla taşırım. Fiyat orta banda dönerse patlama başarısız sayılır, "
            "çıkarım. En fazla 20 gün.")
    idea = _idea(ctx, mgr, side, sl, tp, checks, "günlük volatilite sıkışmasından patlama", plan, tp1_r=1.5,
                 pp={"partial_frac": 0.5, "trail_atr": 2.0, "basabas": True}, max_saat=20 * DAY_H, min_checks=0,
                 setup="selin", kind="squeeze", min_rr=2.0, exit_rule={"type": "mid"})
    return _short_shadow(idea, "Selin")


def a_murat(ctx, mgr, cfg):
    """Baş analistin kendi işlemi: güçlü günlük trend içinde, hacim profilinin gerçek destek bölgesine (ya da
    EMA20'ye) geri çekilmede dönüş mumuyla girer. Fibonacci tek başına kanıtsız; hacim bölgesi gerçek destektir."""
    c, atr, px = ctx["close"], ctx["atr"], ctx["price"]
    if not (_ok(ctx["sma200"]) and _ok(atr) and atr > 0):
        return None
    levels = [x for x in (ctx.get("hvn") or []) if _ok(x)] + [ctx["ema20"]]
    for side in ("long", "short"):
        sgn = 1 if side == "long" else -1
        if side == "long":
            trend = c > ctx["sma50"] and ctx["sma50"] > ctx["sma50_prev5"] and c > ctx["sma200"]
        else:
            trend = (c < ctx["sma50"] and ctx["sma50"] < ctx["sma50_prev5"] and c < ctx["sma200"]
                     and _shorts_allowed(ctx))
        if not trend:
            continue
        hv_near = [x for x in levels[:-1] if 0 <= sgn * (c - x) <= 3 * atr]      # önce gerçek hacim bölgeleri
        e_near = [ctx["ema20"]] if 0 <= sgn * (c - ctx["ema20"]) <= 3 * atr else []
        near = hv_near or e_near
        if not near:
            continue
        lvl = max(near) if side == "long" else min(near)
        touched = (ctx["low"] <= lvl + 0.5 * atr) if side == "long" else (ctx["high"] >= lvl - 0.5 * atr)
        reversal = ((c > ctx["open"] and ctx["close_pos"] >= 0.6) if side == "long"
                    else (c < ctx["open"] and ctx["close_pos"] <= 0.4))
        rsi_ok = 40 <= ctx["rsi"] <= 65 if side == "long" else 35 <= ctx["rsi"] <= 60
        if not (touched and reversal and rsi_ok):
            continue
        raw = (min(ctx["ll3"], lvl) - 0.5 * atr) if side == "long" else (max(ctx["hh3"], lvl) + 0.5 * atr)
        sl = _clamp_stop(px, raw, atr, side, 1.0, 3.0)
        d = abs(px - sl)
        swing = ctx["hh20"] if side == "long" else ctx["ll20"]
        tp = swing if (_ok(swing) and sgn * (swing - px) >= 1.5 * d) else px + sgn * 2.0 * d
        checks = [("destek gerçek hacim bölgesi (EMA20 değil)", lvl != ctx["ema20"]),
                  ("geri çekilmede hacim azaldı", (ctx.get("vol_ratio") or 9) < 1.0),
                  ("BTC havası aynı yönde", bool(ctx.get("btc_bull")) if side == "long" else bool(ctx.get("btc_bear"))),
                  ("100 günlük trend aynı yönde", _ok(ctx["sma100"]) and sgn * (c - ctx["sma100"]) > 0),
                  ("funding kalabalık değil", _live(ctx, "funding_ann", lambda x: x < 40 if side == "long" else x > -20)),
                  ("alıcı/satıcı baskısı aynı yönde", _live(ctx, "taker_imb", lambda x: sgn * x > 0))]
        plan = (f"Güçlü günlük trend; fiyat {nfp(lvl)} {'destek' if sgn > 0 else 'direnç'} bölgesine geri çekildi ve dönüş mumu "
                f"geldi. Stop bölgenin ve son 3 günün ötesinde; ilk hedef 1R'de yarısı, sonra 20 günlük "
                f"{'tepe' if sgn > 0 else 'dip'} ya da 2R, kalan 2 ATR iz süren stopla. En fazla 20 gün.")
        return _idea(ctx, mgr, side, sl, tp, checks, "trend içi geri çekilme (hacim profili desteği)", plan, tp1_r=1.0,
                     pp={"partial_frac": 0.5, "trail_atr": 2.0, "basabas": True}, max_saat=20 * DAY_H, min_checks=0,
                     setup="murat", kind="trend", min_rr=1.5)
    return None


def a_ayse(ctx, mgr, cfg):
    """Sadece gerçek aşırılık: uzun vadeli yükseliş trendinde kapitülasyon mumu (RSI2 < 5, alt bant dışı, 2x hacim,
    büyük mum). Araştırma 'dipte al' kuralının 2022–24'te çöktüğünü gösterdi; bu yüzden filtre çok sıkı, boyut küçük.
    Canlıda funding/OI/likidasyon teyidi güveni artırır."""
    c, atr, px = ctx["close"], ctx["atr"], ctx["price"]
    vr = ctx.get("vol_ratio") or 0
    if not (_ok(ctx["sma200"]) and _ok(atr) and atr > 0):
        return None
    big = (ctx.get("range_atr") or 0) >= 1.5 and vr >= 2.0
    long_core = big and c > ctx["sma200"] and ctx["rsi2"] < 5 and c < ctx["bb_lo"]
    short_core = big and c < ctx["sma200"] and ctx["rsi2"] > 95 and c > ctx["bb_up"] and _shorts_allowed(ctx)
    if not (long_core or short_core):
        return None
    side = "long" if long_core else "short"
    sgn = 1 if side == "long" else -1
    raw = ctx["low"] - 0.5 * atr if side == "long" else ctx["high"] + 0.5 * atr
    sl = _clamp_stop(px, raw, atr, side, 1.5, 2.5)
    d = abs(px - sl)
    tp = ctx["sma20"] if sgn * (ctx["sma20"] - px) >= 1.2 * d else px + sgn * 1.5 * d
    checks = [("mumda geri dönüş izi (fitil)", ctx["close_pos"] >= 0.3 if side == "long" else ctx["close_pos"] <= 0.7),
              ("BTC havası aynı yönde", bool(ctx.get("btc_bull")) if side == "long" else bool(ctx.get("btc_bear"))),
              ("hacim 3 katın üstünde (zincirleme likidasyon izi)", vr >= 3.0),
              ("kalabalık ters tarafta (funding)", _live(ctx, "funding_ann", lambda x: x < -10 if side == "long" else x > 40)),
              ("açık pozisyon eridi (likidasyon)", _live(ctx, "oi_chg24", lambda x: x < -5)),
              ("satış/alış baskısı tükeniyor", _live(ctx, "taker_imb", lambda x: sgn * x > 0))]
    plan = (f"Uzun vadeli trend {'yukarı' if sgn > 0 else 'aşağı'} ama bugün {vr:.1f}x hacimle aşırı bir "
            f"{'çöküş' if sgn > 0 else 'şişme'} mumu oldu (RSI2 {ctx['rsi2']:.0f}). Kısa vadeli ortalamaya dönüş bekliyorum; "
            "1R'de yarısını alırım, 20 günlük ortalamaya varınca çıkarım. Stop mumun ötesinde. En fazla 7 gün.")
    return _idea(ctx, mgr, side, sl, tp, checks, "kapitülasyon mumu (uzun vadeli trend içinde)", plan, tp1_r=1.0,
                 pp={"partial_frac": 0.5, "trail_atr": 0.0, "basabas": True}, max_saat=7 * DAY_H, min_checks=0,
                 setup="ayse", kind="donus", min_rr=1.2, exit_rule={"type": "mean", "n": 20})


DENIZ_COINS = ("BTC", "ETH")


def a_deniz(ctx, mgr, cfg):
    """BTC/ETH 15dk, sadece piyasa canlıyken: trend içi geri çekilmede limit emirle (maker) girer, hedef de limit emir.
    v9 (kullanıcı kararı): saat kuralı kalktı. Yerine dinamik likidite: son 15 dakikanın hacmi son 24 saatin 15 dakikalık
    ortalamasını aşmalı. Gün içi hareketin kaynağı likidite sağlamak (Shen ve ark. 2022)."""
    if ctx.get("sym") not in DENIZ_COINS:
        return None
    vol, vma = ctx.get("volume"), ctx.get("vol_ma96")
    if not (_ok(vol) and _ok(vma) and vma > 0 and vol > vma):
        return None
    c, atr = ctx["close"], ctx["atr"]
    if not (_ok(ctx["sma200"]) and _ok(atr) and atr > 0):
        return None
    up = c > ctx["sma200"] and ctx["sma50"] > ctx["sma200"] and ctx["rsi"] < 40
    dn = c < ctx["sma200"] and ctx["sma50"] < ctx["sma200"] and ctx["rsi"] > 60
    if not (up or dn):
        return None
    side = "long" if up else "short"
    sgn = 1 if side == "long" else -1
    lim = c - sgn * 0.5 * atr
    sl = lim - sgn * 1.5 * atr
    tp = lim + sgn * 1.5 * atr
    checks = [("günlük trend aynı yönde", bool(ctx.get("d_up")) if side == "long" else bool(ctx.get("d_dn"))),
              ("BTC havası aynı yönde", bool(ctx.get("btc_bull")) if side == "long" else bool(ctx.get("btc_bear"))),
              ("alıcı/satıcı baskısı tükeniyor", _live(ctx, "taker_imb", lambda x: sgn * x > -0.05)),
              ("funding kalabalık değil", _live(ctx, "funding_ann", lambda x: x < 30 if side == "long" else x > -10))]
    plan = (f"Piyasa canlı (son 15 dk hacmi 24 saat ortalamasının {vol / vma:.1f} katı), trend içi geri çekilme (RSI {ctx['rsi']:.0f}). {nfp(lim)} seviyesine limit emir koydum "
            "(maker ücret, kayma yok). Hedef de limit emir: +1,5 ATR; stop 1,5 ATR. 1 saatte dolmazsa iptal, en fazla 8 saat.")
    return _idea(ctx, mgr, side, sl, tp, checks, "15dk trend içi geri çekilme (maker)", plan, tp1_r=None,
                 pp={"partial_frac": 0.0, "trail_atr": 0.0, "basabas": False}, max_saat=8, min_checks=0,
                 entry_type="limit", limit_px=lim, limit_bars=4, setup="deniz", kind="scalp", min_rr=0.9)


# ── Baş analist (Murat): coin karnesi ─────────────────────────────────

def analyst_card(ctx: dict, side: str, news: dict | None = None) -> dict:
    """Her fikre baş analistin görüşü: boyut çarpanı + gerekçeler. Çarpan 0 → işlem açılmaz.
    Ölçüler az ve her biri ayrı ayrı mantıklı: likidite, kalabalık, trend uyumu, haber."""
    mult, notes = 1.0, []
    sgn = 1 if side == "long" else -1
    vol = ctx.get("vol24_usd") or 0
    if vol and vol < 20_000_000:
        mult *= 0.5
        notes.append("likidite zayıf (24s hacim < 20M$)")
    fa = ctx.get("funding_ann")
    if fa is not None and fa == fa and ((side == "long" and fa > 60) or (side == "short" and fa < -40)):
        mult *= 0.7
        notes.append(f"kalabalık aynı tarafta (funding yıllık %{fa:.0f})")
    if ctx.get("tf") != "1D":
        d_up, d_dn = ctx.get("d_up"), ctx.get("d_dn")
        if (side == "long" and d_dn) or (side == "short" and d_up):
            mult *= 0.6
            notes.append("günlük trend ters")
    if news:
        if news.get("level") == "kirmizi":
            mult = 0.0
            notes.append(f"Haber Masası kırmızı alarm: {news.get('title', '')[:80]}")
        elif news.get("level") == "sari" and side == "long":
            mult = 0.0
            notes.append("yeni listeleme şişkinliği (araştırma: 1–2 haftada geri veriliyor)")
    return {"mult": round(mult, 3), "notes": notes}


def nfp(p: float) -> str:
    if p is None or p != p:
        return "-"
    if p >= 1000:
        return f"{p:,.0f}".replace(",", ".")
    if p >= 1:
        return f"{p:.3f}".rstrip("0").rstrip(".")
    return f"{p:.6f}".rstrip("0")


ROSTER = [
    Manager("kemal", "Kemal", "Trend Yöneticisi (çekirdek)", "🧭",
            "Günlük trend takibi: 50 günlük ortalama + 20/100 trend filtresi. Trend bitene kadar tutar, stop 3 ATR geriden izler.",
            ["1D"], 1.5, 2.5, 20, analyze=a_kemal, extra={"lev_cap": 10}),
    Manager("deniz", "Deniz", "Likidite Sağlayıcı", "⚡",
            "BTC/ETH'de yoğun saatlerde trend içi geri çekilmeye limit emirle (maker) girer; dar stop, en fazla 8 saat.",
            ["15m"], 1.0, 1.5, 15, analyze=a_deniz, extra={"lev_cap": 20}),
    Manager("ayse", "Ayşe", "Aşırılık Avcısı", "🔄",
            "Sadece gerçek aşırılıklarda: uzun vadeli trend içinde kapitülasyon mumu; canlıda funding/OI teyidi arar.",
            ["1D"], 1.0, 1.5, 15, analyze=a_ayse, extra={"lev_cap": 10}),
    Manager("murat", "Murat", "Baş Analist", "📐",
            "Her fikre coin karnesi verir (likidite, kalabalık, trend uyumu, haber). Kendisi hacim profili desteğinde trend içi geri çekilmeyi oynar.",
            ["1D"], 1.5, 2.5, 20, analyze=a_murat, extra={"lev_cap": 10}),
    Manager("elif", "Elif", "Piyasa-Nötr", "⚖️",
            "Yön tahmini yapmaz: likit coinlerde spot alıp vadelide short açar, funding masrafı aşacak kadar yüksekken toplar.",
            [], 0.0, 0.0, 10, analyze=None, kind="carry", extra={"lev_cap": 1}),
    Manager("kaan", "Kaan", "Kırılım Avcısı (agresif)", "🚀",
            "Günlük 20 günlük zirve kırılımı + hacim (Turtle tarzı); 10 günlük dip kırılınca çıkar. Kanıt çoksa büyük oynar.",
            ["1D"], 3.0, 5.0, 30, aggressive=True, analyze=a_kaan, extra={"lev_cap": 20}),
    Manager("selin", "Selin", "Sıkışma Avcısı (agresif)", "🌪",
            "Günlük volatilite 120 günün en dar seviyesinden patlarken trend yönünde girer; patlama başarısızsa hemen çıkar.",
            ["1D"], 3.0, 5.0, 30, aggressive=True, analyze=a_selin, extra={"lev_cap": 20}),
    Manager("ortak", "Ortak Masa", "Konsensüs Masası", "🤝",
            "En az iki yönetici aynı coinde aynı yönde hemfikir olunca kendi bütçesiyle ortak işlem açar; "
            "yöneticilerin kendi işlemlerine dokunmaz. Hemfikir sayısı arttıkça büyür, uzlaşı dağılınca çıkar.",
            ["1D"], 2.0, 4.0, 25, analyze=None, kind="consensus", extra={"lev_cap": 10}),
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


LEARN_BOUNDS = {"stop_k": (0.7, 1.5), "trail_k": (0.7, 1.5)}


def learned_for(cfg: dict | None, mid: str) -> dict:
    L = (((cfg or {}).get("_learned") or {}).get("managers") or {}).get(mid) or {}
    out = {}
    for k, (lo, hi) in LEARN_BOUNDS.items():
        try:
            out[k] = float(min(max(float(L.get(k, 1.0)), lo), hi))
        except (TypeError, ValueError):
            out[k] = 1.0
    return out


def apply_learned(idea: dict | None, cfg: dict | None) -> dict | None:
    """Öğrenme koşusunun bulduğu (sınırlı) ayarları fikre uygular: stop mesafesi ve iz süren stop katsayısı.
    Hedefler R cinsinden korunur. Canlı ve test aynı fonksiyonu kullanır."""
    if not idea:
        return idea
    L = learned_for(cfg, idea["mgr"])
    ks, kt = L["stop_k"], L["trail_k"]
    if ks == 1.0 and kt == 1.0:
        return idea
    sgn = 1 if idea["side"] == "long" else -1
    ref = idea["entry"]
    if ks != 1.0:
        d = abs(ref - idea["sl"]) * ks
        idea["sl"] = ref - sgn * d
        tp = ref + (idea["tp"] - ref) * ks
        idea["tp"] = tp if sgn > 0 else max(tp, ref * 0.05)
        if idea.get("tp1") is not None:
            idea["tp1"] = ref + (idea["tp1"] - ref) * ks
    if kt != 1.0 and idea.get("pp"):
        idea["pp"] = {**idea["pp"], "trail_atr": round(idea["pp"].get("trail_atr", 0) * kt, 3)}
    idea["learned"] = {"stop_k": ks, "trail_k": kt}
    idea["reasons"] = idea.get("reasons", []) + [f"öğrenilmiş ayar: stop ×{ks:g}, iz stop ×{kt:g}"]
    return idea


def ideas_for(ctx: dict, managers: list[Manager], cfg: dict) -> list[dict]:
    out = []
    for m in managers:
        if m.analyze is None or ctx["tf"] not in m.tfs:
            continue
        try:
            idea = apply_learned(m.analyze(ctx, m, cfg), cfg)
        except Exception as e:  # noqa: BLE001 — bir yöneticinin hatası diğerlerini durdurmasın
            print(f"[ekip] {m.id} {ctx.get('sym')} {ctx.get('tf')}: {e}")
            idea = None
        if idea:
            out.append(idea)
    return out
