"""Demo hesap / kağıt üstü işlem defteri.

Her güçlü sinyal sanal bir pozisyon açar (varsayılan 20$). Pozisyon stop / hedef /
süre dolumu kurallarıyla, canlı döngüde DAKİKALIK mumlara bakılarak kapatılır.
Komisyon, kayma ve funding ödemesi düşülür; sonuç hem dolar hem R olarak kaydedilir.

R = risk birimi. Stop'a giden işlem ≈ -1R, 2R hedefe giden işlem ≈ +2R (masraflar hariç).
"""
from __future__ import annotations

import time
import uuid

import numpy as np
import pandas as pd


def empty() -> dict:
    return {"version": 2, "open": [], "closed": []}


def _now_ms() -> int:
    return int(time.time() * 1000)


def demo_cfg(cfg: dict) -> dict:
    d = {"baslangic_bakiye": 1000.0, "islem_tutari_usd": 20.0, "kaldirac": 1.0,
         "max_acik_toplam": 20, "max_acik_periyot": 8}
    d.update(cfg.get("demo") or {})
    return d


def migrate(ledger: dict, cfg: dict) -> dict:
    """Eski (v1) kayıtlara dolar alanlarını ekler."""
    dc = demo_cfg(cfg)
    for t in ledger.get("open", []) + ledger.get("closed", []):
        t.setdefault("tf", "4H")
        if "margin_usd" not in t:
            t["margin_usd"] = float(dc["islem_tutari_usd"])
            t["lev"] = 1.0
            t["notional_usd"] = t["margin_usd"]
            t["qty"] = t["notional_usd"] / t["entry"] if t.get("entry") else 0.0
        if "pnl_pct" in t and "pnl_usd" not in t:
            t["pnl_usd"] = round(t["notional_usd"] * t["pnl_pct"] / 100, 4)
    ledger["version"] = 2
    return ledger


def balance(ledger: dict, cfg: dict) -> dict:
    dc = demo_cfg(cfg)
    realized = sum(t.get("pnl_usd", 0.0) for t in ledger["closed"])
    in_use = sum(t.get("margin_usd", 0.0) for t in ledger["open"])
    unreal = sum(t.get("upnl_usd", 0.0) for t in ledger["open"])
    cash = dc["baslangic_bakiye"] + realized
    return {"start": dc["baslangic_bakiye"], "realized": realized, "unrealized": unreal,
            "cash": cash, "in_use": in_use, "free": cash - in_use, "equity": cash + unreal}


def open_new(ledger: dict, signals: list[dict], cfg: dict, now_ms: int | None = None) -> list[dict]:
    """Güçlü sinyallerden yeni demo pozisyon açar. Açılanları (sinyal bilgisiyle) döner."""
    now_ms = now_ms or _now_ms()
    d, dc = cfg["defter"], demo_cfg(cfg)
    cooldown = d["tekrar_bekleme_saat"] * 3600 * 1000
    opened = []
    open_keys = {(t["inst"], t["setup"], t.get("tf", "4H")) for t in ledger["open"]}
    recent_closed = {}
    for t in ledger["closed"]:
        k = (t["inst"], t["setup"], t.get("tf", "4H"))
        recent_closed[k] = max(recent_closed.get(k, 0), t.get("exit_ts") or 0)
    tf_h = {"1m": 0.02, "5m": 0.08, "15m": 0.25, "30m": 0.5, "1H": 1, "2H": 2, "4H": 4}
    for s in sorted(signals, key=lambda x: (-x["score"], -tf_h.get(x.get("tf", "4H"), 4))):
        tf = s.get("tf", "4H")
        k = (s["inst"], s["setup"], tf)
        if k in open_keys or now_ms - recent_closed.get(k, 0) < cooldown:
            continue
        # Coin başına tek pozisyon: aynı coinde açık işlem varsa (aynı ya da zıt yön) yenisini açma
        if any(t["inst"] == s["inst"] for t in ledger["open"]):
            continue
        if len(ledger["open"]) >= dc["max_acik_toplam"]:
            break
        if sum(1 for t in ledger["open"] if t.get("tf", "4H") == tf) >= dc["max_acik_periyot"]:
            continue
        margin = float(dc["islem_tutari_usd"])
        if balance(ledger, cfg)["free"] < margin:
            break
        lev = float(dc["kaldirac"])
        notional = margin * lev
        t = {
            "id": uuid.uuid4().hex[:10],
            "inst": s["inst"], "sym": s["sym"], "setup": s["setup"], "side": s["side"],
            "kind": s["kind"], "tf": tf, "score": s["score"], "raw": s["raw"],
            "entry_ts": now_ms, "entry": s["entry"], "sl": s["sl"], "tp": s["tp"],
            "sl_pct": s["sl_pct"], "tp_pct": s["tp_pct"], "max_saat": s["max_saat"],
            "margin_usd": margin, "lev": lev, "notional_usd": notional,
            "qty": notional / s["entry"] if s["entry"] else 0.0,
            "funding_rate": s.get("funding_rate", 0.0) or 0.0,
            "interval_h": s.get("interval_h", 8) or 8,
            "regime": s.get("regime", 0), "tags": s.get("tags", []),
            "reasons": s.get("reasons", [])[:6],
            "last": s["entry"], "r_now": 0.0, "upnl_usd": 0.0, "mfe_r": 0.0, "mae_r": 0.0,
        }
        ledger["open"].append(t)
        open_keys.add(k)
        opened.append({**s, **t})
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
    notional = t.get("notional_usd", 20.0)
    t.update({
        "exit_ts": exit_ts, "exit": exit_px, "outcome": outcome,
        "pnl_pct": round(net, 3), "r": round(r, 3), "hours": round(hours, 2),
        "pnl_usd": round(notional * net / 100, 4),
        "roi_pct": round(notional * net / 100 / t.get("margin_usd", notional) * 100, 2),
        "fee_usd": round(notional * (d["komisyon_gidis_donus"] + d["kayma"]) / 100, 4),
        "funding_usd": round(notional * funding_cost / 100, 4),
    })
    for k in ("last", "r_now", "upnl_usd"):
        t.pop(k, None)
    return t


