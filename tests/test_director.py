"""v9 Faz 0 — Genel Müdür: çatışma hakemi, coin tavanı, kanıt sınıfı, IOC limit, gölge oy, stop kayması, hız sayacı."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import director as gmd  # noqa: E402
from radar import ledger as lg  # noqa: E402
from radar import team  # noqa: E402
from radar import desk as dk  # noqa: E402
from tests.test_team import CFG, M, NOW, qctx  # noqa: E402

DAY = 86_400_000
OLD = lambda i: {"list_ts": NOW - 4 * 365 * DAY}          # noqa: E731  A sınıfı
MID = lambda i: {"list_ts": NOW - 2 * 365 * DAY}          # noqa: E731  B sınıfı
NEW = lambda i: {"list_ts": NOW - 60 * DAY}               # noqa: E731  C sınıfı


def _desk():
    st, L = {}, lg.empty()
    return dk.Desk(CFG, st, L), st, L


def _kaan(**kw):
    c = qctx(close=103.0, price=103.0, hh20=101.0, vol_ratio=2.2, close_pos=0.9, **kw)
    return team.a_kaan(c, M["kaan"], CFG)


def _kemal_short(inst="X-USDT-SWAP"):
    i = _kaan()
    return dict(i, mgr="kemal", side="short", sl=110.0, tp=80.0, tp1=None, inst=inst, shadow_only=False)


def test_coin_class_and_sizing():
    fc = dk.firm_cfg(CFG)
    assert gmd.coin_class(OLD("x"), NOW, fc) == "A"
    assert gmd.coin_class(MID("x"), NOW, fc) == "B"
    assert gmd.coin_class(NEW("x"), NOW, fc) == "C"
    assert gmd.coin_class({"ct_val": 1}, NOW, fc) == "B", "listeleme tarihi yoksa temkinli"
    d, _, _ = _desk()
    idea = _kaan()
    _, a, _ = d.evaluate(idea, NOW, meta=OLD)
    _, b, _ = d.evaluate(idea, NOW, meta=MID)
    _, c, _ = d.evaluate(idea, NOW, meta=NEW)
    assert not a["shadow"] and a["coin_class"] == "A"
    assert not b["shadow"] and abs(b["risk_pct"] - a["risk_pct"] * 0.5) < 0.05, "B sınıfı yarım boyut"
    assert c["shadow"] and any("kanıt sınıfı" in g for g in c["gm"]), "C sınıfı gölgede"


def test_conflict_referee_blocks_opposite_real_position():
    d, st, L = _desk()
    o, _, _ = d.execute([_kaan()], NOW, meta=OLD)
    assert o and not o[0]["shadow"] and o[0]["side"] == "long"
    k, det, _ = d.evaluate(_kemal_short(), NOW, meta=OLD)
    assert det["shadow"] and any("çatışma hakemi" in g for g in det["gm"]), "ters gerçek pozisyon açılmamalı"
    # farklı coinde serbest
    k, det, _ = d.evaluate(_kemal_short("Y-USDT-SWAP"), NOW, meta=OLD)
    assert not det["shadow"]
    # Elif'in hedge'li çifti çatışma sayılmaz
    L["open"] = [dict(o[0], mgr="elif", carry=True, side="short", risk_usd=0.0)]
    k, det, _ = d.evaluate(dict(_kaan(), mgr="selin"), NOW, meta=OLD)
    assert not det["shadow"]


def test_coin_cap_resizes_then_shadows():
    d, st, L = _desk()
    cap = d.firm_equity() * d.fc["coin_tavan_yuzde"] / 100
    first = _kaan()
    _, det, _ = d.evaluate(first, NOW, meta=OLD)
    want = det["risk_usd"]
    # aynı coinde kalan yerin yarısı kadar mevcut risk → küçültme
    L["open"].append({"mgr": "kemal", "inst": first["inst"], "sym": "X", "side": "long", "entry": 100.0,
                      "sl": 100.0 - (cap - want * 0.5) / 10, "qty": 10.0, "qty_open": 10.0, "shadow": False, "real": True,
                      "fee_entry_usd": 0.0})
    _, det2, _ = d.evaluate(first, NOW, meta=OLD)
    assert not det2["shadow"] and det2["risk_usd"] < want * 0.6 and any("küçültüldü" in g for g in det2["gm"])
    # tavan dolu → gölge
    L["open"][-1]["sl"] = 100.0 - cap / 10
    _, det3, _ = d.evaluate(first, NOW, meta=OLD)
    assert det3["shadow"] and any("tavanı dolu" in g for g in det3["gm"])


def test_ioc_band_partial_fill_and_reject():
    d, _, _ = _desk()
    idea = _kaan()
    q = lambda i: (102.99, 103.0, 103.0)                       # noqa: E731
    deep = lambda i: {"asks": [[103.0, 1e6]], "bids": [[102.99, 1e6]]}   # noqa: E731
    k, det, _ = d.evaluate(idea, NOW, quote=q, meta=OLD, book=deep)
    assert k == "market"
    full = det["qty"]
    half = lambda i: {"asks": [[103.0, full * 0.6], [103.5, 1e6]], "bids": [[102.99, 1e6]]}   # noqa: E731
    k, det2, _ = d.evaluate(idea, NOW, quote=q, meta=OLD, book=half)
    assert k == "market" and abs(det2["qty"] - full * 0.6) < 1e-6 * full, "bant dışı seviye kullanılmaz"
    assert det2["entry"] <= 103.0 * 1.001 and any("IOC kısmi" in g for g in det2["gm"])
    thin = lambda i: {"asks": [[103.0, full * 0.2], [104.0, 1e6]], "bids": [[102.99, 1e6]]}   # noqa: E731
    k, _, why = d.evaluate(idea, NOW, quote=q, meta=OLD, book=thin)
    assert k == "red" and "IOC" in why


def test_consensus_ignores_shadow_votes():
    d, st, L = _desk()
    base = {"inst": "Q-USDT-SWAP", "sym": "Q", "side": "long", "entry": 100.0, "sl": 95.0, "atr": 3.0, "qty": 1.0}
    L["open"] = [dict(base, mgr="kemal", shadow=False), dict(base, mgr="murat", shadow=True)]
    to_open, _ = d.consensus_plan(NOW, {"Q-USDT-SWAP": 101.0})
    assert not to_open, "gölgedeki oy uzlaşı sayılmaz"
    L["open"].append(dict(base, mgr="selin", shadow=False))
    to_open, _ = d.consensus_plan(NOW, {"Q-USDT-SWAP": 101.0})
    assert to_open and set(to_open[0]["consensus"]) == {"kemal", "selin"}


def test_benched_managers_trade_in_shadow():
    d, _, _ = _desk()
    for mid in ("murat", "ayse", "deniz"):
        assert d.segment(mid, "long") == "golge" and d.segment(mid, "short") == "golge"
    assert d.segment("kemal", "long") is None and d.segment("kaan", "long") is None


def test_dynamic_stop_slippage():
    fc = lg.fees_cfg(CFG)
    calm = gmd_t(atr=2.5, entry=100.0)
    wild = gmd_t(atr=8.0, entry=100.0)
    young = gmd_t(atr=8.0, entry=100.0, coin_class="C")
    a, b, c = lg.stop_slip_bp(calm, fc), lg.stop_slip_bp(wild, fc), lg.stop_slip_bp(young, fc)
    assert 15 <= a <= 20 and 40 <= b <= 50 and c > b and c <= 150
    assert abs(lg.stop_slip_bp(gmd_t(atr=0.25, entry=100.0, tf="15m"), fc) - a) < 2, "√zaman ölçeği"


def gmd_t(**kw):
    t = {"entry": 100.0, "atr": 2.5, "tf": "1D"}
    t.update(kw)
    return t


def test_class_learning_promotes_c_after_proof():
    d, st, L = _desk()
    for i in range(30):
        L["closed"].append({"mgr": "kaan", "side": "long", "shadow": True, "coin_class": "C",
                            "r": 0.5 if i % 3 else -0.4, "exit_ts": NOW + i, "inst": f"C{i}"})
    assert gmd.class_state(d, "kaan", "C") == "golge"
    msgs = gmd.class_tick(d, L, NOW - 1, NOW + 100)
    assert gmd.class_state(d, "kaan", "C") == "yarim" and msgs
    _, det, _ = d.evaluate(_kaan(), NOW, meta=NEW)
    assert not det["shadow"] and abs(det["mults"]["sinif"] - 0.5) < 1e-9


def test_audit_and_perf():
    d, st, L = _desk()
    base = {"inst": "Q-USDT-SWAP", "sym": "Q", "entry": 100.0, "sl": 95.0, "qty": 1.0, "qty_open": 1.0, "real": True,
            "fee_entry_usd": 0.0}
    L["open"] = [dict(base, mgr="kemal", side="long", shadow=False), dict(base, mgr="murat", side="short", shadow=False)]
    a = gmd.audit(d, OLD, NOW)
    assert a["conflicts"] and a["conflicts"][0]["sym"] == "Q" and a["crowd"]
    p = gmd.PerfMeter()
    p.timed("x", lambda: sum(range(1000)))
    p.loop_done(0.01)
    r = p.report()
    assert "x" in r["parts"] and r["loop_last_s"] == 0.01


def test_rejected_ideas_are_tracked_in_shadow():
    d, st, L = _desk()
    d.env = {"cal_block": "olay takvimi: Fed faiz kararı (±60 dk)"}
    o, _, r = d.execute([_kaan()], NOW, meta=OLD)
    assert not o and r
    sh = [t for t in L["open"] if t.get("filtered")]
    assert sh and sh[0]["shadow"] and sh[0]["filter_kind"] == "olay takvimi"
    o, _, r = d.execute([_kaan()], NOW + 1, meta=OLD)
    assert len([t for t in L["open"] if t.get("filtered")]) == 1, "aynı fikir iki kez gölgeye yazılmaz"
    L["closed"].append(dict(sh[0], r=0.8, exit_ts=NOW + 5))
    a = gmd.audit(d, OLD, NOW)
    assert a["filters"] and a["filters"][0]["kind"] == "olay takvimi" and a["filters"][0]["n"] == 1


def test_variants_shared_by_live_and_backtest():
    import copy
    base = _kaan()
    cfg = copy.deepcopy(CFG)
    def var(v, ctx=None):
        c = dict(cfg, _variant={"kaan": v})
        return team.apply_variant(copy.deepcopy(base), ctx or qctx(close=103.0, price=103.0, hh20=101.0, low=100.5,
                                                                    high=103.5, adx=30.0), c)
    a = var(["cikis_iz"])
    assert a["tp1"] is None and a["pp"]["partial_frac"] == 0 and a["pp"]["trail_now"] and "Yarışı kazanan" in a["plan"]
    b = var(["cikis_hedef"])
    assert abs(b["tp"] - base["tp1"]) < 1e-9 and b["tp1"] is None and b["pp"]["trail_atr"] == 0
    c = var(["cikis_iz_tp1"])
    assert c["tp1"] == base["tp1"] and c["pp"]["partial_frac"] == 0 and c["pp"]["basabas"]
    r = var(["retest"])
    assert r["entry_type"] == "limit" and abs(r["limit_px"] - (101.0 + 0.1 * base["atr"])) < 1e-9 and r["entry"] == r["limit_px"]
    assert r["sl"] < r["limit_px"] and r["limit_valid_ms"] == 5 * 86_400_000
    f = var(["fitil_stop"])
    assert f["sl"] > base["sl"] and abs(f["tp"] - (f["entry"] + 6 * (f["entry"] - f["sl"]))) < 1e-9
    k = dict(cfg, _variant={"kemal": ["adx25"]})
    idea = team.a_kemal(qctx(above50_prev=False), M["kemal"], cfg)
    assert idea and team.apply_variant(copy.deepcopy(idea), qctx(adx=18.0), k) is None
    assert team.apply_variant(copy.deepcopy(idea), qctx(adx=31.0), k) is not None
    assert team.apply_variant(copy.deepcopy(base), qctx(), cfg) == base, "varyant yoksa fikir aynen kalır"


def test_committee_trust_family_and_contribution():
    d, st, L = _desk()
    base = {"inst": "Q-USDT-SWAP", "sym": "Q", "side": "long", "entry": 100.0, "sl": 95.0, "atr": 3.0, "qty": 1.0,
            "shadow": False}
    L["open"] = [dict(base, mgr="kemal"), dict(base, mgr="kaan")]
    to_open, _ = d.consensus_plan(NOW, {"Q-USDT-SWAP": 101.0})
    assert not to_open, "iki trendcinin anlaşması yetmez (aynı aile)"
    assert any(j["action"] == "komite reddetti" for j in st["desk"]["journal"])
    L["open"].append(dict(base, mgr="selin"))
    to_open, _ = d.consensus_plan(NOW, {"Q-USDT-SWAP": 101.0})
    i = to_open[0]
    assert set(i["contrib"]) == {"kemal", "kaan", "selin"} and abs(i["notional_target"] - 3 * 500.0) < 1e-6
    _, det, _ = d.evaluate(i, NOW, meta=OLD)
    assert abs(det["qty"] * det["entry"] - 1500.0) < 1.0, "pozisyon = katkıların toplamı (3 × %5 × 10.000$)"
    # kapanışta kâr/zarar katkı payına göre dağıtılır
    o, _, _ = d.execute([i], NOW, meta=OLD)
    t = o[0]
    tr = next(x for x in L["open"] if x.get("id") == t["id"])
    L["open"].remove(tr)
    tr = lg.close_trade(tr, 110.0, NOW + 1000, "hedef", CFG, maker=True)
    L["closed"].append(tr)
    eq0 = {m: d.equity(m) for m in ("kemal", "kaan", "selin", "ortak")}
    d.on_closed([tr])
    assert tr["distributed"] and abs(sum(tr["distributed"].values()) - tr["pnl_usd"]) < 0.05
    assert abs(d.equity("ortak") - (eq0["ortak"] - tr["pnl_usd"])) < 1e-6
    assert abs(d.equity("kaan") - eq0["kaan"] - tr["pnl_usd"] / 3) < 0.02
    d.on_closed([tr])
    assert abs(d.equity("ortak") - (eq0["ortak"] - tr["pnl_usd"])) < 1e-6, "iki kez dağıtılmaz"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
