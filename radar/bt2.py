"""v8 test altyapısı: yöneticilerin CANLI fonksiyonlarıyla 5 yıllık test.

İlkeler (PLAN.md, araştırma bölümü):
  - Parametre optimizasyonu YOK. Kurallar literatürden önceden sabitlendi; test sadece ölçer.
    (Quantopian: geçmiş Sharpe canlıyı %1–2 açıklıyor; Bailey ve López de Prado: çok deneme = sahte başarı.)
  - Sert maliyet: taker %0,10, maker %0,04, girişte 2 bp, stop'ta 5 bp kayma, long funding günde %0,03 (≈ yıllık %11),
    short funding geliri SIFIR sayılır.
  - Belirsizlikte kötümser: aynı mumda stop ve hedef → önce stop; limit/hedef ancak fiyat seviyeyi GEÇERSE dolar;
    sinyal mum kapanışında, giriş bir sonraki mumun açılışında.
  - Deflated Sharpe (deneme sayısına göre indirimli) ile "şans mı?" sorusu.
  - Bilinen yanlılık: evren bugünün en likit coinleridir (borsadan silinenler yok) → sonuçlar iyimser tarafta olabilir.
"""
from __future__ import annotations

import copy
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from . import quant, store, team
from .config import ROOT, load_config

COST = {"taker": 0.10, "maker": 0.04, "slip_entry_bp": 2.0, "slip_stop_bp": 5.0, "funding_long_day_pct": 0.03}
BAR_H = {"1D": 24.0, "15m": 0.25}
EULER = 0.5772156649


# ── veri ──────────────────────────────────────────────────────────────

def _universe(client, cfg, n):
    from .scan import build_universe
    c = copy.deepcopy(cfg)
    c["tarama"]["coin_sayisi"] = n
    uni, _ = build_universe(client, c)
    return uni


def load_data(client, cfg, n_coins=30, years=5.0, deniz_days=365):
    now_ms = int(time.time() * 1000)
    start_d = now_ms - int((years * 365 + 260) * 86400_000)
    uni = _universe(client, cfg, n_coins)
    daily = {}
    for r in uni:
        try:
            df = client.history_candles(r["inst"], "1D", start_d, max_pages=40)
            if len(df) >= 260:
                daily[r["sym"]] = (r["inst"], df, r.get("vol24_usd"))
                print(f"  {r['sym']}: {len(df)} gün ({df.index[0].date()} → {df.index[-1].date()})")
            else:
                print(f"  {r['sym']}: {len(df)} gün (yetersiz, atlandı)")
        except Exception as e:  # noqa: BLE001
            print(f"  {r['sym']} atlandı: {e}")
    if "BTC" not in daily:
        df = client.history_candles("BTC-USDT-SWAP", "1D", start_d, max_pages=40)
        daily["BTC"] = ("BTC-USDT-SWAP", df, None)
    intraday = {}
    start_i = now_ms - int(deniz_days * 86400_000)
    for sym in team.DENIZ_COINS:
        try:
            df = client.history_candles(f"{sym}-USDT-SWAP", "15m", start_i, max_pages=int(deniz_days * 96 / 100) + 5)
            if len(df) > 2000:
                intraday[sym] = df
                print(f"  {sym} 15dk: {len(df)} mum")
        except Exception as e:  # noqa: BLE001
            print(f"  {sym} 15dk atlandı: {e}")
    return daily, intraday, now_ms


# ── tek işlem simülasyonu (canlı defterin kurallarıyla) ──────────────

def _entry_fill(idea, o, h, l, j, side, valid_bars):
    """Döner: (giriş fiyatı, giriş mum indeksi, maker mı) ya da None."""
    sgn = 1 if side == "long" else -1
    if idea.get("entry_type") == "limit" and idea.get("limit_px"):
        lim = idea["limit_px"]
        for k in range(j, min(j + max(valid_bars, 1), len(o))):
            if sgn > 0 and o[k] < lim:
                return o[k], k, True
            if sgn < 0 and o[k] > lim:
                return o[k], k, True
            if (sgn > 0 and l[k] < lim) or (sgn < 0 and h[k] > lim):
                return lim, k, True
        return None
    if j >= len(o):
        return None
    return o[j] * (1 + sgn * COST["slip_entry_bp"] / 1e4), j, False


