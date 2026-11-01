from __future__ import annotations

import base64
import json
from types import SimpleNamespace

from scripts import polat_bridge_sync as sync


def test_pull_once_writes_atomic_snapshot_bytes(tmp_path, monkeypatch):
    payload = b'{"schema_version":2,"scope":"research_only"}'
    monkeypatch.setattr(sync, "_api", lambda method, path, body=None:
                        (200, {"content": base64.b64encode(payload).decode()}))
    target = tmp_path / "bridge.json"
    assert sync.pull_once(target)
    assert target.read_bytes() == payload
    assert not list(tmp_path.glob("*.tmp"))


def test_push_once_rejects_stale_or_invalid_snapshot(tmp_path, monkeypatch):
    target = tmp_path / "bridge.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sync, "read_snapshot", lambda *a, **k: SimpleNamespace(status="stale", reason="stale"))
    called = []
    monkeypatch.setattr(sync, "_api", lambda *a, **k: called.append((a, k)))
    import pytest
    with pytest.raises(RuntimeError, match="not_live"):
        sync.push_once(target)
    assert not called


def test_push_once_updates_dedicated_branch_and_skips_identical_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("POLAT_UNIFIED_BRIDGE_HMAC_KEY", "test-bridge-hmac-key-at-least-32-bytes-long")
    target = tmp_path / "bridge.json"
    raw = b'{"snapshot":"valid"}'
    target.write_bytes(raw)
    monkeypatch.setattr(sync, "read_snapshot", lambda *a, **k: SimpleNamespace(status="fresh", sequence=7))
    calls = []

    def api(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET" and path == f"git/ref/heads/{sync.BRANCH}":
            return 200, {}
        if method == "GET" and path.startswith("contents/"):
            return 200, {"content": base64.b64encode(raw).decode()}
        if method == "POST" and path == "git/blobs":
            return 201, {"sha": "blob-sha"}
        if method == "POST" and path == "git/trees":
            return 201, {"sha": "tree-sha"}
        if method == "POST" and path == "git/commits":
            return 201, {"sha": "commit-sha"}
        if method == "PATCH" and path == f"git/refs/heads/{sync.BRANCH}":
            return 200, {"ref": f"refs/heads/{sync.BRANCH}"}
        return 200, {}

    monkeypatch.setattr(sync, "_api", api)
    assert not sync.push_once(target)
    assert len(calls) == 2
    calls.clear()
    target.write_bytes(raw + b" ")
    assert sync.push_once(target)
    assert any(method == "PATCH" and path == f"git/refs/heads/{sync.BRANCH}" for method, path, _ in calls)


def test_push_once_requires_hmac_key_before_any_github_write(tmp_path, monkeypatch):
    target = tmp_path / "bridge.json"
    target.write_text("{\"snapshot\":\"valid\"}", encoding="utf-8")
    monkeypatch.delenv("POLAT_UNIFIED_BRIDGE_HMAC_KEY", raising=False)
    called = []
    monkeypatch.setattr(sync, "_api", lambda *a, **k: called.append((a, k)))
    import pytest
    with pytest.raises(RuntimeError, match="hmac_key_missing"):
        sync.push_once(target)
    assert not called
