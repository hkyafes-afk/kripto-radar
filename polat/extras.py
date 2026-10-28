"""OKX dışındaki ücretsiz veriler: BTC dominansı (CoinGecko), stablecoin arzı (DefiLlama), ekonomik takvim (ForexFactory).

Token unlock tarihleri için güvenilir, anahtarsız ve ücretsiz bir kaynak yok; Polat bu veriyi KULLANMAZ
ve bunu açıkça bilir (raporda "veri yok" olarak görünür).
"""
from __future__ import annotations

import asyncio
import json
import urllib.request
from datetime import datetime, timezone

FF_URLS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
           "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json"]


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "polat/1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


async def btc_dominance() -> float | None:
    try:
        js = await asyncio.to_thread(_get, "https://api.coingecko.com/api/v3/global")
        return float(js["data"]["market_cap_percentage"]["btc"])
    except Exception:  # noqa: BLE001
        return None


async def stablecoins() -> tuple[float, float] | None:
    """(toplam USD arzı, 24 saatlik değişim %)."""
    try:
        js = await asyncio.to_thread(_get, "https://stablecoins.llama.fi/stablecoincharts/all?stablecoin=1")
        if not isinstance(js, list) or len(js) < 2:
            js = await asyncio.to_thread(_get, "https://stablecoins.llama.fi/stablecoincharts/all")
        a = float(js[-1]["totalCirculatingUSD"]["peggedUSD"])
        b = float(js[-2]["totalCirculatingUSD"]["peggedUSD"])
        return a, (a / b - 1) * 100 if b else 0.0
    except Exception:  # noqa: BLE001
        return None


def parse_ff(rows: list) -> list[dict]:
    out = []
    for r in rows or []:
        if r.get("impact") != "High" or r.get("country") not in ("USD", "ALL"):
            continue
        try:
            ts = int(datetime.fromisoformat(r["date"]).astimezone(timezone.utc).timestamp() * 1000)
        except Exception:  # noqa: BLE001
            continue
        out.append({"ts": ts, "title": r.get("title", "")})
    return sorted(out, key=lambda x: x["ts"])


async def calendar() -> list[dict] | None:
    for u in FF_URLS:
        try:
            return parse_ff(await asyncio.to_thread(_get, u))
        except Exception:  # noqa: BLE001
            continue
    return None
