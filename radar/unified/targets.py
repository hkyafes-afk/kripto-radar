"""Hedef doğrulama (saf fonksiyon). NaN, sonsuz, sıfır, negatif ve yanlış taraftaki hedefler reddedilir.
Mekanik R hedefleri (giriş ± 2R / 6R, R = |giriş − stop|) yapısal kaynak değil, `mekanik` referansıdır.
LONG : hedef > giriş > 0      SHORT : 0 < hedef < giriş
"""
from __future__ import annotations

import math
from typing import Iterable

from .models import Side

MECHANICAL_R = (2.0, 6.0)
_TOL = 1e-3


def _pos(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def classify(side, entry, stop, targets: Iterable) -> dict:
    """{"hedef": yapısal geçerli, "mekanik": mekanik R referansı, "reddedilen": geçersiz}."""
    out = {"hedef": [], "mekanik": [], "reddedilen": []}
    tl = list(targets or ())
    if not (_pos(entry) and _pos(stop)) or stop == entry:
        out["reddedilen"] = tl
        return out
    long_ = side is Side.LONG
    risk = abs(entry - stop)
    for t in tl:
        if not _pos(t) or (long_ and not t > entry) or (not long_ and not t < entry):
            out["reddedilen"].append(t)
            continue
        k = abs(t - entry) / risk
        key = "mekanik" if any(abs(k - m) < _TOL for m in MECHANICAL_R) else "hedef"
        out[key].append(t)
    return out
