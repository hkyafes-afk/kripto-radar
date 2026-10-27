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
    assert s > 0.7 and set(p) == set(bas.W) - {"balina"}, (s, p)
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


def test_whale_component_reads_smart_money_crowd_and_liquidations():
    up = {"hl": {"net": 0.8, "n_long": 6, "n_short": 1}, "ls_acct": 1.0, "ls_top": 2.0,
          "liq_1h": {"short_usd": 3e6, "long_usd": 2e5}}
    dn = {"hl": {"net": -0.7, "n_long": 1, "n_short": 5}, "ls_acct": 2.5, "ls_top": 1.0,
          "liq_1h": {"short_usd": 1e5, "long_usd": 2e6}}
    assert bas.parts(_ctx(crowd=up))["balina"] > 0.6
    assert bas.parts(_ctx(crowd=dn))["balina"] < -0.6
    assert "balina" not in bas.parts(_ctx(crowd={"hl": {"net": 1.0, "n_long": 1, "n_short": 0}}))


def test_does_not_chase_extended_move():
    M = team.BY_ID["bas"]
    calm = team.a_bas(_ctx(ema20=104.0), M, {})
    hot = team.a_bas(_ctx(ema20=90.0), M, {})                  # fiyat ortalamadan 4 ATR uzakta
    assert calm["entry_type"] == "piyasa"
    assert hot["entry_type"] == "limit" and hot["limit_px"] < 110 and "kovalamıyorum" in hot["plan"]
    assert hot["limit_valid_ms"] == 86_400_000


def test_learns_from_own_scorecard_after_enough_trades():
    trs = []
    for k in range(120):
        win = k % 2 == 0
        trs.append({"mgr": "bas", "side": "long", "r": 2.0 if win else -1.0,
                    "bas_parts": {"akis": 0.8 if win else -0.2, "trend": 0.3 if win else 0.6}})
    assert bas.learn(trs[:50])[0] == {}, "100 işlemden önce dokunmaz"
    mult, rep = bas.learn(trs)
    assert mult["akis"] > 1.2 and mult["trend"] < 0.8 and rep["n"] == 120
    try:
        bas.W_LEARN.update(mult)
        assert bas.weights()["akis"] > bas.W["akis"]
    finally:
        bas.W_LEARN.clear()


def test_pyramid_adds_only_to_risk_free_winner():
    import tempfile
    from tests.test_engine_live import _engine
    with tempfile.TemporaryDirectory() as d:
        e = _engine(d)
        idea = team.a_bas(_ctx(inst="PYR-USDT-SWAP", sym="PYR"), team.BY_ID["bas"], {})
        base = {"mgr": "bas", "inst": "PYR-USDT-SWAP", "sym": "PYR", "side": "long", "entry": 100.0, "sl": 95.0,
                "qty": 1.0, "qty_open": 1.0, "real": True, "entry_ts": 1, "fee_entry_usd": 0.0}
        e.ledger["open"].append(dict(base, r_now=0.5))
        assert not e._bas_pyramid(dict(idea)), "kârda değil / stop başabaşa gelmedi → ekleme yok"
        e.ledger["open"][-1].update({"r_now": 1.4, "sl": 100.5})
        j = dict(idea)
        assert e._bas_pyramid(j) and j["layer"] == 2 and j["risk_scale"] == 0.5
        assert not e._bas_pyramid(dict(idea, side="short")), "ters yöne ekleme olmaz"
        d2, st, L = _desk()
        L["open"].append(dict(base, r_now=1.4, sl=100.5))
        _, det1, _ = d2.evaluate(dict(idea), NOW, meta=OLD)
        assert det1 is None, "piramit işareti olmadan aynı coine ikinci işlem açılmaz"
        _, det2, _ = d2.evaluate(j, NOW, meta=OLD)
        assert det2 and det2["risk_pct"] > 0


def test_turbo_collects_whole_team_and_morning_summary():
    T = team.BY_ID["turbo"]
    assert T.analyze is team.a_turbo and T.extra["tam_yetki"]
    # iki kaynak aynı yönde → en güçlüsü seçilir, diğeri teyit; yönler eşit çatışırsa girmez
    mk = lambda mid, side, conv: {"mgr": mid, "side": side, "conviction": conv, "rr": 2.0, "plan": "p", "reasons": [],
                                  "entry": 100.0, "sl": 95.0 if side == "long" else 105.0, "tp": 120.0 if side == "long" else 80.0}
    orig = {k: team.BY_ID[k].analyze for k in team.TURBO_SOURCES}
    try:
        for k in team.TURBO_SOURCES:
            team.BY_ID[k].analyze = lambda ctx, m, cfg: None
        team.BY_ID["kemal"].analyze = lambda ctx, m, cfg: mk("kemal", "long", 0.5)
        team.BY_ID["kaan"].analyze = lambda ctx, m, cfg: mk("kaan", "long", 0.8)
        ctx = {"tf": "1D", "sym": "X"}
        i = team.a_turbo(ctx, T, {"ekip": {}})
        assert i and i["mgr"] == "turbo" and i["setup"] == "turbo:kaan" and i["sources"] == ["kaan", "kemal"]
        assert i["conviction"] > 0.8 and "Kemal" in i["plan"]
        team.BY_ID["selin"].analyze = lambda ctx, m, cfg: mk("selin", "short", 0.9)
        team.BY_ID["murat"].analyze = lambda ctx, m, cfg: mk("murat", "short", 0.9)
        assert team.a_turbo(ctx, T, {"ekip": {}}) is None, "2 long – 2 short: çatışma, girmez"
        assert team.a_turbo(dict(ctx, tf="15m"), T, {"ekip": {}}) is None
    finally:
        for k, f in orig.items():
            team.BY_ID[k].analyze = f
    import tempfile
    from tests.test_engine_live import _engine
    with tempfile.TemporaryDirectory() as d:
        e = _engine(d)
        sent = []
        e._notify = lambda txt, buttons=None: sent.append(txt)
        e.state.pop("daily", None)
        e.daily_tick(hour=0)
        assert sent and "başlangıçtan beri" in sent[0] and e.state["daily"]["eq"], "ilk özet başlangıca göre"
        e.daily_tick(hour=0)
        assert len(sent) == 1, "günde bir kez"
        e.state["daily"]["date"] = "2000-01-01"
        e.daily_tick(hour=0)
        assert len(sent) == 2 and "dünden beri" in sent[1] and "Sabah özeti" in sent[1]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
