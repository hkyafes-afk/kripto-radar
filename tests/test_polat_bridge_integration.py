from __future__ import annotations

import json
from types import SimpleNamespace

from polat import agent as polat_agent
from polat.agent import Polat
from radar.engine import Engine
from radar.unified.market_data import normalize_bbo
from radar.unified.polat_bridge import read_snapshot
from radar.unified.quality import QualityState


def _polat_telemetry():
    return {
        "kalite_telemetrisi": {
            "kanallar": {"tickers": {"sembol_sayisi": 1, "durumlar": {
                "LIVE": 1, "STALE": 0, "INVALID": 0, "UNKNOWN": 0, "DISCONNECTED": 0
            }, "son_olay_yasi_ms_max": 0}},
            "kanal_sayaclari": {"tickers": {
                "observations": 1, "live": 1, "stale": 0, "invalid": 0, "unknown": 0,
                "duplicate": 0, "out_of_order": 0, "sequence_gap": 0, "disconnected": 0,
                "dropped_sequence_updates": 0, "duplicate_event_id": 0
            }},
        },
        "gecikme_ornek_sayisi": 1, "gecikme_ms_p50": 1.0, "gecikme_ms_p95": 1.0,
        "gecikme_ms_p99": 1.0, "gecikme_ms_max": 1.0,
        "isleme_ornek_sayisi": 1, "isleme_us_p50": 1.0, "isleme_us_p95": 1.0,
        "isleme_us_p99": 1.0, "isleme_us_max": 1.0,
    }



def _bridge_telemetry():
    metrics = _polat_telemetry()
    quality = metrics["kalite_telemetrisi"]
    return {
        "channels": quality["kanallar"],
        "channel_counters": quality["kanal_sayaclari"],
        "latency": {key: metrics[key] for key in (
            "gecikme_ornek_sayisi", "gecikme_ms_p50", "gecikme_ms_p95",
            "gecikme_ms_p99", "gecikme_ms_max", "isleme_ornek_sayisi",
            "isleme_us_p50", "isleme_us_p95", "isleme_us_p99", "isleme_us_max",
        )},
        "connections": {},
    }



def test_polat_publisher_exports_only_fresh_live_quality_tickers(tmp_path, monkeypatch):
    now = 10_000
    monkeypatch.setattr(polat_agent, "now_ms", lambda: now)
    good = SimpleNamespace(last=100.0, bid=99.9, ask=100.1, tick_ts=now)
    stale = SimpleNamespace(last=200.0, bid=199.9, ask=200.1, tick_ts=now - 10_000)
    agent = object.__new__(Polat)
    agent.bridge_path = tmp_path / "shared" / "polat.json"
    agent.bridge_sequence = 0
    agent.bridge_run_id = "polat-run-1"
    agent.bridge_hmac_key = "test-only-bridge-secret-32-bytes-minimum"
    agent.market = SimpleNamespace(
        coins={"BTC-USDT-SWAP": good, "ETH-USDT-SWAP": stale},
        quality_latest={
            ("BTC-USDT-SWAP", "tickers"): SimpleNamespace(state=QualityState.LIVE),
            ("ETH-USDT-SWAP", "tickers"): SimpleNamespace(state=QualityState.LIVE),
        },
        stats=_polat_telemetry,
    )
    agent.feed = None
    agent.bridge_stats = {"published": 0, "failed": 0, "last_published_ms": None, "last_error": None}

    assert agent._publish_research_bridge() is True
    result = read_snapshot(agent.bridge_path, now_ms=now)
    assert result.status == "fresh"
    assert [item.symbol for item in result.observations] == ["BTC-USDT-SWAP"]
    assert result.source_run_id == "polat-run-1"
    assert result.telemetry["channels"]["tickers"]["durumlar"]["LIVE"] == 1
    assert result.telemetry["latency"]["gecikme_ms_p95"] == 1.0
    assert agent.bridge_stats["published"] == 1


