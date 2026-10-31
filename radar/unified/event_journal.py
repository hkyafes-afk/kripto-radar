"""Hash-chained append-only JSONL event journal.

This is a persistence primitive, not a live feed integration. A single writer
per file is required; callers must redact credentials before appending.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
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
