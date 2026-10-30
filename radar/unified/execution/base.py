"""Yürütme adaptörü sözleşmesi. Strateji ve karar katmanı bu modülü import etmez; yalnız orkestratör kullanır."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Sequence

from ..models import ExecutionIntent, ExecutionMode, ExecutionResult


@dataclass(frozen=True, slots=True)
class Quote:
    """Anlık kitap. asks/bids: [(fiyat, coin_miktarı), ...] en iyiden kötüye; yoksa boş (derinlik bilinmiyor)."""
    inst: str
    bid: float
    ask: float
    last: float
    ts_ms: int
    asks: Sequence[tuple[float, float]] = field(default_factory=tuple)
    bids: Sequence[tuple[float, float]] = field(default_factory=tuple)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def valid(self) -> bool:
        return self.bid > 0 and self.ask > 0 and self.ask >= self.bid and self.last > 0


class ExecutionAdapter(ABC):
    mode: ExecutionMode

    @abstractmethod
    def submit(self, intent: ExecutionIntent, quote: Quote | None, now_ms: int) -> ExecutionResult: ...

    @abstractmethod
    def cancel(self, client_order_id: str, now_ms: int, reason: str = "") -> ExecutionResult | None: ...

    @abstractmethod
    def amend_stop(self, client_order_id: str, stop_price: float, now_ms: int) -> ExecutionResult | None: ...

    @abstractmethod
    def on_quote(self, quote: Quote, now_ms: int) -> list[ExecutionResult]: ...

    @abstractmethod
    def status(self, client_order_id: str) -> ExecutionResult | None: ...

    @abstractmethod
    def reconcile(self, now_ms: int) -> list[str]: ...
