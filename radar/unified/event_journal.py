"""Hash-chained append-only JSONL event journal.

This is a persistence primitive, not a live feed integration. A single writer
per file is required; callers must redact credentials before appending.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

_GENESIS = "0" * 64


class JournalIntegrityError(ValueError):
    """Raised when a journal line, sequence, or hash chain is invalid."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class AppendOnlyEventJournal:
    """Single-process-writer journal with sequence numbers and SHA-256 chaining.

    Timestamps are supplied by the caller and kept distinct: event time,
    receive wall-clock time, and receive monotonic time. The journal never
    fabricates missing timing fields.
    """

    def __init__(self, path: str | os.PathLike[str], *, fsync: bool = True):
        self.path = Path(path)
        self.fsync = fsync
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._next_seq, self._last_hash = self._recover()

    def _recover(self) -> tuple[int, str]:
        if not self.path.exists():
            return 1, _GENESIS
        previous = _GENESIS
        expected_seq = 1
        with self.path.open("rb") as stream:
            for line_number, raw in enumerate(stream, 1):
                if not raw.endswith(b"\n"):
                    raise JournalIntegrityError(f"truncated_line:{line_number}")
                try:
                    record = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise JournalIntegrityError(f"invalid_json:{line_number}") from exc
                digest = record.pop("record_hash", None)
                if record.get("seq") != expected_seq:
                    raise JournalIntegrityError(f"sequence_mismatch:{line_number}")
                if record.get("prev_hash") != previous:
                    raise JournalIntegrityError(f"previous_hash_mismatch:{line_number}")
                actual = hashlib.sha256(_canonical(record)).hexdigest()
                if digest != actual:
                    raise JournalIntegrityError(f"record_hash_mismatch:{line_number}")
                previous = digest
                expected_seq += 1
        return expected_seq, previous

    def append(
        self,
        *,
        source: str,
        channel: str,
        event_type: str,
        payload: Mapping[str, Any],
        event_ts_ms: int | None = None,
        receive_ts_ms: int | None = None,
        receive_monotonic_ns: int | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not source or not channel or not event_type:
            raise ValueError("source, channel ve event_type boş olamaz")
        for name, value in (
            ("event_ts_ms", event_ts_ms),
            ("receive_ts_ms", receive_ts_ms),
            ("receive_monotonic_ns", receive_monotonic_ns),
        ):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} negatif olmayan integer veya None olmalı")
        # Validate JSON compatibility before taking the writer lock.
        try:
            _canonical(payload)
            _canonical(provenance or {})
        except (TypeError, ValueError) as exc:
            raise ValueError("payload ve provenance JSON uyumlu olmalı") from exc
        with self._lock:
            record = {
                "seq": self._next_seq,
                "prev_hash": self._last_hash,
                "source": source,
                "channel": channel,
                "event_type": event_type,
                "event_ts_ms": event_ts_ms,
                "receive_ts_ms": receive_ts_ms,
                "receive_monotonic_ns": receive_monotonic_ns,
                "provenance": dict(provenance or {}),
                "payload": dict(payload),
            }
            digest = hashlib.sha256(_canonical(record)).hexdigest()
            stored = {**record, "record_hash": digest}
            encoded = _canonical(stored) + b"\n"
            # One append write prevents interleaving within this process.
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                written = os.write(fd, encoded)
                if written != len(encoded):
                    raise OSError("short journal write")
                if self.fsync:
                    os.fsync(fd)
            finally:
                os.close(fd)
            self._next_seq += 1
            self._last_hash = digest
            return stored

    def verify(self) -> dict[str, Any]:
        """Re-scan the file and report integrity without modifying it."""
        next_seq, last_hash = self._recover()
        return {
            "valid": True,
            "records": next_seq - 1,
            "last_hash": last_hash,
        }

    def records(self) -> Iterable[dict[str, Any]]:
        """Yield verified records; raises on the first corrupt/truncated line."""
        self._recover()
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                yield json.loads(line)



