from __future__ import annotations

from radar.unified.carry import CarryPairPaper
from radar.unified.execution.base import Quote

NOW = 1_800_000_000_000
CFG = {"ucret": {"spot_taker_yuzde": 0.1, "taker_yuzde": 0.06},
       "birlesik": {"elif_carry": {"enabled": True, "max_notional_usd": 500,
                                    "slippage_bp": 1, "min_projected_net_usd": 0.0}}}


def q(inst, bid=99.99, ask=100.01, ts=NOW):
    return Quote(inst=inst, bid=bid, ask=ask, last=(bid + ask) / 2, ts_ms=ts, asks=((ask, 10.0),), bids=((bid, 10.0),))


def test_carry_disabled_by_default_and_enabled_requires_positive_edge(tmp_path):
    disabled = CarryPairPaper({}, tmp_path / "off")
    r = disabled.open_pair(pair_id="p1", symbol="BTC", spot_inst="BTC-USDT", perp_inst="BTC-USDT-SWAP",
                           qty=1, spot=q("BTC-USDT"), perp=q("BTC-USDT-SWAP"), funding_rate=0.002,
                           funding_interval_h=8, expected_holding_hours=24, now_ms=NOW)
    assert r["status"] == "blocked" and r["reason"] == "elif_carry_disabled"
    a = CarryPairPaper(CFG, tmp_path / "on")
    r = a.open_pair(pair_id="p1", symbol="BTC", spot_inst="BTC-USDT", perp_inst="BTC-USDT-SWAP",
                    qty=1, spot=q("BTC-USDT"), perp=q("BTC-USDT-SWAP"), funding_rate=0.002,
                    funding_interval_h=8, expected_holding_hours=24, now_ms=NOW)
    assert r["status"] == "open" and r["spot_qty"] == r["perp_qty"] == 1
    assert a.reconcile()["ok"]


def test_carry_pair_is_idempotent_and_persistent(tmp_path):
    a = CarryPairPaper(CFG, tmp_path)
    kwargs = dict(pair_id="p1", symbol="BTC", spot_inst="BTC-USDT", perp_inst="BTC-USDT-SWAP",
                  qty=1, spot=q("BTC-USDT"), perp=q("BTC-USDT-SWAP"), funding_rate=0.002,
                  funding_interval_h=8, expected_holding_hours=24, now_ms=NOW)
    one = a.open_pair(**kwargs)
    two = a.open_pair(**kwargs)
    b = CarryPairPaper(CFG, tmp_path)
    assert one["spot_entry"] == two["spot_entry"] == b.positions["p1"]["spot_entry"]
    assert len(b.positions) == 1


def test_carry_one_stale_leg_rejects_both_without_partial_exposure(tmp_path):
    a = CarryPairPaper(CFG, tmp_path)
    r = a.open_pair(pair_id="p1", symbol="BTC", spot_inst="BTC-USDT", perp_inst="BTC-USDT-SWAP",
                    qty=1, spot=q("BTC-USDT", ts=NOW-10_000), perp=q("BTC-USDT-SWAP"),
                    funding_rate=0.002, funding_interval_h=8, expected_holding_hours=24, now_ms=NOW)
    assert r["status"] == "rejected" and not a.positions and not a.path.exists()


def test_carry_funding_sign_and_four_leg_close_accounting(tmp_path):
    a = CarryPairPaper(CFG, tmp_path)
    a.open_pair(pair_id="p1", symbol="BTC", spot_inst="BTC-USDT", perp_inst="BTC-USDT-SWAP",
                qty=1, spot=q("BTC-USDT"), perp=q("BTC-USDT-SWAP"), funding_rate=0.002,
                funding_interval_h=8, expected_holding_hours=24, now_ms=NOW)
    received = a.apply_funding("p1", rate=0.001, perp_mark=100, ts_ms=NOW+8*3_600_000)
    paid = a.apply_funding("p1", rate=-0.001, perp_mark=100, ts_ms=NOW+16*3_600_000)
    assert received == 0.1 and paid == -0.1
    closed = a.close_pair("p1", spot=q("BTC-USDT"), perp=q("BTC-USDT-SWAP"), now_ms=NOW+24*3_600_000)
    assert closed["status"] == "closed" and closed["spot_qty"] == closed["perp_qty"] == 0
    assert closed["net_usd"] < 0  # round-trip fees/spread are charged
    assert a.reconcile()["ok"] and not a.positions and len(a.closed) == 1


def test_carry_close_fails_closed_if_one_leg_quote_is_missing(tmp_path):
    a = CarryPairPaper(CFG, tmp_path)
    a.open_pair(pair_id="p1", symbol="BTC", spot_inst="BTC-USDT", perp_inst="BTC-USDT-SWAP",
                qty=1, spot=q("BTC-USDT"), perp=q("BTC-USDT-SWAP"), funding_rate=0.002,
                funding_interval_h=8, expected_holding_hours=24, now_ms=NOW)
    r = a.close_pair("p1", spot=q("BTC-USDT"), perp=None, now_ms=NOW+1000)
    assert r["status"] == "blocked" and "p1" in a.positions



def test_elif_strategy_requires_fresh_two_leg_and_30d_positive_funding():
    from radar.unified.carry import ElifCarryStrategy
    s = ElifCarryStrategy(CFG)
    kwargs = dict(symbol="BTC", spot=q("BTC-USDT"), perp=q("BTC-USDT-SWAP"),
                  funding_rate=0.002, funding_ts_ms=NOW, funding_30d_avg=0.001, positive_funding_ratio=0.9,
                  history_ts_ms=NOW, funding_interval_h=8, now_ms=NOW)
    result = s.evaluate_pair(**kwargs)
    assert result["status"] == "eligible"
    assert result["direction"] == "spot_long_perp_short"
    assert result["paper_execution_required"] is True
    stale = s.evaluate_pair(**{**kwargs, "history_ts_ms": NOW-30*3_600_000})
    assert stale["status"] == "blocked" and "stale" in stale["reason"]
    negative = s.evaluate_pair(**{**kwargs, "funding_rate": -0.001})
    assert negative["status"] == "rejected"



def test_carry_never_assumes_fills_without_both_orderbook_depths(tmp_path):
    from radar.unified.execution.base import Quote
    a = CarryPairPaper(CFG, tmp_path)
    spot = Quote("BTC-USDT", 99.99, 100.01, 100.0, NOW)
    r = a.open_pair(pair_id="p1", symbol="BTC", spot_inst="BTC-USDT", perp_inst="BTC-USDT-SWAP",
                    qty=1, spot=spot, perp=q("BTC-USDT-SWAP"), funding_rate=0.002,
                    funding_interval_h=8, expected_holding_hours=24, now_ms=NOW)
    assert r["status"] == "rejected"
    assert r["reason"] == "entry_orderbook_depth_insufficient_or_missing"
    assert not a.positions
