"""Gölge çalıştırıcı: `birlesik.karar_modu: shadow` iken canlı motorun günlük bağlamlarıyla birleşik kararları üretir ve
günlüğe yazar. Defter, pozisyon ve emir üzerinde HİÇBİR etkisi yoktur; legacy işlemleri açmaya devam eder.

Sıklık: `birlesik.golge_aralik_sn` (varsayılan 300 sn) — canlı döngü her dakika bakar ama birleşik motor 5 dakikada bir.
"""
from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from .decision import UnifiedDecisionEngine
from .factors import FactorEngine
from .flags import unified_cfg
from .journal import DecisionJournal
from .models import Decision


class ShadowRunner:
    def __init__(self, cfg: Mapping | None, root: str | Path, interval_s: float | None = None):
        self.cfg = dict(cfg or {})
        u = unified_cfg(self.cfg)
        self.interval_s = float(interval_s if interval_s is not None else u.get("golge_aralik_sn", 300))
        self.factors = FactorEngine(self.cfg)
        self.engine = UnifiedDecisionEngine(self.cfg)
        self.journal = DecisionJournal(root, keep_days=int(u.get("gunluk_saklama_gun", 14)))
        self.last_run = 0.0

    def _coin_class(self, meta: Mapping | None, now_ms: int) -> str | None:
        if not meta:
            return None
        try:
            from .. import director
            from ..desk import firm_cfg
            return director.coin_class(meta, now_ms, firm_cfg(self.cfg))
        except Exception:  # noqa: BLE001
            return None

    def run(self, ctxs: Iterable[Mapping[str, Any]], *, now_ms: int | None = None,
            meta: Mapping[str, Mapping] | None = None, ws_healthy: bool | None = None,
            force: bool = False) -> dict | None:
        now_s = time.time()
        if not force and now_s - self.last_run < self.interval_s:
            return None
        self.last_run = now_s
        now = int(now_ms if now_ms is not None else now_s * 1000)
        mctxs = []
        for c in ctxs:
            m = (meta or {}).get(c.get("inst")) if meta else None
            try:
                mctxs.append(self.factors.from_legacy_ctx(
                    c, now_ms=now, live_ts_ms=now if c.get("live_bar") else None, data_issue=c.get("suspect"),
                    ws_healthy=ws_healthy, list_ts_ms=(m or {}).get("list_ts"),
                    coin_class=c.get("coin_class") or self._coin_class(m, now)))
            except Exception as e:  # noqa: BLE001
                self.engine.errors.append(f"bağlam {c.get('sym')}: {type(e).__name__}: {e}")
        decisions = self.engine.decide(mctxs, now)
        written = self.journal.write(decisions, now)
        self.journal.prune(now)
        counts = Counter(d.decision.value for d in decisions)
        live = [d for d in decisions if d.decision in (Decision.OPEN, Decision.REDUCE_SIZE)]
        live.sort(key=lambda d: -(d.net_edge_bp or 0))
        summary = {"ts": now, "coin": len(mctxs), "karar": dict(counts), "yeni_satir": written,
                   "acilacaklar": [{"sym": d.symbol, "yon": d.side.value if d.side else None, "strateji": d.strategy,
                                    "karar": d.decision.value, "net_bp": d.net_edge_bp,
                                    "ozet": d.explanation_tr.split("\n")[0]} for d in live[:8]],
                   "hatalar": self.engine.errors[-5:]}
        self.engine.errors.clear()
        return summary