def simulate_trade(idea, F, i, bar_h, ctx_fn):
    """i. mum kapanışında üretilen fikri i+1'den itibaren yönetir. Döner: işlem kaydı ya da None (dolmadı)."""
    o, h, l, c = (F[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    ts = F.index
    side = idea["side"]
    sgn = 1 if side == "long" else -1
    fill = _entry_fill(idea, o, h, l, i + 1, side, int(idea.get("limit_valid_ms", 0) / (bar_h * 3.6e6)) or 1)
    if not fill:
        return None
    entry, j0, maker_in = fill
    ref = idea["entry"]
    shift = entry - ref
    sl = idea["sl"] + shift
    sl_init = sl
    tp = idea["tp"] + shift
    tp1 = idea["tp1"] + shift if idea.get("tp1") is not None else None
    pp = idea.get("pp") or {}
    frac = pp.get("partial_frac", 0.0) if tp1 is not None else 0.0
    trail_k = pp.get("trail_atr", 0.0)
    be = pp.get("basabas", False)
    trail_on = bool(pp.get("trail_now"))
    atr = idea["atr"]
    risk = abs(entry - sl_init)
    if risk <= 0:
        return None
    fee_in = entry * (COST["maker"] if maker_in else COST["taker"]) / 100
    qty_open, pnl, tp1_done = 1.0, -fee_in, tp1 is None
    hw = entry
    max_bars = int(idea["max_saat"] / bar_h)
    path = []
    exit_px, exit_k, why = None, None, None

    def close_all(px, k, reason, maker=False):
        nonlocal pnl, qty_open
        fee = px * qty_open * (COST["maker"] if maker else COST["taker"]) / 100
        pnl += sgn * qty_open * (px - entry) - fee
        qty_open = 0.0
        return px, k, reason

    k = j0
    while k < len(o):
        # funding (long öder, short alamaz say)
        if sgn > 0:
            pnl -= qty_open * c[k] * COST["funding_long_day_pct"] / 100 * bar_h / 24
        stop_hit = (l[k] <= sl) if sgn > 0 else (h[k] >= sl)
        if stop_hit and not (k == j0 and maker_in and False):
            px = (min(o[k], sl) if sgn > 0 else max(o[k], sl)) if k > j0 else sl
            px *= (1 - sgn * COST["slip_stop_bp"] / 1e4)
            exit_px, exit_k, why = close_all(px, k, "stop" if not tp1_done and not trail_on else "iz/başabaş stop")
            break
        if k > j0 or not maker_in:
            if not tp1_done and ((h[k] > tp1) if sgn > 0 else (l[k] < tp1)):
                q = qty_open * frac
                if q > 0:
                    pnl += sgn * q * (tp1 - entry) - q * tp1 * COST["maker"] / 100
                    qty_open -= q
                tp1_done = True
                trail_on = trail_on or trail_k > 0
                if be:
                    fee_rt = (COST["taker"] + COST["taker"]) / 100
                    be_px = entry * (1 + sgn * fee_rt)
                    if sgn * (be_px - sl) > 0:
                        sl = be_px
            if (h[k] > tp) if sgn > 0 else (l[k] < tp):
                exit_px, exit_k, why = close_all(tp, k, "hedef", maker=True)
                break
        # mum kapanışı: iz süren stop, kural çıkışı, süre
        if trail_on and trail_k > 0:
            hw = max(hw, h[k]) if sgn > 0 else min(hw, l[k])
            new_sl = hw - sgn * trail_k * atr
            if sgn * (new_sl - sl) > 0:
                sl = new_sl
        path.append((ts[k], (pnl + sgn * qty_open * (c[k] - entry)) / risk))
        rule = idea.get("exit")
        if rule and k + 1 < len(o):
            reason = quant.exit_signal(rule, ctx_fn(k), side)
            if reason:
                px = o[k + 1] * (1 - sgn * COST["slip_entry_bp"] / 1e4)
                exit_px, exit_k, why = close_all(px, k + 1, "kural: " + reason)
                break
        if k - j0 + 1 >= max_bars and k + 1 < len(o):
            px = o[k + 1] * (1 - sgn * COST["slip_entry_bp"] / 1e4)
            exit_px, exit_k, why = close_all(px, k + 1, "süre")
            break
        k += 1
    if exit_px is None:          # veri bitti: son kapanıştan kapat (açık işlem)
        k = len(o) - 1
        exit_px, exit_k, why = close_all(c[k], k, "açık (veri sonu)")
    r = pnl / risk
    path.append((ts[exit_k], r))
    return {"mgr": idea["mgr"], "sym": idea["sym"], "side": side, "entry_ts": ts[j0], "exit_ts": ts[exit_k],
            "entry_i": j0, "exit_i": exit_k, "r": float(r), "why": why, "hours": (exit_k - j0 + 1) * bar_h,
            "risk_frac": risk / entry, "path": path, "conviction": idea.get("conviction", 0)}


# ── bir yöneticinin tüm geçmişi ───────────────────────────────────────

def run_manager(mgr, F, sym, inst, bar_h, warm, ctx_extra_fn, cfg, cooldown_bars=3):
    trades = []
    n = len(F)
    hv_cache = {}

    def ctx_fn(k):
        x = quant.ctx_at(F, k)
        x.update(ctx_extra_fn(k))
        return x

    i = warm
    while i < n - 1:
        ctx = ctx_fn(i)
        if mgr.id == "murat":
            if i not in hv_cache:
                hv_cache[i] = quant.hvn_levels(F, i)
            ctx["hvn"] = hv_cache[i]
        ctx.update({"price": ctx["close"], "sym": sym, "inst": inst, "tf": "1D" if bar_h == 24 else "15m",
                    "bar_ms": int(bar_h * 3.6e6)})
        idea = mgr.analyze(ctx, mgr, cfg)
        if not idea:
            i += 1
            continue
        t = simulate_trade(idea, F, i, bar_h, ctx_fn)
        if not t:
            i += 1
            continue
        t["weather_trend"] = ctx.get("w_trend")
        t["high_vol"] = ctx.get("high_vol")
        trades.append(t)
        i = t["exit_i"] + cooldown_bars
    return trades


# ── ölçüm ─────────────────────────────────────────────────────────────

def r_stats(trs):
    if not trs:
        return {"n": 0}
    r = np.array([t["r"] for t in trs])
    gl = -r[r <= 0].sum()
    return {"n": int(len(r)), "win_pct": round(float((r > 0).mean()) * 100, 1), "avg_r": round(float(r.mean()), 3),
            "med_r": round(float(np.median(r)), 3), "sum_r": round(float(r.sum()), 1),
            "pf": round(float(r[r > 0].sum() / gl), 2) if gl > 0 else None,
            "best_r": round(float(r.max()), 2), "worst_r": round(float(r.min()), 2),
            "avg_hours": round(float(np.mean([t["hours"] for t in trs])), 1),
            "t_stat": round(float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))), 2) if len(r) > 2 and r.std() > 0 else None}


