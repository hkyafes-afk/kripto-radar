"""Durable forward-PAPER observation gate based only on live runner heartbeats and closed ledger rows."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable, Mapping

DAY_MS = 86_400_000
DEFAULT_MAX_GAP_MS = 30 * 60_000


class ForwardPaperEvidence:
    def __init__(self, path: str | Path, *, required_days: int = 14, required_closed: int = 100,
                 max_gap_ms: int = DEFAULT_MAX_GAP_MS):
        self.path = Path(path)
        self.required_days = int(required_days)
        self.required_closed = int(required_closed)
        self.max_gap_ms = int(max_gap_ms)
        if min(self.required_days, self.required_closed, self.max_gap_ms) <= 0:
            raise ValueError("forward_evidence_threshold_invalid")
        self.state = {"schema": 1, "window_started_ts_ms": None, "last_seen_ts_ms": None,
                      "heartbeat_count": 0, "window_resets": 0, "last_reason": "not_started"}
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or loaded.get("schema") != 1:
                raise RuntimeError("forward_paper_evidence_corrupt_fail_closed")
            self.state.update(loaded)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".forward-paper-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    def record(self, *, now_ms: int, mode: str, ws_healthy: bool | None,
               global_issue: str | None, quotes: Mapping, closed: Iterable[Mapping]) -> dict:
        valid_quotes = [q for q in quotes.values()
                        if getattr(q, "valid", False) and type(getattr(q, "ts_ms", None)) is int
                        and -1_000 <= now_ms - q.ts_ms <= 15_000]
        if mode != "unified" or ws_healthy is not True or global_issue or not valid_quotes:
            self.state["last_reason"] = ("not_unified_paper" if mode != "unified" else
                                         "websocket_not_healthy" if ws_healthy is not True else
                                         "global_data_issue" if global_issue else "no_fresh_live_quotes")
            self._save()
            return self.snapshot(now_ms, closed)
        start = self.state.get("window_started_ts_ms")
        last = self.state.get("last_seen_ts_ms")
        if start is None or last is None or now_ms - int(last) > self.max_gap_ms or now_ms < int(last):
            if start is not None:
                self.state["window_resets"] = int(self.state.get("window_resets", 0)) + 1
            self.state["window_started_ts_ms"] = now_ms
            self.state["heartbeat_count"] = 0
        self.state["last_seen_ts_ms"] = now_ms
        self.state["heartbeat_count"] = int(self.state.get("heartbeat_count", 0)) + 1
        self.state["last_reason"] = "observing"
        self._save()
        return self.snapshot(now_ms, closed)

    def snapshot(self, now_ms: int, closed: Iterable[Mapping]) -> dict:
        start = self.state.get("window_started_ts_ms")
        last = self.state.get("last_seen_ts_ms")
        eligible = []
        seen = set()
        for row in closed:
            if row.get("execution_scope") != "unified":
                continue
            opened, ended = row.get("opened_ts"), row.get("closed_ts")
            if (type(opened) is not int or type(ended) is not int or opened <= 0 or ended <= opened
                    or start is None or opened < int(start) or ended > now_ms):
                continue
            key = str(row.get("open_cid") or row.get("decision_key") or f"{opened}:{ended}:{row.get('inst','')}")
            if key not in seen:
                seen.add(key)
                eligible.append(row)
        elapsed = max(0, now_ms - int(start)) if start is not None else 0
        recent = last is not None and 0 <= now_ms - int(last) <= self.max_gap_ms
        days_ok = elapsed >= self.required_days * DAY_MS and recent and self.state.get("last_reason") == "observing"
        trades_ok = len(eligible) >= self.required_closed
        return {
            "status": "eligible" if days_ok and trades_ok else "observing",
            "source": "unified_paper_execution_ledger",
            "window_started_ts_ms": start, "last_seen_ts_ms": last,
            "elapsed_days": round(elapsed / DAY_MS, 3),
            "required_days": self.required_days, "days_ok": days_ok,
            "heartbeat_count": int(self.state.get("heartbeat_count", 0)),
            "window_resets": int(self.state.get("window_resets", 0)),
            "closed_paper_trades": len(eligible), "required_closed_trades": self.required_closed,
            "trades_ok": trades_ok, "last_reason": self.state.get("last_reason"),
            "promotion_evidence_ready": bool(days_ok and trades_ok),
        }
