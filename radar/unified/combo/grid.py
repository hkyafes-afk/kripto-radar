"""24 eşit yüzdesel aralıktan oluşan 25 çizgili logaritmik grid."""
from __future__ import annotations

from dataclasses import dataclass
import math

from .config import ComboConfig


@dataclass(frozen=True, slots=True)
class GridLevel:
    index: int
    price: float


class GridBuilder:
    def __init__(self, cfg: ComboConfig):
        self.cfg = cfg
        ratio = cfg.high_price / cfg.low_price
        self.levels = tuple(
            GridLevel(i, round(cfg.low_price * math.exp(math.log(ratio) * i / cfg.grid_intervals)
                               / cfg.price_tick) * cfg.price_tick)
            for i in range(cfg.grid_intervals + 1)
        )
        # Yuvarlama sonrası uç noktalar konfigürasyondaki canlı bantla aynı kalır.
        self.levels = (
            GridLevel(0, cfg.low_price),
            *self.levels[1:-1],
            GridLevel(cfg.grid_intervals, cfg.high_price),
        )

    def below(self, price: float) -> list[GridLevel]:
        return [level for level in self.levels if level.price < price]

    def above(self, price: float) -> GridLevel | None:
        return next((level for level in self.levels if level.price > price), None)

    def by_index(self, index: int) -> GridLevel | None:
        if 0 <= index < len(self.levels):
            return self.levels[index]
        return None

    def next_above(self, index: int) -> GridLevel | None:
        return self.by_index(index + 1)

    def next_below(self, index: int) -> GridLevel | None:
        return self.by_index(index - 1)
