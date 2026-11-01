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
import http.client
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .wsclient import WS, dumps, loads

TF_CHANNEL = {"1m": "candle1m", "5m": "candle5m", "15m": "candle15m", "1H": "candle1H",
              "4H": "candle4H", "1D": "candle1Dutc"}   # 4Hutc yok; 4H zaten UTC sınırında
TF_REST = {"1m": "1m", "5m": "5m", "15m": "15m", "1H": "1H", "4H": "4H", "1D": "1Dutc"}
TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000}
CHANNEL_TF = {v: k for k, v in TF_CHANNEL.items()}


def now_ms() -> int:
    return int(time.time() * 1000)


class Conn:
    """Tek bir WebSocket bağlantısı: abonelik kümesi + yeniden bağlanma + ping."""

    def __init__(self, name: str, urls: list[str], on_msg, stats, start_delay: float = 0.0, on_disconnect=None):
        self.name, self.urls, self.on_msg, self.stats = name, urls, on_msg, stats
        self.on_disconnect = on_disconnect
        self.start_delay = start_delay           # OKX: IP başına saniyede en çok 3 bağlantı → aç, kademeli
        self.subs: dict[str, dict] = {}          # anahtar → arg
        self.ws: WS | None = None
        self.task: asyncio.Task | None = None
        self.connected = asyncio.Event()
        self.stopping = False
        self.pend_sub: dict[str, dict] = {}
        self.pend_unsub: dict[str, dict] = {}
        self.pend_resub: dict[str, dict] = {}    # önce unsubscribe, sonra subscribe (yeni snapshot için)
        self.confirmed: set[str] = set()         # OKX'in "subscribe" olayıyla onayladığı abonelikler
        self.flusher: asyncio.Task | None = None
        self.min_gap = 0.6                       # OKX: bağlantı başına abonelik isteği sınırı → en çok ~2/sn
        self.cool_until = 0.0                    # 60014 sonrası soğuma bitişi (monotonic)

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
        for a in args:
            k = self.key(a)
            if k not in self.subs:
                self.subs[k] = a
                self.pend_sub[k] = a
            self.pend_unsub.pop(k, None)
        self._kick()

    async def unsubscribe(self, args: list[dict]):
        for a in args:
            k = self.key(a)
            if self.subs.pop(k, None) is not None:
                self.pend_unsub[k] = a
            self.pend_sub.pop(k, None)
            self.pend_resub.pop(k, None)
        self._kick()

    async def resubscribe(self, args: list[dict]):
        """Abonelikten çık → tekrar abone ol. OKX aynı kanala ikinci 'subscribe'a yeni snapshot göndermiyor
        (28.10.2026 teşhisi); yeni snapshot ancak unsubscribe + subscribe ile geliyor. Birleştirilmez."""
        for a in args:
            k = self.key(a)
            if k in self.subs:
                self.pend_resub[k] = a
        self._kick()

    def _kick(self):
        if self.flusher is None or self.flusher.done():
            try:
                self.flusher = asyncio.get_running_loop().create_task(self._flush())
            except RuntimeError:
                pass

    async def _flush(self):
        """Bekleyen abonelikleri birleştirip aralıklı gönder (her istek ≤100 arg, istekler arası ≥0,6 sn)."""
        while self.pend_sub or self.pend_unsub or self.pend_resub:
            await self.connected.wait()
            wait = self.cool_until - time.monotonic()
            if wait > 0:                                  # 60014 sonrası soğuma
                await asyncio.sleep(wait)
            for op, pend in (("unsubscribe", self.pend_unsub), ("resub", self.pend_resub),
                             ("subscribe", self.pend_sub)):
                if not pend:
                    continue
                keys = list(pend)[:100]
                args = [pend.pop(k) for k in keys]
                if op == "resub":
                    args = [a for a in args if self.key(a) in self.subs]
                    if not args:
                        continue
                try:
                    if op == "resub":
                        await self.ws.send_text(dumps({"op": "unsubscribe", "args": args}))
                        await asyncio.sleep(self.min_gap)
                        await self.ws.send_text(dumps({"op": "subscribe", "args": args}))
                    else:
                        await self.ws.send_text(dumps({"op": op, "args": args}))
                except Exception:  # noqa: BLE001
                    pass                                  # yeniden bağlanınca self.subs zaten tamamen gönderilir
                await asyncio.sleep(self.min_gap)

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
            was_connected = False
            telemetry = self.stats.setdefault("baglanti_telemetrisi", {}).setdefault(
                self.name, {"attempts": 0, "connected": 0, "disconnects": 0,
                            "last_attempt_ms": None, "last_connected_ms": None,
                            "last_disconnected_ms": None, "last_error": None})
            telemetry["attempts"] += 1
            telemetry["last_attempt_ms"] = now_ms()
            try:
                self.ws = await WS.connect(url)
                telemetry["connected"] += 1
                telemetry["last_connected_ms"] = now_ms()
                telemetry["last_error"] = None
                self.stats["baglanti"][self.name] = url
                self.pend_sub.clear()
                self.pend_unsub.clear()
                self.pend_resub.clear()
                self.confirmed.clear()
                allargs = list(self.subs.values())
                for i in range(0, len(allargs), 100):         # yeniden bağlanınca hepsi, aralıklı
                    await self.ws.send_text(dumps({"op": "subscribe", "args": allargs[i:i + 100]}))
                    if i + 100 < len(allargs):
                        await asyncio.sleep(self.min_gap)
                self.connected.set()
                was_connected = True
                delay = 1.0
                await self._loop()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.stats["kopma"][self.name] = self.stats["kopma"].get(self.name, 0) + 1
                self.stats["son_hata"] = f"{self.name}: {type(e).__name__}: {str(e)[:120]}"
            finally:
                self.connected.clear()
                if was_connected:
                    telemetry["disconnects"] += 1
                    telemetry["last_disconnected_ms"] = now_ms()
                if was_connected and self.on_disconnect:
                    try:
                        self.on_disconnect(self.name, now_ms())
                    except Exception:  # noqa: BLE001 — telemetri hatası reconnect döngüsünü durdurmaz
                        pass
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
                ev = msg["event"]
                if ev == "subscribe" and msg.get("arg"):
                    self.confirmed.add(self.key(msg["arg"]))
                elif ev == "unsubscribe" and msg.get("arg"):
                    self.confirmed.discard(self.key(msg["arg"]))
                elif ev == "error":
                    code = str(msg.get("code"))
                    self.stats["son_hata"] = f"{self.name}: {code} {msg.get('msg', '')[:120]}"
                    self.stats.setdefault("hata_kodlari", {})[code] = self.stats.setdefault("hata_kodlari", {}).get(code, 0) + 1
                    if code == "60014":
                        # Çok sık istek: soğu, aralığı aç; yalnızca OKX'in henüz onaylamadığı abonelikleri yeniden iste
                        # (hepsini yeniden istemek fırtınayı büyütüyordu; hiç istememek aboneliği kaybettiriyordu).
                        self.cool_until = time.monotonic() + 8
                        self.min_gap = min(self.min_gap * 1.5, 3.0)
                        for k, a in self.subs.items():
                            if k not in self.confirmed and k not in self.pend_resub:
                                self.pend_sub[k] = a
                        self._kick()
                continue
            self.on_msg(msg, t)


