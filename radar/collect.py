"""Veri toplayıcı (v9 Faz 0 — Balina Gözü'nün hafızası).

Borsalar açık pozisyon (OI), long/short oranı ve likidasyon geçmişini kısa tutuyor (günler–haftalar). Balina Gözü
uzmanlarını ileride geçmiş veride test edebilmek için bu veriyi BUGÜNDEN biriktiriyoruz. Canlı döngüden tamamen ayrı
çalışır (saatlik GitHub işi); canlı ajanların hızını etkilemez. Kayıtlar `veri` dalında aylık JSONL dosyalarına eklenir.

Her saat (OKX public, ücretsiz):
  - Bütün SWAP'lar için açık pozisyon (tek istek) ve son fiyat/hacim (tek istek)
  - En likit N coin için: hesap bazında long/short oranı, büyük hesapların (top trader) long/short oranı,
    taker alış/satış hacmi, son likidasyonlar (uzun/kısa, adet ve dolar)
Bir kaynak hata verirse atlanır, diğerleri yazılır (kayıtta "errors" listesi).
"""
from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path


def _f(x, d=None):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def snapshot(client, n_coins: int = 40) -> dict:
    now = int(time.time() * 1000)
    rec = {"ts": now, "coins": {}, "errors": []}
    try:
        tick = {x["instId"]: x for x in client.tickers("SWAP") if x.get("instId", "").endswith("-USDT-SWAP")}
    except Exception as e:  # noqa: BLE001
        rec["errors"].append(f"tickers: {e}")
        tick = {}
    try:
        oi = {x["instId"]: x for x in client.get("/api/v5/public/open-interest", {"instType": "SWAP"})}
    except Exception as e:  # noqa: BLE001
        rec["errors"].append(f"open-interest: {e}")
        oi = {}
    for iid, x in tick.items():
        last = _f(x.get("last"), 0.0)
        o = oi.get(iid) or {}
        rec["coins"][iid.split("-")[0]] = {
            "px": last, "vol24_usd": round(_f(x.get("volCcy24h"), 0.0) * last),
            "oi_ccy": _f(o.get("oiCcy")), "oi_usd": _f(o.get("oiUsd")) or (_f(o.get("oiCcy"), 0.0) * last or None)}
    top = sorted(rec["coins"].items(), key=lambda kv: -(kv[1]["vol24_usd"] or 0))[:n_coins]
    for sym, c in top:
        inst = f"{sym}-USDT-SWAP"
        for key, path, params, parse in (
            ("ls_acct", "/api/v5/rubik/stat/contracts/long-short-account-ratio-contract",
             {"instId": inst, "period": "1H", "limit": "1"}, lambda r: _f(r[0][1]) if r and len(r[0]) > 1 else None),
            ("ls_top", "/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader",
             {"instId": inst, "period": "1H", "limit": "1"}, lambda r: _f(r[0][1]) if r and len(r[0]) > 1 else None),
            ("taker", "/api/v5/rubik/stat/taker-volume-contract",
             {"instId": inst, "period": "1H", "unit": "2", "limit": "1"},
             lambda r: {"sell": _f(r[0][1]), "buy": _f(r[0][2])} if r and len(r[0]) > 2 else None),
        ):
            try:
                c[key] = parse(client.get(path, params))
            except Exception as e:  # noqa: BLE001
                rec["errors"].append(f"{key} {sym}: {str(e)[:80]}")
        try:
            rows = client.get("/api/v5/public/liquidation-orders",
                              {"instType": "SWAP", "uly": f"{sym}-USDT", "state": "filled", "limit": "100"})
            det = [d for r in rows for d in (r.get("details") or [])]
            cut = now - 3600_000
            lq = {"long_n": 0, "short_n": 0, "long_usd": 0.0, "short_usd": 0.0}
            for d in det:
                if _f(d.get("ts"), 0) < cut:
                    continue
                usd = _f(d.get("sz"), 0.0) * _f(d.get("bkPx"), 0.0) * _ct(client, inst)
                side = "long" if d.get("posSide") == "long" or d.get("side") == "sell" else "short"
                lq[f"{side}_n"] += 1
                lq[f"{side}_usd"] += usd
            lq["long_usd"], lq["short_usd"] = round(lq["long_usd"]), round(lq["short_usd"])
            c["liq_1h"] = lq
        except Exception as e:  # noqa: BLE001
            rec["errors"].append(f"liq {sym}: {str(e)[:80]}")
    return rec


