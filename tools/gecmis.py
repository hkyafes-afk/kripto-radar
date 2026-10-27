"""Geçmiş veri seti toplayıcı (araştırma için, canlı döngüden bağımsız).

Kaynak: data.binance.vision (Binance'in herkese açık arşivi, ücretsiz, anahtar gerekmez). Neden Binance:
  - USDT vadeli arşivi 2020'den beri aylık dosya olarak duruyor (OKX API'si sayfa sayfa, yavaş).
  - Borsadan SİLİNMİŞ coinler de arşivde → "bugün hayatta kalanlar" yanlılığı (survivorship bias) olmaz.
  - Funding geçmişi yıllarca geriye gider (OKX API'si yalnızca son ~3 ay).
Canlı sistem OKX'te işlem yapar; likit coinlerde iki borsanın fiyatı neredeyse aynıdır. Araştırma getiriler
üzerinden yapıldığı için fark önemsizdir; OKX'e özgü şeyler (kontrat, ücret) bt2'de ayrıca modellenir.

Çıktı (her sembol için ayrı .csv.gz):
  <out>/1d/<SYM>.csv.gz     ts,open,high,low,close,volume,quote_volume,taker_buy_quote
  <out>/4h/<SYM>.csv.gz     (aynı sütunlar)
  <out>/1h/<SYM>.csv.gz     (yalnızca en likit N sembol)
  <out>/15m/<SYM>.csv.gz    (BTC, ETH)
  <out>/funding/<SYM>.csv.gz  ts,rate
  <out>/metrics/<SYM>.csv.gz  ts,oi,oi_usd,top_ls_acct,top_ls_pos,ls_acct,taker_ls_vol (5 dk; yalnızca --metrics N)
  <out>/semboller.json      sembol listesi, ilk/son ay, son ay hacmi
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import gzip
import io
import json
import re
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://data.binance.vision"
LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
S = requests.Session()
S.headers["User-Agent"] = "kripto-radar-arastirma/1.0"


def _get(url: str, tries: int = 4) -> bytes | None:
    for i in range(tries):
        try:
            r = S.get(url, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                return r.content
        except requests.RequestException:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def list_prefixes(prefix: str) -> list[str]:
    out, marker = [], ""
    while True:
        url = f"{LIST}?delimiter=/&prefix={prefix}" + (f"&marker={marker}" if marker else "")
        b = _get(url)
        if not b:
            break
        txt = b.decode()
        ps = re.findall(r"<Prefix>([^<]+)</Prefix>", txt)
        ps = [p for p in ps if p != prefix]
        out += ps
        if "<IsTruncated>true</IsTruncated>" in txt and ps:
            m = re.findall(r"<NextMarker>([^<]+)</NextMarker>", txt)
            marker = m[0] if m else ps[-1]
        else:
            break
    return out


def list_keys(prefix: str) -> list[str]:
    out, marker = [], ""
    while True:
        url = f"{LIST}?prefix={prefix}" + (f"&marker={marker}" if marker else "")
        b = _get(url)
        if not b:
            break
        txt = b.decode()
        ks = re.findall(r"<Key>([^<]+)</Key>", txt)
        out += ks
        if "<IsTruncated>true</IsTruncated>" in txt and ks:
            marker = ks[-1]
        else:
            break
    return [k for k in out if k.endswith(".zip")]


def _rows_from_zip(b: bytes) -> list[list[str]]:
    z = zipfile.ZipFile(io.BytesIO(b))
    rows = []
    for n in z.namelist():
        txt = z.read(n).decode()
        for row in csv.reader(io.StringIO(txt)):
            if row and row[0][:1].isdigit():
                rows.append(row)
    return rows


def klines(sym: str, iv: str, months: list[str], daily_days: list[str]) -> list[list]:
    urls = [f"{BASE}/data/futures/um/monthly/klines/{sym}/{iv}/{sym}-{iv}-{m}.zip" for m in months]
    urls += [f"{BASE}/data/futures/um/daily/klines/{sym}/{iv}/{sym}-{iv}-{d}.zip" for d in daily_days]
    seen = {}
    for u in urls:
        b = _get(u)
        if not b:
            continue
        for r in _rows_from_zip(b):
            ts = int(r[0])
            if ts > 10**14:          # bazı yeni dosyalar mikro saniye
                ts //= 1000
            # open_time, o, h, l, c, v, close_time, quote_vol, trades, taker_buy_base, taker_buy_quote
            seen[ts] = [ts, r[1], r[2], r[3], r[4], r[5], r[7], r[10]]
    return [seen[k] for k in sorted(seen)]


def funding(sym: str, months: list[str]) -> list[list]:
    seen = {}
    for m in months:
        b = _get(f"{BASE}/data/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip")
        if not b:
            continue
        for r in _rows_from_zip(b):
            ts = int(r[0])
            # calc_time, funding_interval_hours, last_funding_rate
            rate = r[2] if len(r) >= 3 else r[1]
            seen[ts] = [ts, rate]
    return [seen[k] for k in sorted(seen)]


def metrics(sym: str, days: list[str]) -> list[list]:
    out = []
    for d in days:
        b = _get(f"{BASE}/data/futures/um/daily/metrics/{sym}/{sym}-metrics-{d}.zip")
        if not b:
            continue
        z = zipfile.ZipFile(io.BytesIO(b))
        for n in z.namelist():
            rd = csv.reader(io.StringIO(z.read(n).decode()))
            for r in rd:
                if not r or not r[0][:1].isdigit():
                    continue
                # create_time, symbol, oi, oi_value, top_ls_acct, top_ls_pos, ls_acct, taker_ls_vol
                try:
                    ts = int(datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
                except ValueError:
                    continue
                out.append([ts] + r[2:8])
    return out


def write(path: Path, header: list[str], rows: list[list]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="", compresslevel=9) as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def month_list(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="gecmis")
    ap.add_argument("--start", default="2020-01")
    ap.add_argument("--h1", type=int, default=80, help="1 saatlik veri için en likit kaç sembol")
    ap.add_argument("--metrics", type=int, default=0, help="OI/long-short metrikleri için en likit kaç sembol")
    ap.add_argument("--metrics_days", type=int, default=1500)
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0, help="test için en fazla kaç sembol")
    a = ap.parse_args()
    out = Path(a.out)
    t0 = time.time()

    today = datetime.now(timezone.utc).date()
    last_full = (today.replace(day=1) - timedelta(days=1))
    sy, sm = map(int, a.start.split("-"))
    months = month_list(date(sy, sm, 1), last_full)
    cur_days = [(today.replace(day=1) + timedelta(days=i)).isoformat() for i in range((today - today.replace(day=1)).days)]

    syms = [p.rstrip("/").split("/")[-1] for p in list_prefixes("data/futures/um/monthly/klines/")]
    syms = sorted(s for s in syms if s.endswith("USDT") and re.fullmatch(r"[A-Z0-9]+USDT", s))
    print(f"{len(syms)} USDT vadeli sembol (silinenler dahil)", flush=True)
    if not syms:
        print("HATA: data.binance.vision listesine ulaşılamadı", file=sys.stderr)
        sys.exit(2)
    if a.limit:
        syms = [s for s in syms if s in ("BTCUSDT", "ETHUSDT")] + [s for s in syms if s not in ("BTCUSDT", "ETHUSDT")][:a.limit]

    info: dict[str, dict] = {}

    def do_daily(sym):
        rows = klines(sym, "1d", months, cur_days)
        if rows:
            write(out / "1d" / f"{sym}.csv.gz", ["ts", "open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote"], rows)
        return sym, rows

    with cf.ThreadPoolExecutor(a.workers) as ex:
        for i, (sym, rows) in enumerate(ex.map(do_daily, syms)):
            if rows:
                last30 = rows[-30:]
                qv = sum(float(r[6]) for r in last30) / max(len(last30), 1)
                info[sym] = {"first": rows[0][0], "last": rows[-1][0], "n_1d": len(rows), "qv30": qv}
            if i % 50 == 0:
                print(f"  1d {i}/{len(syms)} ({time.time() - t0:.0f} sn)", flush=True)
    print(f"1d bitti: {len(info)} sembol, {time.time() - t0:.0f} sn", flush=True)

    alive = [s for s in info]
    by_vol = sorted([s for s in alive if info[s]["last"] >= (time.time() - 5 * 86400) * 1000], key=lambda s: -info[s]["qv30"])
    month_ok = {s: [m for m in months if m >= datetime.fromtimestamp(info[s]["first"] / 1000, timezone.utc).strftime("%Y-%m")
                    and m <= datetime.fromtimestamp(info[s]["last"] / 1000, timezone.utc).strftime("%Y-%m")] for s in alive}

    def do_iv(args):
        sym, iv, extra_days = args
        rows = klines(sym, iv, month_ok[sym], extra_days)
        if rows:
            write(out / iv / f"{sym}.csv.gz", ["ts", "open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote"], rows)
        return sym, iv, len(rows)

    jobs = [(s, "4h", []) for s in alive]
    jobs += [(s, "1h", []) for s in by_vol[:a.h1]]
    jobs += [(s, "15m", []) for s in ("BTCUSDT", "ETHUSDT") if s in info]
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for i, (sym, iv, n) in enumerate(ex.map(do_iv, jobs)):
            info[sym][f"n_{iv}"] = n
            if i % 50 == 0:
                print(f"  mum {i}/{len(jobs)} ({time.time() - t0:.0f} sn)", flush=True)
    print(f"4h/1h/15m bitti, {time.time() - t0:.0f} sn", flush=True)

    def do_f(sym):
        rows = funding(sym, month_ok[sym])
        if rows:
            write(out / "funding" / f"{sym}.csv.gz", ["ts", "rate"], rows)
        return sym, len(rows)

    with cf.ThreadPoolExecutor(a.workers) as ex:
        for sym, n in ex.map(do_f, alive):
            info[sym]["n_funding"] = n
    print(f"funding bitti, {time.time() - t0:.0f} sn", flush=True)

    if a.metrics:
        days_all = [(today - timedelta(days=i)).isoformat() for i in range(a.metrics_days, 0, -1)]

        def do_m(sym):
            f0 = datetime.fromtimestamp(info[sym]["first"] / 1000, timezone.utc).date().isoformat()
            rows = metrics(sym, [d for d in days_all if d >= f0])
            if rows:
                write(out / "metrics" / f"{sym}.csv.gz",
                      ["ts", "oi", "oi_usd", "top_ls_acct", "top_ls_pos", "ls_acct", "taker_ls_vol"], rows)
            return sym, len(rows)

        with cf.ThreadPoolExecutor(min(a.workers * 2, 64)) as ex:
            for sym, n in ex.map(do_m, by_vol[:a.metrics]):
                info[sym]["n_metrics"] = n
                print(f"  metrics {sym}: {n}", flush=True)

    (out / "semboller.json").write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(), "months": [months[0], months[-1]],
                                                   "symbols": info, "by_vol": by_vol}, indent=0))
    print(f"✓ Bitti: {len(info)} sembol, {time.time() - t0:.0f} sn", flush=True)


if __name__ == "__main__":
    main()