def test_polat_publisher_does_not_publish_when_no_fresh_live_tickers(tmp_path, monkeypatch):
    now = 10_000
    monkeypatch.setattr(polat_agent, "now_ms", lambda: now)
    coin = SimpleNamespace(last=100.0, bid=99.9, ask=100.1, tick_ts=now)
    agent = object.__new__(Polat)
    agent.bridge_path = tmp_path / "shared" / "polat.json"
    agent.bridge_sequence = 0
    agent.bridge_run_id = "polat-run-2"
    agent.bridge_hmac_key = "test-only-bridge-secret-32-bytes-minimum"
    agent.market = SimpleNamespace(
        coins={"BTC-USDT-SWAP": coin},
        quality_latest={("BTC-USDT-SWAP", "tickers"): SimpleNamespace(state=QualityState.STALE)},
        stats=_polat_telemetry,
    )
    agent.feed = None
    assert agent._publish_research_bridge() is False
    assert not agent.bridge_path.exists()


def test_unified_consumer_writes_research_only_status_and_detects_replay(tmp_path, monkeypatch):
    now = 10_000
    bridge = tmp_path / "shared" / "polat.json"
    write_digest = __import__("radar.unified.polat_bridge", fromlist=["write_snapshot"]).write_snapshot
    write_digest(
        bridge, sequence=10_000, generated_at_ms=now,
        observations=[normalize_bbo(venue="okx", symbol="BTC-USDT-SWAP", last=100,
                                    bid=99.9, ask=100.1, event_ts_ms=now)],
        source_run_id="polat-run-3", telemetry=_bridge_telemetry(),
        hmac_key="test-only-bridge-secret-32-bytes-minimum",
    )
    monkeypatch.setenv("UNIFIED_POLAT_BRIDGE_PATH", str(bridge))
    monkeypatch.setenv("UNIFIED_POLAT_BRIDGE_HMAC_KEY", "test-only-bridge-secret-32-bytes-minimum")
    engine = object.__new__(Engine)
    engine.site = tmp_path / "site"

    monkeypatch.setattr("radar.engine.time.time", lambda: now / 1000)
    fresh = engine._poll_polat_research_bridge()
    assert fresh["status"] == "fresh"
    assert fresh["scope"] == "research_only"
    assert fresh["observation_count"] == 1
    assert fresh["source_run_id"] == "polat-run-3"
    assert fresh["telemetry"]["channels"]["tickers"]["durumlar"]["LIVE"] == 1
    assert fresh["telemetry"]["latency"]["gecikme_ms_p95"] == 1.0
    status_path = engine.site / "birlesik" / "polat_kopru.json"
    saved = json.loads(status_path.read_text(encoding="utf-8"))
    assert saved["status"] == "fresh"
    assert saved["telemetry"]["channel_counters"]["tickers"]["observations"] == 1
    assert "last" not in saved and "bid" not in saved and "ask" not in saved

    replay = engine._poll_polat_research_bridge()
    assert replay["status"] == "replay"
    assert replay["reason"] == "sequence_not_increasing"


def test_unified_consumer_reports_disabled_without_shared_path(tmp_path, monkeypatch):
    monkeypatch.delenv("UNIFIED_POLAT_BRIDGE_PATH", raising=False)
    monkeypatch.delenv("POLAT_UNIFIED_BRIDGE_PATH", raising=False)
    engine = object.__new__(Engine)
    engine.site = tmp_path / "site"
    result = engine._poll_polat_research_bridge()
    assert (result["status"], result["reason"]) == ("disabled", "shared_path_not_configured")


def test_bridge_telemetry_publish_failure_is_nonfatal(tmp_path, monkeypatch):
    engine = object.__new__(Engine)
    engine.site = tmp_path / "site"

    def fail_publish():
        raise OSError("read-only volume")

    monkeypatch.setattr(engine, "_poll_polat_research_bridge", fail_publish)
    status = engine._safe_poll_polat_research_bridge()
    assert status["status"] == "error"
    assert status["reason"] == "status_publish_failed:OSError"
    assert engine.polat_bridge_status == status
