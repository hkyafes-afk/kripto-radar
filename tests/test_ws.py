"""WebSocket istemcisi ve borsa akışları — yerel sahte sunucuyla test (internet gerekmez)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import ws  # noqa: E402


def _frame(payload: bytes, op=1, fin=True):
    b1 = (0x80 if fin else 0) | op
    n = len(payload)
    if n < 126:
        return bytes([b1, n]) + payload
    if n < 65536:
        return bytes([b1, 126]) + struct.pack("!H", n) + payload
    return bytes([b1, 127]) + struct.pack("!Q", n) + payload


def _read_frame(conn, buf):
    def need(k):
        nonlocal buf
        while len(buf) < k:
            c = conn.recv(65536)
            if not c:
                raise EOFError
            buf += c
        out, buf = buf[:k], buf[k:]
        return out
    b1, b2 = need(2)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", need(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", need(8))[0]
    key = need(4)
    data = bytes(b ^ key[i % 4] for i, b in enumerate(need(n)))
    return b1 & 0x0F, data, buf


class FakeServer(threading.Thread):
    """Tek bağlantı kabul eden basit WS sunucusu. behaviour(conn, recv) senaryoyu yürütür."""

    def __init__(self, behaviour, accept_count=1):
        super().__init__(daemon=True)
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.behaviour = behaviour
        self.accept_count = accept_count
        self.received = []
        self.connections = 0

    def run(self):
        for _ in range(self.accept_count):
            conn, _ = self.srv.accept()
            self.connections += 1
            req = b""
            while b"\r\n\r\n" not in req:
                req += conn.recv(4096)
            key = [ln.split(b":", 1)[1].strip() for ln in req.split(b"\r\n") if ln.lower().startswith(b"sec-websocket-key")][0]
            acc = base64.b64encode(hashlib.sha1(key + ws.GUID.encode()).digest())
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         b"Sec-WebSocket-Accept: " + acc + b"\r\n\r\n")
            state = {"buf": b""}

            def recv():
                op, data, state["buf"] = _read_frame(conn, state["buf"])
                self.received.append((op, data))
                return op, data
            try:
                self.behaviour(conn, recv)
            except (EOFError, OSError):
                pass
            finally:
                conn.close()


def test_wsconn_roundtrip():
    def beh(conn, recv):
        op, data = recv()                         # istemci mesajı (maskeli)
        conn.sendall(_frame(b"echo:" + data))
        conn.sendall(_frame(b"hb", op=9))         # ping → istemci pong dönmeli
        conn.sendall(_frame(b'{"a":', fin=False) + _frame(b'1}', op=0))   # parçalı mesaj
        big = b"x" * 70000
        conn.sendall(_frame(big))                 # 64-bit uzunluk
        recv()                                    # pong
        time.sleep(0.3)
    srv = FakeServer(beh)
    srv.start()
    c = ws.WSConn(f"ws://127.0.0.1:{srv.port}/ws")
    c.settimeout(5)
    c.send("merhaba")
    assert c.recv() == (1, b"echo:merhaba")
    assert c.recv() == (1, b'{"a":1}')
    op, big = c.recv()
    assert len(big) == 70000
    time.sleep(0.2)
    c.close()
    srv.join(2)
    assert (1, b"merhaba") in srv.received and (10, b"hb") in srv.received


def test_okx_feed_parse_and_reconnect():
    ticks, funds = [], []

    def beh(conn, recv):
        op, data = recv()
        sub = json.loads(data)
        assert sub["op"] == "subscribe"
        conn.sendall(_frame(json.dumps({"event": "subscribe", "arg": sub["args"][0]}).encode()))
        conn.sendall(_frame(json.dumps({"arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
                                        "data": [{"instId": "BTC-USDT-SWAP", "last": "80000.5", "bidPx": "80000.4",
                                                  "askPx": "80000.6", "ts": "1792990000000"}]}).encode()))
        conn.sendall(_frame(json.dumps({"arg": {"channel": "funding-rate", "instId": "BTC-USDT-SWAP"},
                                        "data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "0.0001",
                                                  "fundingTime": "1792995200000", "nextFundingTime": "1793024000000"}]}).encode()))
        time.sleep(0.4)
        conn.close()   # kopma → istemci yeniden bağlanmalı

    srv = FakeServer(beh, accept_count=2)
    srv.start()
    f = ws.OKXFeed(on_tick=lambda *a: ticks.append(a), on_funding=lambda *a: funds.append(a))
    f.urls = [f"ws://127.0.0.1:{srv.port}/ws/v5/public"]
    f.set_symbols(["BTC-USDT-SWAP", "ETH-USDT-SWAP"])
    f.start()
    t0 = time.time()
    while srv.connections < 2 and time.time() - t0 < 8:
        time.sleep(0.1)
    f.stop()
    assert ticks and ticks[0][:5] == ("okx", "BTC-USDT-SWAP", 80000.5, 80000.4, 80000.6)
    assert funds and funds[0][2] == 0.0001 and funds[0][3] == 1792995200000
    assert srv.connections >= 2 and f.reconnects >= 1


def test_bitget_feed_parse():
    got = []
    f = ws.BitgetFeed(on_tick=lambda *a: got.append(a), on_funding=lambda *a: got.append(("F",) + a))
    msgs = f.sub_messages(["BTC", "SOL"])
    assert json.loads(msgs[0])["args"][1]["instId"] == "SOLUSDT"
    f.handle({"action": "snapshot", "arg": {"instType": "USDT-FUTURES", "channel": "ticker", "instId": "SOLUSDT"},
              "data": [{"instId": "SOLUSDT", "lastPr": "185.2", "bidPr": "185.1", "askPr": "185.3",
                        "fundingRate": "0.0002", "nextFundingTime": "1792995200000", "ts": "1792990000000"}]})
    assert got[0][:5] == ("bitget", "SOL", 185.2, 185.1, 185.3)
    assert got[1][0] == "F" and got[1][3] == 0.0002


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
