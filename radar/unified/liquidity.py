"""Public BBO'dan spread durumu (saf fonksiyon). Durumlar: spread_ok / spread_missing / spread_stale / spread_invalid.
Yalnız spread_ok iken gerçek spread kullanılır; diğer durumlarda operasyonel shadow açılışı YAPILMAZ.
Varsayılan spread hiçbir durumda operasyonel açılışta kullanılmaz."""
from __future__ import annotations

import math

MAX_AGE_S = 15.0


def _f(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def spread_status(bid, ask, age_s, max_age_s: float = MAX_AGE_S) -> tuple[str, float | None]:
    if bid is None or ask is None:
        return "spread_missing", None
    b, a = _f(bid), _f(ask)
    if b is None or a is None or b <= 0 or a <= 0 or a < b:
        return "spread_invalid", None
    if age_s is None or _f(age_s) is None or age_s > max_age_s:
        return "spread_stale", None
    mid = (a + b) / 2
    return "spread_ok", (a - b) / mid * 1e4
