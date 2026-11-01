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

The normal `polat.yml` and `canli.yml` workflows run on separate GitHub-hosted runners and cannot share a local path. `polat.yml` now enables Polat's existing one-second bridge publisher and starts `scripts/polat_bridge_sync.py push-loop`; the relay publishes the latest public research snapshot to the dedicated `polat-unified-bridge` branch. `canli.yml` runs the matching `pull-loop` and places the retrieved snapshot in `$RUNNER_TEMP/polat-unified-bridge.json`, which the UnifiedTrader engine reads through `read_snapshot`. Both workflows require the same repository secret `POLAT_UNIFIED_BRIDGE_HMAC_KEY` (at least 32 UTF-8 bytes). The publisher refuses to relay without it, and the consumer always requires and verifies HMAC. If the secret is missing or invalid, the bridge is unavailable/UNKNOWN and contributes no observations; UnifiedTrader continues using only its own trusted execution feed. SHA-256 integrity alone is not treated as sender authentication. The branch carries no portfolio, order or private data. The bridge cannot feed execution quotes or open positions.

The dedicated branch provides a durable latest-snapshot handoff between the two separate Actions runners. It is not a message broker or lossless event stream: intermediate snapshots are overwritten, delivery is delayed by the publish/poll intervals, and GitHub API limits/outages can interrupt it. The producer publishes every 30 seconds and the consumer polls every 5 seconds; the engine accepts up to 45 seconds of age for this delayed research telemetry. GitHub Actions/Pages still cannot guarantee continuous 7/24 runtime, zero interruption or a millisecond SLA.

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

## digest-protected telemetry payload — schema v2

The SHA-256 checksum covers the BBO observations **and** a strictly validated `telemetry` object. It detects accidental/tampered content when the expected digest is trusted, but it is not keyed sender authentication:

- `channels`: per-channel symbol count, LIVE/STALE/INVALID/UNKNOWN/DISCONNECTED distribution, maximum event age.
- `channel_counters`: observation/state counters, duplicate events and trade IDs, out-of-order events, order-book sequence gaps, estimated missing sequence updates and disconnect marks.
- `latency`: event-to-receive p50/p95/p99/max and sample count; processing-time microsecond p50/p95/p99/max and sample count. Empty samples remain null.
- `connections`: per-WebSocket-group attempts, successful connections, disconnects, last attempt/connect/disconnect timestamps and last error.

Unknown fields, invalid state/count types, negative counters, NaN/Infinity, oversized telemetry and digest mismatches fail closed. The dashboard renders these values strictly as diagnostics. Sequence-gap-derived missing updates are not packet-capture measurements.


## Sender authentication

For cryptographic sender authentication, configure the same optional secret (at least 32 UTF-8 bytes) in both workflows:

- Polat: `POLAT_UNIFIED_BRIDGE_HMAC_KEY`
- Unified: `UNIFIED_POLAT_BRIDGE_HMAC_KEY`

Configure both as deployment secrets/environment variables, never in `config.yaml`, source control, dashboard JSON or logs. When the secret is configured, the producer signs snapshots and the consumer requires and verifies HMAC; a missing or mismatched key fails closed. If the secret is not configured, runtime uses the SHA-256 integrity digest and GitHub's workflow token permissions for the research-only public-data branch. Do not treat unsigned snapshots as cryptographic sender-authenticated.

