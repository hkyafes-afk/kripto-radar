"""Brüt avantaj önselleri: stratejinin maliyet ÖNCESİ beklenen getirisi (R) → nominalin bp'si.

Kaynak yalnız Binance EĞİTİM dönemi (2020-10-28 → 2025-01-06), `tools/arastirma/team_test.py` çıktısı (batan coinler dahil,
hacme göre ilk 100, yarışın kendi kuralları). Saklı test dönemi KULLANILMAZ. İki koruma:
  - örnek sayısına göre sıfıra büzülme: avg_r × n / (n + 50)
  - üst sınır 0,5R: eğitimde Kaan +1,14R çıktı ama testte +0,44R'ye indi; iyimserlik sınırlanır.
Önseli olmayan strateji (yeni ya da kanıtsız) için None döner → CostEngine SHADOW (avantaj uydurulmaz).
Canlı paper sonuçları birikince PR 4'teki EffectivenessAuditor bu önselleri günceller.
"""
from __future__ import annotations

from dataclasses import dataclass

CAP_R = 0.5
SHRINK_N = 50


@dataclass(frozen=True, slots=True)
class EdgePrior:
    avg_r: float
    n: int
    source: str

    @property
    def shrunk_r(self) -> float:
        return min(self.avg_r * self.n / (self.n + SHRINK_N), CAP_R)


_SRC = "Binance eğitim 2020-10-28→2025-01-06, tools/arastirma/team_test.py"
PRIORS: dict[str, EdgePrior] = {
    "swing_trend": EdgePrior(0.292, 252, _SRC + " (Kemal)"),
    "controlled_breakout": EdgePrior(1.139, 142, _SRC + " (Kaan)"),
    "compression_breakout": EdgePrior(0.708, 97, _SRC + " (Selin)"),
    "legacy_murat": EdgePrior(0.236, 185, _SRC + " (Murat)"),
}


def prior_r(strategy: str) -> float | None:
    p = PRIORS.get(strategy)
    return p.shrunk_r if p else None


def gross_edge_bp(strategy: str, stop_bp: float) -> float | None:
    r = prior_r(strategy)
    if r is None or not stop_bp or stop_bp <= 0:
        return None
    return r * stop_bp
