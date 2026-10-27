"""Araştırma laboratuvarı: yöneticilerin CANLI fonksiyonlarıyla, geleceği görmeyen evren üzerinde varyant karşılaştırması.

bt2'den farkları (bt2 canlı testin resmi aracı olarak kalır):
  - Evren her gün o günün verisiyle seçilir: listelenme yaşı, 30 günlük medyan hacim, hacim sırası (point-in-time).
    Silinen coinler dahil (tools/gecmis.py) → survivorship yanlılığı yok.
  - Gerçek funding geçmişi: long öder, short alır (bt2 sabit %0,03/gün ve short geliri sıfır sayıyordu).
  - Gerçek alıcı/satıcı baskısı (taker alış hacmi / toplam hacim) ve funding → yöneticilerin "canlı" doğrulamaları
    testte de çalışır (bt2'de hep 'tutmadı' sayılıyordu).
  - Aynı veri geçişinde birden çok çıkış/stop/yeniden giriş/filtre varyantı → adil karşılaştırma.
  - Örneklem içi (eğitim) < 2025-01-01 ≤ örneklem dışı (doğrulama). Seçim yalnızca eğitimle yapılır.

Kullanım: python tools/lab.py --data <gecmis klasörü> --out data/LAB.md
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar import bt2, quant, team  # noqa: E402
from radar import ledger as lg  # noqa: E402
from radar.config import load_config  # noqa: E402

SPLIT = pd.Timestamp("2025-01-01", tz="UTC")
COST = dict(bt2.COST)


# ── veri ──────────────────────────────────────────────────────────────

def read_csv_gz(p: Path) -> pd.DataFrame:
    with gzip.open(p, "rt") as f:
        df = pd.read_csv(f)
    df.index = pd.to_datetime(df.pop("ts").astype("int64"), unit="ms", utc=True)
    return df[~df.index.duplicated(keep="last")].sort_index()


def load(data: Path, iv: str = "1d", syms: list[str] | None = None) -> dict[str, pd.DataFrame]:
    out = {}
    for p in sorted((data / iv).glob("*.csv.gz")):
        s = p.name[:-7]
        if syms and s not in syms:
            continue
        if short_name(s) in NON_CRYPTO:
            continue
        df = read_csv_gz(p)
        if len(df) >= 60:
            out[s] = df.astype(float)
    return out


def funding_daily(data: Path, sym: str) -> pd.Series | None:
    p = data / "funding" / f"{sym}.csv.gz"
    if not p.exists():
        return None
    f = read_csv_gz(p)["rate"].astype(float)
    return f.groupby(f.index.floor("1D")).sum()          # o günün toplam funding oranı (long öder)


# Hisse/ETF/emtia vadelileri (canlı evren `sadece_kripto: true` ile bunları dışlar; OKX instCategory ≠ 1)
NON_CRYPTO = set("""XAU XAUT PAXG XAG XPT XPD CL BZ NATGAS COPPER SOXL SOXS SNDK SPCX SKHYNIX SKHY KORU MU SNXX MSTR CRCL
NVDA INTC SAMSUNG TSLA QQQ TQQQ SQQQ DRAM GOOGL EWY SPY AMD MRVL MUU LITE META NBIS AAPL AAOI CBRS MVLL COIN BE WDC AVGO
ZHIPU HOOD CRWV AMZN ORCL UNITREE MSFT RKLB AXTI DELL BMNR PLTR MINIMAX ARM INTW MRNA COHR TSM GLW IREN CXMT BABA
ZHONGJI QCOM CSOPSKHYNIX2L ANTHROPIC OPENAI STXX HK1810 NFLX SKDD ASTS SMCI AMAT CRDO UVXY EUR GBP JPY""".split())


def short_name(binance_sym: str) -> str:
    return binance_sym[:-4] if binance_sym.endswith("USDT") else binance_sym


# ── evren (point-in-time) ─────────────────────────────────────────────

def eligibility(D: dict[str, pd.DataFrame], min_age_d: int, min_qv: float, top_n: int) -> pd.DataFrame:
    """Gün × sembol: o gün kapanışta bilinen veriyle evrende mi (True/False)."""
    qv = pd.DataFrame({s: df["quote_volume"] for s, df in D.items()}).sort_index()
    med = qv.rolling(30, min_periods=20).median()
    first = {s: df.index[0] for s, df in D.items()}
    age = pd.DataFrame({s: (qv.index - first[s]).days for s in qv.columns}, index=qv.index)
    ok = (age >= min_age_d) & (med >= min_qv)
    rank = med.where(ok).rank(axis=1, ascending=False)
    return ok & (rank <= top_n)


# ── simülasyon (bt2.simulate_trade + çıkış varyantları + gerçek funding) ─────

def simulate(idea, F, i, ctx_fn, fund: np.ndarray | None, X: dict):
    """X: çıkış varyantı. Anahtarlar:
       ratchet: [(tepe_R, kilit_R), ...]  tepe R'ye ulaşınca stop en az kilit_R'ye çekilir (mum kapanışında)
       giveback: (başlangıç_R, oran)       tepe ≥ başlangıç_R olunca açık kârın en fazla `oran`'ı geri verilir
       donmid: N                           stop = max(stop, son N kapanışın tepe/dip orta noktası) (Zarattini)
       close_stop: True                    ilk stop yalnızca KAPANIŞ stop'u geçerse (çıkış ertesi açılış); fitil avlamaz
       no_partial: True                    kısmi kâr yok
       no_tp: True                         sabit hedef yok (çıkışı iz/kural belirler)
    """
    o, h, l, c = F["_o"], F["_h"], F["_l"], F["_c"]
    ts = F["_ts"]
    n = len(o)
    side = idea["side"]
    sgn = 1 if side == "long" else -1
    j = i + 1
    if j >= n:
        return None
    if idea.get("entry_type") == "limit" and idea.get("limit_px"):
        lim = idea["limit_px"]
        vb = max(int(idea.get("limit_valid_ms", 0) / 86_400_000), 1)
        fill = None
        for k in range(j, min(j + vb, n)):
            if (sgn > 0 and o[k] < lim) or (sgn < 0 and o[k] > lim):
                fill = (o[k], k, True)
                break
            if (sgn > 0 and l[k] < lim) or (sgn < 0 and h[k] > lim):
                fill = (lim, k, True)
                break
        if not fill:
            return None
        entry, j0, maker_in = fill
    else:
        entry, j0, maker_in = o[j] * (1 + sgn * COST["slip_entry_bp"] / 1e4), j, False
    shift = entry - idea["entry"]
    sl = idea["sl"] + shift
    sl_init = sl
    tp = None if X.get("no_tp") else idea["tp"] + shift
    tp1 = idea["tp1"] + shift if idea.get("tp1") is not None else None
    pp = idea.get("pp") or {}
    frac = 0.0 if X.get("no_partial") else (pp.get("partial_frac", 0.0) if tp1 is not None else 0.0)
    trail_k = pp.get("trail_atr", 0.0) if X.get("trail", True) else 0.0
    be = pp.get("basabas", False)
    trail_on = bool(pp.get("trail_now"))
    atr = idea["atr"]
    risk = abs(entry - sl_init)
    if risk <= 0:
        return None
    slip_stop = lg.stop_slip_bp({"atr": atr, "entry": entry, "tf": "1D", "coin_class": idea.get("coin_class")},
                                {"stop_kayma_bp": COST["slip_stop_bp"]})
    pnl = -entry * (COST["maker"] if maker_in else COST["taker"]) / 100
    qty, tp1_done = 1.0, tp1 is None
    hw = entry
    peak_r = 0.0
    max_bars = int(idea["max_saat"] / 24)
    rule = idea.get("exit")
    path = []
    why = None
    exit_px, exit_k = None, None
    mfe = 0.0

    def close_all(px, k, reason, maker=False):
        nonlocal pnl, qty
        pnl += sgn * qty * (px - entry) - px * qty * (COST["maker"] if maker else COST["taker"]) / 100
        qty = 0.0
        return px, k, reason

    k = j0
    while k < n:
        if fund is not None and not np.isnan(fund[k]):
            pnl -= sgn * qty * c[k] * fund[k]                 # gerçek funding: long öder, short alır (negatifte tersi)
        elif sgn > 0:
            pnl -= qty * c[k] * COST["funding_long_day_pct"] / 100
        locked = sl if sgn * (sl - sl_init) > 1e-12 else None   # stop başlangıç dışına taşındıysa (kilit/iz)
        use_close = X.get("close_stop") and locked is None
        stop_hit = ((l[k] <= sl) if sgn > 0 else (h[k] >= sl)) if not use_close else False
        dz = X.get("disaster")
        if use_close and dz:                              # kapanış stop'unun güvenlik ağı: gün içinde dz×R'lik felaket stop'u
            dpx = sl_init - sgn * (dz - 1.0) * risk
            if (l[k] <= dpx) if sgn > 0 else (h[k] >= dpx):
                px = (min(o[k], dpx) if sgn > 0 else max(o[k], dpx)) if k > j0 else dpx
                exit_px, exit_k, why = close_all(px * (1 - sgn * slip_stop / 1e4), k, "felaket stop")
                break
        if stop_hit:
            px = (min(o[k], sl) if sgn > 0 else max(o[k], sl)) if k > j0 else sl
            px *= (1 - sgn * slip_stop / 1e4)
            exit_px, exit_k, why = close_all(px, k, "stop" if locked is None else "kilit/iz stop")
            break
        if use_close and ((c[k] <= sl) if sgn > 0 else (c[k] >= sl)) and k + 1 < n:
            px = o[k + 1] * (1 - sgn * slip_stop / 1e4)
            exit_px, exit_k, why = close_all(px, k + 1, "kapanış stop")
            break
        if k > j0 or not maker_in:
            if not tp1_done and ((h[k] > tp1) if sgn > 0 else (l[k] < tp1)):
                q = qty * frac
                if q > 0:
                    pnl += sgn * q * (tp1 - entry) - q * tp1 * COST["maker"] / 100
                    qty -= q
                tp1_done = True
                trail_on = trail_on or trail_k > 0
                if be:
                    be_px = entry * (1 + sgn * 2 * COST["taker"] / 100)
                    if sgn * (be_px - sl) > 0:
                        sl = be_px
            if tp is not None and ((h[k] > tp) if sgn > 0 else (l[k] < tp)):
                exit_px, exit_k, why = close_all(tp, k, "hedef", maker=True)
                break
        # ── mum kapanışı: stop güncellemeleri (bir sonraki mumdan itibaren geçerli) ──
        best = h[k] if sgn > 0 else l[k]
        hw = max(hw, best) if sgn > 0 else min(hw, best)
        peak_r = max(peak_r, sgn * (hw - entry) / risk)
        mfe = peak_r
        if trail_on and trail_k > 0:
            new_sl = hw - sgn * trail_k * atr
            if sgn * (new_sl - sl) > 0:
                sl = new_sl
        for (pr, lr) in X.get("ratchet") or []:
            if peak_r >= pr:
                lk = entry + sgn * lr * risk
                if sgn * (lk - sl) > 0:
                    sl = lk
        gb = X.get("giveback")
        if gb and peak_r >= gb[0]:
            keep = peak_r * (1 - gb[1])
            lk = entry + sgn * keep * risk
            if sgn * (lk - sl) > 0:
                sl = lk
        dm = X.get("donmid")
        if dm and k - dm + 1 >= 0:
            w = c[k - dm + 1:k + 1]
            mid = (w.max() + w.min()) / 2
            if sgn * (mid - sl) > 0 and sgn * (c[k] - mid) > 0:
                sl = mid
        path.append((ts[k], (pnl + sgn * qty * (c[k] - entry)) / risk))
        if rule and k + 1 < n and not X.get("no_rule"):
            reason = quant.exit_signal(rule, ctx_fn(k), side)
            if reason:
                px = o[k + 1] * (1 - sgn * COST["slip_entry_bp"] / 1e4)
                exit_px, exit_k, why = close_all(px, k + 1, "kural")
                break
        if k - j0 + 1 >= max_bars and k + 1 < n:
            exit_px, exit_k, why = close_all(o[k + 1] * (1 - sgn * COST["slip_entry_bp"] / 1e4), k + 1, "süre")
            break
        k += 1
    if exit_px is None:
        k = n - 1
        exit_px, exit_k, why = close_all(c[k], k, "açık")
    r = pnl / risk
    path.append((ts[exit_k], r))
    return {"mgr": idea["mgr"], "sym": idea["sym"], "side": side, "entry_ts": ts[j0], "exit_ts": ts[exit_k],
            "entry_i": j0, "exit_i": exit_k, "r": float(r), "why": why, "hours": (exit_k - j0 + 1) * 24.0,
            "risk_frac": risk / entry, "path": path, "mfe_r": float(mfe), "coin_class": idea.get("coin_class"),
            "conviction": idea.get("conviction", 0), "stop_pct": risk / entry * 100}


# ── yeni kişilik adayı: Zarattini tarzı Donchian topluluğu (long-only) ─────

ZAR_LB = (10, 20, 30, 60, 90, 150, 250)


def zarattini_ideas(F, i, sym):
    """i. kapanışta hangi bakış pencerelerinde yeni kapanış zirvesi var. Döner: [(N, stop)]"""
    c = F["_c"]
    out = []
    for N in ZAR_LB:
        if i - N < 0:
            continue
        w = c[i - N:i]                      # önceki N kapanış (bugün hariç)
        if c[i] > w.max() and not (c[i - 1] > c[i - N - 1:i - 1].max() if i - N - 1 >= 0 else False):
            w2 = c[i - N + 1:i + 1]
            mid = (w2.max() + w2.min()) / 2
            if mid < c[i]:
                out.append((N, mid))
    return out


# ── koşu ──────────────────────────────────────────────────────────────

MANAGERS = ("kemal", "kaan", "selin", "murat", "ayse", "bas")


def run(data: Path, cfg, variants: dict[str, dict], min_age=0, min_qv=5e6, top_n=100, syms_limit=0,
        managers=MANAGERS, with_zar=True):
    t0 = time.time()
    D = load(data, "1d")
    if syms_limit:
        keep = sorted(D, key=lambda s: -D[s]["quote_volume"].tail(30).median())[:syms_limit]
        if "BTCUSDT" not in keep:
            keep.append("BTCUSDT")
        D = {s: D[s] for s in keep}
    print(f"{len(D)} sembol yüklendi ({time.time() - t0:.0f} sn)", flush=True)
    E = eligibility(D, min_age, min_qv, top_n)
    BF = quant.features(D["BTCUSDT"])
    wx = {BF.index[k]: quant.btc_weather(BF, k) for k in range(len(BF))}
    mgrs = {m.id: m for m in team.roster(cfg) if m.id in managers}
    out = {v: {m: [] for m in list(mgrs) + (["zar"] if with_zar else [])} for v in variants}
    # BTC 30g getirisi (göreli güç)
    btc_ret30 = BF["ret30"]
    for si, (s, df) in enumerate(D.items()):
        if s not in E.columns:
            continue
        el = E[s].reindex(df.index).fillna(False).to_numpy()
        if not el.any():
            continue
        F = quant.features(df)
        fund = funding_daily(data, s)
        fnd = fund.reindex(F.index).to_numpy(float) if fund is not None else None
        tb = (2 * df["taker_buy_quote"] / df["quote_volume"].replace(0, np.nan) - 1).to_numpy(float)
        Fa = {"_o": F["open"].to_numpy(float), "_h": F["high"].to_numpy(float), "_l": F["low"].to_numpy(float),
              "_c": F["close"].to_numpy(float), "_ts": F.index}
        first_ts = df.index[0]
        idx = F.index
        rs = (F["ret30"] - btc_ret30.reindex(idx)).to_numpy(float)
        qv30 = df["quote_volume"].rolling(30, min_periods=10).median().to_numpy(float)
        cache: dict[int, dict] = {}
        sym = short_name(s)

        def ctx_fn(k):
            x = cache.get(k)
            if x is None:
                x = quant.ctx_at(F, k)
                w = wx.get(idx[k]) or {}
                fa = fnd[k] * 365 * 100 if fnd is not None and not np.isnan(fnd[k]) else None
                x.update({"btc_bear": w.get("btc_bear", False), "btc_bull": w.get("btc_bull", False),
                          "w_trend": w.get("trend"), "high_vol": w.get("high_vol"), "vol24_usd": qv30[k],
                          "price": x["close"], "sym": sym, "inst": f"{sym}-USDT-SWAP", "tf": "1D", "bar_ms": 86_400_000,
                          "funding_ann": fa, "funding_rate": (fnd[k] / 3) if fa is not None else 0.0, "interval_h": 8,
                          "taker_imb": tb[k] if not np.isnan(tb[k]) else None,
                          "rs_rel": rs[k] if not np.isnan(rs[k]) else None})
                cache[k] = x
            return x

        n = len(F)
        warm = 210
        for vname, V in variants.items():
            X = V.get("exit", {})
            for mid, m in mgrs.items():
                cd_bars = V.get("cooldown", 3)
                i = warm
                last_exit = None          # (k, side, was_loss)
                trs = out[vname][mid]
                while i < n - 1:
                    if not el[i]:
                        i += 1
                        continue
                    ctx = ctx_fn(i)
                    if mid == "murat" and "hvn" not in ctx:
                        ctx["hvn"] = quant.hvn_levels(F, i)
                    age_d = (idx[i] - first_ts).days
                    cls = "A" if age_d >= 1095 else "B" if age_d >= 365 else "C"
                    ctx["coin_class"] = cls
                    idea = team.apply_learned(team.apply_variant(m.analyze(dict(ctx), m, cfg), ctx, cfg), cfg)
                    if idea is None:
                        i += 1
                        continue
                    idea["coin_class"] = cls
                    if not passes(V, idea, ctx, last_exit, i, F):
                        i += 1
                        continue
                    t = simulate(idea, Fa, i, ctx_fn, fnd if V.get("real_funding", True) else None, X)
                    if not t:
                        i += 1
                        continue
                    t.update({"age_d": age_d, "fa": ctx.get("funding_ann"), "btc_bear": bool(ctx.get("btc_bear")),
                              "w_trend": ctx.get("w_trend"), "high_vol": ctx.get("high_vol"),
                              "atr_pct": ctx["atr"] / ctx["close"] * 100})
                    trs.append(t)
                    last_exit = (t["exit_i"], t["side"], t["r"] <= 0, t["why"])
                    i = t["exit_i"] + cd_bars
            if with_zar:
                trs = out[vname]["zar"]
                busy_until = {N: -1 for N in ZAR_LB}
                for i in range(warm, n - 1):
                    if not el[i]:
                        continue
                    for (N, mid_px) in zarattini_ideas(Fa, i, sym):
                        if i <= busy_until[N]:
                            continue
                        ctx = ctx_fn(i)
                        if V.get("zar_btc") and not ctx.get("btc_bull"):
                            continue
                        idea = {"mgr": "zar", "sym": sym, "side": "long", "entry": Fa["_c"][i], "sl": mid_px,
                                "tp": Fa["_c"][i] * 100, "tp1": None, "atr": ctx["atr"], "max_saat": 365 * 24,
                                "pp": {}, "exit": None, "coin_class": "A" if (idx[i] - first_ts).days >= 1095 else "B"}
                        t = simulate(idea, Fa, i, ctx_fn, fnd, {"donmid": N, "no_tp": True, **(V.get("zar_exit") or {})})
                        if t:
                            t.update({"lb": N, "age_d": (idx[i] - first_ts).days, "fa": ctx.get("funding_ann"),
                                      "btc_bear": bool(ctx.get("btc_bear")), "w_trend": ctx.get("w_trend"),
                                      "high_vol": ctx.get("high_vol"), "atr_pct": ctx["atr"] / ctx["close"] * 100})
                            trs.append(t)
                            busy_until[N] = t["exit_i"]
        if si % 25 == 0:
            print(f"  {si}/{len(D)} {s} ({time.time() - t0:.0f} sn)", flush=True)
    return out


def passes(V, idea, ctx, last_exit, i, F) -> bool:
    """Giriş filtreleri ve yeniden giriş kuralları (varyant)."""
    side = idea["side"]
    if V.get("long_only") and side == "short":
        return False
    if V.get("long_only_mgrs") and idea["mgr"] in V["long_only_mgrs"] and side == "short":
        return False
    if V.get("min_age_trade") and (ctx.get("coin_class") == "C"):
        return False
    if V.get("max_atr_pct") and ctx["atr"] / ctx["close"] * 100 > V["max_atr_pct"]:
        return False
    if V.get("max_stop_pct"):
        if abs(idea["entry"] - idea["sl"]) / idea["entry"] * 100 > V["max_stop_pct"]:
            return False
    fa = ctx.get("funding_ann")
    if V.get("crowd_long") is not None and side == "long" and fa is not None and fa > V["crowd_long"]:
        return False
    if V.get("crowd_short") is not None and side == "short" and fa is not None and fa < V["crowd_short"]:
        return False
    if V.get("bear_no_long") and side == "long" and ctx.get("w_trend") == "ayi":
        return False
    rr = V.get("reentry")
    if rr and last_exit:
        k, s0, loss, why = last_exit
        if s0 == side:
            if loss and i - k < rr.get("after_loss", 0):
                return False
            if not loss and rr.get("fresh_high"):
                c = F["close"].to_numpy(float)
                seg = c[k:i]
                if len(seg) and ((side == "long" and c[i] <= seg.max()) or (side == "short" and c[i] >= seg.min())):
                    return False
    return True


# ── rapor ─────────────────────────────────────────────────────────────

def stats(trs):
    s = bt2.r_stats(trs)
    if trs:
        g = np.array([t["mfe_r"] for t in trs])
        r = np.array([t["r"] for t in trs])
        big = g >= 2
        s["giveback"] = round(float(np.mean(g[big] - r[big])), 2) if big.any() else None   # 2R+ görmüş işlemlerde geri verilen R
    return s


def equity(trs, risk_pct=1.0):
    """Yönetici başına işlem riski %1 (zar: bakış penceresi başına 1/7). Isı tavanı %20."""
    if not trs:
        return {}
    rp = {m: (risk_pct / len(ZAR_LB) if m == "zar" else risk_pct) for m in {t["mgr"] for t in trs}}
    ec = bt2.equity_curve(trs, rp, heat_cap_pct=20.0)
    return bt2.perf(ec["curve"]) if ec else {}


def summarize(out):
    rows = []
    for v, by in out.items():
        for mid, trs in by.items():
            a = stats(trs)
            is_ = stats([t for t in trs if t["entry_ts"] < SPLIT])
            oos = stats([t for t in trs if t["entry_ts"] >= SPLIT])
            rows.append({"variant": v, "mgr": mid, "all": a, "is": is_, "oos": oos,
                         "eq_is": equity([t for t in trs if t["entry_ts"] < SPLIT]),
                         "eq_oos": equity([t for t in trs if t["entry_ts"] >= SPLIT])})
        allt = [t for mid, trs in by.items() if mid != "zar" for t in trs]
        rows.append({"variant": v, "mgr": "EKİP", "all": stats(allt),
                     "is": stats([t for t in allt if t["entry_ts"] < SPLIT]),
                     "oos": stats([t for t in allt if t["entry_ts"] >= SPLIT]),
                     "eq_is": equity([t for t in allt if t["entry_ts"] < SPLIT], 0.5),
                     "eq_oos": equity([t for t in allt if t["entry_ts"] >= SPLIT], 0.5)})
    return rows


def fmt(x, nd=2):
    return "–" if x is None or (isinstance(x, float) and x != x) else f"{x:.{nd}f}"


def markdown(rows, title):
    L = [f"# {title}", "", "Eğitim (IS) < 2025-01-01 ≤ doğrulama (OOS). R = masraf ve gerçek funding sonrası, ilk riske göre.", "",
         "| Varyant | Yönetici | IS n | IS kazanma | IS ort R | IS PF | OOS n | OOS kazanma | OOS ort R | OOS PF | 2R+ geri verilen | IS Sharpe | OOS Sharpe | OOS maks düşüş |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        i, o = r["is"], r["oos"]
        L.append(f"| {r['variant']} | {r['mgr']} | {i.get('n', 0)} | {fmt(i.get('win_pct'), 1)} | {fmt(i.get('avg_r'), 3)} | {fmt(i.get('pf'))} | "
                 f"{o.get('n', 0)} | {fmt(o.get('win_pct'), 1)} | {fmt(o.get('avg_r'), 3)} | {fmt(o.get('pf'))} | {fmt(r['all'].get('giveback'))} | "
                 f"{fmt(r['eq_is'].get('sharpe'))} | {fmt(r['eq_oos'].get('sharpe'))} | {fmt(r['eq_oos'].get('max_dd_pct'), 1)} |")
    return "\n".join(L) + "\n"


def dump_trades(out, path: Path):
    flat = []
    for v, by in out.items():
        for mid, trs in by.items():
            for t in trs:
                d = {k: t[k] for k in t if k != "path"}
                d["variant"] = v
                d["entry_ts"], d["exit_ts"] = str(t["entry_ts"]), str(t["exit_ts"])
                flat.append(d)
    with gzip.open(path, "wt") as f:
        json.dump(flat, f, default=lambda x: x.item() if hasattr(x, "item") else str(x))


LOCK = [(2.0, 0.5), (3.0, 1.5), (4.0, 2.5), (6.0, 4.0), (10.0, 7.0)]
PRESETS = {
    "temel": {"temel": {}},
    "tekli": {                     # her değişikliğin tek başına etkisi (aynı veri geçişi)
        "temel": {},
        "kilit_merdiveni": {"exit": {"ratchet": LOCK}},
        "geri_ver_40": {"exit": {"giveback": (1.5, 0.40)}},
        "geri_ver_25": {"exit": {"giveback": (1.5, 0.25)}},
        "kapanis_stop": {"exit": {"close_stop": True}},
        "donchian_orta20": {"exit": {"donmid": 20}},
        "kismi_yok": {"exit": {"no_partial": True}},
        "yeniden_giris": {"reentry": {"after_loss": 5, "fresh_high": True}, "cooldown": 1},
        "sadece_long": {"long_only": True},
        "genc_coin_yok": {"min_age_trade": True},
        "stop_en_fazla_15": {"max_stop_pct": 15},
        "kalabalik_filtresi": {"crowd_long": 30, "crowd_short": -20},
        "ayi_long_yok": {"bear_no_long": True},
    },
    "birlesik": {                  # tekli testte eğitimde (IS) işe yarayanların birleşimi + eşik duyarlılığı
        "temel": {},
        "K_kapanis_kalabalik": {"exit": {"close_stop": True}, "crowd_long": 30, "crowd_short": -20},
        "K_stop12": {"exit": {"close_stop": True}, "crowd_long": 30, "crowd_short": -20, "max_stop_pct": 12},
        "K_stop15": {"exit": {"close_stop": True}, "crowd_long": 30, "crowd_short": -20, "max_stop_pct": 15},
        "K_stop20": {"exit": {"close_stop": True}, "crowd_long": 30, "crowd_short": -20, "max_stop_pct": 20},
        "K_atr5": {"exit": {"close_stop": True}, "crowd_long": 30, "crowd_short": -20, "max_atr_pct": 5},
        "K_atr6": {"exit": {"close_stop": True}, "crowd_long": 30, "crowd_short": -20, "max_atr_pct": 6},
        "K_atr8": {"exit": {"close_stop": True}, "crowd_long": 30, "crowd_short": -20, "max_atr_pct": 8},
        "K_stop15_yg": {"exit": {"close_stop": True}, "crowd_long": 30, "crowd_short": -20, "max_stop_pct": 15,
                        "reentry": {"after_loss": 5, "fresh_high": True}, "cooldown": 1},
        "K_stop15_kl10": {"exit": {"close_stop": True}, "crowd_long": 10, "crowd_short": -20, "max_stop_pct": 15},
    },
    "final": {
        "temel": {},
        "F1_atr5_yg": {"exit": {"close_stop": True}, "crowd_long": 30, "crowd_short": -20, "max_atr_pct": 5,
                       "reentry": {"after_loss": 5, "fresh_high": True}, "cooldown": 1},
        "F2_atr5_yg_felaket2": {"exit": {"close_stop": True, "disaster": 2.0}, "crowd_long": 30, "crowd_short": -20,
                                "max_atr_pct": 5, "reentry": {"after_loss": 5, "fresh_high": True}, "cooldown": 1},
        "F3_atr5_fitilstop": {"crowd_long": 30, "crowd_short": -20, "max_atr_pct": 5,
                              "reentry": {"after_loss": 5, "fresh_high": True}, "cooldown": 1},
        "F4_atr5_kalabaliksiz": {"exit": {"close_stop": True, "disaster": 2.0}, "max_atr_pct": 5,
                                 "reentry": {"after_loss": 5, "fresh_high": True}, "cooldown": 1},
        "F5_atr5_felaket15": {"exit": {"close_stop": True, "disaster": 1.5}, "crowd_long": 30, "crowd_short": -20,
                              "max_atr_pct": 5, "reentry": {"after_loss": 5, "fresh_high": True}, "cooldown": 1},
        "F6_atr5_felaket2_geriver": {"exit": {"close_stop": True, "disaster": 2.0, "giveback": (3.0, 0.5)}, "crowd_long": 30,
                                     "crowd_short": -20, "max_atr_pct": 5, "reentry": {"after_loss": 5, "fresh_high": True}, "cooldown": 1},
    },
}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="data/LAB.md")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min_age", type=int, default=0)
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--min_qv", type=float, default=5e6)
    ap.add_argument("--set", default="temel")
    ap.add_argument("--only", default="", help="virgülle varyant adları (paralel koşu için)")
    ap.add_argument("--zar", type=int, default=0, help="Zarattini adayını da koş (1)")
    a = ap.parse_args()
    cfg = load_config()
    VS = PRESETS[a.set] if a.set in PRESETS else json.loads(a.set)
    if a.only:
        VS = {k: v for k, v in VS.items() if k in a.only.split(",")}
    res = run(Path(a.data), cfg, VS,
              min_age=a.min_age, min_qv=a.min_qv, top_n=a.top, syms_limit=a.limit, with_zar=bool(a.zar))
    rows = summarize(res)
    dump_trades(res, Path(a.out).with_suffix(".trades.json.gz"))
    md = markdown(rows, f"Laboratuvar — {a.set}")
    Path(a.out).write_text(md, encoding="utf-8")
    print(md)
