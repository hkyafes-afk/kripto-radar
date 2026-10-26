"""Otomatik kağıt üstü işlem defteri (paper trading).

Her güçlü sinyal sanal bir pozisyon olarak açılır; stop / hedef / süre dolumu
kurallarıyla 1 saatlik mumlara bakılarak otomatik kapatılır. Böylece Excel
tutmadan her setup'ın gerçek karnesi (isabet, ortalama R, beklenti) çıkar.

R = risk birimi. Stop'a giden işlem -1R, 2R hedefe giden işlem +2R (masraflar hariç).
Beklenti (expectancy) > 0 ise setup uzun vadede kazandırıyor demektir.
"""
from __future__ import annotations

import time
import uuid

import numpy as np
import pandas as pd


def empty() -> dict:
    return {"version": 1, "open": [], "closed": []}


def _now_ms() -> int:
    return int(time.time() * 1000)


def open_new(ledger: dict, signals: list[dict], cfg: dict, now_ms: int | None = None) -> list[dict]:
    """Güçlü sinyallerden yeni sanal pozisyon açar. Açılanları döner."""
    now_ms = now_ms or _now_ms()
    d = cfg["defter"]
    cooldown = d["tekrar_bekleme_saat"] * 3600 * 1000
    opened = []
    open_keys = {(t["inst"], t["setup"]) for t in ledger["open"]}
    recent_closed = {}
    for t in ledger["closed"]:
        k = (t["inst"], t["setup"])
        recent_closed[k] = max(recent_closed.get(k, 0), t.get("exit_ts") or 0)
    for s in signals:
        k = (s["inst"], s["setup"])
        if k in open_keys:
            continue
        if now_ms - recent_closed.get(k, 0) < cooldown:
            continue
        # Aynı coinde zıt yönlü açık pozisyon varsa yenisini açma (çelişki)
        if any(t["inst"] == s["inst"] and t["side"] != s["side"] for t in ledger["open"]):
            continue
        t = {
            "id": uuid.uuid4().hex[:10],
            "inst": s["inst"], "sym": s["sym"], "setup": s["setup"], "side": s["side"],
            "kind": s["kind"], "score": s["score"], "raw": s["raw"],
            "entry_ts": now_ms, "entry": s["entry"], "sl": s["sl"], "tp": s["tp"],
            "sl_pct": s["sl_pct"], "max_saat": s["max_saat"],
            "funding_rate": s.get("funding_rate", 0.0) or 0.0,
            "interval_h": s.get("interval_h", 8) or 8,
            "regime": s.get("regime", 0), "tags": s.get("tags", []),
            "reasons": s.get("reasons", [])[:6],
            "last": s["entry"], "r_now": 0.0, "mfe_r": 0.0, "mae_r": 0.0,
        }
        ledger["open"].append(t)
        open_keys.add(k)
        opened.append(t)
    return opened


