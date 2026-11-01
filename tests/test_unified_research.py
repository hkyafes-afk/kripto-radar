from radar.unified.research import evaluate_candidate, purged_walk_forward_splits


def _rows(n=120):
    start = 1_700_000_000_000
    rows = []
    for i in range(n):
        entry = start + i * 86_400_000
        rows.append({
            "entry_ts_ms": entry,
            "exit_ts_ms": entry + 3_600_000,
            "net_r": 0.20 + (i % 3) * 0.01,
        })
    return rows


def test_purged_walk_forward_keeps_final_holdout_separate():
    rows = _rows()
    result = purged_walk_forward_splits(rows, folds=4, holdout_fraction=0.2, embargo_ms=60_000)
    assert result["valid"] is True
    assert len(result["folds"]) == 4
    assert result["holdout_untouched"] is True
    assert result["holdout"]["n"] == 24
    assert all(fold["train"]["n"] > 0 for fold in result["folds"])
    assert all(fold["validation"]["n"] > 0 for fold in result["folds"])


def test_research_candidate_needs_costs_provenance_and_multiple_testing_evidence():
    rows = _rows()
    report = evaluate_candidate(
        rows, n_trials=2, embargo_ms=60_000,
        costs_complete=True, data_provenance_complete=True,
    )
    assert report["automatic_promotion"] is False
    assert report["comparable_trades"] == 120
    assert report["walk_forward"]["valid"] is True
    assert report["deflated_sharpe"] is not None
    assert report["research_eligible"] is True

    blocked = evaluate_candidate(
        rows, n_trials=100, embargo_ms=60_000,
        costs_complete=False, data_provenance_complete=False,
    )
    assert blocked["research_eligible"] is False
    assert "costs_incomplete" in blocked["reasons"]
    assert "data_provenance_incomplete" in blocked["reasons"]


def test_research_candidate_does_not_pass_small_or_negative_holdout():
    rows = _rows(40)
    report = evaluate_candidate(
        rows, n_trials=1, embargo_ms=0,
        costs_complete=True, data_provenance_complete=True,
    )
    assert report["research_eligible"] is False
    assert "minimum_100_comparable_trades_not_met" in report["reasons"]



def test_research_cli_writes_machine_readable_report(tmp_path):
    import csv
    import json

    from scripts.unified_research import main

    source = tmp_path / "trades.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["entry_ts_ms", "exit_ts_ms", "net_r"])
        writer.writeheader()
        writer.writerows(_rows())
    output = tmp_path / "report.json"
    code = main([
        str(source), "--trials", "2", "--embargo-ms", "60000",
        "--costs-audited", "--provenance-audited", "--output", str(output),
    ])
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["automatic_promotion"] is False
    assert report["comparable_trades"] == 120
