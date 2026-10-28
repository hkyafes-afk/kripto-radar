"""CoinAPI (APIbricks) ölçüm denemesi: her veri türünün kaç kredi harcadığını KÜÇÜK isteklerle ölçer.

Para güvenliği: ücretsiz 25$ kredi aşılırsa kart çekilir. Bu betik yalnızca küçük `limit`li istekler atar,
her yanıtın `x-credits-used` başlığını toplar ve BUTCE_KREDI'yi geçmeden durur. Büyük veri çekimi YOK;
sonuç raporuna göre ayrı ve onaylı bir toplama adımı yazılır.
Anahtar ortam değişkeninden okunur (COINAPI_KEY, GitHub gizli değişkeni); hiçbir yere yazılmaz.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://rest.coinapi.io"
BUTCE_KREDI = float(os.environ.get("BUTCE_KREDI", "10"))   # bu denemenin harcayabileceği en fazla kredi
KEY = os.environ.get("COINAPI_KEY", "")
OUT = sys.argv[1] if len(sys.argv) > 1 else "coinapi_rapor.json"
rep = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "butce_kredi": BUTCE_KREDI, "istekler": [],
       "toplam_kredi": 0.0, "hata": None}


def call(path: str, params: dict | None = None, label: str = "") -> object | None:
    if rep["toplam_kredi"] >= BUTCE_KREDI:
        rep["istekler"].append({"etiket": label, "atlandi": "bütçe doldu"})
        return None
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"X-CoinAPI-Key": KEY, "Accept": "application/json"})
    row = {"etiket": label, "yol": path, "param": params}
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            h = {k.lower(): v for k, v in r.headers.items()}
            row["durum"] = r.status
    except urllib.error.HTTPError as e:
        body = e.read()
        h = {k.lower(): v for k, v in e.headers.items()}
        row["durum"] = e.code
    except Exception as e:  # noqa: BLE001
        row["durum"], row["hata"] = None, f"{type(e).__name__}: {e}"
        rep["istekler"].append(row)
        return None
    row["sure_sn"] = round(time.time() - t0, 2)
    row["bayt"] = len(body)
    row["basliklar"] = {k: v for k, v in h.items() if k.startswith("x-")}
    used = h.get("x-credits-used") or h.get("x-ratelimit-request-cost")
    try:
        row["kredi"] = float(used) if used is not None else None
    except ValueError:
        row["kredi"] = None
    rep["toplam_kredi"] += row["kredi"] or 0.0
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        data = None
    if isinstance(data, list):
        row["kayit"] = len(data)
        row["ornek"] = data[:2]
    elif isinstance(data, dict):
        row["ornek"] = {k: data[k] for k in list(data)[:12]}
    rep["istekler"].append(row)
    return data


def main():
    if not KEY:
        rep["hata"] = "COINAPI_KEY yok (GitHub → Settings → Secrets and variables → Actions)"
        return
    # 1) OKX vadeli BTC/ETH sembol kimlikleri (filtreli, küçük)
    syms = {}
    for coin in ("BTC", "ETH"):
        d = call("/v1/symbols/OKEX", {"filter_symbol_id": f"OKEX_PERP_{coin}_USDT"}, f"sembol {coin}")
        if isinstance(d, list) and d:
            syms[coin] = d[0].get("symbol_id")
            rep.setdefault("semboller", {})[coin] = {k: d[0].get(k) for k in
                                                       ("symbol_id", "data_start", "data_end", "data_orderbook_start",
                                                        "data_trade_start", "data_quote_start", "symbol_type")}
    sid = syms.get("BTC") or "OKEX_PERP_BTC_USDT"
    day = "2026-10-20T00:00:00"
    # 2) her veri türü için tek küçük istek → kredi maliyeti
    call(f"/v1/ohlcv/{sid}/history", {"period_id": "1MIN", "time_start": day, "limit": 100}, "mum 1dk ×100")
    call(f"/v1/trades/{sid}/history", {"time_start": day, "limit": 100}, "işlem ×100")
    call(f"/v1/quotes/{sid}/history", {"time_start": day, "limit": 100}, "kotasyon ×100")
    call(f"/v1/orderbooks/{sid}/history", {"time_start": day, "limit": 10, "limit_levels": 20},
         "emir defteri ×10 anlık görüntü, 20 kademe")
    call(f"/v1/orderbooks/{sid}/history", {"time_start": day, "limit": 100, "limit_levels": 20},
         "emir defteri ×100 anlık görüntü, 20 kademe")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        rep["hata"] = f"{type(e).__name__}: {e}"
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps({"toplam_kredi": rep["toplam_kredi"], "istek": len(rep["istekler"]), "hata": rep["hata"]},
                     ensure_ascii=False))
