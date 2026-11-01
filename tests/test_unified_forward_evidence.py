from __future__ import annotations

from radar.unified.forward_evidence import DAY_MS, ForwardPaperEvidence
from radar.unified.execution.base import Quote

NOW = 1_800_000_000_000


def quote(ts=NOW):
    return Quote("BTC-USDT-SWAP", 99.9, 100.1, 100.0, ts)


def closed_rows(start, count=100, scope="unified"):
    return [{"execution_scope": scope, "opened_ts": start + i * 1000,
             "closed_ts": start + i * 1000 + 500, "open_cid": f"cid-{i}",
             "net_usd": 0.01} for i in range(count)]


def test_forward_evidence_requires_live_healthy_paper_heartbeats(tmp_path):
    e = ForwardPaperEvidence(tmp_path / "evidence.json")
    first = e.record(now_ms=NOW, mode="unified", ws_healthy=True, global_issue=None,
                     quotes={"BTC": quote()}, closed=[])
    assert first["status"] == "observing" and first["heartbeat_count"] == 1
    blocked = e.record(now_ms=NOW + 1000, mode="unified", ws_healthy=False, global_issue=None,
                       quotes={"BTC": quote(NOW + 1000)}, closed=[])
    assert blocked["last_reason"] == "websocket_not_healthy"
    restored = ForwardPaperEvidence(tmp_path / "evidence.json")
    assert restored.state["heartbeat_count"] == 1


def test_forward_evidence_requires_14_days_and_100_real_scope_closed_trades(tmp_path):
    e = ForwardPaperEvidence(tmp_path / "evidence.json")
    start = NOW
    e.record(now_ms=start, mode="unified", ws_healthy=True, global_issue=None,
             quotes={"BTC": quote(start)}, closed=[])
    rows = closed_rows(start + 1, 100)
    # Legacy/shadow records never count.
    rows += closed_rows(start + 1, 50, scope="shadow")
    later = start + 14 * DAY_MS
    snap = e.record(now_ms=later, mode="unified", ws_healthy=True, global_issue=None,
                    quotes={"BTC": quote(later)}, closed=rows)
    assert snap["days_ok"] and snap["trades_ok"] and snap["promotion_evidence_ready"]
    assert snap["closed_paper_trades"] == 100


def test_forward_evidence_gap_restarts_observation_window(tmp_path):
    e = ForwardPaperEvidence(tmp_path / "evidence.json", max_gap_ms=60_000)
    e.record(now_ms=NOW, mode="unified", ws_healthy=True, global_issue=None,
             quotes={"BTC": quote()}, closed=[])
    snap = e.record(now_ms=NOW + 61_000, mode="unified", ws_healthy=True, global_issue=None,
                    quotes={"BTC": quote(NOW + 61_000)}, closed=[])
    assert snap["window_started_ts_ms"] == NOW + 61_000
    assert snap["window_resets"] == 1 and not snap["days_ok"]


def test_forward_evidence_ignores_backdated_or_incomplete_rows(tmp_path):
    e = ForwardPaperEvidence(tmp_path / "evidence.json")
    e.record(now_ms=NOW, mode="unified", ws_healthy=True, global_issue=None,
             quotes={"BTC": quote()}, closed=[])
    rows = [{"execution_scope": "unified", "opened_ts": NOW-1, "closed_ts": NOW+1,
             "open_cid": "backdated"}, {"execution_scope": "unified", "opened_ts": NOW+1,
             "closed_ts": None, "open_cid": "not-closed"}]
    snap = e.snapshot(NOW+10, rows)
    assert snap["closed_paper_trades"] == 0 and not snap["trades_ok"]