class QueuedEventJournal:
    """Bounded background writer for raw feed messages.

    Queue overflow, size limits, or disk errors are explicit and make the
    journal incomplete. The feed callback never waits for disk I/O.
    """

    def __init__(self, path: str | os.PathLike[str], *, queue_size: int = 4096,
                 max_bytes: int = 64 * 1024 * 1024, max_records: int = 100_000):
        if queue_size <= 0 or max_bytes <= 0 or max_records <= 0:
            raise ValueError("journal limits must be positive")
        self.path = Path(path)
        self.journal = AppendOnlyEventJournal(self.path, fsync=False)
        verified = self.journal.verify()
        self._bytes = self.path.stat().st_size if self.path.exists() else 0
        self._records = int(verified["records"])
        self.max_bytes = max_bytes
        self.max_records = max_records
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._lock = threading.Lock()
        self._dropped = 0
        self._written = 0
        self._error: str | None = None
        self._limit_reached = False
        self._closed = False
        self._worker = threading.Thread(target=self._run, name="market-event-journal", daemon=True)
        self._worker.start()

    @staticmethod
    def _event_ts(msg: Mapping[str, Any]) -> int | None:
        rows = msg.get("data") or []
        values = {str(row.get("ts")) for row in rows if isinstance(row, Mapping) and row.get("ts")}
        if len(values) != 1:
            return None
        try:
            value = int(next(iter(values)))
            return value if value >= 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_payload(value: Any) -> Any:
        if isinstance(value, Mapping):
            safe = {}
            for key, item in value.items():
                name = str(key)
                if any(part in name.lower() for part in ("token", "secret", "password", "api_key", "apikey", "signature")):
                    safe[name] = "***"
                else:
                    safe[name] = QueuedEventJournal._safe_payload(item)
            return safe
        if isinstance(value, (list, tuple)):
            return [QueuedEventJournal._safe_payload(item) for item in value]
        return value

    def record(self, message: Mapping[str, Any], *, receive_ts_ms: int) -> bool:
        if self._closed or self._error or self._limit_reached:
            with self._lock:
                self._dropped += 1
            return False
        arg = message.get("arg") or {}
        item = {
            "source": "okx_public",
            "channel": str(arg.get("channel") or "unknown"),
            "event_type": str(message.get("action") or "message"),
            "event_ts_ms": self._event_ts(message),
            "receive_ts_ms": receive_ts_ms,
            "receive_monotonic_ns": time.monotonic_ns(),
            "payload": self._safe_payload(message),
            "provenance": {"adapter": "polat.okx.Feed", "instrument": arg.get("instId")},
        }
        try:
            _canonical(item["payload"])
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return False
        except (TypeError, ValueError):
            with self._lock:
                self._dropped += 1
            return False

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                encoded_size = len(_canonical(item["payload"])) + 512
                if self._records >= self.max_records or self._bytes + encoded_size > self.max_bytes:
                    self._limit_reached = True
                    with self._lock:
                        self._dropped += 1
                    continue
                self.journal.append(
                    source=item["source"], channel=item["channel"], event_type=item["event_type"],
                    event_ts_ms=item["event_ts_ms"], receive_ts_ms=item["receive_ts_ms"],
                    receive_monotonic_ns=item["receive_monotonic_ns"],
                    payload=item["payload"], provenance=item["provenance"],
                )
                self._records += 1
                self._written += 1
                self._bytes = self.path.stat().st_size
            except Exception as exc:  # disk failures are reported, never hidden
                self._error = f"{type(exc).__name__}: {exc}"
                with self._lock:
                    self._dropped += 1
            finally:
                self._queue.task_done()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            dropped = self._dropped
        return {
            "enabled": True, "complete": dropped == 0 and self._error is None and not self._limit_reached,
            "records_total": self._records, "records_this_run": self._written,
            "queued": self._queue.qsize(), "dropped": dropped, "bytes": self._bytes,
            "max_bytes": self.max_bytes, "max_records": self.max_records,
            "limit_reached": self._limit_reached, "error": self._error,
            "integrity": "hash_chain_sha256",
        }

    def close(self, timeout: float = 5.0) -> dict[str, Any]:
        if not self._closed:
            self._closed = True
            try:
                self._queue.put(None, timeout=max(0.1, timeout))
                self._worker.join(timeout=max(0.1, timeout))
                if self._worker.is_alive():
                    self._error = self._error or "writer_shutdown_timeout"
            except queue.Full:
                self._error = self._error or "writer_queue_did_not_drain"
        return self.stats()