def _close(t: dict, exit_px: float, exit_ts: int, outcome: str, cfg: dict) -> dict:
    d = cfg["defter"]
    sgn = 1 if t["side"] == "long" else -1
    gross = sgn * (exit_px / t["entry"] - 1) * 100
    hours = max(0.0, (exit_ts - t["entry_ts"]) / 3.6e6)
    n_fund = int(hours // t["interval_h"])
    funding_cost = sgn * t["funding_rate"] * n_fund * 100   # long pozitif funding öder
    net = gross - d["komisyon_gidis_donus"] - d["kayma"] - funding_cost
    r = net / t["sl_pct"] if t["sl_pct"] else 0.0
    t.update({
        "exit_ts": exit_ts, "exit": exit_px, "outcome": outcome,
        "pnl_pct": round(net, 3), "r": round(r, 3), "hours": round(hours, 1),
    })
    t.pop("last", None)
    t.pop("r_now", None)
    return t


def update(ledger: dict, fetch_1h, prices: dict[str, float], cfg: dict,
           now_ms: int | None = None) -> list[dict]:
    """Açık pozisyonları 1S mumlarla değerlendirir. Kapananları döner.

    fetch_1h(inst) → DataFrame(open/high/low/close, UTC index = mum açılışı)
    Aynı mumda hem stop hem hedef görüldüyse muhafazakâr olarak STOP sayılır.
    """
    now_ms = now_ms or _now_ms()
    closed, still = [], []
    for t in ledger["open"]:
        sgn = 1 if t["side"] == "long" else -1
        deadline = t["entry_ts"] + t["max_saat"] * 3600 * 1000
        try:
            df = fetch_1h(t["inst"])
        except Exception as e:  # noqa: BLE001
            print(f"[defter] {t['inst']} mum alınamadı: {e}")
            df = None
        result = None
        if df is not None and len(df):
            # pandas sürümünden bağımsız (ns/ms çözünürlük) milisaniye dönüşümü
            ts_ms = ((df.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy()
            mask = (ts_ms >= t["entry_ts"]) & (ts_ms < deadline)
            sub = df[mask]
            sub_ts = ts_ms[mask]
            risk = abs(t["entry"] - t["sl"]) or 1e-12
            for (idx, row), bts in zip(sub.iterrows(), sub_ts):
                fav = (row["high"] - t["entry"]) if sgn > 0 else (t["entry"] - row["low"])
                adv = (t["entry"] - row["low"]) if sgn > 0 else (row["high"] - t["entry"])
                t["mfe_r"] = round(max(t.get("mfe_r", 0.0), fav / risk), 2)
                t["mae_r"] = round(max(t.get("mae_r", 0.0), adv / risk), 2)
                hit_sl = row["low"] <= t["sl"] if sgn > 0 else row["high"] >= t["sl"]
                hit_tp = row["high"] >= t["tp"] if sgn > 0 else row["low"] <= t["tp"]
                bar_end = int(bts) + 3600 * 1000
                if hit_sl:
                    result = (t["sl"], bar_end, "stop")
                    break
                if hit_tp:
                    result = (t["tp"], bar_end, "hedef")
                    break
            if result is None and now_ms >= deadline and len(sub):
                result = (float(sub["close"].iloc[-1]), deadline, "süre")
        if result is None and now_ms >= deadline + 6 * 3600 * 1000:
            # Veri hiç gelmiyorsa (coin delist vb.) son bilinen fiyattan kapat
            px = prices.get(t["inst"], t.get("last", t["entry"]))
            result = (px, now_ms, "süre")
        if result:
            closed.append(_close(t, result[0], result[1], result[2], cfg))
        else:
            px = prices.get(t["inst"])
            if px:
                t["last"] = px
                risk = abs(t["entry"] - t["sl"]) or 1e-12
                t["r_now"] = round(sgn * (px - t["entry"]) / risk, 2)
            still.append(t)
    ledger["open"] = still
    ledger["closed"].extend(closed)
    return closed


# ── istatistik ─────────────────────────────────────────────────────────

def _summ(trs: list[dict]) -> dict:
    if not trs:
        return {"n": 0}
    r = np.array([t["r"] for t in trs], dtype=float)
    pnl = np.array([t["pnl_pct"] for t in trs], dtype=float)
    wins = r > 0
    gp, gl = r[wins].sum(), -r[~wins].sum()
    return {
        "n": int(len(r)),
        "win_rate": round(float(wins.mean()) * 100, 1),
        "avg_r": round(float(r.mean()), 3),
        "sum_r": round(float(r.sum()), 2),
        "pf": round(float(gp / gl), 2) if gl > 0 else None,
        "avg_pnl": round(float(pnl.mean()), 2),
        "best_r": round(float(r.max()), 2), "worst_r": round(float(r.min()), 2),
        "avg_hours": round(float(np.mean([t.get("hours", 0) for t in trs])), 1),
    }


def verdict(s: dict, min_n: int) -> dict:
    n = s.get("n", 0)
    if n < min_n:
        return {"code": "collect", "text": f"Veri toplanıyor ({n}/{min_n})"}
    if s["avg_r"] > 0.1 and (s.get("pf") or 0) > 1.2:
        return {"code": "good", "text": "Olumlu: pozitif beklenti"}
    if s["avg_r"] < 0:
        return {"code": "bad", "text": "Olumsuz: bu setup'a güvenme"}
    return {"code": "meh", "text": "Belirsiz: başabaş civarı"}


def stats(ledger: dict, cfg: dict) -> dict:
    min_n = cfg["defter"]["min_ornek"]
    closed = ledger["closed"]
    out = {"overall": _summ(closed), "by_setup": {}, "by_score": {}, "by_tag": [],
           "open_count": len(ledger["open"])}
    out["overall"]["verdict"] = verdict(out["overall"], min_n)
    for key in ("trend_long", "trend_short", "donus_long", "donus_short"):
        s = _summ([t for t in closed if t["setup"] == key])
        s["verdict"] = verdict(s, min_n)
        s["open"] = sum(1 for t in ledger["open"] if t["setup"] == key)
        out["by_setup"][key] = s
    for lo, hi, name in ((0, 75, "65-74"), (75, 85, "75-84"), (85, 101, "85+")):
        out["by_score"][name] = _summ([t for t in closed if lo <= t["score"] < hi])
    # Hangi "malzeme" işe yarıyor? Etiket varken / yokken ortalama R
    tags = sorted({tg for t in closed for tg in t.get("tags", [])})
    for tg in tags:
        with_t = [t["r"] for t in closed if tg in t.get("tags", [])]
        without = [t["r"] for t in closed if tg not in t.get("tags", [])]
        if len(with_t) >= 5:
            out["by_tag"].append({
                "tag": tg, "n": len(with_t), "avg_r": round(float(np.mean(with_t)), 3),
                "avg_r_without": round(float(np.mean(without)), 3) if without else None,
            })
    out["by_tag"].sort(key=lambda x: x["avg_r"], reverse=True)
    # Son 30 gün eğrisi
    cut = _now_ms() - 30 * 86400 * 1000
    out["last30"] = _summ([t for t in closed if (t.get("exit_ts") or 0) >= cut])
    eq, cum = [], 0.0
    for t in sorted(closed, key=lambda x: x.get("exit_ts") or 0):
        cum += t["r"]
        eq.append([t.get("exit_ts"), round(cum, 2)])
    out["equity"] = eq[-300:]
    return out
