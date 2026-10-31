"""Leakage-aware chronological research validation for candidate strategy trade records.

Inputs must be one row per comparable, closed trade with entry/exit timestamps and
net R after fees, spread, slippage and funding. This module evaluates evidence;
it never changes strategy authority or promotes a strategy automatically.
"""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Iterable, Mapping, Sequence


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _metrics(rows: Sequence[Mapping]) -> dict:
    values = [float(row["net_r"]) for row in rows]
    if not values:
        return {"n": 0, "mean_net_r": None, "profit_factor": None, "max_drawdown_r": None}
    gains = sum(x for x in values if x > 0)
    losses = -sum(x for x in values if x < 0)
    equity = peak = 0.0
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "n": len(values),
        "mean_net_r": sum(values) / len(values),
        "profit_factor": gains / losses if losses else (math.inf if gains else None),
        "max_drawdown_r": max_dd,
    }


def _normal_lower_bound(values: Sequence[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    se = math.sqrt(variance / n)
    return mean - 1.96 * se


def purged_walk_forward_splits(
    rows: Iterable[Mapping], *, folds: int = 4, holdout_fraction: float = 0.2,
    embargo_ms: int = 0,
) -> dict:
    """Expanding-window WF folds plus a final untouched holdout.

    Training rows must be fully exited before the validation window minus embargo.
    The final holdout is never used in fold training or fold validation.
    """
    if folds < 2 or not 0.1 <= holdout_fraction <= 0.4 or embargo_ms < 0:
        raise ValueError("folds, holdout_fraction veya embargo_ms geçersiz")
    clean = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if not all(_finite(row.get(k)) for k in ("entry_ts_ms", "exit_ts_ms", "net_r")):
            continue
        entry, exit_ = int(row["entry_ts_ms"]), int(row["exit_ts_ms"])
        if entry <= 0 or exit_ <= entry:
            continue
        clean.append({**row, "entry_ts_ms": entry, "exit_ts_ms": exit_,
                      "net_r": float(row["net_r"])})
    clean.sort(key=lambda x: (x["entry_ts_ms"], x["exit_ts_ms"]))
    n = len(clean)
    holdout_start = int(n * (1 - holdout_fraction))
    pre_holdout = clean[:holdout_start]
    holdout = clean[holdout_start:]
    if len(pre_holdout) < folds + 2 or not holdout:
        return {"valid": False, "reason": "insufficient_rows", "rows": n,
                "folds": [], "holdout": _metrics(holdout)}
    # Divide the pre-holdout region into sequential validation blocks.
    first_val = max(2, len(pre_holdout) // (folds + 1))
    remaining = len(pre_holdout) - first_val
    block = max(1, remaining // folds)
    results = []
    for index in range(folds):
        val_start = first_val + index * block
        val_end = len(pre_holdout) if index == folds - 1 else min(len(pre_holdout), val_start + block)
        validation = pre_holdout[val_start:val_end]
        if not validation:
            continue
        boundary = validation[0]["entry_ts_ms"] - embargo_ms
        train = [row for row in pre_holdout[:val_start] if row["exit_ts_ms"] < boundary]
        if not train:
            continue
        results.append({
            "fold": index + 1,
            "train": _metrics(train),
            "validation": _metrics(validation),
            "purged_train_rows": val_start - len(train),
            "validation_start_ts_ms": validation[0]["entry_ts_ms"],
            "validation_end_ts_ms": validation[-1]["exit_ts_ms"],
        })
    valid = len(results) >= 2 and all(f["train"]["n"] > 0 and f["validation"]["n"] > 0 for f in results)
    return {
        "valid": valid,
        "reason": None if valid else "insufficient_purged_folds",
        "rows": n,
        "folds": results,
        "holdout": _metrics(holdout),
        "holdout_start_ts_ms": holdout[0]["entry_ts_ms"],
        "holdout_end_ts_ms": holdout[-1]["exit_ts_ms"],
        "holdout_untouched": True,
        "embargo_ms": embargo_ms,
    }


def evaluate_candidate(
    rows: Iterable[Mapping], *, n_trials: int, embargo_ms: int,
    costs_complete: bool, data_provenance_complete: bool,
) -> dict:
    """Return evidence report and a conservative research-only eligibility flag."""
    materialized = list(rows)
    split = purged_walk_forward_splits(materialized, embargo_ms=embargo_ms)
    valid_rows = [r for r in materialized if _finite(r.get("net_r"))]
    wf = split.get("folds", [])
    holdout = split.get("holdout", {})
    holdout_values = [float(r["net_r"]) for r in sorted(
        [r for r in materialized if _finite(r.get("entry_ts_ms")) and _finite(r.get("exit_ts_ms"))
         and _finite(r.get("net_r"))],
        key=lambda x: (int(x["entry_ts_ms"]), int(x["exit_ts_ms"]))
    )[int(len(valid_rows) * 0.8):]]
    lower = _normal_lower_bound(holdout_values)
    fold_positive = bool(wf) and all((f["validation"]["mean_net_r"] or 0) > 0 for f in wf)
    holdout_positive = bool(holdout_values) and sum(holdout_values) / len(holdout_values) > 0
    eligible = (
        split.get("valid") is True and len(valid_rows) >= 100 and n_trials >= 1
        and fold_positive and holdout_positive and lower is not None and lower > 0
        and costs_complete and data_provenance_complete and holdout.get("n", 0) >= 20
    )
    reasons = []
    if len(valid_rows) < 100: reasons.append("minimum_100_comparable_trades_not_met")
    if not split.get("valid"): reasons.append(split.get("reason") or "walk_forward_invalid")
    if not fold_positive: reasons.append("all_walk_forward_validation_folds_not_positive")
    if not holdout_positive or lower is None or lower <= 0: reasons.append("untouched_holdout_not_proven_positive")
    if not costs_complete: reasons.append("costs_incomplete")
    if not data_provenance_complete: reasons.append("data_provenance_incomplete")
    if holdout.get("n", 0) < 20: reasons.append("holdout_sample_below_20")
    return {
        "research_eligible": eligible,
        "automatic_promotion": False,
        "reasons": reasons,
        "comparable_trades": len(valid_rows),
        "walk_forward": split,
        "holdout_ci95_lower_net_r": lower,
        "holdout_positive": holdout_positive,
        "costs_complete": bool(costs_complete),
        "data_provenance_complete": bool(data_provenance_complete),
        "multiple_testing_trials": max(1, int(n_trials)),
        "note": "Research-only eligibility; forward PAPER observation and explicit human approval remain mandatory.",
    }
