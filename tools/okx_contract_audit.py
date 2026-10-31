"""Public OKX SWAP contract-size audit for a 100 USDT paper account.

No API keys and no trading endpoints. Fetches public instrument metadata and tickers,
then estimates each live linear USDT-settled contract's minimum order notional.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://www.okx.com"


def fetch_json(path: str, opener=urllib.request.urlopen):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "kripto-radar-public-audit/1.0"})
    with opener(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX API error for {path}: {payload.get('msg')}")
    return payload.get("data") or []


def audit(instruments: list[dict], tickers: list[dict], equity=100.0, max_position_pct=0.35,
          risk_pct=0.005, stop_pct=0.05) -> dict:
    prices = {x.get("instId"): float(x["last"]) for x in tickers
              if x.get("instId") and _positive(x.get("last"))}
    live, eligible, rejected, unknown, risk_fit = [], [], [], [], []
    max_notional = equity * max_position_pct
    risk_budget = equity * risk_pct
    target_notional = risk_budget / stop_pct if stop_pct > 0 else 0.0
    for m in instruments:
        inst = m.get("instId", "")
        if not inst.endswith("-USDT-SWAP"):
            continue
        if m.get("state") != "live":
            continue
        live.append(inst)
        ct_type = m.get("ctType")
        settle = m.get("settleCcy")
        ccy = m.get("ctValCcy")
        ct_val, min_sz, lot_sz = m.get("ctVal"), m.get("minSz"), m.get("lotSz")
        if ct_type != "linear" or settle != "USDT" or not all(_positive(x) for x in (ct_val, min_sz, lot_sz)):
            unknown.append({"instId": inst, "reason": "metadata_missing_or_not_linear",
                            "ctType": ct_type, "settleCcy": settle})
            continue
        ct_val, min_sz, lot_sz = float(ct_val), float(min_sz), float(lot_sz)
        price = 1.0 if ccy == "USDT" else prices.get(inst)
        if price is None:
            unknown.append({"instId": inst, "reason": "ticker_missing", "ctValCcy": ccy})
            continue
        min_notional = min_sz * ct_val * price
        row = {"instId": inst, "ctVal": ct_val, "ctValCcy": ccy, "lotSz": lot_sz,
               "minSz": min_sz, "last": price, "min_notional_usdt": round(min_notional, 8),
               "max_account_notional_usdt": round(max_notional, 4),
               "risk_budget_usdt": round(risk_budget, 4),
               "target_notional_at_stop_pct": round(target_notional, 4)}
        if min_notional <= max_notional:
            eligible.append(row)
        else:
            rejected.append(row)
        if min_notional <= target_notional:
            risk_fit.append(row)
    eligible.sort(key=lambda x: x["min_notional_usdt"])
    rejected.sort(key=lambda x: x["min_notional_usdt"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "account_equity_usdt": equity, "max_position_pct": max_position_pct,
        "risk_pct": risk_pct, "assumed_stop_pct": stop_pct,
        "max_position_notional_usdt": round(max_notional, 4),
        "risk_budget_usdt": round(risk_budget, 4),
        "target_notional_at_assumed_stop_usdt": round(target_notional, 4),
        "live_usdt_swap_count": len(live), "eligible_count": len(eligible),
        "too_large_minimum_count": len(rejected), "unknown_metadata_count": len(unknown),
        "risk_target_fit_count": len(risk_fit), "risk_target_too_large_count": len(eligible) - len(risk_fit),
        "eligible": eligible, "too_large_minimum": rejected, "risk_target_fit": risk_fit, "unknown": unknown,
        "scope_note": "Public OKX metadata snapshot; minimum-order notional estimate. Not a fill/liquidity guarantee. Fee tier, spread, depth, slippage and funding are not inferred."
    }


def _positive(v):
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


def render(report: dict) -> str:
    lines = [
        "# OKX live USDT-SWAP minimum-contract audit", "",
        f"Snapshot: {report['generated_at']}", "",
        f"- Live USDT swaps: **{report['live_usdt_swap_count']}**",
        f"- Minimum order within max position cap: **{report['eligible_count']}**",
        f"- Minimum order exceeds max position cap: **{report['too_large_minimum_count']}**",
        f"- Unknown/incomplete metadata: **{report['unknown_metadata_count']}**",
        f"- Minimum fits illustrative stop-risk-sized notional: **{report['risk_target_fit_count']}**",
        f"- Within max-position cap but above risk-sized notional: **{report['risk_target_too_large_count']}**", "",
        f"- Paper equity: {report['account_equity_usdt']:.2f} USDT",
        f"- Max position cap ({report['max_position_pct']:.0%}): {report['max_position_notional_usdt']:.2f} USDT",
        f"- Risk budget ({report['risk_pct']:.2%}): {report['risk_budget_usdt']:.2f} USDT",
        f"- Illustrative notional at {report['assumed_stop_pct']:.1%} stop: {report['target_notional_at_assumed_stop_usdt']:.2f} USDT",
        "", "## Smallest eligible contracts", "",
        "| Instrument | Min contracts | Contract value | Min notional (USDT) |",
        "|---|---:|---:|---:|",
    ]
    for x in report["eligible"][:50]:
        lines.append(f"| {x['instId']} | {x['minSz']} | {x['ctVal']} {x['ctValCcy']} | {x['min_notional_usdt']:.6f} |")
    lines += ["", "## Caveats", "", report["scope_note"], "",
              "Eligibility here means only that the exchange minimum order fits the configured notional cap. It does not mean the 0.5% stop-risk target is met; that depends on the actual stop distance and final rounded quantity."]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/OKX_100USDT_KONTRAT_DENETIMI.md")
    ap.add_argument("--json", default="data/okx_contract_audit.json")
    ap.add_argument("--equity", type=float, default=100.0)
    ap.add_argument("--max-position-pct", type=float, default=0.35)
    ap.add_argument("--risk-pct", type=float, default=0.005)
    ap.add_argument("--stop-pct", type=float, default=0.05)
    args = ap.parse_args()
    instruments = fetch_json("/api/v5/public/instruments?instType=SWAP")
    tickers = fetch_json("/api/v5/market/tickers?instType=SWAP")
    result = audit(instruments, tickers, args.equity, args.max_position_pct, args.risk_pct, args.stop_pct)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.out).write_text(render(result), encoding="utf-8")
    print(render(result))


if __name__ == "__main__":
    main()
