"""Binance Vision arşivi: USDT-M vadeli günlük mumlar + funding geçmişi, BATAN COİNLER DAHİL.

Neden: 5 yıllık yarış bugün işlem gören 27–36 coinle yapılıyor (hayatta kalan yanılgısı) ve 2021'den başlıyor.
Binance Vision (data.binance.vision, MIT lisanslı resmi arşiv) listeden çıkmış coinlerin dosyalarını da saklıyor
ve vadeli verisi 2019'a gidiyor. Coin listesi S3 dizin listesinden alınır (bugünkü sembol listesinden değil),
böylece her tarihte gerçekten işlem gören evren kurulabilir.

Çıktı (--out klasörüne):
  um_1d/<SEMBOL>.csv.gz      ts_ms,open,high,low,close,volume,quote_volume,trades,taker_buy_volume (UTC gün açılışı)
  funding/<SEMBOL>.csv.gz    ts_ms,rate
  manifest.json              sembol başına ilk/son gün, gün sayısı, batmış mı, funding kaydı, atlanan dosya

Her zip, yanındaki .CHECKSUM (sha256) ile doğrulanır; tutmayan dosya atlanır ve manifest'e yazılır.
Kullanım: python tools/binance_arsiv.py --out binance [--limit 5] [--workers 32]
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DL = "https://data.binance.vision/"
NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
UA = {"User-Agent": "kripto-radar-arsiv/1.0"}
SYM_OK = re.compile(r"^[A-Z0-9]+USDT$")          # vadeli dönemli kontratlar (BTCUSDT_230331) ve USDC/BUSD hariç


def _get(url: str, tries: int = 4) -> bytes:
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.5 * (k + 1))
    raise RuntimeError("unreachable")


def s3_list(prefix: str, delimiter: str | None = "/") -> tuple[list[str], list[str]]:
    """(alt klasörler, dosyalar). S3 1000'erli sayfalar; marker ile devam."""
    dirs, keys, marker = [], [], ""
    while True:
        q = {"prefix": prefix}
        if delimiter:
            q["delimiter"] = delimiter
        if marker:
            q["marker"] = marker
        root = ET.fromstring(_get(S3 + "?" + urllib.parse.urlencode(q)))
        dirs += [e.findtext(f"{NS}Prefix") for e in root.findall(f"{NS}CommonPrefixes")]
        keys += [e.findtext(f"{NS}Key") for e in root.findall(f"{NS}Contents")]
        if root.findtext(f"{NS}IsTruncated") != "true":
            return dirs, keys
        marker = root.findtext(f"{NS}NextMarker") or (keys[-1] if keys else dirs[-1])


def fetch_zip_rows(key: str) -> list[list[str]]:
    """Zip'i indir, CHECKSUM ile doğrula, CSV satırlarını döndür (başlık satırı varsa atılır)."""
    raw = _get(DL + key)
    want = _get(DL + key + ".CHECKSUM").decode().split()[0].strip().lower()
    if hashlib.sha256(raw).hexdigest() != want:
        raise ValueError(f"checksum tutmadı: {key}")
    return parse_zip(raw)


