"""Çevrimdışı test ve önizleme için sahte borsa (sentetik ama gerçekçi veri)."""
from __future__ import annotations

import hashlib
import time

import numpy as np
import pandas as pd

COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "AVAX", "LINK", "SUI", "TON", "DOT",
         "LTC", "BCH", "NEAR", "APT", "ARB", "OP", "INJ", "TIA", "SEI", "WIF", "PEPE", "ORDI",
         "FIL", "ATOM", "ETC", "UNI", "AAVE", "RNDR", "FET", "TAO", "JUP", "ENA", "ONDO", "HYPE",
         "TRX", "XLM", "HBAR", "ICP"]
BASE_PX = {"BTC": 98000, "ETH": 3600, "SOL": 185, "BNB": 640, "XRP": 2.3, "DOGE": 0.32, "TON": 5.4,
           "LTC": 105, "BCH": 470, "AAVE": 330, "TAO": 480, "HYPE": 28, "PEPE": 0.000018}


def _seed(s: str) -> int:
    return int(hashlib.md5(s.encode()).hexdigest()[:8], 16)


class DemoClient:
    name = "demo"

    def __init__(self, hours: int = 24 * 420, seed: int = 7):
        self.calls = 0
        self.now = pd.Timestamp(int(time.time() // 3600 * 3600), unit="s", tz="UTC")
        self.hours = hours
        self.seed = seed
        self._cache: dict[str, pd.DataFrame] = {}
        self._btc_ret = None

    def _series(self, sym: str) -> pd.DataFrame:
        if sym in self._cache:
            return self._cache[sym]
        rng = np.random.default_rng(_seed(sym) + self.seed)
        n = self.hours
        idx = pd.date_range(end=self.now - pd.Timedelta(hours=1), periods=n, freq="1h", tz="UTC")
        if self._btc_ret is None and sym != "BTC":
            self._series("BTC")
        vol = 0.006 if sym == "BTC" else rng.uniform(0.008, 0.016)
        # rejim değiştiren drift (trend dönemleri)
        seg = rng.integers(200, 900)
        drift = np.repeat(rng.normal(0, 0.0006, n // seg + 2), seg)[:n]
        eps = rng.standard_t(4, n) * vol / np.sqrt(2)
        ret = drift + eps
        if sym != "BTC":
            beta = rng.uniform(0.6, 1.4)
            ret = ret + beta * self._btc_ret
        else:
            self._btc_ret = ret.copy()
        px0 = BASE_PX.get(sym, float(np.exp(rng.uniform(np.log(0.3), np.log(60)))))
        close = px0 * np.exp(np.cumsum(ret) - np.sum(ret))
        op = np.r_[close[0], close[:-1]]
        spread = np.abs(rng.normal(0, vol * 0.7, n)) * close
        high = np.maximum(op, close) + spread
        low = np.minimum(op, close) - spread
        v = rng.lognormal(10, 0.5, n) * (1 + 40 * np.abs(ret))
        df = pd.DataFrame({"open": op, "high": high, "low": low, "close": close,
                           "volume": v, "quote_volume": v * close}, index=idx)
        self._cache[sym] = df
        return df

    @staticmethod
    def _sym(inst: str) -> str:
        return inst.split("-")[0]

    def _resample(self, df, bar):
        rule = {"1m": "1h", "15m": "1h", "1H": "1h", "4H": "4h", "1D": "1D"}[bar]
        return df.resample(rule, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last",
             "volume": "sum", "quote_volume": "sum"}).dropna()

    # ── OKXClient arayüzü ───────────────────────────────────────────
    def instruments(self, inst_type="SWAP"):
        self.calls += 1
        return [{"instId": f"{c}-USDT-SWAP", "settleCcy": "USDT", "state": "live", "ctType": "linear",
                 "listTime": "1500000000000", "instCategory": "1", "ruleType": "normal",
                 "ctVal": "1", "lotSz": "0.01", "minSz": "0.01"} for c in COINS] + [
                {"instId": "CL-USDT-SWAP", "settleCcy": "USDT", "state": "live", "ctType": "linear",
                 "listTime": "1500000000000", "instCategory": "4", "ruleType": "normal"}]

    def tickers(self, inst_type="SWAP"):
        self.calls += 1
        out = []
        for i, c in enumerate(COINS):
            df = self._series(c)
            last, o24 = df["close"].iloc[-1], df["close"].iloc[-25]
            vol_base = df["volume"].iloc[-24:].sum() * (60 - i) * 50
            if inst_type == "SWAP":
                out.append({"instId": f"{c}-USDT-SWAP", "last": str(last), "open24h": str(o24),
                            "high24h": str(df["high"].iloc[-24:].max()), "low24h": str(df["low"].iloc[-24:].min()),
                            "volCcy24h": str(vol_base * 20_000 / max(last, 1e-9) / 1000)})
            elif i % 7 != 6:  # birkaç coinin spotu yok
                out.append({"instId": f"{c}-USDT", "last": str(last),
                            "volCcy24h": str(vol_base * 3)})
        return out

    def candles(self, inst, bar="4H", limit=300, closed_only=True):
        self.calls += 1
        df = self._resample(self._series(self._sym(inst)), bar)
        return df.iloc[-limit:].copy()

    def history_candles(self, inst, bar, start_ms, max_pages=60):
        self.calls += 1
        df = self._resample(self._series(self._sym(inst)), bar)
        return df[df.index >= pd.to_datetime(start_ms, unit="ms", utc=True)].copy()

    def _rate(self, sym, k=0):
        rng = np.random.default_rng(_seed(sym) + 99 + k)
        base = rng.choice([0.0001, 0.00005, 0.0003, -0.0002, 0.0006], p=[.4, .25, .15, .1, .1])
        return float(base + rng.normal(0, 0.00004))

    def books(self, inst, sz=50):
        self.calls += 1
        last = float(self._series(self._sym(inst))["close"].iloc[-1])
        step = last * 0.0001
        return {"asks": [(last + step * (i + 1), 50.0 * (i + 1)) for i in range(sz)],
                "bids": [(last - step * (i + 1), 50.0 * (i + 1)) for i in range(sz)], "ts": 0}

    def funding_rates_all(self):
        self.calls += 1
        now = int(self.now.timestamp() * 1000)
        return {f"{c}-USDT-SWAP": {"instId": f"{c}-USDT-SWAP", "fundingRate": str(self._rate(c)),
                                   "fundingTime": str(now), "nextFundingTime": str(now + 8 * 3600 * 1000)}
                for c in COINS}

    def funding_rate(self, inst):
        return self.funding_rates_all()[inst]

    def funding_history(self, inst, days=30):
        self.calls += 1
        sym = self._sym(inst)
        n = days * 3
        idx = pd.date_range(end=self.now, periods=n, freq="8h", tz="UTC")
        rng = np.random.default_rng(_seed(sym) + 5)
        base = self._rate(sym)
        rates = base + rng.normal(0, abs(base) * 0.6 + 0.00003, n)
        return pd.DataFrame({"rate": rates}, index=idx)

    def open_interest_all(self):
        self.calls += 1
        return {f"{c}-USDT-SWAP": 1e8 * (1 + _seed(c) % 50) for c in COINS}

    def oi_history(self, inst, period="1H", limit=30):
        self.calls += 1
        sym = self._sym(inst)
        rng = np.random.default_rng(_seed(sym) + 11)
        idx = pd.date_range(end=self.now, periods=limit, freq="1h", tz="UTC")
        trend = rng.normal(0, 0.004)
        vals = 1e8 * np.exp(np.cumsum(trend + rng.normal(0, 0.003, limit)))
        return pd.Series(vals, index=idx)

    def taker_volume(self, inst, period="1H", limit=48):
        self.calls += 1
        df = self._series(self._sym(inst)).iloc[-limit:]
        rng = np.random.default_rng(_seed(inst) + 3)
        share = 0.5 + np.clip(np.sign(df["close"] - df["open"]) * 0.05 + rng.normal(0, 0.04, len(df)), -.3, .3)
        tot = df["quote_volume"].to_numpy()
        return pd.DataFrame({"sell": tot * (1 - share), "buy": tot * share}, index=df.index)
