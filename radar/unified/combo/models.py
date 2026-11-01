"""COMBO emir, fiyat ve dolum tipleri."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math


class ComboOrderStatus(StrEnum):
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ComboQuote:
    symbol: str
    bid: float
    ask: float
    last: float
    ts_ms: int
    bid_size: float | None = None
    ask_size: float | None = None

    @property
    def valid(self) -> bool:
        vals = (self.bid, self.ask, self.last)
        sizes = (self.bid_size, self.ask_size)
        return (
            all(isinstance(x, (int, float)) and math.isfinite(float(x)) and float(x) > 0 for x in vals)
            and self.ask > self.bid
            and all(x is None or (math.isfinite(float(x)) and float(x) >= 0) for x in sizes)
        )


@dataclass(slots=True)
class ComboOrder:
    order_id: str
    side: str
    price: float
    qty: float
    role: str
    level_index: int | None
    lot_id: str | None
    reduce_only: bool
    status: ComboOrderStatus = ComboOrderStatus.ACCEPTED
    filled_qty: float = 0.0
    created_ts_ms: int = 0
    updated_ts_ms: int = 0
    reason: str = ""
    parent_order_id: str | None = None

    @property
    def remaining_qty(self) -> float:
        return max(0.0, self.qty - self.filled_qty)

    @property
    def active(self) -> bool:
        return self.status in (ComboOrderStatus.ACCEPTED, ComboOrderStatus.PARTIALLY_FILLED)


@dataclass(frozen=True, slots=True)
class ComboFill:
    order_id: str
    side: str
    role: str
    level_index: int | None
    lot_id: str | None
    qty: float
    price: float
    fee_usdt: float
    ts_ms: int


@dataclass(frozen=True, slots=True)
class ComboEvent:
    ts_ms: int
    kind: str
    message: str
    order_id: str | None = None
    qty: float | None = None
    price: float | None = None
