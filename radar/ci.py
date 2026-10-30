"""Güven aralığı yardımcıları (bootstrap, sabit tohum → tekrar üretilebilir).

Sabit eşik ("+0,02R iyi") yerine: farkın %95 güven aralığı sıfırın üstündeyse "iyi" denir. Küçük örnekte aralık geniş
olur ve kural kendiliğinden temkinli davranır.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np

SEED = 7


def _arr(x: Iterable[float]) -> np.ndarray:
    return np.array([float(v) for v in x if v is not None and isinstance(v, (int, float)) and math.isfinite(v)], float)


def mean_ci(x: Iterable[float], n_boot: int = 2000, alpha: float = 0.05, seed: int = SEED) -> tuple[float | None, float | None]:
    """Ortalamanın bootstrap güven aralığı. n < 5 → (None, None)."""
    a = _arr(x)
    if len(a) < 5:
        return None, None
    rng = np.random.default_rng(seed)
    m = a[rng.integers(0, len(a), size=(n_boot, len(a)))].mean(axis=1)
    return float(np.quantile(m, alpha / 2)), float(np.quantile(m, 1 - alpha / 2))


def diff_ci(a: Iterable[float], b: Iterable[float], n_boot: int = 2000, alpha: float = 0.05,
            seed: int = SEED) -> tuple[float | None, float | None]:
    """ort(a) − ort(b) için bağımsız bootstrap güven aralığı. Her iki tarafta en az 5 değer gerekir."""
    x, y = _arr(a), _arr(b)
    if len(x) < 5 or len(y) < 5:
        return None, None
    rng = np.random.default_rng(seed)
    mx = x[rng.integers(0, len(x), size=(n_boot, len(x)))].mean(axis=1)
    my = y[rng.integers(0, len(y), size=(n_boot, len(y)))].mean(axis=1)
    d = mx - my
    return float(np.quantile(d, alpha / 2)), float(np.quantile(d, 1 - alpha / 2))


def subset_improvement_ci(base: Sequence[tuple], variant_keys: set, n_boot: int = 2000, alpha: float = 0.05,
                          seed: int = SEED) -> tuple[float | None, float | None, int]:
    """Eşleştirilmiş filtre testi: varyant tabanın alt kümesiyse (yalnız işlem eliyorsa) ELENEN işlemlerin ort. R'sinin
    güven aralığı. Üst sınır < 0 → filtre kaybedenleri eliyor (iyi). base: [(anahtar, r), ...]. Döner: (alt, üst, n_elenen)."""
    removed = [r for k, r in base if k not in variant_keys]
    lo, hi = mean_ci(removed, n_boot, alpha, seed)
    return lo, hi, len(removed)