def equity_curve(trades, risk_pct, start=1000.0, heat_cap_pct=10.0, max_notional_x=2.0, mult_fn=None,
                 firm_limits=None, days=None):
    """Kronolojik, bileşik getirili, günlük piyasa değerli (MTM) özsermaye. Limitler aşılırsa işlem ATLANIR.
    firm_limits: {"coin": %, "side": %, "cluster": %, "corr": fn(sym_a, sym_b, ts) -> ρ}"""
    trades = sorted(trades, key=lambda t: t["entry_ts"])
    if days is None:
        if not trades:
            return None
        d0 = min(t["entry_ts"] for t in trades).normalize()
        d1 = max(t["exit_ts"] for t in trades).normalize()
        days = pd.date_range(d0, d1, freq="1D", tz="UTC")
    eq_real = start
    open_, taken, skipped = [], [], 0
    ti = 0
    curve = []
    for day in days:
        day_end = day + pd.Timedelta(days=1)
        # açılışlar (bugün girilenler)
        while ti < len(trades) and trades[ti]["entry_ts"] < day_end:
            t = trades[ti]
            ti += 1
            m = mult_fn(t) if mult_fn else 1.0
            if m <= 0:
                skipped += 1
                continue
            rp = (risk_pct[t["mgr"]] if isinstance(risk_pct, dict) else risk_pct) * m
            risk_usd = eq_real * rp / 100
            notional = risk_usd / t["risk_frac"]
            if notional > eq_real * max_notional_x:
                risk_usd = eq_real * max_notional_x * t["risk_frac"]
            heat = sum(x["risk_usd"] for x in open_)
            if heat + risk_usd > eq_real * heat_cap_pct / 100:
                skipped += 1
                continue
            if firm_limits:
                same_coin = sum(x["risk_usd"] for x in open_ if x["t"]["sym"] == t["sym"])
                same_side = sum(x["risk_usd"] for x in open_ if x["t"]["side"] == t["side"])
                if same_coin + risk_usd > eq_real * firm_limits["coin"] / 100 or \
                        same_side + risk_usd > eq_real * firm_limits["side"] / 100:
                    skipped += 1
                    continue
                cf = firm_limits.get("corr")
                if cf and firm_limits.get("cluster"):
                    cl = sum(x["risk_usd"] for x in open_ if x["t"]["side"] == t["side"]
                             and (x["t"]["sym"] == t["sym"] or cf(x["t"]["sym"], t["sym"], t["entry_ts"]) >= 0.7))
                    if cl + risk_usd > eq_real * firm_limits["cluster"] / 100:
                        skipped += 1
                        continue
            open_.append({"t": t, "risk_usd": risk_usd, "pi": 0})
            taken.append(t)
        # kapanışlar ve MTM
        unreal = 0.0
        still = []
        for x in open_:
            t = x["t"]
            if t["exit_ts"] < day_end:
                eq_real += x["risk_usd"] * t["r"]
                continue
            r_now = 0.0
            for (pts, pr) in t["path"]:
                if pts < day_end:
                    r_now = pr
                else:
                    break
            unreal += x["risk_usd"] * r_now
            still.append(x)
        open_ = still
        curve.append((day, eq_real + unreal))
    s = pd.Series([v for _, v in curve], index=[d for d, _ in curve])
    return {"curve": s, "taken": taken, "skipped": skipped}


