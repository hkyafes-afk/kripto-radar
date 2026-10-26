"""OKX v5 public REST istemcisi (API anahtarı gerekmez).

- Uç nokta bazlı hız sınırlayıcı (OKX limitlerinin biraz altında)
- Host yedeği: www.okx.com engellenirse us.okx.com / eea.okx.com denenir
- 429 / 50011 (rate limit) durumunda bekleyip tekrar dener
"""
from __future__ import annotations

import threading
import time
from collections import deque

import pandas as pd
import requests

HOSTS = ["https://www.okx.com", "https://us.okx.com", "https://eea.okx.com"]
UA = "Mozilla/5.0 (X11; Linux x86_64) kripto-radar/1.0"

# (istek sayısı, saniye) — OKX dokümanındaki limitlerin altında tutuldu
LIMITS = {
    "/api/v5/market/candles": (36, 2),
    "/api/v5/market/history-candles": (18, 2),
    "/api/v5/market/tickers": (15, 2),
    "/api/v5/market/books": (30, 2),
    "/api/v5/public/instruments": (15, 2),
    "/api/v5/public/funding-rate": (9, 2),
    "/api/v5/public/funding-rate-history": (9, 2),
    "/api/v5/public/open-interest": (15, 2),
    "/api/v5/rubik/stat/contracts/open-interest-history": (4, 2),
    "/api/v5/rubik/stat/taker-volume-contract": (4, 2),
}


class OKXError(Exception):
    pass


class _Limiter:
    def __init__(self, n: int, per: float):
        self.n, self.per = n, per
        self.calls: deque = deque()
        self.lock = threading.Lock()

    def wait(self):
        while True:
            with self.lock:
                now = time.monotonic()
                while self.calls and now - self.calls[0] > self.per:
                    self.calls.popleft()
                if len(self.calls) < self.n:
                    self.calls.append(now)
                    return
                delay = self.per - (now - self.calls[0]) + 0.02
            time.sleep(max(delay, 0.02))


