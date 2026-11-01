"""Bağımlılıksız WebSocket istemcisi (RFC 6455) + borsa canlı veri akışları.

Neden kendi istemcimiz? Ek paket kurulumu gerektirmez; GitHub'da bozulacak bir bağımlılık yok.
Sadece ihtiyacımız olan kısım: TLS, el sıkışma, maskeli gönderim, parçalı mesaj, ping/pong, kapanış.

Akışlar:
  OKXFeed    → tickers (son fiyat, bid, ask; olay bazlı, ~100 ms) + funding-rate kanalı
  BitgetFeed → USDT-M futures ticker (fiyat, bid/ask, funding)
Her akış kendi thread'inde çalışır, kopunca kademeli bekleyip yeniden bağlanır, birden çok adres dener.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urlparse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WSClosed(Exception):
    pass


def _mask(data: bytes, key: bytes) -> bytes:
    if not data:
        return data
    n = len(data)
    k = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(k, "big")).to_bytes(n, "big")


class WSConn:
    def __init__(self, url: str, timeout: float = 15.0, headers: dict | None = None):
        u = urlparse(url)
        self.host = u.hostname
        self.port = u.port or (443 if u.scheme == "wss" else 80)
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        raw = socket.create_connection((self.host, self.port), timeout=timeout)
        if u.scheme == "wss":
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        self.sock = raw
        self._buf = b""
        self.lock = threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode()
        extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
        req = (f"GET {path} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               f"User-Agent: Mozilla/5.0 kripto-radar/4\r\n{extra}\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSClosed("el sıkışma sırasında bağlantı kapandı")
            resp += chunk
            if len(resp) > 65536:
                raise WSClosed("el sıkışma cevabı çok büyük")
        head, self._buf = resp.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if " 101" not in lines[0]:
            raise WSClosed(f"el sıkışma reddedildi: {lines[0]}")
        hdrs = {ln.split(":", 1)[0].strip().lower(): ln.split(":", 1)[1].strip() for ln in lines[1:] if ":" in ln}
        expect = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if hdrs.get("sec-websocket-accept") != expect:
            raise WSClosed("geçersiz Sec-WebSocket-Accept")

    def settimeout(self, t: float):
        self.sock.settimeout(t)

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WSClosed("sunucu bağlantıyı kapattı")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, data, opcode: int = 1):
        if isinstance(data, str):
            data = data.encode()
        n = len(data)
        hdr = bytearray([0x80 | opcode])
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack("!H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack("!Q", n)
        key = os.urandom(4)
        with self.lock:
            self.sock.sendall(bytes(hdr) + key + _mask(data, key))

    def recv(self) -> tuple[int, bytes]:
        """Tam bir mesaj döner (op, payload). Ping'e otomatik pong verir."""
        msg, op0 = b"", None
        while True:
            b1, b2 = self._recv_exact(2)
            fin, op = b1 & 0x80, b1 & 0x0F
            masked, n = b2 & 0x80, b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._recv_exact(8))[0]
            key = self._recv_exact(4) if masked else None
            payload = self._recv_exact(n)
            if key:
                payload = _mask(payload, key)
            if op == 9:
                self.send(payload, 10)
                continue
            if op == 10:
                continue
            if op == 8:
                raise WSClosed("sunucu kapanış çerçevesi gönderdi")
            if op in (1, 2):
                op0, msg = op, payload
            elif op == 0:
                msg += payload
            if fin:
                return op0 or 1, msg

    def close(self):
        try:
            self.send(b"", 8)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.sock.close()
        except Exception:  # noqa: BLE001
            pass


