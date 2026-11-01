"""Fail-closed local snapshot bridge for Polat research telemetry.

The bridge is a cross-process file transport when both processes share a
filesystem. It is deliberately research-only: UnifiedTrader must never use
these observations as execution quotes or order authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .market_data import NormalizedBBO, normalize_bbo

SCHEMA_VERSION = 1
MAX_SNAPSHOT_BYTES = 2_000_000
MAX_OBSERVATIONS = 500
DEFAULT_MAX_AGE_MS = 5_000


@dataclass(frozen=True)
class BridgeRead:
    status: str
    reason: str | None
    sequence: int | None
    generated_at_ms: int | None
    observations: tuple[NormalizedBBO, ...] = ()
    digest: str | None = None


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _observation_dict(item: NormalizedBBO) -> dict[str, Any]:
    return {
        "venue": item.venue, "symbol": item.symbol, "last": item.last,
        "bid": item.bid, "ask": item.ask, "event_ts_ms": item.event_ts_ms,
        "valid": item.valid, "reason": item.reason,
    }


def write_snapshot(
    path: str | Path, *, sequence: int, generated_at_ms: int,
    observations: list[NormalizedBBO], source_run_id: str,
) -> str:
    """Atomically publish one Polat research snapshot and return its SHA-256."""
    if not isinstance(sequence, int) or sequence < 1:
        raise ValueError("sequence_must_be_positive_integer")
    if not isinstance(generated_at_ms, int) or generated_at_ms <= 0:
        raise ValueError("generated_at_ms_must_be_positive_integer")
    if not source_run_id or len(source_run_id) > 128:
        raise ValueError("source_run_id_invalid")
    if not observations or len(observations) > MAX_OBSERVATIONS:
        raise ValueError("observation_count_out_of_range")
    rows = []
    for item in observations:
        if not isinstance(item, NormalizedBBO) or not item.valid or item.event_ts_ms is None:
            raise ValueError("only_valid_timestamped_normalized_bbo_allowed")
        if item.venue.lower() != "okx":
            raise ValueError("unsupported_venue")
        rows.append(_observation_dict(item))
    body = {
        "schema_version": SCHEMA_VERSION, "source": "polat",
        "scope": "research_only", "sequence": sequence,
        "generated_at_ms": generated_at_ms, "source_run_id": source_run_id,
        "observations": rows,
    }
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    envelope = {**body, "sha256": digest}
    encoded = _canonical(envelope)
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot_too_large")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, target)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    return digest


def read_snapshot(
    path: str | Path, *, now_ms: int | None = None,
    max_age_ms: int = DEFAULT_MAX_AGE_MS, last_sequence: int | None = None,
) -> BridgeRead:
    """Read and validate a snapshot; never returns stale or replayed observations."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return BridgeRead("missing", "snapshot_missing", None, None)
    except OSError:
        return BridgeRead("error", "snapshot_unreadable", None, None)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        return BridgeRead("invalid", "snapshot_too_large", None, None)
    try:
        envelope = json.loads(raw)
        digest = envelope.pop("sha256")
        expected = hashlib.sha256(_canonical(envelope)).hexdigest()
        if not isinstance(digest, str) or not hashlib.compare_digest(digest, expected):
            return BridgeRead("invalid", "digest_mismatch", None, None)
        if envelope.get("schema_version") != SCHEMA_VERSION:
            return BridgeRead("invalid", "schema_version_unsupported", None, None)
        if envelope.get("source") != "polat" or envelope.get("scope") != "research_only":
            return BridgeRead("invalid", "source_or_scope_invalid", None, None)
        seq = envelope.get("sequence")
        stamp = envelope.get("generated_at_ms")
        rows = envelope.get("observations")
        if not isinstance(seq, int) or seq < 1 or not isinstance(stamp, int) or stamp <= 0:
            return BridgeRead("invalid", "sequence_or_timestamp_invalid", None, None)
        if last_sequence is not None and seq <= last_sequence:
            return BridgeRead("replay", "sequence_not_increasing", seq, stamp)
        if not isinstance(rows, list) or not rows or len(rows) > MAX_OBSERVATIONS:
            return BridgeRead("invalid", "observation_count_invalid", seq, stamp)
        current = int(time.time() * 1000) if now_ms is None else int(now_ms)
        age = current - stamp
        if age < -1_000:
            return BridgeRead("invalid", "snapshot_from_future", seq, stamp)
        if age > max_age_ms:
            return BridgeRead("stale", "snapshot_expired", seq, stamp)
        observations = []
        for row in rows:
            if not isinstance(row, dict):
                return BridgeRead("invalid", "observation_shape_invalid", seq, stamp)
            item = normalize_bbo(
                venue=row.get("venue"), symbol=row.get("symbol"), last=row.get("last"),
                bid=row.get("bid"), ask=row.get("ask"), event_ts_ms=row.get("event_ts_ms"),
            )
            if not item.valid or item.event_ts_ms is None or item.venue.lower() != "okx":
                return BridgeRead("invalid", "observation_quality_invalid", seq, stamp)
            if current - item.event_ts_ms > max_age_ms or item.event_ts_ms > current + 1_000:
                return BridgeRead("stale", "observation_timestamp_out_of_range", seq, stamp)
            observations.append(item)
        return BridgeRead("fresh", None, seq, stamp, tuple(observations), digest)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return BridgeRead("invalid", "snapshot_parse_or_schema_error", None, None)
