"""OKX copy-trading halka açık uç noktalarının probu (kimlik doğrulamasız).

Amaç: hangi uç noktalar açık, ne kadar geçmiş döndürüyor, sayfalama nasıl. Trader kimlikleri
çıktıya yazılmaz; yalnızca durum kodu, alan adları ve kayıt sayıları.
"""
import json
import time
import urllib.parse
import urllib.request

BASE = "https://www.okx.com/api/v5/copytrading/"
REPORT = {}


def get(path, **params):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = BASE + path + ("?" + q if q else "")
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            body = json.loads(r.read().decode())
            return {"http": r.status, "code": body.get("code"), "msg": (body.get("msg") or "")[:120],
                    "data": body.get("data")}
    except Exception as e:  # noqa: BLE001
        return {"http": None, "error": repr(e)[:200], "data": None}


def summarize(name, res):
    d = res.get("data")
    info = {k: v for k, v in res.items() if k != "data"}
    if isinstance(d, list):
        info["n"] = len(d)
        info["alanlar"] = sorted(d[0].keys())[:40] if d and isinstance(d[0], dict) else None
    elif isinstance(d, dict):
        info["alanlar"] = sorted(d.keys())[:40]
    REPORT[name] = info
    print(name, json.dumps(info, ensure_ascii=False)[:400], flush=True)


def main():
    lt = get("public-lead-traders", instType="SWAP", sortType="pnl", limit="5")
    summarize("public-lead-traders", lt)
    codes = []
    if isinstance(lt.get("data"), list) and lt["data"]:
        first = lt["data"][0]
        codes = [x.get("uniqueCode") for x in lt["data"] if x.get("uniqueCode")]
    for name, path, extra in [
        ("public-stats", "public-stats", {}),
        ("public-current-subpositions", "public-current-subpositions", {}),
        ("public-subpositions-history", "public-subpositions-history", {"limit": "100"}),
        ("public-pnl", "public-pnl", {"period": "weekly"}),
    ]:
        if not codes:
            REPORT[name] = {"atlandı": "lider listesi boş"}
            continue
        res = get(path, instType="SWAP", uniqueCode=codes[0], **extra)
        summarize(name, res)
        time.sleep(1)
    with open("okx_probe_report.json", "w", encoding="utf-8") as f:
        json.dump(REPORT, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