class OKXClient:
    name = "okx"

    def __init__(self, timeout: float = 15.0):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "application/json"})
        self.timeout = timeout
        self.host_idx = 0
        self.limiters: dict[str, _Limiter] = {}
        self.lock = threading.Lock()
        self.calls = 0
        self._fails: dict[str, int] = {}
        self.dead: set[str] = set()

    def _optional(self, path: str, params: dict) -> list:
        """İsteğe bağlı uç noktalar (rubik): üst üste 6 hata → bu çalıştırmada kapat (zaman kaybetme)."""
        if path in self.dead:
            raise OKXError(f"{path} bu çalıştırmada devre dışı")
        try:
            rows = self.get(path, params, retries=2)
        except OKXError:
            with self.lock:
                self._fails[path] = self._fails.get(path, 0) + 1
                if self._fails[path] >= 6:
                    self.dead.add(path)
                    print(f"[uyarı] {path} erişilemiyor, bu taramada atlanıyor (yedek yöntem kullanılacak)")
            raise
        with self.lock:
            self._fails[path] = -10**6   # bir kez çalıştıysa kapatma
        return rows

    def _limiter(self, path: str) -> _Limiter:
        with self.lock:
            if path not in self.limiters:
                n, per = LIMITS.get(path, (10, 2))
                self.limiters[path] = _Limiter(n, per)
            return self.limiters[path]

    def get(self, path: str, params: dict | None = None, retries: int = 4) -> list:
        lim = self._limiter(path)
        last_err: Exception | None = None
        tried_hosts = 0
        attempt = 0
        while attempt < retries:
            lim.wait()
            host = HOSTS[self.host_idx]
            try:
                r = self.s.get(host + path, params=params, timeout=self.timeout)
                self.calls += 1
            except requests.RequestException as e:
                last_err = e
                attempt += 1
                time.sleep(1.0 * attempt)
                continue
            if r.status_code in (403, 451) and tried_hosts < len(HOSTS) - 1:
                # Bu host bu IP'yi engelliyor → sıradakini dene
                with self.lock:
                    self.host_idx = (self.host_idx + 1) % len(HOSTS)
                tried_hosts += 1
                continue
            if r.status_code == 429:
                attempt += 1
                time.sleep(1.5 * attempt)
                continue
            try:
                js = r.json()
            except ValueError:
                last_err = OKXError(f"HTTP {r.status_code}: {r.text[:200]}")
                attempt += 1
                time.sleep(1.0 * attempt)
                continue
            code = str(js.get("code", ""))
            if code == "0":
                return js.get("data", [])
            if code in ("50011", "50013", "50001"):  # rate limit / sistem meşgul
                attempt += 1
                time.sleep(1.5 * attempt)
                continue
            raise OKXError(f"{path} {params} → code={code} msg={js.get('msg')}")
        raise OKXError(f"{path} {params} başarısız: {last_err}")

    # ── piyasa verisi ────────────────────────────────────────────────
    def instruments(self, inst_type: str = "SWAP") -> list[dict]:
        return self.get("/api/v5/public/instruments", {"instType": inst_type})

    def tickers(self, inst_type: str = "SWAP") -> list[dict]:
        return self.get("/api/v5/market/tickers", {"instType": inst_type})

    def candles(self, inst_id: str, bar: str = "4H", limit: int = 300,
                closed_only: bool = True) -> pd.DataFrame:
        """Son mumlar (en fazla 300). bar: 1H, 4H → UTC hizalı (4Hutc), 1D → 1Dutc."""
        rows = self._candles_req("/api/v5/market/candles",
                                 {"instId": inst_id, "limit": str(min(limit, 300))}, bar)
        return _candles_df(rows, closed_only)

    # 4H: OKX'te Hong Kong (UTC+8) hizalı ama 8 saat 4'e bölündüğü için UTC ile aynı sınırlar.
    # 1D: UTC hizası için "1Dutc" denenir; borsa kabul etmezse "1D"ye düşülür.
    _BAR_TRY = {"1D": ["1Dutc", "1D"], "12H": ["12Hutc", "12H"], "6H": ["6Hutc", "6H"]}
    _bar_ok: dict = {}

    def _candles_req(self, path: str, params: dict, bar: str) -> list:
        options = [self._bar_ok[bar]] if bar in self._bar_ok else self._BAR_TRY.get(bar, [bar])
        last = None
        for b in options:
            try:
                rows = self.get(path, {**params, "bar": b})
                self._bar_ok[bar] = b
                return rows
            except OKXError as e:
                if "51000" not in str(e):
                    raise
                last = e
        raise last

    def history_candles(self, inst_id: str, bar: str, start_ms: int) -> pd.DataFrame:
        """start_ms'den bugüne kadar sayfalayarak geçmiş mumlar (backtest için)."""
        frames, after = [], None
        for _ in range(60):
            params = {"instId": inst_id, "limit": "100"}
            if after:
                params["after"] = str(after)
            rows = self._candles_req("/api/v5/market/history-candles", params, bar)
            if not rows:
                break
            df = _candles_df(rows, closed_only=True)
            frames.append(df)
            oldest = int(rows[-1][0])
            if oldest <= start_ms:
                break
            after = oldest
        if not frames:
            return _candles_df([], True)
        out = pd.concat(frames).sort_index()
        out = out[~out.index.duplicated(keep="last")]
        return out[out.index >= pd.to_datetime(start_ms, unit="ms", utc=True)]

    def books(self, inst_id: str, sz: int = 50) -> dict:
        """Emir defteri. Döner: {"asks": [(fiyat, kontrat)], "bids": [...], "ts": ms} (en iyiden kötüye)."""
        rows = self.get("/api/v5/market/books", {"instId": inst_id, "sz": str(sz)})
        if not rows:
            return {"asks": [], "bids": [], "ts": 0}
        b = rows[0]
        return {"asks": [(_f(x[0]), _f(x[1])) for x in b.get("asks", [])],
                "bids": [(_f(x[0]), _f(x[1])) for x in b.get("bids", [])],
                "ts": int(_f(b.get("ts")) or 0)}

    def funding_rate(self, inst_id: str) -> dict:
        rows = self.get("/api/v5/public/funding-rate", {"instId": inst_id})
        return rows[0] if rows else {}

    def funding_rates_all(self) -> dict[str, dict] | None:
        """Bazı OKX sürümleri instId=ANY ile topluca döner; desteklenmezse None."""
        try:
            rows = self.get("/api/v5/public/funding-rate", {"instId": "ANY"}, retries=2)
        except OKXError:
            return None
        if not rows or len(rows) < 5:
            return None
        return {r["instId"]: r for r in rows if "instId" in r}

    def funding_history(self, inst_id: str, days: int = 30) -> pd.DataFrame:
        cutoff = int((time.time() - days * 86400) * 1000)
        out, after = [], None
        for _ in range(8):
            params = {"instId": inst_id, "limit": "100"}
            if after:
                params["after"] = str(after)
            rows = self.get("/api/v5/public/funding-rate-history", params)
            if not rows:
                break
            out.extend(rows)
            oldest = min(int(r["fundingTime"]) for r in rows)
            if oldest <= cutoff or len(rows) < 100:
                break
            after = oldest
        if not out:
            return pd.DataFrame(columns=["rate"])
        df = pd.DataFrame({
            "ts": pd.to_datetime([int(r["fundingTime"]) for r in out], unit="ms", utc=True),
            "rate": [_f(r.get("realizedRate") or r.get("fundingRate")) for r in out],
        }).drop_duplicates("ts").set_index("ts").sort_index()
        return df[df.index >= pd.to_datetime(cutoff, unit="ms", utc=True)]

    def open_interest_all(self) -> dict[str, float]:
        """Tüm SWAP'ların anlık açık pozisyonu (USD)."""
        rows = self.get("/api/v5/public/open-interest", {"instType": "SWAP"})
        out = {}
        for r in rows:
            usd = _f(r.get("oiUsd"))
            if usd == usd and usd > 0:  # NaN kontrolü
                out[r["instId"]] = usd
        return out

    def oi_history(self, inst_id: str, period: str = "1H", limit: int = 30) -> pd.Series:
        rows = self._optional("/api/v5/rubik/stat/contracts/open-interest-history",
                              {"instId": inst_id, "period": period, "limit": str(limit)})
        if not rows:
            return pd.Series(dtype=float)
        idx = pd.to_datetime([int(r[0]) for r in rows], unit="ms", utc=True)
        vals = [_f(r[3]) if len(r) > 3 else _f(r[1]) for r in rows]
        return pd.Series(vals, index=idx).sort_index()

    def taker_volume(self, inst_id: str, period: str = "1H", limit: int = 48) -> pd.DataFrame:
        rows = self._optional("/api/v5/rubik/stat/taker-volume-contract",
                              {"instId": inst_id, "period": period, "unit": "2", "limit": str(limit)})
        if not rows:
            return pd.DataFrame(columns=["sell", "buy"])
        idx = pd.to_datetime([int(r[0]) for r in rows], unit="ms", utc=True)
        df = pd.DataFrame({"sell": [_f(r[1]) for r in rows], "buy": [_f(r[2]) for r in rows]},
                          index=idx)
        return df.sort_index()


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def _candles_df(rows: list, closed_only: bool) -> pd.DataFrame:
    cols = ["open", "high", "low", "close", "volume", "quote_volume"]
    if not rows:
        return pd.DataFrame(columns=cols, index=pd.DatetimeIndex([], tz="UTC"))
    if closed_only:
        rows = [r for r in rows if len(r) < 9 or str(r[8]) == "1"]
    data = {
        "open": [_f(r[1]) for r in rows],
        "high": [_f(r[2]) for r in rows],
        "low": [_f(r[3]) for r in rows],
        "close": [_f(r[4]) for r in rows],
        "volume": [_f(r[6]) for r in rows],          # baz coin cinsinden
        "quote_volume": [_f(r[7]) if len(r) > 7 else float("nan") for r in rows],
    }
    idx = pd.to_datetime([int(r[0]) for r in rows], unit="ms", utc=True)
    return pd.DataFrame(data, index=idx).sort_index()
