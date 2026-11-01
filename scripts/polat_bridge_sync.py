"""GitHub-branch fan-out for Polat's public research-only bridge snapshots.

The Polat and UnifiedTrader workflows run on different ephemeral runners. This
small stdlib-only relay uses a dedicated Git branch as durable latest-snapshot
storage. It carries no orders, positions, private data or execution authority.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import tempfile
import time
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar.unified.polat_bridge import read_snapshot

BRANCH = "polat-unified-bridge"
FILE_PATH = "snapshot.json"
MAX_BYTES = 900_000


def _api(method: str, path: str, body: dict | None = None):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo or "/" not in repo:
        raise RuntimeError("GITHUB_TOKEN_and_GITHUB_REPOSITORY_required")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "kripto-radar-bridge",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if exc.code == 404:
            return 404, {}
        raise RuntimeError(f"github_api_{exc.code}:{raw[:300].decode(errors='replace')}") from exc


def _ensure_branch() -> None:
    status, _ = _api("GET", f"git/ref/heads/{BRANCH}")
    if status == 200:
        return
    _, main = _api("GET", "git/ref/heads/main")
    _api("POST", "git/refs", {"ref": f"refs/heads/{BRANCH}", "sha": main["object"]["sha"]})


def push_once(path: Path) -> bool:
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_BYTES:
        raise RuntimeError("bridge_snapshot_empty_or_over_github_contents_limit")
    # Verify before publishing: never relay malformed, stale, replayed or unsigned-invalid data.
    key = os.environ.get("POLAT_UNIFIED_BRIDGE_HMAC_KEY") or None
    checked = read_snapshot(path, now_ms=int(time.time() * 1000), max_age_ms=45_000,
                            hmac_key=key, require_hmac=bool(key))
    if checked.status != "fresh":
        raise RuntimeError(f"bridge_snapshot_not_live:{checked.reason}")
    _ensure_branch()
    status, current = _api("GET", f"contents/{FILE_PATH}?ref={BRANCH}")
    encoded = base64.b64encode(raw).decode("ascii")
    if status == 200 and current.get("content"):
        try:
            old = base64.b64decode(current["content"], validate=False)
        except Exception:
            old = b""
        if old == raw:
            return False
    # Publish an orphan/root commit and force-update the dedicated ref. Only the
    # latest snapshot remains reachable; the relay does not grow an unbounded
    # per-interval commit history.
    _, blob = _api("POST", "git/blobs", {"content": encoded, "encoding": "base64"})
    _, tree = _api("POST", "git/trees", {"tree": [
        {"path": FILE_PATH, "mode": "100644", "type": "blob", "sha": blob["sha"]}
    ]})
    _, commit = _api("POST", "git/commits", {
        "message": f"Polat research snapshot seq={checked.sequence}",
        "tree": tree["sha"], "parents": [],
    })
    _api("PATCH", f"git/refs/heads/{BRANCH}", {"sha": commit["sha"], "force": True})
    return True


def pull_once(path: Path) -> bool:
    status, item = _api("GET", f"contents/{FILE_PATH}?ref={BRANCH}")
    if status == 404:
        return False
    raw = base64.b64decode(item.get("content", ""), validate=False)
    if not raw or len(raw) > MAX_BYTES:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("push-loop", "pull-loop", "push-once", "pull-once"))
    parser.add_argument("--path", default=os.environ.get("POLAT_UNIFIED_BRIDGE_PATH") or
                        os.environ.get("UNIFIED_POLAT_BRIDGE_PATH") or "")
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args()
    if not args.path:
        parser.error("--path or bridge path environment variable required")
    path = Path(args.path)
    if args.mode == "push-once":
        return 0 if push_once(path) else 0
    if args.mode == "pull-once":
        return 0 if pull_once(path) else 1
    if args.interval < 2:
        parser.error("--interval must be >= 2 seconds")
    last_error_at = 0.0
    while True:
        try:
            if args.mode == "push-loop":
                if path.exists():
                    changed = push_once(path)
                    if changed:
                        print("Polat research snapshot relayed.", flush=True)
            else:
                pull_once(path)
                if path.exists():
                    key = os.environ.get("UNIFIED_POLAT_BRIDGE_HMAC_KEY") or None
                    result = read_snapshot(path, now_ms=int(time.time() * 1000), max_age_ms=45_000,
                                           hmac_key=key, require_hmac=bool(key))
                    if result.status == "fresh":
                        print(f"Polat bridge LIVE seq={result.sequence}", flush=True)
            last_error_at = 0.0
        except Exception as exc:  # noqa: BLE001
            if time.time() - last_error_at >= 60:
                print(f"Polat bridge relay error: {type(exc).__name__}: {str(exc)[:180]}", flush=True)
                last_error_at = time.time()
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