def update(ledger: dict, fetch, prices: dict[str, float], cfg: dict,
           now_ms: int | None = None, bar_ms: int = 3600_000) -> list[dict]:
    """Açık pozisyonları mumlarla değerlendirir. Kapananları döner.

    fetch(inst) → DataFrame(open/high/low/close, UTC index = mum açılışı), bar süresi bar_ms.
    Aynı mumda hem stop hem hedef görüldüyse muhafazakâr olarak STOP sayılır.
    """
    now_ms = now_ms or _now_ms()
    closed, still = [], []
    for t in ledger["open"]:
        sgn = 1 if t["side"] == "long" else -1
        deadline = t["entry_ts"] + t["max_saat"] * 3600 * 1000
        try:
            df = fetch(t["inst"])
        except Exception as e:  # noqa: BLE001
            print(f"[defter] {t['inst']} mum alınamadı: {e}")
            df = None
        result = None
        risk = abs(t["entry"] - t["sl"]) or 1e-12
        if df is not None and len(df):
            # pandas sürümünden bağımsız (ns/ms çözünürlük) milisaniye dönüşümü
            ts_ms = ((df.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy()
            # Giriş anından SONRA açılan mumlar (girişten önceki fiyatlar sayılmasın)
            mask = (ts_ms >= t["entry_ts"]) & (ts_ms < deadline)
            sub = df[mask]
            sub_ts = ts_ms[mask]
            for (_, row), bts in zip(sub.iterrows(), sub_ts):
                fav = (row["high"] - t["entry"]) if sgn > 0 else (t["entry"] - row["low"])
                adv = (t["entry"] - row["low"]) if sgn > 0 else (row["high"] - t["entry"])
                t["mfe_r"] = round(max(t.get("mfe_r", 0.0), fav / risk), 2)
                t["mae_r"] = round(max(t.get("mae_r", 0.0), adv / risk), 2)
                hit_sl = row["low"] <= t["sl"] if sgn > 0 else row["high"] >= t["sl"]
                hit_tp = row["high"] >= t["tp"] if sgn > 0 else row["low"] <= t["tp"]
                bar_end = min(int(bts) + bar_ms, now_ms)
                if hit_sl:
                    result = (t["sl"], bar_end, "stop")
                    break
                if hit_tp:
                    result = (t["tp"], bar_end, "hedef")
                    break
            if result is None and now_ms >= deadline:
                px = prices.get(t["inst"]) or (float(sub["close"].iloc[-1]) if len(sub) else None)
                if px:
                    result = (px, min(now_ms, deadline + bar_ms), "süre")
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
                t["r_now"] = round(sgn * (px - t["entry"]) / risk, 2)
                t["upnl_usd"] = round(t.get("notional_usd", 20.0) * sgn * (px / t["entry"] - 1), 4)
            still.append(t)
    ledger["open"] = still
    ledger["closed"].extend(closed)
    return closed


def mark(ledger: dict, prices: dict[str, float]) -> None:
    """Açık pozisyonların anlık K/Z'sini günceller (mum çekmeden)."""
    for t in ledger["open"]:
        px = prices.get(t["inst"])
        if not px:
            continue
        sgn = 1 if t["side"] == "long" else -1
        risk = abs(t["entry"] - t["sl"]) or 1e-12
        t["last"] = px
        t["r_now"] = round(sgn * (px - t["entry"]) / risk, 2)
        t["upnl_usd"] = round(t.get("notional_usd", 20.0) * sgn * (px / t["entry"] - 1), 4)


def near_levels(ledger: dict, prices: dict[str, float]) -> list[dict]:
    """Fiyatı stop ya da hedefe değmiş görünen pozisyonlar (hemen mum kontrolü gerekir)."""
    out = []
    for t in ledger["open"]:
        px = prices.get(t["inst"])
        if not px:
            continue
        if t["side"] == "long" and (px <= t["sl"] or px >= t["tp"]):
            out.append(t)
        elif t["side"] == "short" and (px >= t["sl"] or px <= t["tp"]):
            out.append(t)
    return out


# ── istatistik ─────────────────────────────────────────────────────────

def _summ(trs: list[dict]) -> dict:
    if not trs:
        return {"n": 0}
    r = np.array([t["r"] for t in trs], dtype=float)
    pnl = np.array([t["pnl_pct"] for t in trs], dtype=float)
    usd = np.array([t.get("pnl_usd", 0.0) for t in trs], dtype=float)
    wins = r > 0
    gp, gl = r[wins].sum(), -r[~wins].sum()
    return {
        "n": int(len(r)),
        "win_rate": round(float(wins.mean()) * 100, 1),
        "avg_r": round(float(r.mean()), 3),
        "sum_r": round(float(r.sum()), 2),
        "pf": round(float(gp / gl), 2) if gl > 0 else None,
        "avg_pnl": round(float(pnl.mean()), 2),
        "pnl_usd": round(float(usd.sum()), 2),
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


def stats(ledger: dict, cfg: dict, tfs: list[str] | None = None) -> dict:
    min_n = cfg["defter"]["min_ornek"]
    closed = ledger["closed"]
    out = {"overall": _summ(closed), "by_setup": {}, "by_score": {}, "by_tag": [], "by_tf": {},
           "open_count": len(ledger["open"])}
    out["overall"]["verdict"] = verdict(out["overall"], min_n)
    for key in ("trend_long", "trend_short", "donus_long", "donus_short"):
        s = _summ([t for t in closed if t["setup"] == key])
        s["verdict"] = verdict(s, min_n)
        s["open"] = sum(1 for t in ledger["open"] if t["setup"] == key)
        out["by_setup"][key] = s
    for tf in (tfs or sorted({t.get("tf", "4H") for t in closed + ledger["open"]})):
        s = _summ([t for t in closed if t.get("tf", "4H") == tf])
        s["verdict"] = verdict(s, min_n)
        s["open"] = sum(1 for t in ledger["open"] if t.get("tf", "4H") == tf)
        out["by_tf"][tf] = s
    for lo, hi, name in ((0, 75, "65-74"), (75, 85, "75-84"), (85, 101, "85+")):
        out["by_score"][name] = _summ([t for t in closed if lo <= t["score"] < hi])
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
    cut = _now_ms() - 30 * 86400 * 1000
    out["last30"] = _summ([t for t in closed if (t.get("exit_ts") or 0) >= cut])
    eq, cum, eq_usd = [], 0.0, []
    start = demo_cfg(cfg)["baslangic_bakiye"]
    cum_usd = 0.0
    for t in sorted(closed, key=lambda x: x.get("exit_ts") or 0):
        cum += t["r"]
        cum_usd += t.get("pnl_usd", 0.0)
        eq.append([t.get("exit_ts"), round(cum, 2)])
        eq_usd.append([t.get("exit_ts"), round(start + cum_usd, 2)])
    out["equity"] = eq[-300:]
    b = balance(ledger, cfg)
    out["demo"] = {
        **{k: round(v, 2) for k, v in b.items()},
        "return_pct": round((b["equity"] / b["start"] - 1) * 100, 2) if b["start"] else 0.0,
        "trade_size": demo_cfg(cfg)["islem_tutari_usd"], "lev": demo_cfg(cfg)["kaldirac"],
        "equity_curve": eq_usd[-400:],
        "fees_usd": round(sum(t.get("fee_usd", 0.0) for t in closed), 2),
        "funding_usd": round(sum(t.get("funding_usd", 0.0) for t in closed), 2),
    }
    return out
