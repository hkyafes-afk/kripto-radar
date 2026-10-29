"""Binance Vision metrik arşivi: günlük açık pozisyon (OI), long/short oranları, taker oranı.

Kaynak: data/futures/um/daily/metrics/<SEMBOL>/<SEMBOL>-metrics-YYYY-MM-DD.zip (2020-09-01'den beri, 5 dk'lık satırlar).
Her gün için son satır (UTC 23:55 civarı) ve taker oranının günlük ortalaması tutulur.

Evren: binance-veri dalındaki son 30 günün quote hacmine göre ilk --top coin (varsayılan 100).
Neden sınırlı: her coin için her gün ayrı zip var; 600 coin × 2000 gün ≈ 1,2 milyon istek.

Kullanım: python -u tools/binance_metrik.py --veri bv --out bvm --top 100
Çıktı: bvm/oi/<SEMBOL>.csv.gz  (gun, oi_deger_son, oi_son, lsr_son, taker_ort)
       bvm/manifest.json
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import binance_arsiv as ba  # noqa: E402

START = date(2020, 9, 1)


def universe(veri: Path, top: int) -> list[str]:
    """Son 30 günün quote hacmi (binance-veri klines dosyalarından) → ilk `top` sembol."""
    scores = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp() * 1000
    for f in (veri / "um_1d").glob("*.csv.gz"):
        with gzip.open(f, "rt") as fh:
            rows = list(csv.DictReader(fh))
        scores[f.name[:-7]] = sum(float(r["quote_volume"]) for r in rows if float(r["ts_ms"]) >= cutoff)
    return [s for s, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:top]]


def day_row(raw: bytes) -> tuple | None:
    """Sütunları başlık satırından adla okur (sıra varsayımı yok). Eksik sütun → o alan boş."""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    rd = list(csv.DictReader(io.StringIO(text)))
    if not rd:
        return None
    last = rd[-1]
    taker = []
    for r in rd:
        try:
            taker.append(float(r.get("sum_taker_long_short_vol_ratio") or "nan"))
        except ValueError:
            pass
    taker = [t for t in taker if t == t]
    return (last["create_time"][:10], last.get("sum_open_interest_value", ""), last.get("sum_open_interest", ""),
            last.get("count_long_short_ratio", ""), round(sum(taker) / len(taker), 4) if taker else "")


def one(sym: str, out: Path) -> dict:
    info = {"sembol": sym, "gun": 0, "atlanan": 0}
    days = [START + timedelta(days=i) for i in range((date.today() - START).days)]
    base = f"data/futures/um/daily/metrics/{sym}/"

    def fetch(d: date):
        key = f"{base}{sym}-metrics-{d.isoformat()}.zip"
        try:
            return day_row(ba._get(ba.DL + key))
        except Exception:
            return None

    got = []
    with ThreadPoolExecutor(8) as ex:
        for r in ex.map(fetch, days):
            if r is None:
                info["atlanan"] += 1
            else:
                got.append(r)
    got.sort()
    info["gun"] = len(got)
    if got:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["gun", "oi_deger_son", "oi_son", "lsr_son", "taker_ort"])
        w.writerows(got)
        p = out / "oi" / f"{sym}.csv.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(gzip.compress(buf.getvalue().encode(), mtime=0))
        info["ilk"], info["son"] = got[0][0], got[-1][0]
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--veri", default="bv")
    ap.add_argument("--out", default="bvm")
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args(argv)
    t0 = time.time()
    out = Path(a.out)
    syms = universe(Path(a.veri), a.top)
    print(f"{len(syms)} sembol metrik için", flush=True)
    res = []
    with ThreadPoolExecutor(a.workers) as ex:
        for i, info in enumerate(ex.map(lambda s: one(s, out), syms), 1):
            res.append(info)
            print(f"  {i}/{len(syms)} {info['sembol']} {info['gun']} gün · {time.time() - t0:.0f} sn", flush=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps({
        "olusturuldu": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "sembol": len(res),
        "kaynak": "data.binance.vision metrics (MIT)", "sure_sn": round(time.time() - t0),
        "semboller": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ {len(res)} sembol · {time.time() - t0:.0f} sn", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
