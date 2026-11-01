"""Same-runner Polat research feed sidecar for the UnifiedTrader workflow.

This process owns no broker, portfolio, order adapter, or position authority. It
publishes only fresh OKX public BBO observations and feed-quality telemetry to a
runner-local HMAC-authenticated snapshot consumed by radar.engine.
"""
from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from types import SimpleNamespace

import yaml

from polat.agent import Polat
from polat.market import Market
from polat.okx import Feed, Rest, now_ms


async def run() -> None:
    bridge_path = os.environ.get("POLAT_UNIFIED_BRIDGE_PATH", "").strip()
    bridge_key = os.environ.get("POLAT_UNIFIED_BRIDGE_HMAC_KEY", "")
    if not bridge_path:
        raise SystemExit("POLAT_UNIFIED_BRIDGE_PATH is required")
    if len(bridge_key.encode("utf-8")) < 32:
        raise SystemExit("POLAT_UNIFIED_BRIDGE_HMAC_KEY must be at least 32 bytes")

    cfg_path = Path(__file__).resolve().parents[1] / "polat" / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    rest = Rest(cfg)
    await rest.pick_fastest()
    meta = await rest.instruments()
    if not meta:
        raise RuntimeError("OKX returned no eligible USDT swap instruments")

    market = Market(cfg, meta)
    # Prime only from exchange-stamped public REST tickers; missing/stale timestamps
    # remain UNKNOWN/STALE and are never replaced with local receive time.
    for ticker in await rest.tickers():
        if ticker.get("instId") in meta:
            market._ticker(ticker, now_ms())

    feed = Feed(cfg, market.handle, on_disconnect=market.disconnect_channels)
    feed.start()
    await feed.market_wide(list(meta))

    publisher = SimpleNamespace(
        bridge_path=Path(bridge_path),
        bridge_hmac_key=bridge_key,
        bridge_sequence=0,
        bridge_run_id=os.environ.get("GITHUB_RUN_ID", "local-sidecar"),
        bridge_stats={"published": 0, "failed": 0, "last_published_ms": None, "last_error": None},
        market=market,
        feed=feed,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        # Publish the primed REST snapshot immediately, then refresh from the live
        # Polat/OKX market ingestion loop once per second.
        Polat._publish_research_bridge(publisher)
        while not stop.is_set():
            try:
                Polat._publish_research_bridge(publisher)
            except Exception as exc:  # noqa: BLE001
                publisher.bridge_stats["failed"] += 1
                publisher.bridge_stats["last_error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
            await asyncio.sleep(1)
    finally:
        await feed.stop()
        market.close()
        for conn in rest.pool.values():
            for item in conn:
                try:
                    item.close()
                except Exception:  # noqa: BLE001
                    pass


if __name__ == "__main__":
    asyncio.run(run())
