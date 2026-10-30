"""Karar günlüğü: birleşik motorun kararları günlük JSONL dosyalarına (site/birlesik/kararlar-YYYY-MM-DD.jsonl).

Büyüme kontrolü (Polat'ın 100 MB'lık kararlar.jsonl'ü gönderimleri durdurmuştu):
  - aynı fırsat (idempotency anahtarı = coin + strateji + kurulum + mum) aynı kararla tekrar YAZILMAZ; karar ya da sebep
    değişirse yeni satır yazılır,
  - `keep_days` günden eski dosyalar silinir,
  - son durum dizini yalnız son 3 günün anahtarlarını tutar.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import DecisionResult, to_jsonable

DAY_MS = 86_400_000
_FILE = re.compile(r"^kararlar-(\d{4}-\d{2}-\d{2})\.jsonl$")


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def signature(d: DecisionResult) -> str:
    """Kararın kimliği: karar + sebep kodlarının sayısız kökleri (fiyat oynadıkça satır yağmasın)."""
    roots = sorted({c.split(":")[0] for c in d.reason_codes})
    return f"{d.decision.value}|{d.side.value if d.side else '-'}|{'+'.join(roots)}"


class DecisionJournal:
    def __init__(self, root: str | Path, keep_days: int = 14):
        self.root = Path(root)
        self.keep_days = int(keep_days)
        self.index_path = self.root / "son_durum.json"
        self._last: dict[str, list] = self._load()

    def _load(self) -> dict[str, list]:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, now_ms: int) -> None:
        cutoff = now_ms - 3 * DAY_MS
        self._last = {k: v for k, v in self._last.items() if v[1] >= cutoff}
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._last, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.index_path)

    def write(self, decisions: Iterable[DecisionResult], now_ms: int | None = None) -> int:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        lines = []
        for d in decisions:
            key = d.idempotency_key or f"{d.symbol}|{d.strategy}|{d.side}"
            sig = signature(d)
            prev = self._last.get(key)
            if prev and prev[0] == sig:
                continue
            self._last[key] = [sig, now]
            rec = to_jsonable(d)
            rec["kayit_ms"] = now
            lines.append(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
        if lines:
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / f"kararlar-{_day(now)}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        self._save(now)
        return len(lines)

    def prune(self, now_ms: int | None = None) -> list[str]:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        limit = _day(now - self.keep_days * DAY_MS)
        removed = []
        if not self.root.exists():
            return removed
        for f in self.root.iterdir():
            m = _FILE.match(f.name)
            if m and m.group(1) < limit:
                f.unlink(missing_ok=True)
                removed.append(f.name)
        return removed

    def files(self) -> list[Path]:
        return sorted(f for f in self.root.glob("kararlar-*.jsonl")) if self.root.exists() else []
