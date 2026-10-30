"""Strateji kaydı. Carry (Elif) iki bacaklı yürütme gerektirdiği için PR 3'te eklenir (docs/birlesik/PLAN.md).

Bu listede olmayan legacy roller: Turbo ve Ortak Masa (bağımsız açma yetkisi kalktı; kopya bahis), Baş Trader (yerini
UnifiedDecisionEngine alır), Deniz (kapalı). Kayıtları ve geçmiş performansları silinmez.
"""
from __future__ import annotations

from typing import Mapping

from ..models import StrategyAuthority
from .base import LegacyManagerStrategy, Strategy, narrower
from .intraday import IntradayTrendStrategy, MicrostructureStrategy
from .short_trend import ShortTrendStrategy
from .swing import (CompressionBreakoutStrategy, ControlledBreakoutStrategy, LegacyMuratStrategy, PanicReversalStrategy,
                    SwingTrendStrategy)

REGISTRY: tuple[type[Strategy], ...] = (
    SwingTrendStrategy, CompressionBreakoutStrategy, ControlledBreakoutStrategy, PanicReversalStrategy,
    ShortTrendStrategy, IntradayTrendStrategy, MicrostructureStrategy, LegacyMuratStrategy,
)


def build_strategies(cfg: Mapping | None = None) -> list[Strategy]:
    """Kayıtlı stratejileri config yetkileriyle kurar; DISABLED olanlar listeye girmez."""
    out = []
    for cls in REGISTRY:
        s = cls(cfg)
        if s.authority is not StrategyAuthority.DISABLED:
            out.append(s)
    return out


__all__ = ["REGISTRY", "build_strategies", "Strategy", "LegacyManagerStrategy", "narrower",
           "SwingTrendStrategy", "CompressionBreakoutStrategy", "ControlledBreakoutStrategy", "PanicReversalStrategy",
           "ShortTrendStrategy", "IntradayTrendStrategy", "MicrostructureStrategy", "LegacyMuratStrategy"]