def perf(curve: pd.Series, start=1000.0):
    if curve is None or len(curve) < 30:
        return {}
    ret = curve.pct_change().dropna()
    yrs = len(curve) / 365.0
    cagr = (curve.iloc[-1] / start) ** (1 / yrs) - 1 if curve.iloc[-1] > 0 else -1
    dd = (curve / curve.cummax() - 1).min()
    sd = ret.std()
    sharpe_d = ret.mean() / sd if sd > 0 else 0.0
    down = ret[ret < 0].std()
    return {"final": round(float(curve.iloc[-1]), 2), "cagr_pct": round(cagr * 100, 2), "max_dd_pct": round(float(dd) * 100, 2),
            "sharpe": round(float(sharpe_d * math.sqrt(365)), 2),
            "sortino": round(float(ret.mean() / down * math.sqrt(365)), 2) if down and down > 0 else None,
            "sr_daily": float(sharpe_d), "T": int(len(ret)),
            "skew": float(ret.skew()) if len(ret) > 3 else 0.0, "kurt": float(ret.kurt() + 3) if len(ret) > 3 else 3.0,
            "days": int(len(curve))}


def deflated_sharpe(sr, T, skew, kurt, trials_sr: list[float]):
    """Bailey ve López de Prado (2014). sr ve trials_sr günlük (yıllıklandırılmamış)."""
    N = max(len(trials_sr), 1)
    nd = NormalDist()
    var = float(np.var(trials_sr, ddof=1)) if N > 1 else 0.0
    if N > 1 and var > 0:
        sr0 = math.sqrt(var) * ((1 - EULER) * nd.inv_cdf(1 - 1 / N) + EULER * nd.inv_cdf(1 - 1 / (N * math.e)))
    else:
        sr0 = 0.0
    den = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if den <= 0 or T < 30:
        return None, sr0
    z = (sr - sr0) * math.sqrt(T - 1) / math.sqrt(den)
    return round(nd.cdf(z), 3), sr0


