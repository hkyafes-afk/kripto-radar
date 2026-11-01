import json

import pytest

from radar.unified.event_journal import (
    AppendOnlyEventJournal,
    JournalIntegrityError,
)


def test_journal_appends_hash_chained_records_and_recovers(tmp_path):
    path = tmp_path / "events" / "market.jsonl"
    journal = AppendOnlyEventJournal(path, fsync=False)
    first = journal.append(
        source="okx", channel="books", event_type="snapshot",
        event_ts_ms=100, receive_ts_ms=105, receive_monotonic_ns=999,
        payload={"bids": [["10", "2"]]},
        provenance={"adapter": "fixture", "environment": "test"},
    )
    second = journal.append(
        source="okx", channel="trades", event_type="trade",
        event_ts_ms=110, receive_ts_ms=111, payload={"price": "10.1"},
    )

    assert first["seq"] == 1
    assert second["seq"] == 2
    assert second["prev_hash"] == first["record_hash"]
    assert journal.verify() == {
        "valid": True, "records": 2, "last_hash": second["record_hash"]
    }

    recovered = AppendOnlyEventJournal(path, fsync=False)
    third = recovered.append(
        source="okx", channel="ticker", event_type="tick",
        payload={"last": "10.2"},
    )
    assert third["seq"] == 3
    assert [r["seq"] for r in recovered.records()] == [1, 2, 3]


def test_journal_detects_tampering_and_truncated_tail(tmp_path):
    path = tmp_path / "events.jsonl"
    journal = AppendOnlyEventJournal(path, fsync=False)
    journal.append(source="okx", channel="ticker", event_type="tick",
                   payload={"last": "10"})
    original = path.read_text()
    record = json.loads(original)
    record["payload"]["last"] = "999"
    path.write_text(json.dumps(record, separators=(",", ":")) + "\n")
    with pytest.raises(JournalIntegrityError, match="record_hash_mismatch"):
        AppendOnlyEventJournal(path, fsync=False)

    path.write_text(original + '{"partial":')
    with pytest.raises(JournalIntegrityError, match="truncated_line"):
        AppendOnlyEventJournal(path, fsync=False)


def test_journal_rejects_invalid_timestamps_and_non_json_payload(tmp_path):
    journal = AppendOnlyEventJournal(tmp_path / "events.jsonl", fsync=False)
    with pytest.raises(ValueError, match="receive_ts_ms"):
        journal.append(source="okx", channel="ticker", event_type="tick",
                       payload={}, receive_ts_ms=-1)
    with pytest.raises(ValueError):
        journal.append(source="okx", channel="ticker", event_type="tick",
                       payload={"bad": object()})



def test_queued_writer_records_raw_messages_and_reports_integrity(tmp_path):
    from radar.unified.event_journal import QueuedEventJournal

    path = tmp_path / "raw.jsonl"
    writer = QueuedEventJournal(path, queue_size=8, max_bytes=100_000, max_records=10)
    assert writer.record(
        {"arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
         "data": [{"ts": "1000", "last": "10"}],
         "api_key": "must-not-leak"},
        receive_ts_ms=1010,
    )
    result = writer.close()
    assert result["complete"] is True
    assert result["records_this_run"] == 1
    record = next(writer.journal.records())
    assert record["event_ts_ms"] == 1000
    assert record["receive_ts_ms"] == 1010
    assert record["payload"]["api_key"] == "***"
    assert writer.journal.verify()["records"] == 1


def test_queued_writer_marks_capacity_exhaustion_incomplete(tmp_path):
    from radar.unified.event_journal import QueuedEventJournal

    writer = QueuedEventJournal(tmp_path / "limited.jsonl", queue_size=4, max_bytes=1, max_records=1)
    writer.record({"arg": {"channel": "trades"}, "data": [{"ts": "1"}]}, receive_ts_ms=2)
    result = writer.close()
    assert result["complete"] is False
    assert result["limit_reached"] is True
    assert result["dropped"] >= 1
