"""Strateji sözleşmesi.

Her strateji:
  - yalnız `MarketContext` okur ve yalnız `Opportunity` üretir,
  - emir gönderemez, risk miktarı ya da kaldıraç belirleyemez (RiskGate ve yürütme belirler),
  - yürütme/defter/borsa modüllerini (ledger, desk, okx, engine, notify, execution) import etmez (test kontrol eder),
  - ortak faktörleri yeniden hesaplamaz (FactorEngine'in ya da legacy bağlamın değerlerini okur).
Yetki (paper / shadow_only / disabled): sınıfın varsayılanı config ile yalnız DARALTILABİLİR, genişletilemez.
"""
from __future__ import annotations

import abc
import dataclasses
from typing import Any, ClassVar, Mapping

from .. import edge
from ..flags import unified_cfg
from ..models import Horizon, MarketContext, Opportunity, StrategyAuthority, opportunity_from_legacy_idea

_RANK = {StrategyAuthority.DISABLED: 0, StrategyAuthority.SHADOW_ONLY: 1, StrategyAuthority.PAPER: 2}


def narrower(a: StrategyAuthority, b: StrategyAuthority) -> StrategyAuthority:
    return a if _RANK[a] <= _RANK[b] else b


class Strategy(abc.ABC):
    name: ClassVar[str]
    horizon: ClassVar[Horizon]
    timeframes: ClassVar[tuple[str, ...]]
    default_authority: ClassVar[StrategyAuthority]
    required_factors: ClassVar[tuple[str, ...]] = ()
    description: ClassVar[str] = ""

    def __init__(self, cfg: Mapping | None = None):
        self.cfg = dict(cfg or {})
        conf = (unified_cfg(self.cfg).get("stratejiler") or {}).get(self.name) or {}
        raw = str(conf.get("yetki") or "").strip().lower()
        try:
            asked = StrategyAuthority(raw) if raw else self.default_authority
        except ValueError:
            asked = StrategyAuthority.SHADOW_ONLY            # tanınmayan değer → güvenli taraf
        self.authority = narrower(self.default_authority, asked)

    @abc.abstractmethod
    def evaluate(self, ctx: MarketContext) -> list[Opportunity]:
        """Bu bağlam için fırsatlar (yoksa boş liste). İstisna fırlatmamalı; motor yine de yakalar."""


class LegacyManagerStrategy(Strategy):
    """Legacy yöneticinin analiz fonksiyonunu (radar/team.py) DEĞİŞTİRMEDEN sarmalar: aynı bağlam → aynı fikir.
    Yarış varyantları ve öğrenilmiş ayarlar (team.ideas_for) aynen uygulanır; fark yalnız çıktının tipli olması,
    stratejinin adı, yetkisi ve brüt avantaj önseli (edge.py)."""
    manager_id: ClassVar[str]

    def evaluate(self, ctx: MarketContext) -> list[Opportunity]:
        if self.authority is StrategyAuthority.DISABLED or ctx.timeframe not in self.timeframes or not ctx.raw:
            return []
        from ... import team                                  # analiz fonksiyonları (yürütme yok)
        mgr = team.BY_ID.get(self.manager_id)
        if mgr is None or mgr.analyze is None:
            return []
        out: list[Opportunity] = []
        for idea in team.ideas_for(dict(ctx.raw), [mgr], self.cfg):
            try:
                o = opportunity_from_legacy_idea(idea, now_ms=ctx.ts_ms)
            except (ValueError, KeyError, TypeError):
                continue
            auth = self.authority if not idea.get("shadow_only") else narrower(self.authority,
                                                                              StrategyAuthority.SHADOW_ONLY)
            candle = ctx.candles.get(ctx.timeframe)
            out.append(dataclasses.replace(
                o, strategy=self.name, authority=auth,
                bar_ts_ms=candle.open_ts_ms if candle is not None else o.bar_ts_ms,
                gross_edge_bp=edge.gross_edge_bp(self.name, o.stop_bp),
                evidence_against=o.evidence_against + self.extra_against(ctx, o)))
        return out

    def extra_against(self, ctx: MarketContext, o: Opportunity) -> tuple[str, ...]:
        return ()


def stop_target(entry: float, side_sign: int, dist: float, r_targets: tuple[float, ...]) -> tuple[float, tuple[float, ...]]:
    stop = entry - side_sign * dist
    tg = tuple(entry + side_sign * r * dist for r in r_targets if entry + side_sign * r * dist > 0)
    return stop, tg


def num(v: Any) -> float | None:
    from ..models import is_finite_number
    return float(v) if is_finite_number(v) else None
