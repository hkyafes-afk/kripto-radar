"""Bağımlılıksız asyncio WebSocket istemcisi (RFC 6455).

Neden kendi istemcimiz: ek paket kurmadan GitHub'da da sunucuda da aynı çalışır. Sunucudan gelen
çerçeveler maskesizdir, okuma ucuzdur; olay döngüsü mesaj gelir gelmez işler (sabit aralık yok).
"""
from __future__ import annotations

import asyncio
import base64
import os
import ssl
import struct
from urllib.parse import urlparse

try:
    import orjson as _oj

    def loads(b):
        return _oj.loads(b)

    def dumps(o) -> str:
        return _oj.dumps(o).decode()
except ImportError:  # pragma: no cover
    import json as _js

    def loads(b):
        return _js.loads(b)

    def dumps(o) -> str:
        return _js.dumps(o, separators=(",", ":"))


class WSClosed(Exception):
    pass


def _mask(data: bytes, key: bytes) -> bytes:
    if not data:
        return data
    n = len(data)
    k = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(k, "big")).to_bytes(n, "big")


def encode_frame(payload: bytes, opcode: int = 1) -> bytes:
    """İstemci çerçevesi (her zaman maskeli)."""
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    key = os.urandom(4)
    return head + key + _mask(payload, key)


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bool, bytes]:
    b1, b2 = await reader.readexactly(2)
    fin, opcode = bool(b1 & 0x80), b1 & 0x0F
    masked, n = bool(b2 & 0x80), b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", await reader.readexactly(8))[0]
    key = await reader.readexactly(4) if masked else b""
    data = await reader.readexactly(n) if n else b""
    if masked:
        data = _mask(data, key)
    return opcode, fin, data


class WS:
    def __init__(self, reader, writer):
        self.r, self.w = reader, writer
        self.closed = False

    @classmethod
    async def connect(cls, url: str, timeout: float = 10.0, headers: dict | None = None) -> "WS":
        u = urlparse(url)
        port = u.port or (443 if u.scheme == "wss" else 80)
        ctx = ssl.create_default_context() if u.scheme == "wss" else None
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(u.hostname, port, ssl=ctx, server_hostname=u.hostname if ctx else None,
                                    limit=2 ** 24), timeout)
        sock = writer.get_extra_info("socket")
        if sock is not None:
            import socket
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # küçük paketleri bekletme
            except OSError:
                pass
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
        req = (f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               f"User-Agent: polat/1\r\n{extra}\r\n")
        writer.write(req.encode())
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            writer.close()
            raise WSClosed(f"el sıkışma reddedildi: {head[:120]!r}")
        return cls(reader, writer)

    async def send_text(self, s: str):
        if self.closed:
            raise WSClosed("kapalı")
        self.w.write(encode_frame(s.encode(), 1))
        await self.w.drain()

    async def recv(self) -> bytes | str:
        """Bir sonraki veri mesajı (parçalı mesajlar birleştirilir, ping'e pong verilir)."""
        buf, op0 = b"", None
        while True:
            op, fin, data = await read_frame(self.r)
            if op == 9:                                   # ping
                self.w.write(encode_frame(data, 10))
                continue
            if op == 10:                                  # pong
                continue
            if op == 8:
                self.closed = True
                raise WSClosed("sunucu kapattı")
            if op in (1, 2):
                op0, buf = op, data
            elif op == 0:
                buf += data
            if fin:
                return buf.decode() if op0 == 1 else buf

    async def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.w.write(encode_frame(b"", 8))
                await self.w.drain()
            except Exception:  # noqa: BLE001
                pass
            self.w.close()
