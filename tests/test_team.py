"""Yönetici kadrosu ve risk müdürü testleri."""
from __future__ import annotations

import copy
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import desk as dk  # noqa: E402
from radar import ledger as lg  # noqa: E402
from radar import signals as sg  # noqa: E402
from radar import team  # noqa: E402
from radar.config import load_config  # noqa: E402
from radar.demo import DemoClient  # noqa: E402

CFG = load_config()
NOW = 1_800_000_000_000


def ctx(**kw):
    base = {"inst": "X-USDT-SWAP", "sym": "X", "tf": "1H", "bar_ms": 3600_000, "price": 100.0, "atr": 2.0,
            "open": 99.0, "high": 100.5, "low": 98.8, "close": 100.0, "prev_high": 99.5, "prev_low": 98.0,
            "low5": 97.0, "high5": 101.0, "ema20": 99.0, "ema50": 97.0, "rsi": 55.0, "rsi_prev": 50.0,
            "rsi_min3": 50.0, "rsi_max3": 56.0, "adx": 26.0, "adx_prev": 24.0, "bb_mid": 98.0, "bb_up": 102.0,
            "bb_lo": 94.0, "bb_pct": 0.75, "bbw_rank": 0.5, "squeeze_prev": False, "don_hi": 101.0, "don_lo": 92.0,
            "vol_ratio": 1.3, "close_pos": 0.8, "st_dir": 1, "div": 0, "swing_up": None, "swing_dn": None,
            "d_trend": 1, "tsmom": 1.0, "regime": 1, "funding_ann": 10.0, "funding_rate": 0.0001, "interval_h": 8,
            "oi_chg24": 4.0, "taker_imb": 0.06, "ret24": 0.02, "chg24": 2.0, "signals": []}
    base.update(kw)
    return base


M = team.BY_ID


def test_extra_features_on_real_shape():
    df = DemoClient().candles("SOL-USDT-SWAP", "1H", 300)
    f = sg.frame_features(df, CFG, 1)
    x = team.extra_features(df, f)
    for k in ("don_hi", "bbw_rank", "squeeze_prev", "vol_ratio", "rsi_min3", "swing_up", "close_pos"):
        assert k in x
    assert x["don_hi"] >= x["don_lo"]


def test_kaan_breakout_and_conviction_scaling():
    c = ctx(close=103.0, price=103.0, high=103.2, don_hi=101.0, vol_ratio=2.5, adx=28, adx_prev=25,
            oi_chg24=6, taker_imb=0.1, bbw_rank=0.3)
    idea = team.a_kaan(c, M["kaan"], CFG)
    assert idea and idea["side"] == "long" and idea["entry_type"] == "piyasa" and idea["rr"] >= 2.0
    assert idea["conviction"] >= 6 / 7 - 1e-9
    # kırılımdan çok uzaklaşmışsa geri testte limit emir
    c2 = dict(c, close=104.5, price=104.5)
    idea2 = team.a_kaan(c2, M["kaan"], CFG)
    assert idea2["entry_type"] == "limit" and abs(idea2["limit_px"] - (101 + 0.5)) < 1e-9
    # az kanıt → fikir yok
    assert team.a_kaan(dict(c, adx=15, oi_chg24=0, taker_imb=0, regime=-1, d_trend=0, bbw_rank=0.9,
                            funding_ann=60), M["kaan"], CFG) is None


def test_selin_squeeze_release():
    c = ctx(squeeze_prev=True, bb_pct=1.2, vol_ratio=2.0, funding_ann=-5, oi_chg24=3, taker_imb=0.05,
            close_pos=0.85, bb_mid=97.0)
    idea = team.a_selin(c, M["selin"], CFG)
    assert idea and idea["side"] == "long" and idea["pp"]["trail_atr"] == 1.5
    assert team.a_selin(dict(c, squeeze_prev=False), M["selin"], CFG) is None


def test_ayse_contrarian_short():
    c = ctx(funding_ann=95, rsi=78, bb_pct=1.15, oi_chg24=8, div=-1, taker_imb=-0.04, d_trend=0, vol_ratio=1.8,
            high5=103.0, bb_mid=96.0)
    idea = team.a_ayse(c, M["ayse"], CFG)
    assert idea and idea["side"] == "short" and idea["sl"] > 100 and idea["tp"] < 100


