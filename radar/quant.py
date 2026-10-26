"""Ortak veri masası (v8): canlı sistem ve 5 yıllık test AYNI kodu kullanır.

Neden tek kod? Canlıda çalışan kural ile testte ölçülen kural farklı olursa test hiçbir şey kanıtlamaz.
Burada:
  features(df)      → mum serisinden tüm göstergeler (vektörel, geleceğe bakmadan: kanal seviyeleri bir önceki
                       mumdan hesaplanır)
  ctx_at(F, i)      → i. mum kapanışındaki bilgi (yöneticilerin gördüğü tek şey)
  hvn_levels(F, i)  → hacim profili: son 120 mumda en çok işlem gören fiyat bölgeleri (gerçek destek/direnç)
  exit_signal(...)  → kural tabanlı çıkışlar (trend bitti, kanal kırıldı...)
  btc_weather(B, i) → piyasa havası (BTC trend + oynaklık rejimi)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind


def features(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c, v = (df[x].astype(float) for x in ("open", "high", "low", "close", "volume"))
    F = pd.DataFrame(index=df.index)
    F["open"], F["high"], F["low"], F["close"], F["volume"] = o, h, l, c, v
    F["atr"] = ind.atr(h, l, c, 14)
    for n in (20, 50, 100, 200):
        F[f"sma{n}"] = c.rolling(n, min_periods=n).mean()
    F["ema20"] = ind.ema(c, 20)
    F["sma50_prev5"] = F["sma50"].shift(5)
    # kanallar: bir ÖNCEKİ mumda biten pencere (bugünün kırılımını bugünün tepesiyle ölçmemek için)
    for n in (10, 20, 50):
        F[f"hh{n}"] = h.shift(1).rolling(n, min_periods=n).max()
        F[f"ll{n}"] = l.shift(1).rolling(n, min_periods=n).min()
    F["hc20"] = c.shift(1).rolling(20, min_periods=20).max()      # önceki 20 mumun en yüksek kapanışı
    F["lc20"] = c.shift(1).rolling(20, min_periods=20).min()
    F["vol_ma20"] = v.shift(1).rolling(20, min_periods=20).mean()
    F["vol_ratio"] = v / F["vol_ma20"]
    F["rsi"] = ind.rsi(c, 14)
    F["rsi2"] = ind.rsi(c, 2)
    mid, up, lo = ind.bollinger(c, 20, 2.0)
    F["bb_mid"], F["bb_up"], F["bb_lo"] = mid, up, lo
    F["bbw"] = (up - lo) / mid.replace(0, np.nan)
    F["bbw_rank"] = F["bbw"].rolling(120, min_periods=60).rank(pct=True)
    F["squeeze3"] = (F["bbw_rank"].shift(1).rolling(3, min_periods=1).min() <= 0.20)
    rng = (h - l).replace(0, np.nan)
    F["close_pos"] = (c - l) / rng
    F["range_atr"] = (h - l) / F["atr"]
    F["ret1"] = c.pct_change()
    F["rv20"] = F["ret1"].rolling(20, min_periods=20).std()
    F["rv_rank"] = F["rv20"].rolling(365, min_periods=120).rank(pct=True)
    F["prev_high"], F["prev_low"] = h.shift(1), l.shift(1)
    return F


def _f(x, default=float("nan")) -> float:
    try:
        x = float(x)
        return x if x == x else default
    except (TypeError, ValueError):
        return default


def ctx_at(F: pd.DataFrame, i: int = -1) -> dict:
    """i. mumun kapanışındaki tüm bilgi (skaler). Canlıda i = -1 (son kapanmış mum)."""
    row = F.iloc[i]
    ctx = {k: _f(row[k]) for k in F.columns if k != "squeeze3"}
    ctx["squeeze3"] = bool(row["squeeze3"]) if row["squeeze3"] == row["squeeze3"] else False
    prev = F.iloc[i - 1] if (i - 1) >= -len(F) and len(F) > 1 else row
    ctx["above50_prev"] = bool(_f(prev["close"]) > _f(prev["sma50"]) and _f(prev["sma20"]) > _f(prev["sma100"]))
    ctx["below50_prev"] = bool(_f(prev["close"]) < _f(prev["sma50"]) and _f(prev["sma20"]) < _f(prev["sma100"]))
    ctx["ll3"] = float(F["low"].iloc[i - 2:i + 1 if i != -1 else None].min()) if len(F) >= 3 else ctx["low"]
    ctx["hh3"] = float(F["high"].iloc[i - 2:i + 1 if i != -1 else None].max()) if len(F) >= 3 else ctx["high"]
    return ctx


def hvn_levels(F: pd.DataFrame, i: int = -1, look: int = 120, bins: int = 40, top: int = 4) -> list[float]:
    """Hacim profili: son `look` mumda hacmin en çok yığıldığı fiyat bölgeleri (orta noktaları)."""
    end = len(F) + i + 1 if i < 0 else i + 1
    start = max(0, end - look)
    w = F.iloc[start:end]
    if len(w) < 30:
        return []
    tp = ((w["high"] + w["low"] + w["close"]) / 3).to_numpy(float)
    vol = w["volume"].to_numpy(float)
    lo, hi = float(w["low"].min()), float(w["high"].max())
    if not (hi > lo):
        return []
    hist, edges = np.histogram(tp, bins=bins, range=(lo, hi), weights=vol)
    order = np.argsort(hist)[::-1]
    mids = (edges[:-1] + edges[1:]) / 2
    out = []
    for k in order:
        if hist[k] <= 0:
            break
        # komşu kutuları ayrı seviye sayma
        if all(abs(mids[k] - x) > (hi - lo) / bins * 1.5 for x in out):
            out.append(float(mids[k]))
        if len(out) >= top:
            break
    return out


def btc_weather(B: pd.DataFrame, i: int = -1) -> dict:
    """Piyasa havası: BTC günlük trend (50 günlük ortalama ve eğimi) + oynaklık rejimi."""
    r = B.iloc[i]
    c, s50, s50p = _f(r["close"]), _f(r["sma50"]), _f(r["sma50_prev5"])
    rvr = _f(r["rv_rank"], 0.5)
    if c > s50 and s50 >= s50p:
        trend = "boga"
    elif c < s50 and s50 <= s50p:
        trend = "ayi"
    else:
        trend = "yatay"
    return {"trend": trend, "btc_bear": bool(c < s50), "btc_bull": bool(c > s50),
            "high_vol": bool(rvr >= 0.80), "rv_rank": round(rvr, 2)}


# Piyasa havasına göre risk çarpanı (testte açık/kapalı karşılaştırılır)
def weather_mult(w: dict | None, mgr: str, side: str) -> float:
    if not w:
        return 1.0
    m = 1.0
    if w["trend"] == "ayi" and side == "long" and mgr != "ayse":
        m *= 0.5
    if w["trend"] == "boga" and side == "short":
        m *= 0.5
    if w["trend"] == "yatay" and mgr in ("kaan", "selin"):
        m *= 0.5
    if w.get("high_vol"):
        m *= 0.75
    return m


def exit_signal(rule: dict | None, ctx: dict, side: str) -> str | None:
    """Kural tabanlı çıkış (mum kapanışında). Döner: sebep ya da None."""
    if not rule:
        return None
    sgn = 1 if side == "long" else -1
    c = ctx.get("close")
    t = rule.get("type")
    if t == "ma":                       # trend bitti: kapanış N günlük ortalamanın ters tarafında
        ma = ctx.get(f"sma{rule['n']}")
        if ma == ma and ma and sgn * (c - ma) < 0:
            return f"kapanış {rule['n']} günlük ortalamanın {'altında' if sgn > 0 else 'üstünde'} (trend bitti)"
    elif t == "chan":                   # kanal çıkışı (Turtle): N günlük dip/tepe kırıldı
        lvl = ctx.get(f"ll{rule['n']}") if sgn > 0 else ctx.get(f"hh{rule['n']}")
        if lvl == lvl and lvl and sgn * (c - lvl) < 0:
            return f"{rule['n']} günlük {'dip' if sgn > 0 else 'tepe'} kırıldı"
    elif t == "mid":                    # sıkışma patlaması başarısız: orta banda geri döndü
        mid = ctx.get("bb_mid")
        if mid == mid and mid and sgn * (c - mid) < 0:
            return "fiyat orta banda geri döndü (patlama başarısız)"
    elif t == "mean":                   # ortalamaya dönüş tamamlandı
        ma = ctx.get(f"sma{rule.get('n', 20)}")
        if ma == ma and ma and sgn * (c - ma) >= 0:
            return "ortalamaya dönüş tamamlandı"
    return None


def corr(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    if n < 20:
        return 0.0
    x, y = np.asarray(a[-n:], float), np.asarray(b[-n:], float)
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 20 or np.std(x[m]) == 0 or np.std(y[m]) == 0:
        return 0.0
    return float(np.corrcoef(x[m], y[m])[0, 1])
