from __future__ import annotations

import asyncio

import pytest

from scripts import polat_unified_sidecar as sidecar


@pytest.mark.asyncio
async def test_sidecar_uses_public_feed_and_only_research_publisher(tmp_path, monkeypatch):
    calls = []

    class FakeRest:
        def __init__(self, _cfg):
            pass

        async def pick_fastest(self):
            calls.append("pick_fastest")

        async def instruments(self):
            return {"BTC-USDT-SWAP": {"ctVal": 1.0}}

        async def tickers(self):
            return [{"instId": "BTC-USDT-SWAP"}]

    class FakeMarket:
        def __init__(self, cfg, meta):
            calls.append(("market", bool(cfg), list(meta)))

        def _ticker(self, ticker, received):
            calls.append(("rest_ticker", ticker["instId"], received))

        def handle(self, *_args):
            pass

        def disconnect_channels(self, *_args):
            pass

        def close(self):
            calls.append("market_closed")

    class FakeFeed:
        def __init__(self, _cfg, _handler, on_disconnect=None):
            self.stats = {"baglanti_telemetrisi": {}}
            self.on_disconnect = on_disconnect

        def start(self):
            calls.append("feed_started")

        async def market_wide(self, instruments):
            calls.append(("market_wide", instruments))

        async def stop(self):
            calls.append("feed_stopped")

    def publish(publisher):
        calls.append(("published", publisher.bridge_path, publisher.bridge_hmac_key,
                      publisher.market is not None, publisher.feed is not None,
                      hasattr(publisher, "broker"), hasattr(publisher, "adapter")))
        return True

    monkeypatch.setenv("POLAT_UNIFIED_BRIDGE_PATH", str(tmp_path / "bridge.json"))
    monkeypatch.setenv("POLAT_UNIFIED_BRIDGE_HMAC_KEY", "test-secret-that-is-at-least-32-bytes-long")
    monkeypatch.setenv("GITHUB_RUN_ID", "test-run")
    monkeypatch.setattr(sidecar, "Rest", FakeRest)
    monkeypatch.setattr(sidecar, "Market", FakeMarket)
    monkeypatch.setattr(sidecar, "Feed", FakeFeed)
    monkeypatch.setattr(sidecar.Polat, "_publish_research_bridge", staticmethod(publish))

    async def stop_after_first_publish(_delay):
        raise asyncio.CancelledError

    monkeypatch.setattr(sidecar.asyncio, "sleep", stop_after_first_publish)
    with pytest.raises(asyncio.CancelledError):
        await sidecar.run()

    assert "pick_fastest" in calls
    assert "feed_started" in calls
    assert ("market_wide", ["BTC-USDT-SWAP"]) in calls
    published = next(item for item in calls if isinstance(item, tuple) and item[0] == "published")
    assert published[1] == tmp_path / "bridge.json"
    assert published[2] == "test-secret-that-is-at-least-32-bytes-long"
    assert published[3:5] == (True, True)
    assert published[5:] == (False, False)  # no broker or execution adapter
    assert "feed_stopped" in calls and "market_closed" in calls