class Feed(threading.Thread):
    """Genel akış: bağlan → abone ol → mesajları işle → koparsa yeniden bağlan."""

    name_ = "feed"
    urls: list[str] = []
    ping_every = 20.0
    stale_after = 30.0

    def __init__(self, on_tick=None, on_funding=None):
        super().__init__(daemon=True, name=self.name_)
        self.on_tick = on_tick or (lambda *a: None)
        self.on_funding = on_funding or (lambda *a: None)
        self.symbols: list[str] = []
        self.conn: WSConn | None = None
        self.stop_ev = threading.Event()
        self.connected = False
        self.msgs = 0
        self.ticks = 0
        self.reconnects = 0
        self.last_msg = 0.0
        self.connected_at = 0.0
        self.url = ""
        self.error = ""
        self._url_i = 0
        self._lock = threading.Lock()
        self._tick_listeners = []

    def add_tick_listener(self, callback):
        """Add an observer without changing the legacy on_tick callback contract."""
        if not callable(callback):
            raise TypeError("tick listener callable olmalı")
        with self._lock:
            if callback not in self._tick_listeners:
                self._tick_listeners.append(callback)

    def _publish_tick(self, event: dict):
        with self._lock:
            listeners = tuple(self._tick_listeners)
        for callback in listeners:
            try:
                callback(dict(event))
            except Exception as exc:  # noqa: BLE001
                self.error = f"tick listener: {type(exc).__name__}: {exc}"

    # alt sınıflar
    def sub_messages(self, symbols: list[str], op: str = "subscribe") -> list[str]:
        raise NotImplementedError

    def handle(self, obj):
        raise NotImplementedError

    # dış arayüz
    def set_symbols(self, symbols: list[str]):
        """Abonelik listesini günceller. Bağlıysa kopmadan ekler/çıkarır."""
        symbols = sorted(set(symbols))
        with self._lock:
            if symbols == self.symbols:
                return
            added = sorted(set(symbols) - set(self.symbols))
            removed = sorted(set(self.symbols) - set(symbols))
            self.symbols = symbols
        c = self.conn
        if c is None or not self.connected:
            return                       # bağlanınca tam liste ile abone olunur
        try:
            for m in (self.sub_messages(added) if added else []) + \
                     (self.sub_messages(removed, op="unsubscribe") if removed else []):
                c.send(m)
                time.sleep(0.12)
        except Exception:  # noqa: BLE001
            c.close()                    # gönderilemezse yeniden bağlanıp tam listeyle abone olur

    def healthy(self) -> bool:
        return self.connected and time.time() - self.last_msg < 15

    def status(self) -> dict:
        extra = {"missing": len(self.missing)} if hasattr(self, "missing") else {}
        return {**extra, "connected": self.connected, "healthy": self.healthy(), "url": self.url,
                "last_msg_age_s": round(time.time() - self.last_msg, 1) if self.last_msg else None,
                "msgs": self.msgs, "ticks": self.ticks, "reconnects": self.reconnects,
                "symbols": len(self.symbols), "error": self.error[:160],
                "uptime_s": round(time.time() - self.connected_at) if self.connected else 0}

    def stop(self):
        self.stop_ev.set()
        if self.conn:
            self.conn.close()

    def run(self):
        backoff = 1.0
        while not self.stop_ev.is_set():
            if not self.symbols:
                time.sleep(0.5)
                continue
            url = self.urls[self._url_i % len(self.urls)]
            try:
                self.url = url
                self.conn = WSConn(url)
                self.conn.settimeout(5)
                for m in self.sub_messages(list(self.symbols)):
                    self.conn.send(m)
                    time.sleep(0.12)   # borsa abonelik hız sınırı
                self.connected = True
                self.connected_at = time.time()
                self.last_msg = time.time()
                self.error = ""
                backoff = 1.0
                last_ping = time.time()
                while not self.stop_ev.is_set():
                    if time.time() - last_ping >= self.ping_every:
                        self.conn.send("ping")
                        last_ping = time.time()
                    try:
                        _, raw = self.conn.recv()
                    except socket.timeout:
                        if time.time() - self.last_msg > self.stale_after:
                            raise WSClosed(f"{self.stale_after:.0f} sn veri gelmedi")
                        continue
                    self.last_msg = time.time()
                    self.msgs += 1
                    if raw in (b"pong", b"ping"):
                        continue
                    try:
                        obj = json.loads(raw)
                    except ValueError:
                        continue
                    self.handle(obj)
            except Exception as e:  # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
                self._url_i += 1
            finally:
                self.connected = False
                if self.conn:
                    self.conn.close()
                self.conn = None
            if self.stop_ev.is_set():
                break
            self.reconnects += 1
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


