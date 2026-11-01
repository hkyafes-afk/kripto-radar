"""Fail-closed local snapshot bridge for Polat research telemetry.

The bridge is a cross-process file transport when both processes share a
filesystem. It is deliberately research-only: UnifiedTrader must never use
these observations as execution quotes or order authority.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .market_data import NormalizedBBO, normalize_bbo

SCHEMA_VERSION = 2
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
    source_run_id: str | None = None
    telemetry: dict[str, Any] | None = None


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _hmac_key_bytes(value: str | bytes | None) -> bytes | None:
    if value is None:
        return None
    key = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("hmac_key_must_be_at_least_32_bytes")
    return key


def _observation_dict(item: NormalizedBBO) -> dict[str, Any]:
    return {
        "venue": item.venue, "symbol": item.symbol, "last": item.last,
        "bid": item.bid, "ask": item.ask, "event_ts_ms": item.event_ts_ms,
        "valid": item.valid, "reason": item.reason,
    }


def _validate_telemetry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"channels", "channel_counters", "latency", "connections"}:
        raise ValueError("telemetry_shape_invalid")
    if any(not isinstance(value[k], dict) for k in value):
        raise ValueError("telemetry_shape_invalid")
    channels = value["channels"]
    counters = value["channel_counters"]
    connections = value["connections"]
    latency = value["latency"]
    if len(channels) > 32 or len(counters) > 32 or len(connections) > 16:
        raise ValueError("telemetry_size_invalid")
    states = {"LIVE", "STALE", "INVALID", "UNKNOWN", "DISCONNECTED"}
    for channel, row in channels.items():
        if not isinstance(channel, str) or not channel or len(channel) > 64 or not isinstance(row, dict):
            raise ValueError("telemetry_channel_invalid")
        if set(row) != {"sembol_sayisi", "durumlar", "son_olay_yasi_ms_max"}:
            raise ValueError("telemetry_channel_fields_invalid")
        counts = row["durumlar"]
        if (type(row["sembol_sayisi"]) is not int or row["sembol_sayisi"] < 0
                or not isinstance(counts, dict) or set(counts) != states
                or any(type(counts[k]) is not int or counts[k] < 0 for k in states)):
            raise ValueError("telemetry_channel_counts_invalid")
        age = row["son_olay_yasi_ms_max"]
        if age is not None and (type(age) is not int or age < 0):
            raise ValueError("telemetry_channel_age_invalid")
    counter_keys = {"observations", "live", "stale", "invalid", "unknown", "duplicate",
                    "out_of_order", "sequence_gap", "disconnected", "dropped_sequence_updates",
                    "duplicate_event_id"}
    for channel, row in counters.items():
        if (not isinstance(channel, str) or not channel or len(channel) > 64
                or not isinstance(row, dict) or set(row) - counter_keys
                or any(type(v) is not int or v < 0 for v in row.values())):
            raise ValueError("telemetry_counters_invalid")
    latency_keys = {"gecikme_ornek_sayisi", "gecikme_ms_p50", "gecikme_ms_p95",
                    "gecikme_ms_p99", "gecikme_ms_max", "isleme_ornek_sayisi",
                    "isleme_us_p50", "isleme_us_p95", "isleme_us_p99", "isleme_us_max"}
    if set(latency) != latency_keys:
        raise ValueError("telemetry_latency_fields_invalid")
    for key, number in latency.items():
        if number is None and key.endswith(("_p50", "_p95", "_p99", "_max")):
            continue
        if type(number) not in (int, float) or number < 0 or not __import__("math").isfinite(number):
            raise ValueError("telemetry_latency_value_invalid")
    connection_keys = {"attempts", "connected", "disconnects", "last_attempt_ms",
                       "last_connected_ms", "last_disconnected_ms", "last_error"}
    for name, row in connections.items():
        if (not isinstance(name, str) or not name or len(name) > 64
                or not isinstance(row, dict) or set(row) != connection_keys):
            raise ValueError("telemetry_connection_fields_invalid")
        for key in ("attempts", "connected", "disconnects"):
            if type(row[key]) is not int or row[key] < 0:
                raise ValueError("telemetry_connection_count_invalid")
        for key in ("last_attempt_ms", "last_connected_ms", "last_disconnected_ms"):
            if row[key] is not None and (type(row[key]) is not int or row[key] <= 0):
                raise ValueError("telemetry_connection_time_invalid")
        if row["last_error"] is not None and (
                not isinstance(row["last_error"], str) or len(row["last_error"]) > 200):
            raise ValueError("telemetry_connection_error_invalid")
    # Enforce a compact, JSON-safe telemetry payload and no NaN/Infinity.
    encoded = _canonical(value)
    if len(encoded) > 64_000:
        raise ValueError("telemetry_too_large")
    return value


def write_snapshot(
    path: str | Path, *, sequence: int, generated_at_ms: int,
    observations: list[NormalizedBBO], source_run_id: str,
    telemetry: Mapping[str, Any] | None = None,
    hmac_key: str | bytes | None = None,
) -> str:
    """Atomically publish one Polat research snapshot and return its SHA-256."""
    if type(sequence) is not int or sequence < 1:
        raise ValueError("sequence_must_be_positive_integer")
    if type(generated_at_ms) is not int or generated_at_ms <= 0:
        raise ValueError("generated_at_ms_must_be_positive_integer")
    if (not isinstance(source_run_id, str) or not source_run_id.strip()
            or len(source_run_id) > 128 or any(ord(ch) < 32 for ch in source_run_id)):
        raise ValueError("source_run_id_invalid")
    if not observations or len(observations) > MAX_OBSERVATIONS:
        raise ValueError("observation_count_out_of_range")
    rows = []
    seen_symbols: set[str] = set()
    for item in observations:
        if not isinstance(item, NormalizedBBO) or not item.valid or item.event_ts_ms is None:
            raise ValueError("only_valid_timestamped_normalized_bbo_allowed")
        if not isinstance(item.venue, str) or item.venue.lower() != "okx":
            raise ValueError("unsupported_venue")
        if not isinstance(item.symbol, str) or not item.symbol.strip():
            raise ValueError("symbol_invalid")
        if item.symbol in seen_symbols:
            raise ValueError("duplicate_symbol")
        seen_symbols.add(item.symbol)
        age = generated_at_ms - item.event_ts_ms
        if age < -1_000 or age > DEFAULT_MAX_AGE_MS:
            raise ValueError("observation_timestamp_out_of_range")
        rows.append(_observation_dict(item))
    safe_telemetry = _validate_telemetry(dict(telemetry or {
        "channels": {}, "channel_counters": {},
        "latency": {"gecikme_ornek_sayisi": 0, "gecikme_ms_p50": None, "gecikme_ms_p95": None,
                    "gecikme_ms_p99": None, "gecikme_ms_max": None, "isleme_ornek_sayisi": 0,
                    "isleme_us_p50": None, "isleme_us_p95": None, "isleme_us_p99": None, "isleme_us_max": None},
        "connections": {},
    }))
    body = {
        "schema_version": SCHEMA_VERSION, "source": "polat",
        "scope": "research_only", "sequence": sequence,
        "generated_at_ms": generated_at_ms, "source_run_id": source_run_id,
        "observations": rows, "telemetry": safe_telemetry,
    }
    body_bytes = _canonical(body)
    digest = hashlib.sha256(body_bytes).hexdigest()
    envelope = {**body, "sha256": digest}
    key_bytes = _hmac_key_bytes(hmac_key)
    if key_bytes is not None:
        envelope["hmac_sha256"] = hmac.new(key_bytes, body_bytes, hashlib.sha256).hexdigest()
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
    hmac_key: str | bytes | None = None, require_hmac: bool = False,
) -> BridgeRead:
    """Read and validate a snapshot; never returns stale or replayed observations."""
    if type(max_age_ms) is not int or max_age_ms <= 0:
        return BridgeRead("invalid", "max_age_ms_invalid", None, None)
    if now_ms is not None and (type(now_ms) is not int or now_ms <= 0):
        return BridgeRead("invalid", "now_ms_invalid", None, None)
    if last_sequence is not None and (type(last_sequence) is not int or last_sequence < 0):
        return BridgeRead("invalid", "last_sequence_invalid", None, None)
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
        if not isinstance(envelope, dict):
            return BridgeRead("invalid", "snapshot_root_not_object", None, None)
        digest = envelope.pop("sha256")
        signature = envelope.pop("hmac_sha256", None)
        body_bytes = _canonical(envelope)
        expected = hashlib.sha256(body_bytes).hexdigest()
        if not isinstance(digest, str) or not hmac.compare_digest(digest, expected):
            return BridgeRead("invalid", "digest_mismatch", None, None)
        key_bytes = _hmac_key_bytes(hmac_key)
        if require_hmac and signature is None:
            return BridgeRead("invalid", "hmac_signature_missing", None, None)
        if signature is not None:
            if key_bytes is None:
                return BridgeRead("invalid", "hmac_key_missing", None, None)
            expected_hmac = hmac.new(key_bytes, body_bytes, hashlib.sha256).hexdigest()
            if not isinstance(signature, str) or not hmac.compare_digest(signature, expected_hmac):
                return BridgeRead("invalid", "hmac_mismatch", None, None)
        elif key_bytes is not None:
            return BridgeRead("invalid", "hmac_signature_missing", None, None)
        if set(envelope) != {"schema_version", "source", "scope", "sequence", "generated_at_ms",
                             "source_run_id", "observations", "telemetry"}:
            return BridgeRead("invalid", "snapshot_fields_invalid", None, None)
        if envelope.get("schema_version") != SCHEMA_VERSION:
            return BridgeRead("invalid", "schema_version_unsupported", None, None)
        if envelope.get("source") != "polat" or envelope.get("scope") != "research_only":
            return BridgeRead("invalid", "source_or_scope_invalid", None, None)
        run_id = envelope.get("source_run_id")
        if (not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 128
                or any(ord(ch) < 32 for ch in run_id)):
            return BridgeRead("invalid", "source_run_id_invalid", None, None)
        seq = envelope.get("sequence")
        stamp = envelope.get("generated_at_ms")
        rows = envelope.get("observations")
        telemetry = _validate_telemetry(envelope.get("telemetry"))
        if type(seq) is not int or seq < 1 or type(stamp) is not int or stamp <= 0:
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
        seen_symbols: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"venue", "symbol", "last", "bid", "ask",
                                                         "event_ts_ms", "valid", "reason"}:
                return BridgeRead("invalid", "observation_shape_invalid", seq, stamp)
            if row.get("venue") != "okx" or not isinstance(row.get("symbol"), str) or not row["symbol"].strip():
                return BridgeRead("invalid", "observation_identity_invalid", seq, stamp)
            if row["symbol"] in seen_symbols or row.get("valid") is not True:
                return BridgeRead("invalid", "observation_identity_invalid", seq, stamp)
            seen_symbols.add(row["symbol"])
            item = normalize_bbo(
                venue=row.get("venue"), symbol=row.get("symbol"), last=row.get("last"),
                bid=row.get("bid"), ask=row.get("ask"), event_ts_ms=row.get("event_ts_ms"),
            )
            if not item.valid or item.event_ts_ms is None or item.venue.lower() != "okx":
                return BridgeRead("invalid", "observation_quality_invalid", seq, stamp)
            if current - item.event_ts_ms > max_age_ms or item.event_ts_ms > current + 1_000:
                return BridgeRead("stale", "observation_timestamp_out_of_range", seq, stamp)
            observations.append(item)
        return BridgeRead("fresh", None, seq, stamp, tuple(observations), digest, run_id, telemetry)
    except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError, UnicodeDecodeError):
        return BridgeRead("invalid", "snapshot_parse_or_schema_error", None, None)