def test_murat_fibonacci():
    sw = {"H": 110.0, "L": 90.0, "iH": 280, "iL": 260, "since": 10, "size_atr": 10.0}
    # %61,8 civarı: 110 − 0.6×20 = 98
    c = ctx(price=98.0, close=98.0, open=97.0, rsi=50, rsi_prev=45, swing_up=sw, vol_ratio=0.8, taker_imb=0.02)
    idea = team.a_murat(c, M["murat"], CFG)
    assert idea and idea["side"] == "long"
    assert idea["sl"] < 110 - 0.786 * 20 and abs(idea["tp"] - (110 + 0.272 * 20)) < 1e-9 and idea["tp1"] is not None
    assert team.a_murat(dict(c, price=105.0, close=105.0), M["murat"], CFG) is None   # bölge dışı


def test_deniz_pullback():
    c = ctx(tf="15m", bar_ms=900_000, rsi_min3=40, rsi=48, rsi_prev=42, close=100.2, price=100.2, prev_high=100.0,
            low5=99.0)
    idea = team.a_deniz(c, M["deniz"], CFG)
    assert idea and idea["side"] == "long" and idea["max_saat"] == 8 and idea["pp"]["partial_frac"] == 0


def test_kemal_uses_trend_signal():
    sig = {"setup": "trend_long", "side": "long", "score": 80, "label": "Trend Long"}
    idea = team.a_kemal(ctx(tf="4H", bar_ms=14_400_000, signals=[sig]), M["kemal"], CFG)
    assert idea and idea["side"] == "long" and idea["max_saat"] == 240
    ext = team.a_kemal(ctx(tf="4H", bar_ms=14_400_000, signals=[sig], price=104.0, close=104.0, ema20=99.0),
                       M["kemal"], CFG)
    assert ext["entry_type"] == "limit"


def _desk():
    st, L = {}, lg.empty()
    return dk.Desk(CFG, st, L), st, L


def test_desk_sizing_limits_and_orders():
    d, st, L = _desk()
    start_each = 1000 / 7
    assert abs(d.equity("kaan") - start_each) < 1e-9 and abs(d.firm_equity() - 1000) < 1e-9
    idea = team.a_kaan(ctx(close=103.0, price=103.0, don_hi=101.0, vol_ratio=2.5, adx=28, adx_prev=25,
                           oi_chg24=6, taker_imb=0.1, bbw_rank=0.3), M["kaan"], CFG)
    kind, det, why = d.evaluate(idea, NOW)
    # agresif: %3 + (%5−%3)×güven
    exp_pct = 3 + 2 * idea["conviction"]
    assert kind == "market" and abs(det["risk_pct"] - exp_pct) < 0.05, (det["risk_pct"], exp_pct)
    opened, orders, rej = d.execute([idea], NOW)
    assert len(opened) == 1 and L["open"][0]["mgr"] == "kaan"
    # aynı yönetici aynı coine ikinci kez giremez
    assert d.execute([idea], NOW)[0] == []
    # başka yönetici aynı coinde: coin başı firma riski (%2,5 = 25$) sınırına kadar
    sel = dict(idea, mgr="selin")
    o2, _, r2 = d.execute([sel], NOW)
    assert o2 or r2
    # limit emir ve dolum
    lim = team.a_kaan(ctx(inst="Y-USDT-SWAP", sym="Y", close=104.5, price=104.5, don_hi=101.0, vol_ratio=2.5, adx=28,
                          adx_prev=25, oi_chg24=6, taker_imb=0.1, bbw_rank=0.3), M["kaan"], CFG)
    _, orders, _ = d.execute([lim], NOW)
    assert len(orders) == 1 and L["orders"]
    t = d.fill_order(orders[0]["oid"], orders[0]["px"], NOW + 60_000)
    assert t["entry_src"] == "limit (maker)" and abs(t["fee_entry_usd"] - t["notional_usd"] * 0.0002) < 1e-9
    _, orders, _ = d.execute([dict(lim, inst="Z-USDT-SWAP", sym="Z", mgr="kemal")], NOW)   # Kaan teminatını kullandı
    assert d.expire_orders(NOW + 10 * 86400_000) and not L["orders"]
    # firma toplam risk limiti
    d2, _, L2 = _desk()
    many = [dict(idea, inst=f"C{i}-USDT-SWAP", sym=f"C{i}", mgr=mid) for i, mid in
            enumerate(["kaan", "selin", "kemal", "deniz", "ayse", "murat"] * 4)]
    o, _, r = d2.execute(many, NOW)
    reasons = {w for _, w in r}
    assert any("yönü riski" in w or "toplam risk" in w for w in reasons), reasons
    heat = d2.heat()
    assert heat <= 1000 * 0.10 + 1e-6 and d2.side_risk("long") <= 1000 * 0.06 + 1e-6