class Feed:
    def __init__(self, cfg: dict, on_msg, on_disconnect=None):
        v = cfg["veri"]
        self.stats = {"baglanti": {}, "kopma": {}, "son_hata": "", "baglanti_telemetrisi": {}}
        names = [("piyasa", "ws_public"), ("defter", "ws_public"), ("tape", "ws_public"),
                 ("mum", "ws_business"), ("mum_genel", "ws_business")]
        self.on_disconnect = on_disconnect
        self.conns = {n: Conn(n, v[u], on_msg, self.stats, start_delay=i * 0.4,
                              on_disconnect=self._disconnected) for i, (n, u) in enumerate(names)}
        self.book_channel = v.get("defter_kanali", "books")
        self.tfs = v["zaman_dilimleri"]

    def _disconnected(self, connection_name: str, receive_ts_ms: int) -> None:
        """Bağlantısı gerçekten kurulmuş kanal grubu koptuğunda kalite durumunu düşür."""
        if self.on_disconnect:
            self.on_disconnect(connection_name, receive_ts_ms)

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
        """Defter bozulunca (sıra boşluğu) yeni snapshot iste: gerçekten abonelikten çık, sonra tekrar gir."""
        await self.conns["defter"].resubscribe([{"channel": self.book_channel, "instId": inst}])


# OKX genel uç nokta sınırları (istek / 2 sn). Her uç noktaya kendi kovası; %80'inde tutulur.
RATE = {"/api/v5/market/candles": 40, "/api/v5/market/history-candles": 20, "/api/v5/market/tickers": 20,
        "/api/v5/public/instruments": 20, "/api/v5/public/time": 10, "/api/v5/rubik/": 5, "_": 10}


class _Bucket:
    def __init__(self, per2s: int):
        self.gap = 2.0 / max(1, int(per2s * 0.8))
        self.next = 0.0

    async def take(self):
        loop = asyncio.get_running_loop()
        t = loop.time()
        wait = self.next - t
        self.next = max(t, self.next) + self.gap
        if wait > 0:
            await asyncio.sleep(wait)


