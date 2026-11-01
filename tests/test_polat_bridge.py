from __future__ import annotations

import json

import pytest

from radar.unified.market_data import normalize_bbo
from radar.unified.polat_bridge import read_snapshot, write_snapshot


def _bbo(ts: int):
    return normalize_bbo(
        venue="okx", symbol="BTC-USDT-SWAP", last=100.0,
        bid=99.9, ask=100.1, event_ts_ms=ts,
    )


def test_snapshot_round_trip_is_research_only_and_fresh(tmp_path):
    path = tmp_path / "bridge" / "polat.json"
    digest = write_snapshot(
        path, sequence=1, generated_at_ms=10_000,
        observations=[_bbo(9_999)], source_run_id="workflow-123",
    )
    result = read_snapshot(path, now_ms=10_000, last_sequence=0)
    assert result.status == "fresh"
    assert result.reason is None
    assert result.sequence == 1
    assert result.digest == digest
    assert len(result.observations) == 1
    assert result.observations[0].symbol == "BTC-USDT-SWAP"


def test_snapshot_rejects_replayed_sequence(tmp_path):
    path = tmp_path / "polat.json"
    write_snapshot(path, sequence=4, generated_at_ms=10_000,
                   observations=[_bbo(10_000)], source_run_id="run-4")
    result = read_snapshot(path, now_ms=10_000, last_sequence=4)
    assert (result.status, result.reason) == ("replay", "sequence_not_increasing")


def test_snapshot_rejects_stale_data(tmp_path):
    path = tmp_path / "polat.json"
    write_snapshot(path, sequence=1, generated_at_ms=1_000,
                   observations=[_bbo(1_000)], source_run_id="old-run")
    result = read_snapshot(path, now_ms=10_000, max_age_ms=2_000)
    assert (result.status, result.reason) == ("stale", "snapshot_expired")


def test_snapshot_rejects_tampered_payload(tmp_path):
    path = tmp_path / "polat.json"
    write_snapshot(path, sequence=1, generated_at_ms=10_000,
                   observations=[_bbo(10_000)], source_run_id="run")
    payload = json.loads(path.read_text())
    payload["observations"][0]["last"] = 101.0
    path.write_text(json.dumps(payload))
    result = read_snapshot(path, now_ms=10_000)
    assert (result.status, result.reason) == ("invalid", "digest_mismatch")


def test_snapshot_writer_rejects_invalid_or_untimestamped_bbo(tmp_path):
    path = tmp_path / "polat.json"
    invalid = normalize_bbo(
        venue="okx", symbol="BTC-USDT-SWAP", last=100,
        bid=100, ask=100, event_ts_ms=10_000,
    )
    try:
        write_snapshot(path, sequence=1, generated_at_ms=10_000,
                       observations=[invalid], source_run_id="run")
    except ValueError as exc:
        assert str(exc) == "only_valid_timestamped_normalized_bbo_allowed"
    else:
        raise AssertionError("invalid BBO must not be published")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sequence": True, "generated_at_ms": 10_000, "source_run_id": "run"},
        {"sequence": 1, "generated_at_ms": True, "source_run_id": "run"},
        {"sequence": 1, "generated_at_ms": 10_000, "source_run_id": 123},
        {"sequence": 1, "generated_at_ms": 10_000, "source_run_id": "   "},
    ],
)
def test_snapshot_writer_rejects_invalid_identity_and_boolean_integers(tmp_path, kwargs):
    values = {"sequence": 1, "generated_at_ms": 10_000, "source_run_id": "run"}
    values.update(kwargs)
    with pytest.raises(ValueError):
        write_snapshot(tmp_path / "bad.json", observations=[_bbo(10_000)], **values)


def test_snapshot_writer_rejects_stale_or_future_observations(tmp_path):
    with pytest.raises(ValueError, match="observation_timestamp_out_of_range"):
        write_snapshot(tmp_path / "stale.json", sequence=1, generated_at_ms=10_000,
                       observations=[_bbo(1_000)], source_run_id="run")
    with pytest.raises(ValueError, match="observation_timestamp_out_of_range"):
        write_snapshot(tmp_path / "future.json", sequence=1, generated_at_ms=10_000,
                       observations=[_bbo(12_000)], source_run_id="run")


def test_snapshot_writer_rejects_duplicate_symbols(tmp_path):
    with pytest.raises(ValueError, match="duplicate_symbol"):
        write_snapshot(tmp_path / "duplicate.json", sequence=1, generated_at_ms=10_000,
                       observations=[_bbo(10_000), _bbo(10_000)], source_run_id="run")


def test_snapshot_reader_rejects_non_object_root_and_invalid_limits(tmp_path):
    path = tmp_path / "root.json"
    path.write_text("[]", encoding="utf-8")
    assert read_snapshot(path, now_ms=10_000).reason == "snapshot_root_not_object"
    assert read_snapshot(path, now_ms=10_000, max_age_ms=0).reason == "max_age_ms_invalid"
    assert read_snapshot(path, now_ms=True).reason == "now_ms_invalid"


def test_snapshot_reader_reports_source_run_id_and_rejects_unknown_fields(tmp_path):
    path = tmp_path / "polat.json"
    write_snapshot(path, sequence=1, generated_at_ms=10_000,
                   observations=[_bbo(10_000)], source_run_id="workflow-456")
    result = read_snapshot(path, now_ms=10_000)
    assert result.status == "fresh"
    assert result.source_run_id == "workflow-456"

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unexpected"] = "not-in-schema"
    payload["sha256"] = __import__("hashlib").sha256(
        json.dumps({k: v for k, v in payload.items() if k != "sha256"},
                   sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")
    rejected = read_snapshot(path, now_ms=10_000)
    assert (rejected.status, rejected.reason) == ("invalid", "snapshot_fields_invalid")