def parse_zip(raw: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = z.namelist()[0]
        text = z.read(name).decode("utf-8", "replace")
    rows = [r for r in csv.reader(io.StringIO(text)) if r]
    if rows and not rows[0][0].strip().lstrip("-").isdigit():   # yeni dosyalarda başlık var, eskilerde yok
        rows = rows[1:]
    return rows


def kline_rows(rows: list[list[str]]) -> list[tuple]:
    out = []
    for r in rows:
        ts = int(r[0])
        if ts > 10**14:                     # bazı yeni dosyalar mikrosaniye
            ts //= 1000
        out.append((ts, r[1], r[2], r[3], r[4], r[5], r[7], r[8], r[9]))
    return out


def funding_rows(rows: list[list[str]]) -> list[tuple]:
    out = []
    for r in rows:                          # calc_time, funding_interval_hours, last_funding_rate
        ts = int(r[0])
        if ts > 10**14:
            ts //= 1000
        out.append((ts, r[-1]))
    return out


def write_gz(path: Path, header: list[str], rows: list[tuple]):
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    path.write_bytes(gzip.compress(buf.getvalue().encode(), mtime=0))   # mtime=0: aynı veri → aynı dosya (git farkı yok)


def one_symbol(sym: str, out: Path) -> dict:
    info = {"sembol": sym, "atlanan": []}
    keys = []
    for base in (f"data/futures/um/monthly/klines/{sym}/1d/", f"data/futures/um/daily/klines/{sym}/1d/"):
        try:
            keys += [k for k in s3_list(base, None)[1] if k.endswith(".zip")]
        except Exception as e:
            info["atlanan"].append(f"{base}: {e}")
    bars: dict[int, tuple] = {}
    for k in sorted(keys):                  # aylık önce, günlük sonra (aynı gün varsa günlük kazanır: daha yeni)
        try:
            for row in kline_rows(fetch_zip_rows(k)):
                bars[row[0]] = row
        except Exception as e:
            info["atlanan"].append(f"{k}: {e}")
    rows = [bars[t] for t in sorted(bars)]
    if rows:
        write_gz(out / "um_1d" / f"{sym}.csv.gz",
                 ["ts_ms", "open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy_volume"], rows)
        info["ilk_gun"] = datetime.fromtimestamp(rows[0][0] / 1000, timezone.utc).strftime("%Y-%m-%d")
        info["son_gun"] = datetime.fromtimestamp(rows[-1][0] / 1000, timezone.utc).strftime("%Y-%m-%d")
        info["gun"] = len(rows)
    fkeys = []
    try:
        fkeys = [k for k in s3_list(f"data/futures/um/monthly/fundingRate/{sym}/", None)[1] if k.endswith(".zip")]
    except Exception as e:
        info["atlanan"].append(f"funding: {e}")
    fund: dict[int, tuple] = {}
    for k in sorted(fkeys):
        try:
            for row in funding_rows(fetch_zip_rows(k)):
                fund[row[0]] = row
        except Exception as e:
            info["atlanan"].append(f"{k}: {e}")
    if fund:
        write_gz(out / "funding" / f"{sym}.csv.gz", ["ts_ms", "rate"], [fund[t] for t in sorted(fund)])
    info["funding"] = len(fund)
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="binance")
    ap.add_argument("--limit", type=int, default=0, help="deneme için ilk N sembol")
    ap.add_argument("--workers", type=int, default=24)
    a = ap.parse_args(argv)
    out = Path(a.out)
    t0 = time.time()
    dirs, _ = s3_list("data/futures/um/monthly/klines/")
    syms = sorted({d.rstrip("/").split("/")[-1] for d in dirs})
    syms = [s for s in syms if SYM_OK.match(s)]
    if a.limit:
        syms = [s for s in syms if s in ("BTCUSDT", "ETHUSDT")] + [s for s in syms if s not in ("BTCUSDT", "ETHUSDT")][: max(0, a.limit - 2)]
    print(f"{len(syms)} USDT-M sembol (batanlar dahil)", flush=True)
    res = []
    with ThreadPoolExecutor(a.workers) as ex:
        for i, info in enumerate(ex.map(lambda s: one_symbol(s, out), syms), 1):
            res.append(info)
            if i % 25 == 0 or i == len(syms):
                print(f"  {i}/{len(syms)} · {time.time() - t0:.0f} sn", flush=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    last = max((r.get("son_gun", "") for r in res), default=today)
    for r in res:                           # en yeni günden 3 günden fazla geride biten = listeden çıkmış
        r["batti"] = bool(r.get("son_gun")) and (
            datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(r["son_gun"], "%Y-%m-%d")).days > 3
    man = {"olusturuldu": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "kaynak": "data.binance.vision (MIT)",
           "sembol": len(res), "veri_olan": sum(1 for r in res if r.get("gun")),
           "batan": sum(1 for r in res if r.get("batti")), "en_yeni_gun": last,
           "atlanan_dosya": sum(len(r["atlanan"]) for r in res), "sure_sn": round(time.time() - t0),
           "semboller": sorted(res, key=lambda r: r["sembol"])}
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ {man['veri_olan']} sembol · {man['batan']} batmış · atlanan dosya {man['atlanan_dosya']} · {man['sure_sn']} sn")
    return 0


if __name__ == "__main__":
    sys.exit(main())
