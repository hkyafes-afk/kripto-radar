# Polat → Unified research snapshot bridge

`radar.unified.polat_bridge` provides a versioned, atomic JSON snapshot contract for two processes that share a filesystem.

## Contract

- Producer: Polat research process calls `write_snapshot(path, sequence, generated_at_ms, observations, source_run_id)`.
- Consumer: a separate research/diagnostics process calls `read_snapshot(path, now_ms, max_age_ms, last_sequence)`.
- The reader checks schema version, source/scope, SHA-256 integrity, monotonically increasing sequence, snapshot age, every BBO's shape, venue, timestamp and price ordering.
- Missing, malformed, stale, replayed, future-dated or corrupted snapshots fail closed. They return a status/reason and no observations.
- Writes use a temporary file + fsync + atomic replace, so readers do not observe a partially written JSON document.
- Snapshot is explicitly marked `scope=research_only`. It is not an execution quote, decision override, order instruction, or permission to open positions.

## Runtime boundary

This module and its tests establish the transport contract; they do **not** create a shared filesystem between GitHub Actions runners. To activate cross-process delivery, both processes must be deployed with the same mounted durable volume and configured to use the same path. Separate GitHub-hosted runners do not share local files. GitHub branch/artifact polling is a delayed snapshot mechanism, not a millisecond feed.

UnifiedTrader continues to obtain execution BBO from its own trusted exchange feed. Polat data must remain a challenger/research input until an independently reviewed ingestion path and end-to-end tests prove provenance, freshness, loss/replay handling and strategy isolation.

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