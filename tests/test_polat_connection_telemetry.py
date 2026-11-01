import asyncio

from polat.okx import Conn


class _BrokenSocket:
    async def recv(self):
        raise ConnectionError("test disconnect")

    async def close(self):
        return None

    async def send_text(self, _payload):
        return None


def test_connection_group_exposes_attempt_connect_and_disconnect_timestamps(monkeypatch):
    import polat.okx as okx

    async def fake_connect(_url):
        return _BrokenSocket()

    monkeypatch.setattr(okx.WS, "connect", fake_connect)

    async def scenario():
        stats = {"baglanti": {}, "kopma": {}, "son_hata": ""}
        disconnected = []
        conn = Conn("piyasa", ["wss://test.invalid"], lambda _msg, _ts: None, stats,
                    on_disconnect=lambda name, ts: disconnected.append((name, ts)))
        conn.start()
        try:
            async def disconnected_once():
                return stats.get("baglanti_telemetrisi", {}).get("piyasa", {}).get("disconnects", 0) >= 1
            for _ in range(100):
                if await disconnected_once():
                    break
                await asyncio.sleep(0.01)
            telemetry = stats["baglanti_telemetrisi"]["piyasa"]
            assert telemetry["attempts"] == 1
            assert telemetry["connected"] == 1
            assert telemetry["disconnects"] == 1
            assert telemetry["last_attempt_ms"] > 0
            assert telemetry["last_connected_ms"] > 0
            assert telemetry["last_disconnected_ms"] > 0
            assert disconnected and disconnected[0][0] == "piyasa"
        finally:
            await conn.stop()
            if conn.task:
                await asyncio.gather(conn.task, return_exceptions=True)

    asyncio.run(scenario())
