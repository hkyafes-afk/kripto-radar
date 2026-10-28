"""OKX veri erişimi: WebSocket akışları (olay bazlı) + REST (geçmiş ve WS'de olmayan veriler).

Bağlantılar ayrı tutulur ki biri yoğunlaşınca diğerini bekletmesin:
  piyasa   → tickers (bütün USDT vadeliler), liquidation-orders, funding-rate, open-interest, mark-price, index-tickers
  defter   → seçilen coinlerin emir defteri (books: 400 kademe artımlı, 100 ms)
  tape     → seçilen coinlerin işlemleri (trades)
  mum      → business: candle5m/15m/1H/4Hutc/1Dutc (seçilenler) + candle1H (bütün piyasa: hacim ivmesi)
Her bağlantı koparsa kademeli bekleyip yeniden bağlanır ve aboneliklerini yeniler.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .wsclient import WS, dumps, loads

TF_CHANNEL = {"1m": "candle1m", "5m": "candle5m", "15m": "candle15m", "1H": "candle1H",
              "4H": "candle4Hutc", "1D": "candle1Dutc"}
TF_REST = {"1m": "1m", "5m": "5m", "15m": "15m", "1H": "1H", "4H": "4Hutc", "1D": "1Dutc"}
TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000}
CHANNEL_TF = {v: k for k, v in TF_CHANNEL.items()}


def now_ms() -> int:
    return int(time.time() * 1000)


class Conn:
    """Tek bir WebSocket bağlantısı: abonelik kümesi + yeniden bağlanma + ping."""

    def __init__(self, name: str, urls: list[str], on_msg, stats, start_delay: float = 0.0):
        self.name, self.urls, self.on_msg, self.stats = name, urls, on_msg, stats
        self.start_delay = start_delay           # OKX: IP başına saniyede en çok 3 bağlantı → aç, kademeli
        self.subs: dict[str, dict] = {}          # anahtar → arg
        self.ws: WS | None = None
        self.task: asyncio.Task | None = None
        self.connected = asyncio.Event()
        self.stopping = False

    @staticmethod
    def key(arg: dict) -> str:
        return "|".join(f"{k}={arg[k]}" for k in sorted(arg))

    async def _send_ops(self, op: str, args: list[dict]):
        if not args or not self.ws:
            return
        # OKX: tek istek ≤ 64 KB. 100 arg'lık parçalar güvenli.
        for i in range(0, len(args), 100):
            await self.ws.send_text(dumps({"op": op, "args": args[i:i + 100]}))

    async def subscribe(self, args: list[dict]):
        new = [a for a in args if self.key(a) not in self.subs]
        for a in new:
            self.subs[self.key(a)] = a
        if self.ws and self.connected.is_set():
            try:
                await self._send_ops("subscribe", new)
            except Exception:  # noqa: BLE001
                pass   # bağlantı yeniden kurulunca hepsi zaten gönderilir

    async def unsubscribe(self, args: list[dict]):
        gone = [a for a in args if self.subs.pop(self.key(a), None) is not None]
        if self.ws and self.connected.is_set():
            try:
                await self._send_ops("unsubscribe", gone)
            except Exception:  # noqa: BLE001
                pass

    def start(self):
        self.task = asyncio.create_task(self._run(), name=f"ws:{self.name}")

    async def stop(self):
        self.stopping = True
        if self.ws:
            await self.ws.close()
        if self.task:
            self.task.cancel()

    async def _run(self):
        delay, i = 1.0, 0
        if self.start_delay:
            await asyncio.sleep(self.start_delay)
        while not self.stopping:
            url = self.urls[i % len(self.urls)]
            try:
                self.ws = await WS.connect(url)
                self.connected.set()
                self.stats["baglanti"][self.name] = url
                await self._send_ops("subscribe", list(self.subs.values()))
                delay = 1.0
                await self._loop()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.stats["kopma"][self.name] = self.stats["kopma"].get(self.name, 0) + 1
                self.stats["son_hata"] = f"{self.name}: {type(e).__name__}: {str(e)[:120]}"
            finally:
                self.connected.clear()
                if self.ws:
                    await self.ws.close()
                self.ws = None
            if self.stopping:
                break
            i += 1
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _loop(self):
        ws = self.ws
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), 25)
            except asyncio.TimeoutError:
                await ws.send_text("ping")                # OKX: 30 sn sessizlikte bağlantıyı keser
                raw = await asyncio.wait_for(ws.recv(), 10)
            t = now_ms()
            if raw == "pong":
                continue
            msg = loads(raw)
            if "event" in msg:
                if msg["event"] == "error":
                    self.stats["son_hata"] = f"{self.name}: {msg.get('code')} {msg.get('msg', '')[:120]}"
                continue
            self.on_msg(msg, t)


class Feed:
    def __init__(self, cfg: dict, on_msg):
        v = cfg["veri"]
        self.stats = {"baglanti": {}, "kopma": {}, "son_hata": ""}
        names = [("piyasa", "ws_public"), ("defter", "ws_public"), ("tape", "ws_public"),
                 ("mum", "ws_business"), ("mum_genel", "ws_business")]
        self.conns = {n: Conn(n, v[u], on_msg, self.stats, start_delay=i * 0.4) for i, (n, u) in enumerate(names)}
        self.book_channel = v.get("defter_kanali", "books")
        self.tfs = v["zaman_dilimleri"]

    def start(self):
        for c in self.conns.values():
            c.start()

    async def stop(self):
        for c in self.conns.values():
            await c.stop()

    async def market_wide(self, insts: list[str]):
        await self.conns["piyasa"].subscribe(
            [{"channel": "tickers", "instId": i} for i in insts]
            + [{"channel": "liquidation-orders", "instType": "SWAP"}])
        await self.conns["mum_genel"].subscribe([{"channel": "candle1H", "instId": i} for i in insts])

    def _deep_args(self, inst: str) -> dict[str, list[dict]]:
        idx = inst.replace("-SWAP", "")
        return {
            "piyasa": [{"channel": "funding-rate", "instId": inst}, {"channel": "open-interest", "instId": inst},
                       {"channel": "mark-price", "instId": inst}, {"channel": "index-tickers", "instId": idx}],
            "defter": [{"channel": self.book_channel, "instId": inst}],
            "tape": [{"channel": "trades", "instId": inst}],
            "mum": [{"channel": TF_CHANNEL[tf], "instId": inst} for tf in self.tfs],
        }

    async def watch(self, inst: str):
        for name, args in self._deep_args(inst).items():
            await self.conns[name].subscribe(args)

    async def unwatch(self, inst: str):
        for name, args in self._deep_args(inst).items():
            await self.conns[name].unsubscribe(args)

    async def resync_book(self, inst: str):
        """Checksum tutmazsa defteri yeniden iste (abonelikten çık + gir → yeni snapshot)."""
        a = [{"channel": self.book_channel, "instId": inst}]
        await self.conns["defter"].unsubscribe(a)
        await self.conns["defter"].subscribe(a)


class Rest:
    """Basit REST istemcisi (urllib, iş parçacığında). İmzalı istekler gelecekteki OKX adaptörü içindir."""

    def __init__(self, cfg: dict, key: str = "", secret: str = "", passphrase: str = "", demo: bool = True):
        self.bases = cfg["veri"]["rest"]
        self.key, self.secret, self.passphrase, self.demo = key, secret, passphrase, demo
        self.sem = asyncio.Semaphore(8)
        self.calls = 0
        self.min_gap = 1 / 15.0                  # genel tavan: saniyede 15 istek (mum uç noktası 20/sn)
        self._next = 0.0

    def _req(self, method: str, path: str, params: dict | None, body: dict | None, signed: bool):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        data = json.dumps(body, separators=(",", ":")) if body is not None else ""
        last = None
        for base in self.bases:
            headers = {"User-Agent": "polat/1", "Content-Type": "application/json"}
            if signed:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
                headers.update(sign_headers(self.key, self.secret, self.passphrase, ts, method, path + q, data))
                if self.demo:
                    headers["x-simulated-trading"] = "1"
            req = urllib.request.Request(base + path + q, data=data.encode() if data else None,
                                         headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    js = json.loads(r.read())
                self.calls += 1
                if str(js.get("code", "0")) not in ("0", ""):
                    raise RuntimeError(f"OKX {js.get('code')}: {js.get('msg')}")
                return js.get("data", js)
            except Exception as e:  # noqa: BLE001
                last = e
        raise last

    async def _pace(self):
        loop = asyncio.get_running_loop()
        t = loop.time()
        wait = self._next - t
        self._next = max(t, self._next) + self.min_gap
        if wait > 0:
            await asyncio.sleep(wait)

    async def get(self, path: str, params: dict | None = None, signed: bool = False):
        async with self.sem:
            await self._pace()
            return await asyncio.to_thread(self._req, "GET", path, params, None, signed)

    async def post(self, path: str, body: dict, signed: bool = True):
        async with self.sem:
            await self._pace()
            return await asyncio.to_thread(self._req, "POST", path, None, body, signed)

    # --- yardımcılar -------------------------------------------------------------------------
    async def instruments(self) -> dict[str, dict]:
        rows = await self.get("/api/v5/public/instruments", {"instType": "SWAP"})
        out = {}
        for r in rows:
            if r.get("settleCcy") == "USDT" and r.get("state") == "live" and r.get("ctType", "linear") == "linear" \
                    and str(r.get("instCategory") or "1") == "1":
                out[r["instId"]] = {"ctVal": float(r["ctVal"]), "lotSz": float(r["lotSz"]),
                                    "minSz": float(r["minSz"]), "tickSz": float(r["tickSz"]),
                                    "maxLever": float(r.get("lever") or 20)}
        return out

    async def tickers(self) -> list[dict]:
        return await self.get("/api/v5/market/tickers", {"instType": "SWAP"})

    async def candles(self, inst: str, tf: str, limit: int = 300) -> list[list]:
        rows = await self.get("/api/v5/market/candles", {"instId": inst, "bar": TF_REST[tf], "limit": min(limit, 300)})
        return [parse_candle(r) for r in reversed(rows)]

    async def candles_since(self, inst: str, tf: str, since_ms: int) -> list[list]:
        rows = await self.get("/api/v5/market/candles", {"instId": inst, "bar": TF_REST[tf], "limit": 300})
        return [c for c in (parse_candle(r) for r in reversed(rows)) if c[0] >= since_ms - TF_MS[tf]]

    async def long_short(self, inst: str) -> float | None:
        try:
            rows = await self.get("/api/v5/rubik/stat/contracts/long-short-account-ratio-contract",
                                  {"instId": inst, "period": "5m", "limit": 1})
            return float(rows[0][1]) if rows else None
        except Exception:  # noqa: BLE001
            return None


def parse_candle(r) -> list:
    """[ts, o, h, l, c, hacim(baz), hacim(USDT), kesin(1/0)]"""
    ts, o, h, l, c = int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])
    vol_base = float(r[6]) if len(r) > 6 and r[6] not in ("", None) else float(r[5])
    vol_q = float(r[7]) if len(r) > 7 and r[7] not in ("", None) else vol_base * c
    conf = int(r[8]) if len(r) > 8 and r[8] not in ("", None) else 1
    return [ts, o, h, l, c, vol_base, vol_q, conf]


def sign_headers(key, secret, passphrase, ts, method, path_q, body) -> dict:
    pre = f"{ts}{method.upper()}{path_q}{body}"
    sig = base64.b64encode(hmac.new(secret.encode(), pre.encode(), hashlib.sha256).digest()).decode()
    return {"OK-ACCESS-KEY": key, "OK-ACCESS-SIGN": sig, "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": passphrase}
