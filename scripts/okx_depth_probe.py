"""OKX genel piyasa verisinin geçmiş derinliği (kimlik doğrulamasız, halka açık uç noktalar).

Her uç nokta için: en eski kaydın zamanı, toplam kayıt, sayfa sayısı. Bot korumasını aşan
bir yöntem kullanılmaz; yalnızca standart istekler. Hata alınırsa hata olarak raporlanır.
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://www.okx.com/api/v5/"
OUT = {}


def get(path, **params):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    try:
        req = urllib.request.Request(BASE + path + "?" + q, headers={"User-Agent": "polat/2"})
        with urllib.request.urlopen(req, timeout=20) as r:
            b = json.loads(r.read().decode())
            return {"code": b.get("code"), "msg": (b.get("msg") or "")[:120], "data": b.get("data") or []}
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e)[:200], "data": []}


def ts(ms):
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def walk_candles(inst, bar, max_pages=400):
    """history-candles: 'after' ile geriye yürü; en eski zamanı ve sayfa sayısını ölç."""
    oldest, pages, total, after = None, 0, 0, None
    for _ in range(max_pages):
        res = get("market/history-candles", instId=inst, bar=bar, limit="100", after=after)
        d = res.get("data") or []
        if "error" in res or not d:
            OUT.setdefault(f"candles_{inst}_{bar}", {})["son_yanit"] = res.get("error") or res.get("msg") or "boş"
            break
        pages += 1
        total += len(d)
        oldest = min(int(x[0]) for x in d)
        after = str(oldest)
        time.sleep(0.25)
    OUT.setdefault(f"candles_{inst}_{bar}", {}).update({"sayfa": pages, "kayit": total,
                                    "en_eski": ts(oldest) if oldest else None})


def walk_funding(inst, max_pages=200):
    oldest, pages, total, after = None, 0, 0, None
    for _ in range(max_pages):
        res = get("public/funding-rate-history", instId=inst, limit="100", after=after)
        d = res.get("data") or []
        if "error" in res or not d:
            OUT.setdefault(f"funding_{inst}", {})["son_yanit"] = res.get("error") or res.get("msg") or "boş"
            break
        pages += 1
        total += len(d)
        oldest = min(int(x["fundingTime"]) for x in d)
        after = str(oldest)
        time.sleep(0.25)
    OUT.setdefault(f"funding_{inst}", {}).update({"sayfa": pages, "kayit": total, "en_eski": ts(oldest) if oldest else None})


def main():
    for inst in ["BTC-USDT-SWAP"]:
        for bar in ["1H", "4H", "1Dutc"]:
            walk_candles(inst, bar)
        walk_funding(inst)
    with open("okx_depth_report.json", "w", encoding="utf-8") as f:
        json.dump(OUT, f, ensure_ascii=False, indent=2)
    print(json.dumps(OUT, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