class Rest:
    """REST istemcisi:
      * kalıcı HTTPS bağlantı havuzu (her istekte yeniden TLS el sıkışması yok → istek başına ~100–300 ms kazanç)
      * uç nokta başına OKX hız sınırı kovası; 429 / 50011 gelirse kademeli bekleyip yeniden dener
      * açılışta en hızlı sunucuyu ölçer ve önce onu kullanır
    İmzalı istekler gelecekteki OKX demo adaptörü içindir."""

    def __init__(self, cfg: dict, key: str = "", secret: str = "", passphrase: str = "", demo: bool = True):
        self.bases = list(cfg["veri"]["rest"])
        self.key, self.secret, self.passphrase, self.demo = key, secret, passphrase, demo
        self.sem = asyncio.Semaphore(8)
        self.calls = 0
        self.retries = 0
        self.buckets: dict[str, _Bucket] = {}
        self.pool: dict[str, list] = {}
        self.rtt: dict[str, float] = {}
        self.proxy = bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))

    def _bucket(self, path: str) -> _Bucket:
        key = next((k for k in RATE if k != "_" and path.startswith(k)), "_")
        b = self.buckets.get(key)
        if b is None:
            b = self.buckets[key] = _Bucket(RATE[key])
        return b

    def _conn(self, base: str):
        lst = self.pool.setdefault(base, [])
        if lst:
            return lst.pop()
        host = urllib.parse.urlparse(base).hostname
        return http.client.HTTPSConnection(host, timeout=10)

    def _release(self, base, conn):
        lst = self.pool.setdefault(base, [])
        if len(lst) < 8:
            lst.append(conn)
        else:
            conn.close()

    def _once(self, base, method, path_q, data, headers):
        if self.proxy:                              # vekil sunucu arkasında (geliştirme ortamı) urllib kullan
            req = urllib.request.Request(base + path_q, data=data.encode() if data else None, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        conn = self._conn(base)
        try:
            conn.request(method, path_q, body=data.encode() if data else None, headers=headers)
            r = conn.getresponse()
            body = r.read()
            self._release(base, conn)
            return r.status, body
        except Exception:
            conn.close()
            raise

    def _req(self, method: str, path: str, params: dict | None, body: dict | None, signed: bool):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        data = json.dumps(body, separators=(",", ":")) if body is not None else ""
        last = None
        for base in self.bases:
            headers = {"User-Agent": "polat/2", "Content-Type": "application/json", "Connection": "keep-alive"}
            if signed:
                now = datetime.now(timezone.utc)
                ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
                headers.update(sign_headers(self.key, self.secret, self.passphrase, ts, method, path + q, data))
                if self.demo:
                    headers["x-simulated-trading"] = "1"
            for attempt in range(3):
                try:
                    status, raw = self._once(base, method, path + q, data, headers)
                    js = json.loads(raw)
                    code = str(js.get("code", "0"))
                    if status == 429 or code == "50011":          # hız sınırı: bekle, tekrar dene
                        self.retries += 1
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    self.calls += 1
                    if code not in ("0", ""):
                        raise RuntimeError(f"OKX {code}: {js.get('msg')}")
                    return js.get("data", js)
                except RuntimeError:
                    raise
                except Exception as e:  # noqa: BLE001
                    last = e
                    self.retries += 1
                    break                                         # bu sunucu sorunlu → sıradakini dene
        raise last or RuntimeError("OKX: hız sınırı aşıldı")

    async def get(self, path: str, params: dict | None = None, signed: bool = False):
        await self._bucket(path).take()
        async with self.sem:
            return await asyncio.to_thread(self._req, "GET", path, params, None, signed)

    async def post(self, path: str, body: dict, signed: bool = True):
        await self._bucket(path).take()
        async with self.sem:
            return await asyncio.to_thread(self._req, "POST", path, None, body, signed)

    async def pick_fastest(self) -> dict:
        """Her sunucuya 3 kez /public/time: medyan gidiş-dönüş süresi en düşük olan öne alınır."""
        res = {}
        for base in list(self.bases):
            ts = []
            for _ in range(3):
                t0 = time.perf_counter()
                try:
                    await asyncio.to_thread(self._once, base, "GET", "/api/v5/public/time", "", {"User-Agent": "polat/2"})
                    ts.append((time.perf_counter() - t0) * 1000)
                except Exception:  # noqa: BLE001
                    ts.append(9e9)
            res[base] = sorted(ts)[1]
        self.rtt = res
        self.bases.sort(key=lambda b: res[b])
        return res

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

    async def oi_history(self, inst: str, limit: int = 100) -> list[tuple[int, float]]:
        rows = await self.get("/api/v5/rubik/stat/contracts/open-interest-history",
                              {"instId": inst, "period": "1H", "limit": limit})
        return sorted((int(r[0]), float(r[2])) for r in rows)

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
