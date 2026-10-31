"""Run leakage-aware Unified strategy research from an audited trade CSV.

CSV columns: entry_ts_ms, exit_ts_ms, net_r. Costs must already be included in
net_r; the audit flags are explicit operator attestations, never inferred.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar.unified.research import evaluate_candidate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_file")
    parser.add_argument("--trials", type=int, required=True,
                        help="All strategy/parameter trials attempted on this data")
    parser.add_argument("--embargo-ms", type=int, required=True,
                        help="At least the maximum label/holding overlap window")
    parser.add_argument("--costs-audited", action="store_true",
                        help="Attest net_r includes fee, spread, slippage, funding and impact")
    parser.add_argument("--provenance-audited", action="store_true",
                        help="Attest timestamps, source, survivorship and data lineage were checked")
    parser.add_argument("--output", default="research-report.json")
    args = parser.parse_args(argv)
    if args.trials < 1 or args.embargo_ms < 0:
        parser.error("--trials must be >=1 and --embargo-ms must be >=0")
    with open(args.csv_file, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    report = evaluate_candidate(
        rows, n_trials=args.trials, embargo_ms=args.embargo_ms,
        costs_complete=args.costs_audited,
        data_provenance_complete=args.provenance_audited,
    )
    report["source_file"] = Path(args.csv_file).name
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps({
        "research_eligible": report["research_eligible"],
        "automatic_promotion": False,
        "comparable_trades": report["comparable_trades"],
        "reasons": report["reasons"],
        "output": args.output,
    }, ensure_ascii=False))
    return 0 if report["walk_forward"].get("valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
