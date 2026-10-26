"""v8 yöneticiler, risk müdürü katmanları, test motoru, Haber Masası ve olay takvimi testleri."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import bt2, macro, quant, team  # noqa: E402
from radar import desk as dk  # noqa: E402
from radar import ledger as lg  # noqa: E402
from radar import news as nw  # noqa: E402
from radar.config import load_config  # noqa: E402

CFG = load_config()
NOW = 1_800_000_000_000
# Limit/politika testleri: gerçek paraya geçişte açılacak kısıtlar açık ve eski tek bütçe (1.000$) ile
import copy  # noqa: E402
CFG_LIVE = copy.deepcopy(CFG)                 # canlı ayar (yarışı kazanan varyantlar dahil)
CFG["ekip"]["varyant"] = {}                   # kural testleri tabanı ölçer (varyantlar ayrı testte)
CFG_RISK = copy.deepcopy(CFG)
CFG_RISK["ekip"]["firma"].update(sinirlar_aktif=True, durdurma_aktif=True, yedek_kulubesi=True, yeniden_dagitim_gun=7)
CFG_RISK["demo"]["baslangic_bakiye"] = 1000
CFG_RISK["ekip"].pop("butce", None)
M = team.BY_ID


def qctx(**kw):
    """Günlük grafikte sağlıklı bir yükseliş trendi (yöneticilerin gördüğü bağlam)."""
    base = {"inst": "X-USDT-SWAP", "sym": "X", "tf": "1D", "bar_ms": 86_400_000, "price": 100.0, "atr": 4.0,
            "open": 98.0, "high": 101.0, "low": 97.5, "close": 100.0, "volume": 1e6,
            "sma20": 96.0, "sma50": 92.0, "sma100": 88.0, "sma200": 80.0, "ema20": 96.5, "sma50_prev5": 91.0,
            "hh10": 99.0, "ll10": 93.0, "hh20": 99.5, "ll20": 90.0, "hh50": 105.0, "ll50": 85.0,
            "hc20": 99.0, "lc20": 91.0, "vol_ma20": 1e6, "vol_ratio": 1.0, "rsi": 58.0, "rsi2": 60.0,
            "bb_mid": 96.0, "bb_up": 102.0, "bb_lo": 90.0, "bbw": 0.12, "bbw_rank": 0.5, "squeeze3": False,
            "close_pos": 0.7, "range_atr": 0.9, "ret1": 0.01, "rv20": 0.03, "rv_rank": 0.5,
            "prev_high": 99.0, "prev_low": 96.0, "above50_prev": True, "below50_prev": False, "ll3": 96.0, "hh3": 101.0,
            "btc_bull": True, "btc_bear": False, "vol24_usd": 5e8, "hvn": [], "signals": []}
    base.update(kw)
    return base


# ── veri masası ──
def test_quant_features_no_lookahead():
    idx = pd.date_range("2024-01-01", periods=80, freq="1D", tz="UTC")
    rng = np.random.default_rng(1)
    c = 100 + np.cumsum(rng.normal(0, 1, 80))
    df = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1000 + rng.random(80)}, index=idx)
    F = quant.features(df)
    i = 50
    assert abs(F["hh20"].iloc[i] - df["high"].iloc[i - 20:i].max()) < 1e-9, "kanal bugünü içermemeli"
    assert abs(F["vol_ma20"].iloc[i] - df["volume"].iloc[i - 20:i].mean()) < 1e-9
    x = quant.ctx_at(F, i)
    assert x["close"] == df["close"].iloc[i] and "squeeze3" in x
    assert len(quant.hvn_levels(F, i)) >= 1


def test_exit_rules():
    c = qctx(close=90.0)
    assert quant.exit_signal({"type": "ma", "n": 50}, c, "long")          # kapanış < sma50
    assert not quant.exit_signal({"type": "ma", "n": 50}, qctx(), "long")
    assert quant.exit_signal({"type": "chan", "n": 10}, qctx(close=92.0), "long")
    assert quant.exit_signal({"type": "mid"}, qctx(close=95.0), "long")
    assert quant.exit_signal({"type": "mean", "n": 20}, qctx(close=97.0), "long")


def test_weather_mult_no_sideways_penalty():
    w = {"trend": "yatay", "high_vol": False}
    assert quant.weather_mult(w, "kaan", "long") == 1.0, "dayanaksız 'yatay' cezası kaldırıldı"
    assert quant.weather_mult({"trend": "ayi", "high_vol": False}, "kaan", "long") == 0.5
    assert quant.weather_mult({"trend": "boga", "high_vol": True}, "kemal", "short") == 0.5 * 0.75


# ── yöneticiler ──
def test_kemal_trend_start_and_reentry():
    start = team.a_kemal(qctx(above50_prev=False), M["kemal"], CFG)
    assert start and start["side"] == "long" and start["exit"] == {"type": "ma", "n": 50}
    assert start["pp"]["trail_now"] and start["tp1"] is None and abs(start["sl"] - (100 - 12)) < 1e-9
    assert team.a_kemal(qctx(hc20=101.0), M["kemal"], CFG) is None, "trend sürüyor ama yeni zirve yok → girme"
    again = team.a_kemal(qctx(close=101.0, price=101.0, hc20=100.5), M["kemal"], CFG)
    assert again, "trend içinde 20 günlük yeni kapanış zirvesi → yeniden gir"


def test_kaan_breakout_and_short_rule():
    c = qctx(close=103.0, price=103.0, hh20=101.0, vol_ratio=2.2, close_pos=0.9)
    idea = team.a_kaan(c, M["kaan"], CFG)
    assert idea and idea["side"] == "long" and idea["rr"] >= 2.0 and idea["exit"]["type"] == "chan"
    assert team.a_kaan(dict(c, vol_ratio=1.2), M["kaan"], CFG) is None, "hacim teyidi şart"
    dn = qctx(close=80.0, price=80.0, ll20=82.0, sma50=90.0, sma100=95.0, vol_ratio=2.0, close_pos=0.1, hh10=88.0)
    assert team.a_kaan(dn, M["kaan"], CFG) is None, "BTC boğadayken short yok"
    s = team.a_kaan(dict(dn, btc_bear=True, btc_bull=False), M["kaan"], CFG)
    assert s and s["side"] == "short"


def test_selin_squeeze_release():
    c = qctx(squeeze3=True, close=103.0, price=103.0, bb_up=102.0, vol_ratio=1.6, close_pos=0.85)
    idea = team.a_selin(c, M["selin"], CFG)
    assert idea and idea["side"] == "long" and idea["exit"] == {"type": "mid"}
    assert team.a_selin(dict(c, squeeze3=False), M["selin"], CFG) is None


def test_murat_pullback_at_volume_node():
    c = qctx(close=98.0, price=98.0, open=96.0, low=95.2, close_pos=0.75, rsi=52, hvn=[95.0, 120.0], vol_ratio=0.8,
             ll3=95.0)
    idea = team.a_murat(c, M["murat"], CFG)
    assert idea and idea["side"] == "long" and idea["sl"] < 95.0 and idea["tp1"] is not None
    assert any("hacim bölgesi" in n and ok for n, ok in idea["checks"])
    assert team.a_murat(dict(c, close=94.0, open=96.0, close_pos=0.2, price=94.0), M["murat"], CFG) is None


def test_ayse_capitulation_only():
    c = qctx(close=88.0, price=88.0, low=84.0, high=97.0, rsi2=3.0, bb_lo=90.0, vol_ratio=2.5, range_atr=3.2,
             close_pos=0.35, sma20=96.0)
    idea = team.a_ayse(c, M["ayse"], CFG)
    assert idea and idea["side"] == "long" and idea["exit"]["type"] == "mean"
    assert team.a_ayse(dict(c, vol_ratio=1.2), M["ayse"], CFG) is None, "hacimsiz düşüş kapitülasyon değil"


def test_deniz_liquidity_coins_and_maker():
    c = qctx(tf="15m", bar_ms=900_000, sym="BTC", rsi=35.0, hour=3, dow=6, atr=50.0, close=60000.0, price=60000.0,
             sma50=59500.0, sma200=59000.0, d_up=True, volume=1500.0, vol_ma96=1000.0)
    idea = team.a_deniz(c, M["deniz"], CFG)
    assert idea and idea["entry_type"] == "limit" and idea["limit_px"] < 60000 and idea["max_saat"] == 8, \
        "v9: saat/gün kuralı yok; gece 3'te hafta sonu da piyasa canlıysa çalışır"
    assert team.a_deniz(dict(c, volume=800.0), M["deniz"], CFG) is None, "piyasa durgun (hacim 24s ortalamasının altında)"
    assert team.a_deniz(dict(c, sym="DOGE"), M["deniz"], CFG) is None, "sadece BTC/ETH"


def test_live_config_applies_race_winners():
    v = (CFG_LIVE["ekip"].get("varyant") or {})
    assert "ttm" in v.get("selin", []), "Selin TTM sıkışması (yarış kararı)"
    c = qctx(close=103.0, price=103.0, bb_up=102.0, bb_mid=97.0, vol_ratio=1.6, close_pos=0.9, squeeze3=False, ttm_prev=1.0)
    idea = team.a_selin(c, M["selin"], CFG_LIVE)
    assert idea and idea["side"] == "long", "TTM sıkışması varken squeeze3 olmadan da girer"
    assert team.a_selin(dict(c, ttm_prev=0.0, squeeze3=True), M["selin"], CFG_LIVE) is None
    mi = team.apply_variant(team.a_murat(qctx(low=96.6, close=97.5, open=96.8, close_pos=0.8, rsi=50.0), M["murat"], CFG_LIVE)
                            or {"mgr": "murat", "side": "long", "entry": 100.0, "sl": 96.0, "tp": 108.0, "tp1": 104.0,
                                "atr": 2.0, "pp": {"partial_frac": 0.5, "trail_atr": 2.0}, "plan": ""}, qctx(), CFG_LIVE)
    assert mi["pp"]["partial_frac"] == 0 and mi["tp1"] is not None


def test_analyst_card():
    c = qctx()
    assert team.analyst_card(c, "long")["mult"] == 1.0
    assert team.analyst_card(dict(c, vol24_usd=5e6), "long")["mult"] == 0.5
    assert team.analyst_card(c, "long", {"level": "kirmizi", "title": "X hacklendi"})["mult"] == 0.0
    assert team.analyst_card(c, "long", {"level": "sari"})["mult"] == 0.0
    assert team.analyst_card(c, "short", {"level": "sari"})["mult"] == 1.0
    assert team.analyst_card(dict(c, funding_ann=90), "long")["mult"] == 0.7


# ── test motoru ──
def test_bt_trade_costs_gap_and_partial():
    idx = pd.date_range("2025-01-01", periods=6, freq="1D", tz="UTC")
    #            sinyal  giriş   tp1     gap stop
    o = np.array([100, 100.0, 101.0, 104.0, 99.0, 99.0])
    h = np.array([100, 101.0, 106.0, 104.5, 99.5, 99.5])
    lo = np.array([100, 99.5, 100.5, 103.5, 95.0, 95.0])
    c = np.array([100, 100.5, 105.5, 104.0, 96.0, 96.0])
    F = quant.features(pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": 1.0}, index=idx))
    idea = {"mgr": "kaan", "sym": "X", "side": "long", "entry": 100.0, "sl": 96.0, "tp": 120.0, "tp1": 104.0,
            "atr": 2.0, "max_saat": 999, "pp": {"partial_frac": 0.5, "trail_atr": 2.5, "basabas": True}}
    t = bt2.simulate_trade(idea, F, 0, 24.0, lambda k: quant.ctx_at(F, k))
    entry = 100 * (1 + 2 / 1e4)
    risk = entry - (96 + entry - 100)
    # gün 2: tp1 104'te yarısı (maker); başabaş stop; iz stop yükselir; gün 4: açılış 99 < stop → açılıştan, kayma ile
    part = 0.5 * (104 + (entry - 100) - entry) - 0.5 * (104 + entry - 100) * 0.0004
    assert t and t["why"].startswith("iz") and t["exit_i"] == 4
    exit_px = 99.0 * (1 - 5 / 1e4)
    rest = 0.5 * (exit_px - entry) - 0.5 * exit_px * 0.001
    fund = sum(q * cc * 0.0003 for q, cc in ((1.0, 100.5), (0.5, 105.5), (0.5, 104.0), (0.5, 96.0)))
    exp = (part + rest - entry * 0.001 - fund) / risk
    assert abs(t["r"] - exp) < 0.02, (t["r"], exp)


def test_deflated_sharpe_penalizes_many_trials():
    one, _ = bt2.deflated_sharpe(0.06, 1500, 0.0, 3.0, [0.06])
    many, sr0 = bt2.deflated_sharpe(0.06, 1500, 0.0, 3.0, [0.06, -0.05, 0.02, 0.04, -0.08, 0.01, 0.03, -0.02])
    assert one > 0.95 and many < one and sr0 > 0


# ── risk müdürü ──
def _desk(cfg=None):
    st, L = {}, lg.empty()
    return dk.Desk(cfg or CFG_RISK, st, L), st, L


def _kaan_idea(**kw):
    c = qctx(close=103.0, price=103.0, hh20=101.0, vol_ratio=2.2, close_pos=0.9, **kw)
    return team.a_kaan(c, M["kaan"], CFG)


def test_desk_layers_sizing_calendar_and_card():
    d, st, L = _desk()
    idea = _kaan_idea()
    kind, det, _ = d.evaluate(idea, NOW)
    base_pct = 3 + 2 * idea["conviction"]
    assert kind == "market" and abs(det["risk_pct"] - base_pct) < 0.05
    d.env = {"weather": {"trend": "ayi", "high_vol": False}}
    kind, det2, _ = d.evaluate(idea, NOW)
    assert abs(det2["risk_pct"] - base_pct * 0.5) < 0.05, "ayı piyasasında long yarım risk"
    d.env = {"cal_block": "olay takvimi: Fed faiz kararı"}
    assert d.evaluate(idea, NOW)[0] == "red"
    d.env = {}
    bad = dict(idea, card={"mult": 0.0, "notes": ["Haber Masası kırmızı alarm"]})
    k, _, why = d.evaluate(bad, NOW)
    assert k == "red" and "baş analist" in why


def test_desk_crowding_limit():
    d, st, L = _desk()
    d.env = {"corr": lambda a, b: 0.9}          # hepsi birlikte hareket ediyor
    ideas = [dict(_kaan_idea(), inst=f"C{i}-USDT-SWAP", sym=f"C{i}", mgr=m)
             for i, m in enumerate(["kaan", "selin", "kemal"] * 5)]
    o, _, r = d.execute(ideas, NOW)
    assert any("yığılma" in w for _, w in r), r
    assert sum(lg.open_risk(t) for t in L["open"] if not t.get("shadow")) <= 1000 * 0.04 + 1e-6
    assert any(t.get("filter_kind") == "yığılma" for t in L["open"]), "elenenler gölgede izlenir"


def test_maker_first_then_fallback():
    d, st, L = _desk()
    idea = dict(_kaan_idea(), maker_first=True)
    _, orders, _ = d.execute([idea], NOW, quote=lambda inst: (102.9, 103.1, 103.0))
    assert orders and orders[0]["px"] == 102.9 and orders[0]["idea"]["fallback_market"]
    gone = d.expire_orders(NOW + 601_000)
    assert gone and not L["orders"]


def test_policy_bench_and_capital():
    d, st, L = _desk()
    msg = d.apply_policy(["kemal", "kaan", "selin", "elif"], {"elif": 0.15}, NOW, "5 yıllık test")
    assert d.benched("deniz") and d.benched("murat") and d.benched("ayse") and not d.benched("kaan")
    assert abs(d.firm_equity() - 1000) < 1e-6
    assert abs(d.capital("elif") - 150) < 1e-6 and abs(d.capital("kaan") - 850 / 3) < 1e-6
    assert d.capital("deniz") == 0 and "gölge" in msg
    # gölgedeki yönetici sermayesiz olsa da gölge işlem açabilmeli (kendini kanıtlaması için)
    sh = dict(_kaan_idea(), mgr="murat", inst="S-USDT-SWAP", sym="S")
    o, _, r = d.execute([sh], NOW)
    assert o and o[0]["shadow"], r
    # gölgede kendini kanıtlayan geri döner (sermayesi bir sonraki dağıtımda gelir)
    for i in range(10):
        L["closed"].append({"mgr": "deniz", "shadow": True, "r": 0.5, "exit_ts": NOW + i, "pnl_usd": 1.0})
    d.manager_tick(NOW + 100)
    assert not d.benched("deniz")


def test_rebalance_gives_benched_nothing():
    d, st, L = _desk()
    d.apply_policy(["kemal", "kaan", "selin", "elif"], {"elif": 0.15}, NOW, "test")
    for i in range(20):
        L["closed"].append({"mgr": "kaan", "r": 0.4, "exit_ts": NOW + i, "pnl_usd": 1.0, "pnl_pct": 1, "hours": 1})
    feq = d.firm_equity()
    st["desk"]["last_rebalance"] = NOW - 8 * 86400_000
    assert d.maybe_rebalance(NOW + 1000)
    assert abs(d.firm_equity() - feq) < 1e-6 and d.capital("murat") == 0
    assert d.equity("kaan") > d.equity("kemal")


def test_carry_plan_and_pnl():
    d, st, L = _desk()
    hv = [{"inst": "BTC-USDT-SWAP", "sym": "BTC", "ann_30d": 20, "ann_7d": 18, "ann_now": 15, "pos_pct": 95,
           "stability": 70, "cooling": False, "interval_h": 8}]
    prices = {"BTC-USDT-SWAP": 80040.0, "BTC-USDT": 80000.0}
    to_open, to_close = d.carry_plan(hv, prices, NOW)
    assert len(to_open) == 1 and not to_close
    h, spot, perp, qty, shadow = to_open[0]
    t = lg.build_carry("elif", h["inst"], "BTC", "BTC-USDT", perp, spot, qty, CFG, NOW, h)
    L["open"].append(t)
    pays = lg.apply_funding(L, "BTC-USDT-SWAP", 0.0002, NOW + 1000, 80040.0)
    assert pays and pays[0][1] < 0 and t["funding_usd"] < 0
    t2 = lg.build_carry("elif", h["inst"], "BTC", "BTC-USDT", perp, spot, qty, CFG, NOW, h)
    t2["neg_count"] = 2
    L["open"].append(t2)
    _, to_close = d.carry_plan(hv, prices, NOW)
    assert to_close and "negatif" in to_close[0][1]


# ── v8.2: ajan başı bütçe, sınırsız demo, kaldıraç yetkisi, Ortak Masa, öğrenme ──
def test_budget_mode_no_limits_and_leverage():
    d, st, L = _desk(CFG)                     # varsayılan config: 10.000$/yönetici, kısıtlar kapalı
    assert abs(d.capital("kaan") - 10_000) < 1e-9 and abs(d.firm_equity() - 10_000 * len(d.mgrs)) < 1e-6
    ideas = [dict(_kaan_idea(), inst=f"C{i}-USDT-SWAP", sym=f"C{i}") for i in range(12)]
    o, _, r = d.execute(ideas, NOW)
    assert len(o) == 12, ("kısıtlar kapalıyken firma risk sınırına takılmamalı", r)
    t = o[0]
    stop_frac = abs(t["entry"] - t["sl"]) / t["entry"]
    assert t["lev"] >= 2 and t["liq"] is not None and not t["liq_first"], "likidasyon stop'un ötesinde olmalı"
    assert abs(t["entry"] - t["liq"]) / t["entry"] >= stop_frac * 1.4
    # düşük güven → daha az kaldıraç
    lo, _ = d.choose_leverage(M["kaan"], {"conviction": 0.0}, 0.03, None)
    hi, why = d.choose_leverage(M["kaan"], {"conviction": 1.0}, 0.03, None)
    assert lo == 1 and hi > lo and "likidasyon" in why
    cap, _ = d.choose_leverage(M["kaan"], {"conviction": 1.0}, 0.002, {"max_lev": 5})
    assert cap == 5, "OKX sınırı aşılmaz"


def test_consensus_desk():
    d, st, L = _desk(CFG)
    a = dict(_kaan_idea(), inst="Q-USDT-SWAP", sym="Q")
    d.execute([a], NOW)
    assert d.consensus_plan(NOW, {"Q-USDT-SWAP": 103.0}) == ([], []), "tek yönetici uzlaşı değil"
    d.execute([dict(a, mgr="selin")], NOW)
    to_open, _ = d.consensus_plan(NOW, {"Q-USDT-SWAP": 103.0})
    assert len(to_open) == 1 and to_open[0]["mgr"] == "ortak" and set(to_open[0]["consensus"]) == {"kaan", "selin"}
    o, _, _ = d.execute(to_open, NOW)
    assert o and o[0]["mgr"] == "ortak"
    # Kaan çıkınca uzlaşı dağılır → Ortak Masa çıkar; yöneticilerin kendi işlemleri etkilenmez
    L["open"] = [t for t in L["open"] if t["mgr"] != "kaan"]
    _, to_close = d.consensus_plan(NOW, {"Q-USDT-SWAP": 103.0})
    assert to_close and to_close[0][0]["mgr"] == "ortak" and any(t["mgr"] == "selin" for t in L["open"])


def test_segment_learning_promotes_and_demotes():
    from radar import learn
    d, st, L = _desk(CFG)
    for i in range(30):
        L["closed"].append({"mgr": "kaan", "side": "short", "shadow": True, "r": 0.4 if i % 3 else -0.3,
                            "exit_ts": NOW + i, "inst": f"K{i}"})
        L["closed"].append({"mgr": "kemal", "side": "long", "shadow": False, "r": -0.3 if i % 4 else 0.2,
                            "exit_ts": NOW + i, "inst": f"M{i}"})
    assert d.segment("murat", "long") == "golge", "v9: Murat kanıtlanana kadar gölgede başlar"
    msgs = learn.segment_tick(d, L, NOW - 1, NOW + 100)
    assert d.segment("kaan", "short") == "gercek" and d.segment("kemal", "long") == "golge"
    assert len(msgs) == 2
    # öğrenilen durum desk kararına yansır: Kaan short artık gerçek, Murat long gölge
    s_idea = dict(_kaan_idea(), side="short", shadow_only=True, inst="Z-USDT-SWAP", sym="Z", sl=106.0, tp=85.0, tp1=None)
    k, det, _ = d.evaluate(s_idea, NOW)
    assert k == "market" and not det["shadow"]
    k, det, _ = d.evaluate(dict(_kaan_idea(), mgr="kemal", inst="Y-USDT-SWAP", sym="Y"), NOW)
    assert det["shadow"]


def test_lessons_and_learned_params():
    from radar import learn
    t = {"outcome": "stop", "r": -1.0, "mfe_r": 1.4, "mae_r": 1.0, "fee_usd": 2.0, "gross_usd": -10.0}
    assert "geri döndü" in learn.lesson(t)
    assert "hemen ters" in learn.lesson(dict(t, mfe_r=0.1))
    assert "iz süren stop" in learn.lesson({"outcome": "iz stop", "r": 1.8, "mfe_r": 2.5, "mae_r": 0.2})
    cfg = copy.deepcopy(CFG)
    cfg["_learned"] = {"managers": {"kaan": {"stop_k": 1.25, "trail_k": 0.8}}}
    base = _kaan_idea()
    tuned = team.apply_learned(_kaan_idea(), cfg)
    d0, d1 = abs(base["entry"] - base["sl"]), abs(tuned["entry"] - tuned["sl"])
    assert abs(d1 / d0 - 1.25) < 1e-9 and abs(tuned["pp"]["trail_atr"] - base["pp"]["trail_atr"] * 0.8) < 1e-9
    assert abs((tuned["tp"] - tuned["entry"]) / d1 - (base["tp"] - base["entry"]) / d0) < 1e-9, "hedef R olarak korunur"
    cfg["_learned"]["managers"]["kaan"]["stop_k"] = 9.0
    assert team.learned_for(cfg, "kaan")["stop_k"] == 1.5, "öğrenme sınırların dışına çıkamaz"


# ── Haber Masası ve olay takvimi ──
UNI = {"BTC", "ETH", "SOL", "ARB", "PEPE", "ONE", "FOO"}


def test_news_mentions_and_classify():
    assert nw.mentions("Arbitrum (ARB) bridge exploited for $20M", UNI) == {"ARB"}
    assert "ONE" not in nw.mentions("ONE more thing about markets", UNI)
    lvl, hit = nw.classify("Exchange hacked, $40M in ETH stolen", nw.mentions("Exchange hacked, $40M in ETH stolen", UNI))
    assert lvl is None and not hit, "çalınan varlık olarak geçen ETH kırmızıya düşmemeli"
    lvl, hit = nw.classify("Solana network halt hits validators", {"SOL"})
    assert lvl == "kirmizi" and hit == {"SOL"}


def test_news_exchange_announcements_and_state():
    d = nw.NewsDesk()
    now = nw._now_ms()
    d._exchange_ann("OKX", [("OKX to delist FOO perpetual swap", now, ""), ("OKX will list PEPE (PEPE)", now, "")],
                    UNI, okx=True)
    assert d.flag("FOO")["level"] == "kirmizi" and "FOO" in d.delist_okx
    assert d.flag("PEPE")["level"] == "sari" and len(d.new_alerts) == 2
    d2 = nw.NewsDesk()
    d2.from_state(d.to_state())
    d2._exchange_ann("OKX", [("OKX to delist FOO perpetual swap", now, "")], UNI, okx=True)
    assert not d2.new_alerts, "devirden sonra aynı uyarı tekrar gönderilmemeli"
    d.fng = {"value": 85}
    assert d.fng_mult("long") == 0.8 and d.fng_mult("short") == 1.0


def test_macro_calendar():
    cal = macro.Calendar()
    fed = [e for e in cal.events if "2026-10-28" in pd.Timestamp(e["ts"], unit="ms", tz="UTC").strftime("%Y-%m-%d")]
    assert fed and pd.Timestamp(fed[0]["ts"], unit="ms", tz="UTC").hour == 18     # 14:00 New York (EDT)
    assert cal.block(fed[0]["ts"] + 30 * 60_000) and not cal.block(fed[0]["ts"] + 3 * 3600_000)
    ev = macro.parse_ff([{"title": "CPI m/m", "country": "USD", "impact": "High", "date": "2026-11-12T08:30:00-05:00"},
                         {"title": "X", "country": "EUR", "impact": "High", "date": "2026-11-12T08:30:00-05:00"}])
    assert len(ev) == 1 and pd.Timestamp(ev[0]["ts"], unit="ms", tz="UTC").hour == 13
    assert macro.weekend_mult(1793500000000, "15m") in (0.75, 1.0) and macro.weekend_mult(0, "1D") == 1.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