def test_bench_shadow_and_return():
    d, st, L = _desk()
    st["desk"]["peak"]["kemal"] = 1000 / 7
    st["desk"]["adj"]["kemal"] = -40           # sermayeyi düşür → zirveden %28 düşüş
    msgs = d.manager_tick(NOW)
    assert d.benched("kemal") and any("yedek" in m for m in msgs)
    sig = {"setup": "trend_long", "side": "long", "score": 80, "label": "Trend Long"}
    idea = team.a_kemal(ctx(tf="4H", bar_ms=14_400_000, signals=[sig]), M["kemal"], CFG)
    opened, _, _ = d.execute([idea], NOW, halted="günlük kayıp limiti")
    assert opened and opened[0]["shadow"], "yedekteki yönetici devre kesicide bile gölgede işlem yapar"
    assert lg.balance(L, CFG)["in_use"] == 0, "gölge işlem firma bakiyesini etkilemez"
    # gölgede 10 kazançlı işlem → geri dönüş
    for i in range(10):
        L["closed"].append({"mgr": "kemal", "shadow": True, "r": 0.5, "exit_ts": NOW + i, "pnl_usd": 1.0})
    msgs = d.manager_tick(NOW + 100)
    assert not d.benched("kemal") and any("döndü" in m for m in msgs)


def test_rebalance_conserves_capital():
    d, st, L = _desk()
    for i in range(20):
        L["closed"].append({"mgr": "kaan", "r": 0.4, "exit_ts": NOW - 1000 + i, "pnl_usd": 1.0, "pnl_pct": 1, "hours": 1})
        L["closed"].append({"mgr": "deniz", "r": -0.3, "exit_ts": NOW - 1000 + i, "pnl_usd": -0.5, "pnl_pct": -1, "hours": 1})
    feq = d.firm_equity()
    st["desk"]["last_rebalance"] = NOW - 8 * 86400_000
    txt = d.maybe_rebalance(NOW)
    assert txt and abs(d.firm_equity() - feq) < 1e-6
    assert d.equity("kaan") > d.equity("kemal") > d.equity("deniz")


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
    # funding kesimi: short vadeli pozitif funding alır
    pays = lg.apply_funding(L, "BTC-USDT-SWAP", 0.0002, NOW + 1000, 80040.0)
    assert pays and pays[0][1] < 0 and t["funding_usd"] < 0
    lg.mark(L, {"BTC-USDT-SWAP": 80100.0, "BTC-USDT": 80060.0}, CFG)
    c = lg.close_trade(L["open"].pop(), 80100.0, NOW + 3600_000, "süre", CFG)
    basis = qty * (80060 - 80000) + qty * (80040 - 80100)
    exp = basis - t["fee_entry_usd"] - c["fee_exit_usd"] + qty * 80040 * 0.0002
    assert abs(c["pnl_usd"] - exp) < 1e-4, (c["pnl_usd"], exp)
    # çıkış: funding iki kez negatif
    t2 = lg.build_carry("elif", h["inst"], "BTC", "BTC-USDT", perp, spot, qty, CFG, NOW, h)
    t2["neg_count"] = 2
    L["open"].append(t2)
    _, to_close = d.carry_plan(hv, prices, NOW)
    assert to_close and "negatif" in to_close[0][1]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
