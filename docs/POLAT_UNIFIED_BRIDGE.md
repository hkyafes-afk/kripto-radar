# Polat → Unified research snapshot bridge

`radar.unified.polat_bridge` provides a versioned (schema v2), atomic JSON snapshot contract for two processes that share a filesystem.

## Contract

- Producer: Polat research process calls `write_snapshot(path, sequence, generated_at_ms, observations, source_run_id)`.
- Consumer: a separate research/diagnostics process calls `read_snapshot(path, now_ms, max_age_ms, last_sequence)`.
- The reader checks schema version, source/scope, SHA-256 integrity, monotonically increasing sequence, snapshot age, every BBO's shape, venue, timestamp and price ordering.
- Missing, malformed, stale, replayed, future-dated or corrupted snapshots fail closed. They return a status/reason and no observations.
- Writes use a temporary file + fsync + atomic replace, so readers do not observe a partially written JSON document.
- Snapshot is explicitly marked `scope=research_only`. It is not an execution quote, decision override, order instruction, or permission to open positions.

## Runtime boundary

The producer and consumer hooks are now in the runtimes:

- Polat: set POLAT_UNIFIED_BRIDGE_PATH=/shared/kripto-radar/polat/latest.json. Its one-second loop publishes only ticker BBOs whose Polat channel quality is LIVE and whose exchange timestamp is no older than five seconds. Standby Polat processes do not publish.
- UnifiedTrader host: set UNIFIED_POLAT_BRIDGE_PATH to the same mounted path. Its unified loop validates the snapshot and writes a status-only file to site/birlesik/polat_kopru.json. Freshness, sequence replay, parse failures and a missing path are surfaced explicitly.
- Both variables must resolve to the same durable filesystem in both processes. No path is configured by default.

Polat's own status payload also exposes per-channel event counters: accepted LIVE observations, stale/invalid/unknown observations, duplicate and out-of-order events, order-book sequence gaps, estimated missing sequence updates, disconnect marks, plus per-connection attempt/connect/disconnect timestamps. A sequence-gap count is an observation from the OKX sequence contract, not a packet-capture count of all network drops.

This code does not create a shared filesystem between GitHub Actions runners. Separate GitHub-hosted runners do not share local files. The current Polat and main workflows therefore do not have an active cross-process transport merely because these hooks exist. A persistent host/volume or a separately provisioned transport is still required to activate it. GitHub branch/artifact polling is a delayed snapshot mechanism, not a millisecond feed.

UnifiedTrader continues to obtain execution BBO exclusively from its own trusted exchange feed. Polat snapshots are research/diagnostic telemetry only: they are not used for Quote, factor inputs, decisions, fills, orders, position valuation or permissions to open positions. A fresh bridge status is not trade evidence and must not be counted toward the 14-day/100-closed-trade promotion gate.

## Example

```python
from radar.unified.polat_bridge import write_snapshot, read_snapshot

# Polat process: observations are valid NormalizedBBO instances.
write_snapshot(
    "/shared/kripto-radar/polat/latest.json",
    sequence=next_sequence,
    generated_at_ms=now_ms,
    observations=normalized_bbos,
    source_run_id=workflow_or_process_id,
)

# Research consumer: retain last_sequence durably between reads.
snapshot = read_snapshot(
    "/shared/kripto-radar/polat/latest.json",
    last_sequence=last_seen_sequence,
)
if snapshot.status == "fresh":
    last_seen_sequence = snapshot.sequence
    # Research/diagnostics only. Never route this to execution.
```

## Tests

`tests/test_polat_bridge.py` covers valid round-trip, replay, staleness, payload tampering and invalid BBO rejection.

## Signed telemetry payload — schema v2

The SHA-256 digest covers the BBO observations **and** a strictly validated `telemetry` object:

- `channels`: per-channel symbol count, LIVE/STALE/INVALID/UNKNOWN/DISCONNECTED distribution, maximum event age.
- `channel_counters`: observation/state counters, duplicate events and trade IDs, out-of-order events, order-book sequence gaps, estimated missing sequence updates and disconnect marks.
- `latency`: event-to-receive p50/p95/p99/max and sample count; processing-time microsecond p50/p95/p99/max and sample count. Empty samples remain null.
- `connections`: per-WebSocket-group attempts, successful connections, disconnects, last attempt/connect/disconnect timestamps and last error.

Unknown fields, invalid state/count types, negative counters, NaN/Infinity, oversized telemetry and digest mismatches fail closed. The dashboard renders these values strictly as diagnostics. Sequence-gap-derived missing updates are not packet-capture measurements.
