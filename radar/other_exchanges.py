"""Diğer borsaların anlık funding oranları (karşılaştırma için, en iyi çaba).

Erişilemeyen (ör. Binance'in ABD IP'lerine verdiği 451) borsa sessizce atlanır.
Dönen yapı: {"BTC": {"binance": 12.3, "bybit": 10.9, ...}}  → yıllık %.
"""
from __future__ import annotations

import requests

UA = {"User-Agent": "Mozilla/5.0 kripto-radar/1.0"}
T = 12


def _get(url, params=None):
    r = requests.get(url, params=params, headers=UA, timeout=T)
    r.raise_for_status()
    return r.json()


def _ann(rate: float, interval_h: float) -> float:
    return rate * (8760.0 / interval_h) * 100.0


def _binance() -> dict[str, float]:
    data = _get("https://fapi.binance.com/fapi/v1/premiumIndex")
    intervals = {}
    try:
        for x in _get("https://fapi.binance.com/fapi/v1/fundingInfo"):
            intervals[x["symbol"]] = float(x.get("fundingIntervalHours") or 8)
    except Exception:
        pass
    out = {}
    for x in data:
        sym = x.get("symbol", "")
        if sym.endswith("USDT"):
            out[sym[:-4]] = _ann(float(x["lastFundingRate"]), intervals.get(sym, 8.0))
    return out


def _bybit() -> dict[str, float]:
    tick = _get("https://api.bybit.com/v5/market/tickers", {"category": "linear"})
    intervals = {}
    try:
        cursor = ""
        for _ in range(5):
            p = {"category": "linear", "limit": "1000"}
            if cursor:
                p["cursor"] = cursor
            info = _get("https://api.bybit.com/v5/market/instruments-info", p)["result"]
            for x in info.get("list", []):
                intervals[x["symbol"]] = float(x.get("fundingInterval") or 480) / 60.0
            cursor = info.get("nextPageCursor") or ""
            if not cursor:
                break
    except Exception:
        pass
    out = {}
    for x in tick["result"]["list"]:
        sym = x.get("symbol", "")
        fr = x.get("fundingRate")
        if sym.endswith("USDT") and fr not in (None, ""):
            out[sym[:-4]] = _ann(float(fr), intervals.get(sym, 8.0))
    return out


def _bitget() -> dict[str, float]:
    tick = _get("https://api.bitget.com/api/v2/mix/market/tickers",
                {"productType": "USDT-FUTURES"})
    intervals = {}
    try:
        cur = _get("https://api.bitget.com/api/v2/mix/market/current-fund-rate",
                   {"productType": "USDT-FUTURES"})
        for x in cur.get("data", []) or []:
            if x.get("fundingRateInterval"):
                intervals[x["symbol"]] = float(x["fundingRateInterval"])
    except Exception:
        pass
    out = {}
    for x in tick.get("data", []) or []:
        sym = x.get("symbol", "")
        fr = x.get("fundingRate")
        if sym.endswith("USDT") and fr not in (None, ""):
            out[sym[:-4]] = _ann(float(fr), intervals.get(sym, 8.0))
    return out


def _gate() -> dict[str, float]:
    data = _get("https://api.gateio.ws/api/v4/futures/usdt/contracts")
    out = {}
    for x in data:
        name = x.get("name", "")
        if name.endswith("_USDT") and x.get("funding_rate") not in (None, ""):
            iv = float(x.get("funding_interval") or 28800) / 3600.0
            out[name[:-5]] = _ann(float(x["funding_rate"]), iv)
    return out


def _mexc() -> dict[str, float]:
    js = _get("https://contract.mexc.com/api/v1/contract/funding_rate")
    out = {}
    for x in js.get("data", []) or []:
        sym = x.get("symbol", "")
        if sym.endswith("_USDT") and x.get("fundingRate") not in (None, ""):
            iv = float(x.get("collectCycle") or 8)
            out[sym[:-5]] = _ann(float(x["fundingRate"]), iv)
    return out


def _htx() -> dict[str, float]:
    js = _get("https://api.hbdm.com/linear-swap-api/v1/swap_batch_funding_rate")
    out = {}
    for x in js.get("data", []) or []:
        code = x.get("contract_code", "")
        if code.endswith("-USDT") and x.get("funding_rate") not in (None, ""):
            iv = 8.0
            try:
                ft, nft = float(x.get("funding_time") or 0), float(x.get("next_funding_time") or 0)
                if nft > ft > 0:
                    iv = max(1.0, min(8.0, (nft - ft) / 3.6e6))
            except (TypeError, ValueError):
                pass
            out[code[:-5]] = _ann(float(x["funding_rate"]), iv)
    return out


FETCHERS = {"binance": _binance, "bybit": _bybit, "bitget": _bitget, "gate": _gate,
            "mexc": _mexc, "htx": _htx}


def arbitrage(by_coin: dict[str, dict[str, float]], universe_syms: set[str], min_spread: float = 15.0,
              top: int = 15) -> list[dict]:
    """Aynı coinin borsalar arasındaki yıllık funding farkı.

    Mantık: funding'i düşük borsada long, yüksek borsada short → fiyat riski ~0, fark kadar funding toplanır.
    Uyarı: borsa başına teminat, transfer, komisyon ve farklı funding aralıkları gerçek getiriyi düşürür.
    """
    out = []
    for sym in universe_syms:
        v = {k: x for k, x in (by_coin.get(sym) or {}).items() if x == x}
        if len(v) < 2:
            continue
        lo_k = min(v, key=v.get)
        hi_k = max(v, key=v.get)
        spread = v[hi_k] - v[lo_k]
        if spread >= min_spread:
            out.append({"sym": sym, "spread": round(spread, 1), "long_at": lo_k, "lo": round(v[lo_k], 1),
                        "short_at": hi_k, "hi": round(v[hi_k], 1), "venues": v})
    out.sort(key=lambda r: -r["spread"])
    return out[:top]


def fetch_all(names: list[str]) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    """Döner: (coin → {borsa: yıllık%}), (borsa → durum)."""
    by_coin: dict[str, dict[str, float]] = {}
    status: dict[str, str] = {}
    for name in names:
        fn = FETCHERS.get(name)
        if not fn:
            continue
        try:
            res = fn()
            status[name] = f"ok ({len(res)})"
            for coin, v in res.items():
                if v == v:  # NaN değil
                    by_coin.setdefault(coin, {})[name] = round(v, 1)
        except Exception as e:  # noqa: BLE001 — en iyi çaba
            msg = str(e)
            status[name] = "erişim yok" if ("451" in msg or "403" in msg) else f"hata: {msg[:60]}"
    return by_coin, status
