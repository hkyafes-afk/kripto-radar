"""👑 Baş Trader: tek puanlı beyin, giriş/çıkış boşluğu, güvene göre boyut, tam yetki."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import bas, quant, team  # noqa: E402
from tests.test_director import OLD, _desk  # noqa: E402
from tests.test_team import NOW  # noqa: E402


def _ctx(**kw):
    c = {"inst": "SOL-USDT-SWAP", "sym": "SOL", "tf": "1D", "bar_ms": 86_400_000, "price": 110.0, "close": 110.0,
         "open": 101.0, "high": 111.0, "low": 100.0, "atr": 5.0, "sma50": 95.0, "sma200": 80.0, "sma50_prev5": 92.0,
         "adx": 30.0, "ret30": 0.35, "hh20": 105.0, "ll20": 85.0, "hh10": 104.0, "ll10": 98.0, "rs_rank": 0.9,
         "vol_ratio": 2.2, "oi_chg24": 12.0, "chg24": 8.0, "taker_imb": 0.08, "funding_ann": -10.0,
         "btc_bull": True, "btc_bear": False, "peers": {"long": 2, "short": 0}}
    c.update(kw)
    return c


def _bear(c):
    m = {k: v for k, v in c.items()}
    m.update({"close": 80.0, "price": 80.0, "open": 88.0, "high": 89.0, "low": 79.0, "sma50": 95.0, "sma200": 110.0,
              "sma50_prev5": 98.0, "ret30": -0.3, "hh20": 100.0, "ll20": 84.0, "hh10": 90.0, "ll10": 82.0,
              "rs_rank": 0.1, "chg24": -9.0, "taker_imb": -0.1, "funding_ann": 30.0, "btc_bull": False,
              "btc_bear": True, "peers": {"long": 0, "short": 1}})
    return m


def test_score_reads_all_evidence():
    s, p = bas.score(_ctx())
    assert s > 0.7 and set(p) == set(bas.W), (s, p)
    sb, _ = bas.score(_bear(_ctx()))
    assert sb < -0.6
    s2, p2 = bas.score(_ctx(oi_chg24=None, taker_imb=None, funding_ann=None))
    assert "akis" not in p2 and "kalabalik" not in p2 and s2 > 0.6, "eksik veri dışarıda kalır, karar yine verilir"
    assert bas.score({"close": 1.0})[0] == 0.0, "verinin çoğu yoksa karar yok"


def test_entry_size_scales_with_conviction_and_stop_always_set():
    M = team.BY_ID["bas"]
    hi = team.a_bas(_ctx(), M, {})
    assert hi and hi["side"] == "long" and hi["sl"] < 110 and hi["exit"] == {"type": "skor"}
    lo = team.a_bas(_ctx(rs_rank=0.5, peers={}, oi_chg24=1.0, taker_imb=0.0, vol_ratio=1.0, ret30=0.05), M, {})
    assert lo is None or lo["conviction"] < hi["conviction"]
    sh = team.a_bas(_bear(_ctx()), M, {})
    assert sh and sh["side"] == "short" and sh["sl"] > 80
    assert team.a_bas(_ctx(close=96.0, price=96.0, open=96.0, ret30=0.0, rs_rank=0.5, vol_ratio=1.0, oi_chg24=0.0,
                           taker_imb=0.0, funding_ann=0.0, peers={}, sma50_prev5=95.0, adx=10.0, btc_bull=False), M, {}) is None


def test_stop_is_set_by_the_coins_own_data():
    M = team.BY_ID["bas"]
    calm = team.a_bas(_ctx(rv_rank=0.05, ll10=108.0), M, {})
    wild = team.a_bas(_ctx(rv_rank=0.95, ll10=108.0), M, {})
    assert calm and wild and (110 - calm["sl"]) < (110 - wild["sl"]), "oynak coinde stop daha geniş"
    assert wild["pp"]["trail_atr"] > calm["pp"]["trail_atr"]
    deep = team.a_bas(_ctx(rv_rank=0.05, ll10=101.0), M, {})
    assert deep and deep["sl"] < 101.0, "yapı seviyesi gürültüden uzaksa stop seviyenin ötesinde"
    assert team.a_bas(_ctx(atr=30.0), M, {}) is None, "stop fiyatın %25'inden uzaksa işlem yok"
    for i in (calm, wild, deep):
        assert i["tp"] > 0 and (i["tp1"] is None or i["tp1"] > 0)


def test_exit_needs_real_reversal_not_a_wick():
    long_ctx = _ctx()
    assert quant.exit_signal({"type": "skor"}, long_ctx, "long") is None
    wick = _ctx(close=94.0, open=101.0, chg24=-1.0, taker_imb=-0.02)        # bir an ortalamanın altına iğne
    assert quant.exit_signal({"type": "skor"}, wick, "long") is None, "tek iğnede çıkmaz"
    assert quant.exit_signal({"type": "skor"}, _bear(_ctx()), "long"), "tablo gerçekten dönünce çıkar"


def test_full_authority_skips_director_gates_but_keeps_stop_and_money():
    d, st, L = _desk()
    idea = team.a_bas(_ctx(inst="KAAN-USDT-SWAP", sym="KAAN"), team.BY_ID["bas"], {})
    young = lambda i: {"list_ts": NOW - 30 * 86_400_000, "ct_val": 1, "lot_sz": 0.001, "min_sz": 0.001, "max_lev": 50}
    kind, det, why = d.evaluate(idea, NOW, meta=young)
    assert kind != "red" and not det.get("shadow"), (kind, why, det and det.get("gm"))
    assert det["mults"].get("uzman", 1.0) == 1.0
    L["open"].append({"mgr": "kemal", "inst": idea["inst"], "sym": "KAAN", "side": "short", "entry": 1, "sl": 2,
                      "qty": 1, "qty_open": 1, "real": True})
    kind2, det2, _ = d.evaluate(idea, NOW, meta=OLD)
    assert kind2 != "red" and not det2.get("shadow"), "çatışma hakemi Baş Trader'ı gölgeye atmaz"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
