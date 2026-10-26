"""Sinyal motoru v3 — "setup" bazlı puanlama.

Orijinal prompt'taki v2 motoru 7 göstergeyi tek bir net skorda topluyordu.
Sorun: trend (SuperTrend) ile ortalamaya dönüş (RSI<30, Bollinger) birbirine
zıt şeyler söyler ve toplandıklarında birbirini götürür → gürültü. Backtest'in
Sharpe -0.7 çıkmasının muhtemel nedenlerinden biri bu.

v3 bunun yerine 4 ayrı senaryo (setup) tanımlar ve her birini kendi mantığıyla
0–100 arası puanlar:
  trend_long / trend_short     → trende katıl (araştırmada en güçlü kanıt)
  donus_long / donus_short     → aşırılaşmış hareketin dönüşü (kontra)

Her setup'ın gerekli şartı (gate) + artı/eksi puanlı parçaları vardır.
Parçalar hem tek değer (canlı tarama) hem numpy dizisi (backtest) üzerinde
aynı kodla çalışır, böylece canlı ve backtest mantığı birebir aynı kalır.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from . import indicators as ind

SETUP_LABELS = {
    "trend_long": "Trend Long",
    "trend_short": "Trend Short",
    "donus_long": "Dönüş Long",
    "donus_short": "Dönüş Short",
}


@dataclass
class Part:
    tag: str
    cond: Callable
    pts: int
    text: str


@dataclass
class Setup:
    key: str
    side: str          # long / short
    kind: str          # trend / donus
    gate: Callable
    base: int
    parts: list[Part] = field(default_factory=list)


def _b(x):
    return np.asarray(x, dtype=bool)


def build_setups(cfg: dict) -> list[Setup]:
    g, fu = cfg["gostergeler"], cfg["funding"]
    adx_min = g["adx_trend_min"]
    f_long, f_short, f_hot = fu["long_esik_yillik"], fu["short_esik_yillik"], fu["trend_asiri_yillik"]

    def oi(f, sgn_oi, sgn_px):
        o, r = f["oi_chg24"], f["ret24"]
        c_oi = (o > 3) if sgn_oi > 0 else (o < -3)
        c_px = (r > 0.01) if sgn_px > 0 else (r < -0.01)
        return _b(c_oi) & _b(c_px)

    trend_long = Setup("trend_long", "long", "trend",
        gate=lambda f: _b(f["st_dir"] == 1) & _b(f["d_trend"] == 1) & _b(f["tsmom"] > 0),
        base=40, parts=[
            Part("adx", lambda f: f["adx"] >= adx_min, 12, "ADX {adx:.0f}: trend güçlü"),
            Part("ema", lambda f: f["ema_f"] > f["ema_s"], 8, "EMA20 > EMA50"),
            Part("rsi_ok", lambda f: _b(f["rsi"] >= 40) & _b(f["rsi"] <= 65), 10,
                 "RSI {rsi:.0f}: şişmemiş, giriş için uygun"),
            Part("rsi_hot", lambda f: f["rsi"] > 75, -15, "RSI {rsi:.0f}: aşırı alım, geç kalınmış olabilir"),
            Part("tsmom_full", lambda f: f["tsmom"] >= 0.99, 8, "7/14/28 günlük momentum hepsi pozitif"),
            Part("oi_build", lambda f: oi(f, +1, +1), 10, "OI %{oi_chg24:+.1f}: yükselişe yeni para giriyor"),
            Part("oi_cover", lambda f: oi(f, -1, +1), -5, "Yükseliş short kapatmadan (OI düşüyor)"),
            Part("fund_ok", lambda f: _b(f["funding_ann"] >= -5) & _b(f["funding_ann"] <= 30), 8,
                 "Funding %{funding_ann:.0f}/yıl: longlar kalabalık değil"),
            Part("fund_hot", lambda f: f["funding_ann"] > f_hot, -15,
                 "Funding %{funding_ann:.0f}/yıl: longlar çok kalabalık"),
            Part("taker", lambda f: f["taker_imb"] > 0.03, 8, "Agresif alıcılar baskın"),
            Part("div_bear", lambda f: f["div"] == -1, -10, "Ayı uyumsuzluğu: momentum zayıflıyor"),
            Part("vol", lambda f: f["rel_vol"] > 1.3, 5, "Hacim ortalamanın üstünde"),
        ])

    trend_short = Setup("trend_short", "short", "trend",
        gate=lambda f: _b(f["st_dir"] == -1) & _b(f["d_trend"] == -1) & _b(f["tsmom"] < 0),
        base=40, parts=[
            Part("adx", lambda f: f["adx"] >= adx_min, 12, "ADX {adx:.0f}: düşüş trendi güçlü"),
            Part("ema", lambda f: f["ema_f"] < f["ema_s"], 8, "EMA20 < EMA50"),
            Part("rsi_ok", lambda f: _b(f["rsi"] >= 35) & _b(f["rsi"] <= 60), 10,
                 "RSI {rsi:.0f}: dibe vurmamış, giriş için uygun"),
            Part("rsi_cold", lambda f: f["rsi"] < 25, -15, "RSI {rsi:.0f}: aşırı satım, tepki riski"),
            Part("tsmom_full", lambda f: f["tsmom"] <= -0.99, 8, "7/14/28 günlük momentum hepsi negatif"),
            Part("oi_build", lambda f: oi(f, +1, -1), 10, "OI %{oi_chg24:+.1f}: düşüşe yeni short parası"),
            Part("oi_liq", lambda f: oi(f, -1, -1), -5, "Düşüş long tasfiyesinden (OI düşüyor)"),
            Part("fund_ok", lambda f: f["funding_ann"] >= -5, 8,
                 "Funding %{funding_ann:.0f}/yıl: shortlar kalabalık değil"),
            Part("fund_cold", lambda f: f["funding_ann"] < -30, -15,
                 "Funding %{funding_ann:.0f}/yıl: shortlar kalabalık, squeeze riski"),
            Part("taker", lambda f: f["taker_imb"] < -0.03, 8, "Agresif satıcılar baskın"),
            Part("div_bull", lambda f: f["div"] == 1, -10, "Boğa uyumsuzluğu: düşüş yavaşlıyor"),
            Part("vol", lambda f: f["rel_vol"] > 1.3, 5, "Hacim ortalamanın üstünde"),
        ])

    donus_long = Setup("donus_long", "long", "donus",
        gate=lambda f: _b(f["rsi"] < 32) | _b(f["bb_pct"] < 0),
        base=35, parts=[
            Part("div", lambda f: f["div"] == 1, 20, "Boğa uyumsuzluğu (fiyat dip yaptı, RSI yapmadı)"),
            Part("rsi_deep", lambda f: f["rsi"] < 25, 5, "RSI {rsi:.0f}: derin aşırı satım"),
            Part("bb", lambda f: f["bb_pct"] < 0, 5, "Bollinger alt bandının altında"),
            Part("fund_neg", lambda f: f["funding_ann"] < f_long, 15,
                 "Funding %{funding_ann:.0f}/yıl: shortlar ödüyor, kalabalık"),
            Part("oi_short", lambda f: oi(f, +1, -1), 10, "Düşüşte OI artıyor: short sıkışması yakıtı"),
            Part("cvd", lambda f: f["cvd_div"] == 1, 10, "Fiyat düşerken alıcı akışı pozitif (CVD uyumsuzluğu)"),
            Part("uptrend", lambda f: f["d_trend"] == 1, 10, "Günlük trend yukarı: düşüşte alım fırsatı"),
            Part("downtrend", lambda f: f["d_trend"] == -1, -10, "Günlük trend aşağı: düşen bıçak riski"),
        ])

    donus_short = Setup("donus_short", "short", "donus",
        gate=lambda f: _b(f["rsi"] > 68) | _b(f["bb_pct"] > 1),
        base=35, parts=[
            Part("div", lambda f: f["div"] == -1, 20, "Ayı uyumsuzluğu (fiyat tepe yaptı, RSI yapmadı)"),
            Part("rsi_hot", lambda f: f["rsi"] > 75, 5, "RSI {rsi:.0f}: aşırı alım"),
            Part("bb", lambda f: f["bb_pct"] > 1, 5, "Bollinger üst bandının üstünde"),
            Part("fund_pos", lambda f: f["funding_ann"] > f_short, 15,
                 "Funding %{funding_ann:.0f}/yıl: longlar ödüyor, kalabalık"),
            Part("oi_long", lambda f: oi(f, +1, +1), 10, "Yükselişte OI şişiyor: geç kalan longlar"),
            Part("cvd", lambda f: f["cvd_div"] == -1, 10, "Fiyat çıkarken satıcı akışı baskın (CVD uyumsuzluğu)"),
            Part("downtrend", lambda f: f["d_trend"] == -1, 10, "Günlük trend aşağı: yükselişte satış fırsatı"),
            Part("uptrend", lambda f: f["d_trend"] == 1, -10, "Günlük trend yukarı: trende karşı risk"),
        ])
    return [trend_long, trend_short, donus_long, donus_short]


def regime_mult(setup: Setup, regime, cfg: dict):
    """regime: +1 boğa, -1 ayı, 0 bilinmiyor (skaler veya dizi)."""
    rj = cfg["rejim"]
    regime = np.asarray(regime)
    bull, bear = regime == 1, regime == -1
    m = np.ones(regime.shape, dtype=float)
    if setup.key == "trend_long":
        m = np.where(bear, rj["ayi_long_carpan"], m)
    elif setup.key == "donus_long":
        m = np.where(bear, rj["ayi_donus_long_carpan"], m)
    else:  # shortlar
        m = m * rj["short_temel_carpan"]
        m = np.where(bull, m * rj["boga_short_carpan"], m)
    return m


def score_setup(setup: Setup, f: dict, regime, cfg: dict):
    """Döner: (raw, score, tag_matrix). Gate geçmeyen yerlerde NaN."""
    with np.errstate(invalid="ignore"):
        gate = _b(setup.gate(f))
        raw = np.full(gate.shape, float(setup.base))
        hits = []
        for p in setup.parts:
            c = _b(p.cond(f))
            raw = raw + np.where(c, p.pts, 0)
            hits.append(c)
    raw = np.clip(raw, 0, 100)
    raw = np.where(gate, raw, np.nan)
    score = np.where(gate, np.round(raw * regime_mult(setup, regime, cfg)), np.nan)
    return raw, score, hits


# ── özellik hesaplama ──────────────────────────────────────────────────

def frame_features(df4: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """4S mumlarından vektörize göstergeler (backtest ve canlı ortak)."""
    g = cfg["gostergeler"]
    o, h, l, c, v = df4["open"], df4["high"], df4["low"], df4["close"], df4["volume"]
    st_dir, st_line = ind.supertrend(h, l, c, g["supertrend_atr"], g["supertrend_carpan"])
    r = ind.rsi(c, g["rsi"])
    adx_v, _, _ = ind.adx(h, l, c, g["adx"])
    _, bb_up, bb_lo = ind.bollinger(c, g["bollinger"], g["bollinger_std"])
    width = (bb_up - bb_lo).replace(0, np.nan)
    delta = ind.clv_delta(o, h, l, c, v)
    delta24 = delta.rolling(6).sum()
    vol24 = v.rolling(6).sum().replace(0, np.nan)
    ret24 = c.pct_change(6)
    out = pd.DataFrame({
        "close": c,
        "atr": ind.atr(h, l, c, 14),
        "st_dir": st_dir,
        "st_line": st_line,
        "rsi": r,
        "adx": adx_v,
        "bb_pct": (c - bb_lo) / width,
        "ema_f": ind.ema(c, g["ema_hizli"]),
        "ema_s": ind.ema(c, g["ema_yavas"]),
        "div": ind.divergence_series(l, h, r, g["divergence_pivot"], g["divergence_bakis"]),
        "rel_vol": v.rolling(6).mean() / v.rolling(60).mean(),
        "ret24": ret24,
        "taker_imb": delta24 / vol24,       # vekil (gerçek taker verisi canlıda üzerine yazılır)
        "delta24": delta24,
    })
    out["cvd_div"] = np.where((ret24 < -0.01) & (delta24 > 0), 1,
                              np.where((ret24 > 0.01) & (delta24 < 0), -1, 0))
    return out


def daily_features(d1: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    c = d1["close"]
    e = ind.ema(c, cfg["gostergeler"]["gunluk_ema"])
    e20 = ind.ema(c, 20)
    d_trend = np.where((c > e) & (e20 > e), 1, np.where((c < e) & (e20 < e), -1, 0))
    d_trend = np.where(e.isna(), 0, d_trend)
    signs = [np.sign(c / c.shift(n) - 1) for n in (7, 14, 28)]
    tsmom = (signs[0] + signs[1] + signs[2]) / 3.0
    return pd.DataFrame({"d_trend": d_trend, "tsmom": tsmom}, index=d1.index)


def align_daily(f4: pd.DataFrame, fd: pd.DataFrame, bar_hours: int = 4) -> pd.DataFrame:
    """Günlük özellikleri, yalnızca kapanmış günleri kullanarak 4S mumlarına hizalar."""
    left = pd.DataFrame({"t": f4.index + pd.Timedelta(hours=bar_hours)})
    right = fd.copy()
    right["t"] = right.index + pd.Timedelta(days=1)   # günlük mum ertesi gün 00:00'da kapanır
    m = pd.merge_asof(left.sort_values("t"), right.sort_values("t"), on="t", direction="backward")
    m.index = f4.index
    return m[["d_trend", "tsmom"]]


def btc_regime_series(btc_d1: pd.DataFrame) -> pd.Series:
    c = btc_d1["close"]
    e200 = ind.ema(c, 200)
    reg = np.where(e200.isna(), 0, np.where(c > e200, 1, -1))
    return pd.Series(reg, index=btc_d1.index)


# ── canlı tarama: tek coin ────────────────────────────────────────────

def levels(side: str, kind: str, price: float, atr_v: float, cfg: dict) -> dict:
    p = cfg["sinyal"][kind]
    sgn = 1 if side == "long" else -1
    sl = price - sgn * p["stop_atr"] * atr_v
    tp = price + sgn * p["hedef_atr"] * atr_v
    return {
        "entry": price, "sl": sl, "tp": tp,
        "sl_pct": abs(price - sl) / price * 100, "tp_pct": abs(tp - price) / price * 100,
        "max_saat": p["max_saat"],
    }


def evaluate_coin(feat: dict, regime: int, setups: list[Setup], cfg: dict) -> list[dict]:
    """feat: son bar için skaler özellikler. Gate geçen her setup için sinyal döner."""
    out = []
    for s in setups:
        raw, score, hits = score_setup(s, feat, regime, cfg)
        raw, score = float(raw), float(score)
        if score != score:  # NaN → gate geçmedi
            continue
        reasons, tags = [], []
        for p, h in zip(s.parts, hits):
            if bool(h):
                tags.append(p.tag)
                try:
                    txt = p.text.format(**{k: (v if v == v else 0) for k, v in feat.items()
                                           if isinstance(v, (int, float))})
                except (KeyError, ValueError):
                    txt = p.text
                reasons.append(("−" if p.pts < 0 else "+") + " " + txt)
        lv = levels(s.side, s.kind, feat["price"], feat["atr"], cfg)
        out.append({
            "setup": s.key, "label": SETUP_LABELS[s.key], "side": s.side, "kind": s.kind,
            "raw": int(raw), "score": int(score), "reasons": reasons, "tags": tags, **lv,
        })
    return out
