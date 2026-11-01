"""Ayrı COMBO LONG lot defteri ve ortalama maliyet."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class LongLot:
    lot_id: str
    entry_price: float
    qty: float
    level_index: int | None
    opened_ts_ms: int


class ComboPosition:
    def __init__(self, lots: list[LongLot] | None = None):
        self.lots: dict[str, LongLot] = {lot.lot_id: lot for lot in (lots or [])}

    @property
    def qty(self) -> float:
        return sum(max(0.0, lot.qty) for lot in self.lots.values())

    @property
    def avg_entry_price(self) -> float | None:
        qty = self.qty
        if qty <= 1e-12:
            return None
        return sum(lot.entry_price * lot.qty for lot in self.lots.values()) / qty

    def add(self, lot: LongLot) -> None:
        if lot.qty <= 0 or lot.entry_price <= 0:
            raise ValueError("LONG lot miktarı ve fiyatı pozitif olmalı")
        if lot.lot_id in self.lots:
            raise ValueError(f"Tekrarlanan lot kimliği: {lot.lot_id}")
        self.lots[lot.lot_id] = lot

    def available_qty(self, lot_id: str) -> float:
        lot = self.lots.get(lot_id)
        return max(0.0, lot.qty) if lot else 0.0

    def close(self, lot_id: str, qty: float, price: float) -> float:
        lot = self.lots.get(lot_id)
        if lot is None:
            raise ValueError("Reduce-only satış için lot bulunamadı")
        if qty <= 0 or qty > lot.qty + 1e-12:
            raise ValueError("Satış miktarı lot LONG miktarını aşamaz")
        actual = min(qty, lot.qty)
        gross_pnl = (price - lot.entry_price) * actual
        lot.qty -= actual
        if lot.qty <= 1e-12:
            del self.lots[lot_id]
        return gross_pnl

    def close_fifo(self, qty: float, price: float) -> tuple[float, float]:
        """Main TP / hard stop için lotları açılış sırasıyla kapatır; SHORT oluşmasına izin vermez."""
        remaining = min(max(qty, 0.0), self.qty)
        closed = 0.0
        gross = 0.0
        for lot_id in list(self.lots):
            if remaining <= 1e-12:
                break
            lot = self.lots[lot_id]
            take = min(remaining, lot.qty)
            gross += self.close(lot_id, take, price)
            remaining -= take
            closed += take
        return closed, gross

    def unrealized_gross(self, mark_price: float) -> float:
        return sum((mark_price - lot.entry_price) * lot.qty for lot in self.lots.values())

    def to_dict(self) -> list[dict[str, Any]]:
        return [asdict(lot) for lot in self.lots.values()]

    @classmethod
    def from_dict(cls, raw: list[dict[str, Any]]) -> "ComboPosition":
        return cls([LongLot(**item) for item in raw])
