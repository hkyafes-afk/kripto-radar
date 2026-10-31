"""Exchange-independent normalized market events.

This module is deliberately small and deterministic: adapters normalize public
exchange payloads; strategy code consumes typed values and never invents
exchange timestamps. It does not connect Polat and UnifiedTrader runtimes.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping


@dataclass(frozen=True)
class NormalizedBBO:
    venue: str
    symbol: str
    last: float
    bid: float
    ask: float
    event_ts_ms: int | None
    valid: bool
    reason: str | None = None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _timestamp(value: Any) -> int | None:
    try:
        stamp = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return stamp if stamp > 0 else None


def normalize_okx_ticker(payload: Mapping[str, Any]) -> NormalizedBBO:
    """Normalize OKX ticker fields while preserving missing/invalid timestamps."""
    symbol = str(payload.get("instId") or "").strip()
    last = _number(payload.get("last"))
    bid = _number(payload.get("bidPx"))
    ask = _number(payload.get("askPx"))
    ts = _timestamp(payload.get("ts"))
    reason = None
    if not symbol:
        reason = "symbol_missing"
    elif last is None or bid is None or ask is None:
        reason = "price_missing_or_non_finite"
    elif last <= 0 or bid <= 0 or ask <= 0:
        reason = "price_non_positive"
    elif bid >= ask:
        reason = "bbo_locked_or_crossed"
    return NormalizedBBO(
        venue="okx", symbol=symbol,
        last=last or 0.0, bid=bid or 0.0, ask=ask or 0.0,
        event_ts_ms=ts, valid=reason is None, reason=reason,
    )


def normalize_bbo(
    *, venue: str, symbol: str, last: Any, bid: Any, ask: Any, event_ts_ms: Any,
) -> NormalizedBBO:
    """Normalize already-decoded BBO fields from any supported venue adapter."""
    values = (_number(last), _number(bid), _number(ask))
    ts = _timestamp(event_ts_ms)
    reason = None
    if not symbol:
        reason = "symbol_missing"
    elif any(value is None for value in values):
        reason = "price_missing_or_non_finite"
    elif any(value <= 0 for value in values if value is not None):
        reason = "price_non_positive"
    elif values[1] is not None and values[2] is not None and values[1] >= values[2]:
        reason = "bbo_locked_or_crossed"
    return NormalizedBBO(
        venue=str(venue), symbol=str(symbol),
        last=values[0] or 0.0, bid=values[1] or 0.0, ask=values[2] or 0.0,
        event_ts_ms=ts, valid=reason is None, reason=reason,
    )