HL_INFO = "https://api.hyperliquid.xyz/info"
HL_BOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"


def _http_json(url: str, body: dict | None = None, timeout: float = 20.0):
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                          "User-Agent": "kripto-radar/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _perf(row: dict, window: str) -> dict:
    for w in row.get("windowPerformances") or []:
        if isinstance(w, list) and len(w) == 2 and w[0] == window:
            return w[1] or {}
    return {}


def hyperliquid_smart_money(n_wallets: int = 40, min_account: float = 1_000_000, fetch=_http_json) -> dict:
    """Hyperliquid (her şey zincir üstünde, herkese açık): tüm zamanlarda ve son ayda kârlı, en az 1M$ hesaplı cüzdanların
    şu anki pozisyonları → coin başına 'akıllı para' net yönü. Kopyalamak için değil; kalabalığın kârlı kesimi nerede?"""
    board = fetch(HL_BOARD)
    rows = board.get("leaderboardRows") if isinstance(board, dict) else board
    picks = []
    for r in rows or []:
        try:
            acct = float(r.get("accountValue") or 0)
            at, mo = _perf(r, "allTime"), _perf(r, "month")
            if acct >= min_account and float(at.get("pnl") or 0) > 0 and float(mo.get("pnl") or 0) > 0:
                picks.append((float(at.get("pnl") or 0), r.get("ethAddress"), acct))
        except (TypeError, ValueError):
            continue
    picks.sort(reverse=True)
    coins: dict = {}
    done = 0
    for _, addr, acct in picks[:n_wallets]:
        if not addr:
            continue
        try:
            st = fetch(HL_INFO, {"type": "clearinghouseState", "user": addr})
        except Exception:  # noqa: BLE001
            continue
        done += 1
        for ap in st.get("assetPositions") or []:
            p = ap.get("position") or {}
            szi, val = _f(p.get("szi"), 0.0), _f(p.get("positionValue"), 0.0)
            if not szi or not val:
                continue
            c = coins.setdefault(p.get("coin"), {"long_usd": 0.0, "short_usd": 0.0, "n_long": 0, "n_short": 0})
            side = "long" if szi > 0 else "short"
            c[f"{side}_usd"] += val
            c[f"n_{side}"] += 1
    for c in coins.values():
        c["long_usd"], c["short_usd"] = round(c["long_usd"]), round(c["short_usd"])
        tot = c["long_usd"] + c["short_usd"]
        c["net"] = round((c["long_usd"] - c["short_usd"]) / tot, 3) if tot else 0.0
    return {"wallets": done, "candidates": len(picks), "coins": coins}


_CT: dict = {}


def _ct(client, inst: str) -> float:
    if not _CT:
        try:
            for i in client.instruments("SWAP"):
                _CT[i["instId"]] = _f(i.get("ctVal"), 1.0) or 1.0
        except Exception:  # noqa: BLE001
            _CT["_"] = 1.0
    return _CT.get(inst, 1.0)


def append(rec: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    month = datetime.fromtimestamp(rec["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m")
    p = out_dir / f"okx-{month}.jsonl.gz"
    with gzip.open(p, "at", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")
    return p


def read(out_dir: Path) -> list[dict]:
    out = []
    for p in sorted(out_dir.glob("okx-*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            out += [json.loads(line) for line in f if line.strip()]
    return out


def run(client=None, out_dir: Path | None = None, n_coins: int = 40) -> dict:
    if client is None:
        from .okx import OKXClient
        client = OKXClient()
    t0 = time.time()
    rec = snapshot(client, n_coins)
    try:
        rec["hl"] = hyperliquid_smart_money()
    except Exception as e:  # noqa: BLE001
        rec["errors"].append(f"hyperliquid: {str(e)[:120]}")
    p = append(rec, out_dir or Path("veri"))
    full = sum(1 for c in rec["coins"].values() if "ls_acct" in c)
    hl = rec.get("hl") or {}
    print(f"::notice title=Veri toplandı::{len(rec['coins'])} coin OI, {full} coin kalabalık+likidasyon, "
          f"Hyperliquid {hl.get('wallets', 0)} kârlı cüzdan / {len(hl.get('coins') or {})} coin, "
          f"{len(rec['errors'])} hata, {time.time() - t0:.0f} sn → {p.name}")
    for e in rec["errors"][:10]:
        print("  hata:", e)
    return rec