class OKXFeed(Feed):
    name_ = "okx-ws"
    urls = ["wss://ws.okx.com/ws/v5/public", "wss://wsaws.okx.com/ws/v5/public",
            "wss://wsus.okx.com/ws/v5/public"]
    ping_every = 20.0

    def sub_messages(self, symbols, op="subscribe"):
        args = [{"channel": "tickers", "instId": s} for s in symbols]
        # funding yalnızca vadeli kontratlarda var; spot sembolde (carry'nin spot ayağı) OKX isteği reddeder
        args += [{"channel": "funding-rate", "instId": s} for s in symbols if s.endswith("-SWAP")]
        return [json.dumps({"op": op, "args": args[i:i + 40]}) for i in range(0, len(args), 40)]

    def handle(self, obj):
        if "event" in obj:
            if obj.get("event") == "error":
                self.error = f"abonelik hatası: {obj.get('msg')}"
            return
        arg, data = obj.get("arg") or {}, obj.get("data") or []
        ch = arg.get("channel")
        for d in data:
            inst = d.get("instId") or arg.get("instId")
            if ch == "tickers":
                self.ticks += 1
                ts_ms = int(_f(d.get("ts"))) or None
                last, bid, ask = _f(d.get("last")), _f(d.get("bidPx")), _f(d.get("askPx"))
                self.on_tick("okx", inst, last, bid, ask, ts_ms)
                self._publish_tick({
                    "exchange": "okx", "inst": inst, "last": last, "bid": bid, "ask": ask,
                    "bid_size": _f(d.get("bidSz")), "ask_size": _f(d.get("askSz")), "ts_ms": ts_ms,
                })
            elif ch == "funding-rate":
                self.on_funding("okx", inst, _f(d.get("fundingRate")), int(_f(d.get("fundingTime"))),
                                int(_f(d.get("nextFundingTime"))))


class BitgetFeed(Feed):
    """Bitget USDT-M vadeli. Sembol: BTCUSDT. Engine tarafına 'BTC' gibi baz coin olarak iletilir."""
    name_ = "bitget-ws"
    urls = ["wss://ws.bitget.com/v2/ws/public"]
    ping_every = 25.0

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.missing: set[str] = set()

    def sub_messages(self, symbols, op="subscribe"):
        args = [{"instType": "USDT-FUTURES", "channel": "ticker", "instId": f"{s}USDT"} for s in symbols]
        return [json.dumps({"op": op, "args": args[i:i + 40]}) for i in range(0, len(args), 40)]

    def handle(self, obj):
        if "event" in obj:
            if obj.get("event") == "error":
                msg = str(obj.get("msg", ""))
                if "doesn't exist" in msg or "not exist" in msg:
                    arg = obj.get("arg") or {}
                    self.missing.add(str(arg.get("instId") or msg)[:20])   # Bitget'te listelenmemiş coin: normal
                else:
                    self.error = f"abonelik hatası: {msg}"
            return
        for d in obj.get("data") or []:
            sym = (d.get("instId") or d.get("symbol") or "")
            if not sym.endswith("USDT"):
                continue
            base = sym[:-4]
            self.ticks += 1
            self.on_tick("bitget", base, _f(d.get("lastPr")), _f(d.get("bidPr")), _f(d.get("askPr")),
                         int(_f(d.get("ts"))) or int(time.time() * 1000))
            if d.get("fundingRate") not in (None, ""):
                self.on_funding("bitget", base, _f(d.get("fundingRate")), int(_f(d.get("nextFundingTime"))), 0)