def by_year(trs):
    out = {}
    for t in trs:
        y = str(t["exit_ts"].year)
        out.setdefault(y, []).append(t["r"])
    return {y: {"n": len(v), "avg_r": round(float(np.mean(v)), 3), "sum_r": round(float(np.sum(v)), 1)}
            for y, v in sorted(out.items())}


# ── ana çalıştırma ────────────────────────────────────────────────────

def run(client=None, cfg_path=None, n_coins=30, years=5.0, deniz_days=365, out_dir: Path | None = None) -> dict:
    t0 = time.time()
    cfg = load_config(cfg_path)
    if client is None:
        from .okx import OKXClient
        client = OKXClient()
    daily, intraday, now_ms = load_data(client, cfg, n_coins, years, deniz_days)
    test_start = pd.Timestamp(now_ms - int(years * 365 * 86400_000), unit="ms", tz="UTC")

    # BTC havası (her gün için, o günün kapanışında bilinen)
    BF = quant.features(daily["BTC"][1])
    wx = {}
    for k in range(len(BF)):
        wx[BF.index[k]] = quant.btc_weather(BF, k)
    feats = {sym: quant.features(df) for sym, (inst, df, _) in daily.items()}
    rets = pd.DataFrame({sym: F["ret1"] for sym, F in feats.items()})

    def corr_fn(a, b, ts):
        if a == b:
            return 1.0
        w = rets.loc[:ts].tail(61)
        if a not in w or b not in w:
            return 0.0
        return quant.corr(w[a].to_numpy(), w[b].to_numpy())

    mgrs = {m.id: m for m in team.roster(cfg) if m.analyze}
    daily_mgrs = [m for m in mgrs.values() if "1D" in m.tfs]
    trades = {m.id: [] for m in mgrs.values()}

    for sym, (inst, df, vol) in daily.items():
        F = feats[sym]
        idx = F.index
        warm = max(int(np.searchsorted(idx, test_start)), 210)

        def extra(k, idx=idx):
            w = wx.get(idx[k]) or {}
            return {"btc_bear": w.get("btc_bear", False), "btc_bull": w.get("btc_bull", False),
                    "w_trend": w.get("trend"), "high_vol": w.get("high_vol"), "vol24_usd": vol}

        for m in daily_mgrs:
            trades[m.id] += run_manager(m, F, sym, inst, 24.0, warm, extra, cfg)
        print(f"  {sym}: " + ", ".join(f"{m.id} {sum(1 for t in trades[m.id] if t['sym'] == sym)}" for m in daily_mgrs))

    # Deniz: 15dk, BTC/ETH, son 1 yıl
    if "deniz" in mgrs:
        for sym, df in intraday.items():
            F = quant.features(df)
            D = feats.get(sym)
            dstate = {}
            if D is not None:
                for k in range(1, len(D)):   # gün D'nin 15dk mumları için D−1 kapanışı bilinir
                    prev = D.iloc[k - 1]
                    dstate[D.index[k].date()] = (bool(prev["close"] > prev["sma50"]), bool(prev["close"] < prev["sma50"]),
                                                 wx.get(D.index[k - 1]) or {})
            idx = F.index

            def extra15(k, idx=idx):
                ts = idx[k]
                du, dd, w = dstate.get(ts.date(), (False, False, {}))
                close_ts = ts + pd.Timedelta(minutes=15)
                return {"d_up": du, "d_dn": dd, "btc_bear": w.get("btc_bear", False), "btc_bull": w.get("btc_bull", False),
                        "hour": close_ts.hour, "dow": close_ts.dayofweek, "w_trend": w.get("trend"),
                        "high_vol": w.get("high_vol")}

            trades["deniz"] += run_manager(mgrs["deniz"], F, sym, f"{sym}-USDT-SWAP", 0.25, 210, extra15, cfg,
                                           cooldown_bars=4)
            print(f"  {sym} 15dk: deniz {sum(1 for t in trades['deniz'] if t['sym'] == sym)}")

    # ── ölçümler ──
    split = test_start + (pd.Timestamp(now_ms, unit="ms", tz="UTC") - test_start) * 0.6
    days = pd.date_range(test_start.normalize(), pd.Timestamp(now_ms, unit="ms", tz="UTC").normalize(), freq="1D", tz="UTC")
    risk = {m.id: m.base_risk for m in mgrs.values()}

    def wmult(t):
        return quant.weather_mult({"trend": t.get("weather_trend") or "yatay", "high_vol": t.get("high_vol")}, t["mgr"], t["side"])

    report = {"managers": {}, "firm": {}}
    trial_srs = []
    for mid, trs in trades.items():
        m = mgrs[mid]
        d_days = None if mid == "deniz" else days
        ec = equity_curve(trs, risk[mid], days=d_days)
        ec_w = equity_curve(trs, risk[mid], days=d_days, mult_fn=wmult)
        p, pw = perf(ec["curve"]) if ec else {}, perf(ec_w["curve"]) if ec_w else {}
        for x in (p, pw):
            if x.get("sr_daily") is not None:
                trial_srs.append(x["sr_daily"])
        report["managers"][mid] = {
            "name": m.name, "title": m.title, "risk_pct": risk[mid],
            "all": r_stats(trs), "is": r_stats([t for t in trs if t["entry_ts"] < split]),
            "oos": r_stats([t for t in trs if t["entry_ts"] >= split]),
            "long": r_stats([t for t in trs if t["side"] == "long"]), "short": r_stats([t for t in trs if t["side"] == "short"]),
            "by_year": by_year(trs), "equity": p, "equity_weather": pw,
            "exits": {k: int(v) for k, v in pd.Series([t["why"].split(":")[0] for t in trs]).value_counts().items()} if trs else {},
            "period_days": 365 if mid == "deniz" else int(years * 365),
        }
    # firma birleşik (Deniz dahil, Elif test edilemez)
    all_trs = [t for trs in trades.values() for t in trs]
    fl = {"coin": 2.5, "side": 6.0}
    for name, kw in (("limitler", {"firm_limits": fl}),
                     ("limitler+hava", {"firm_limits": fl, "mult_fn": wmult}),
                     ("limitler+hava+yigilma", {"firm_limits": {**fl, "cluster": 4.0, "corr": corr_fn}, "mult_fn": wmult})):
        ec = equity_curve(all_trs, risk, days=days, **kw)
        p = perf(ec["curve"]) if ec else {}
        if p.get("sr_daily") is not None:
            trial_srs.append(p["sr_daily"])
        report["firm"][name] = {"equity": p, "taken": len(ec["taken"]) if ec else 0, "skipped": ec["skipped"] if ec else 0,
                                "stats": r_stats(ec["taken"]) if ec else {}}
    # Deflated Sharpe (bu çalıştırmadaki tüm denemelerle)
    for blk in list(report["managers"].values()) + list(report["firm"].values()):
        for key in ("equity", "equity_weather"):
            e = blk.get(key)
            if e and e.get("sr_daily") is not None:
                dsr, sr0 = deflated_sharpe(e["sr_daily"], e["T"], e["skew"], e["kurt"], trial_srs)
                e["dsr"] = dsr
                e["sr0_annual"] = round(sr0 * math.sqrt(365), 2)
    report.update({
        "generated_at": now_ms, "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "years": years, "coins": sorted(s for s in daily), "n_coins": len(daily), "trials": len(trial_srs),
        "test_start": str(test_start.date()), "split": str(split.date()), "cost": COST,
        "duration_s": round(time.time() - t0, 1),
        "notes": ["Parametre optimizasyonu yapılmadı; kurallar önceden sabit.",
                  "Evren bugünün en likit coinleri (survivorship): sonuçlar iyimser tarafta olabilir.",
                  "Funding/OI/alıcı-satıcı verisi 5 yıl geriye yok: bu doğrulamalar testte 'tutmadı' sayıldı, risk taban seviyede.",
                  "Elif (funding carry) 5 yıllık funding geçmişi olmadığı için test edilemedi; yayımlanmış istatistikler kullanıldı.",
                  "Deniz yalnızca BTC/ETH 15dk son 1 yılda test edildi."],
    })
    out_dir = out_dir or (ROOT / "data")
    out_dir.mkdir(parents=True, exist_ok=True)
    slim = copy.deepcopy(report)
    store.dump(out_dir / "backtest_v8.json", slim)
    (out_dir / "BACKTEST.md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
    print(f"✓ v8 test bitti: {len(daily)} coin, {round(time.time() - t0)} sn")
    return report


def _fmt(x, nd=2, pct=False):
    if x is None or (isinstance(x, float) and x != x):
        return "–"
    s = f"{x:.{nd}f}".replace(".", ",")
    return s + ("%" if pct else "")


def verdict(blk) -> str:
    a, o, e = blk["all"], blk["oos"], blk.get("equity") or {}
    if a.get("n", 0) < 30:
        return "yetersiz örnek"
    ok = (a.get("avg_r", 0) > 0 and (o.get("avg_r") or 0) > 0 and (e.get("dsr") or 0) >= 0.90)
    weak = a.get("avg_r", 0) > 0 and (o.get("avg_r") or 0) > 0
    return "GEÇTİ" if ok else ("zayıf pozitif" if weak else "KALDI")


def markdown(rep) -> str:
    L = [f"# v8 Test Raporu — {rep['generated']}", "",
         f"- Dönem: {rep['test_start']} → bugün ({rep['years']:g} yıl), örneklem içi/dışı ayrımı {rep['split']}",
         f"- Evren: {rep['n_coins']} coin · Deneme sayısı (Deflated Sharpe için): {rep['trials']}",
         f"- Maliyet: taker %{rep['cost']['taker']}, maker %{rep['cost']['maker']}, giriş {rep['cost']['slip_entry_bp']} bp, "
         f"stop {rep['cost']['slip_stop_bp']} bp kayma, long funding günde %{rep['cost']['funding_long_day_pct']}", "",
         "| Yönetici | İşlem | Kazanma | Ort. R | Kâr faktörü | Örneklem dışı ort. R | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe | Karar |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for mid, b in rep["managers"].items():
        a, o, e = b["all"], b["oos"], b.get("equity") or {}
        L.append(f"| {b['name']} | {a.get('n', 0)} | {_fmt(a.get('win_pct'), 1, True)} | {_fmt(a.get('avg_r'), 3)} | "
                 f"{_fmt(a.get('pf'))} | {_fmt(o.get('avg_r'), 3)} | {_fmt(e.get('cagr_pct'), 1, True)} | "
                 f"{_fmt(e.get('max_dd_pct'), 1, True)} | {_fmt(e.get('sharpe'))} | {_fmt(e.get('dsr'), 3)} | {verdict(b)} |")
    L += ["", "## Firma (tüm yöneticiler birlikte, firma limitleriyle)", "",
          "| Katmanlar | Alınan / atlanan işlem | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe |", "|---|---|---|---|---|---|"]
    for name, f in rep["firm"].items():
        e = f["equity"]
        L.append(f"| {name} | {f['taken']} / {f['skipped']} | {_fmt(e.get('cagr_pct'), 1, True)} | {_fmt(e.get('max_dd_pct'), 1, True)} | "
                 f"{_fmt(e.get('sharpe'))} | {_fmt(e.get('dsr'), 3)} |")
    L += ["", "## Yıllara göre ortalama R", ""]
    years = sorted({y for b in rep["managers"].values() for y in b["by_year"]})
    L.append("| Yönetici | " + " | ".join(years) + " |")
    L.append("|---|" + "---|" * len(years))
    for b in rep["managers"].values():
        L.append(f"| {b['name']} | " + " | ".join(
            f"{_fmt(b['by_year'].get(y, {}).get('avg_r'), 2)} ({b['by_year'].get(y, {}).get('n', 0)})" for y in years) + " |")
    L += ["", "## Notlar", ""] + [f"- {n}" for n in rep["notes"]]
    return "\n".join(L) + "\n"
