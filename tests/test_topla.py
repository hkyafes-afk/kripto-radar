"""💰 Toplayıcı: yatayda aşırı gerilmeden ortalamaya dönüş, masrafın 5 katı hedef, yarı yolda kâr toplama, 1.000$ bütçe."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import team  # noqa: E402
from tests.test_director import _desk  # noqa: E402

M = team.BY_ID["topla"]


def _c(**kw):
    c = {"inst": "BTC-USDT-SWAP", "sym": "BTC", "tf": "15m", "bar_ms": 900_000, "price": 99_000.0, "close": 99_000.0,
         "atr": 250.0, "bb_lo": 99_200.0, "bb_up": 100_800.0, "bb_mid": 100_000.0, "rsi2": 4.0, "adx": 15.0,
         "d_up": True, "d_dn": False}
    c.update(kw)
    return c


def test_long_at_lower_band_in_range_with_maker_entry_and_half_way_profit():
    i = team.a_topla(_c(), M, {})
    assert i and i["side"] == "long" and i["entry_type"] == "limit" and i["tp"] == 100_000.0
    assert i["pp"]["partial_frac"] == 0.5 and i["pp"]["basabas"]
    assert abs((i["tp1"] - i["entry"]) - 0.5 * (i["tp"] - i["entry"])) < 1e-6, "kârın yarısı yolun yarısında"
    assert i["sl"] < i["entry"] and i["max_saat"] == 6


def test_short_mirror_and_filters():
    s = team.a_topla(_c(close=101_000.0, price=101_000.0, rsi2=96.0, d_up=False, d_dn=True), M, {})
    assert s and s["side"] == "short" and s["tp"] == 100_000.0
    assert team.a_topla(_c(adx=30.0), M, {}) is None, "trend günü: ortalamaya dönüş oynanmaz"
    assert team.a_topla(_c(close=99_900.0, price=99_900.0, bb_lo=99_950.0), M, {}) is None, "hedef masrafa göre çok yakın"
    assert team.a_topla(_c(sym="DOGE", inst="DOGE-USDT-SWAP"), M, {}) is None, "sadece en likit coinler"
    assert team.a_topla(_c(rsi2=30.0), M, {}) is None


def test_own_budget_of_1000():
    d, st, L = _desk()
    assert abs(d.capital("topla") - 1000) < 1e-9 and abs(d.capital("kaan") - 10_000) < 1e-9


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
