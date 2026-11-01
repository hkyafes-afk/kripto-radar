"""İzole Bitsgap COMBO PAPER kuralları için davranış testleri."""
from __future__ import annotations

import time

import pytest

from radar.unified.combo.config import ComboConfig
from radar.unified.combo.grid import GridBuilder
from radar.unified.combo.models import ComboQuote
from radar.unified.combo.paper import ComboPaperAdapter
from radar.unified.combo.position import LongLot
from radar.unified.combo.strategy import ComboStrategy
from radar.unified.combo.feed import ComboWebSocketListener
from radar.unified.combo.instrument import OKXInstrumentMetadata

NOW = 1_800_000_000_000


def quote(last=78_000.0, *, bid=None, ask=None, bid_size=0.05, ask_size=0.05, ts=NOW):
    bid = last - 0.1 if bid is None else bid
    ask = last + 0.1 if ask is None else ask
    return ComboQuote("BTC-USDT-SWAP", bid, ask, last, ts, bid_size, ask_size)


def test_confirmed_combo_parameters_and_safety_lock():
    cfg = ComboConfig()
    assert cfg.low_price == 72_426.7
    assert cfg.high_price == 83_608.5
    assert cfg.grid_intervals == 24
    assert cfg.order_qty_btc == 0.013
    assert cfg.take_profit_pct == 0.015
    assert cfg.paper_capital_usdt == 2_500
    assert cfg.leverage_simulation == 10
    assert cfg.execution_mode == "paper"
    assert not cfg.private_api_enabled and not cfg.live_order_submission
    assert not cfg.allow_short and not cfg.allow_hedge
    with pytest.raises(ValueError):
        ComboConfig(execution_mode="live")
    with pytest.raises(ValueError):
        ComboConfig(private_api_enabled=True)
    with pytest.raises(ValueError):
        ComboConfig(allow_short=True)


def test_grid_has_25_lines_and_24_percent_steps():
    cfg = ComboConfig()
    grid = GridBuilder(cfg)
    assert len(grid.levels) == 25
    assert grid.levels[0].price == cfg.low_price
    assert grid.levels[-1].price == cfg.high_price
    steps = [b.price / a.price - 1 for a, b in zip(grid.levels, grid.levels[1:])]
    assert all(abs(step - 0.006) < 2e-5 for step in steps)


def test_stale_or_duplicate_ticks_never_create_extra_fills():
    strategy = ComboStrategy()
    stale = strategy.on_quote(quote(ts=NOW - 20_000), now_ms=NOW)
    assert stale["status"] == "stale_quote"
    assert not strategy.paper.initialized

    first = strategy.on_quote(quote(ts=NOW), now_ms=NOW)
    assert first["status"] == "base_opened"
    count = len(strategy.paper.fills)
    duplicate = strategy.on_quote(quote(ts=NOW), now_ms=NOW + 1)
    assert duplicate["status"] == "duplicate_quote"
    assert len(strategy.paper.fills) == count


def test_base_order_dca_average_tp_and_paired_sell_lifecycle():
    strategy = ComboStrategy()
    first = quote()
    result = strategy.on_quote(first, now_ms=NOW)
    assert result["status"] == "base_opened"
    assert strategy.paper.position.qty == pytest.approx(0.013)
    assert result["capital_usdt"] == 2_500
    assert result["leverage_simulation"] == 10
    assert result["active_orders"] >= 1
    assert strategy.paper.tp_order["reduce_only"] is True

    before_avg = strategy.paper.position.avg_entry_price
    before_tp = strategy.paper.tp_order["price"]
    buy = max(
        (o for o in strategy.paper.orders.values() if o.active and o.side == "buy"),
        key=lambda o: o.price,
    )
    drop = quote(
        last=buy.price - 0.15, bid=buy.price - 0.2, ask=buy.price - 0.1,
        bid_size=0.05, ask_size=0.013, ts=NOW + 1_000,
    )
    result = strategy.on_quote(drop, now_ms=NOW + 1_000)
    assert result["status"] == "active"
    assert strategy.paper.position.qty == pytest.approx(0.026)
    assert strategy.paper.position.avg_entry_price < before_avg
    assert strategy.paper.tp_order["price"] < before_tp
    assert strategy.paper.tp_order["qty"] == pytest.approx(0.026)
    new_lot = next(l for l in strategy.paper.position.lots.values()
                   if l.level_index == buy.level_index)
    paired = [o for o in strategy.paper.orders.values()
              if o.active and o.side == "sell" and o.lot_id == new_lot.lot_id]
    assert len(paired) == 1
    assert paired[0].qty == pytest.approx(0.013)
    assert paired[0].reduce_only is True
    assert paired[0].level_index == buy.level_index + 1


def test_sell_grid_realizes_lot_pnl_and_rearms_lower_buy():
    strategy = ComboStrategy()
    strategy.on_quote(quote(), now_ms=NOW)
    buy = max((o for o in strategy.paper.orders.values() if o.active and o.side == "buy"),
              key=lambda o: o.price)
    strategy.on_quote(
        quote(last=buy.price - 0.15, bid=buy.price - 0.2, ask=buy.price - 0.1,
              ask_size=0.013, ts=NOW + 1_000),
        now_ms=NOW + 1_000,
    )
    dca_lot = next(l for l in strategy.paper.position.lots.values()
                   if l.level_index == buy.level_index)
    paired = next(o for o in strategy.paper.orders.values()
                  if o.active and o.side == "sell" and o.lot_id == dca_lot.lot_id)
    base_sell = next(o for o in strategy.paper.orders.values()
                     if o.active and o.side == "sell" and o.role == "sell_grid"
                     and o.lot_id != dca_lot.lot_id)
    sell_level = max(paired.price, base_sell.price)
    result = strategy.on_quote(
        quote(last=sell_level, bid=sell_level, ask=sell_level + 0.2,
              bid_size=0.026, ask_size=0.05, ts=NOW + 2_000),
        now_ms=NOW + 2_000,
    )
    assert result["status"] == "active"
    assert strategy.paper.realized_gross_usdt > 0
    assert strategy.paper.position.qty == pytest.approx(0.0)
    assert any(o.active and o.side == "buy" for o in strategy.paper.orders.values())
    assert all(o.reduce_only for o in strategy.paper.orders.values() if o.side == "sell")


def test_main_tp_closes_all_and_cancels_grid_orders():
    strategy = ComboStrategy()
    strategy.on_quote(quote(), now_ms=NOW)
    tp = strategy.paper.tp_order["price"]
    result = strategy.on_quote(
        quote(last=tp + 0.1, bid=tp, ask=tp + 0.2, bid_size=0.013, ts=NOW + 1_000),
        now_ms=NOW + 1_000,
    )
    assert result["status"] == "tp_triggered"
    assert result["position_qty_btc"] == pytest.approx(0.0)
    assert result["halted"] is True
    assert result["halt_reason"] == "MAIN_TP"
    assert result["tp_order"]["status"] == "filled"
    assert result["active_orders"] == 0


def test_hard_stop_cancels_grid_and_never_opens_short():
    strategy = ComboStrategy()
    strategy.on_quote(quote(), now_ms=NOW)
    stop = strategy.cfg.hard_stop_price
    result = strategy.on_quote(
        quote(last=stop - 1, bid=stop - 1.1, ask=stop - 0.9,
              bid_size=0.013, ts=NOW + 1_000),
        now_ms=NOW + 1_000,
    )
    assert result["status"] == "halted_hard_stop"
    assert strategy.paper.position.qty == pytest.approx(0.0)
    assert strategy.paper.halted
    assert result["active_orders"] == 0
    assert all(lot.qty >= 0 for lot in strategy.paper.position.lots.values())


def test_reduce_only_is_checked_at_submission_and_fill():
    cfg = ComboConfig()
    paper = ComboPaperAdapter(cfg)
    paper.position.add(LongLot("only-lot", 78_000, 0.013, 10, NOW))
    rejected = paper.place_limit(
        side="sell", price=78_500, qty=0.014, role="sell_grid", level_index=11,
        lot_id="only-lot", reduce_only=True, now_ms=NOW,
    )
    assert rejected is None
    allowed = paper.place_limit(
        side="sell", price=78_500, qty=0.013, role="sell_grid", level_index=11,
        lot_id="only-lot", reduce_only=True, now_ms=NOW,
    )
    assert allowed is not None
    assert paper.position.qty == pytest.approx(0.013)
    fills = paper.match_limit_orders(
        quote(last=78_500, bid=78_500, ask=78_500.2, bid_size=0.013, ts=NOW + 1),
        NOW + 1,
    )
    assert sum(fill.qty for fill in fills) == pytest.approx(0.013)
    assert paper.position.qty == pytest.approx(0.0)
    assert paper.position.qty >= 0


def test_margin_reservations_are_isolated_and_bounded():
    strategy = ComboStrategy()
    result = strategy.on_quote(quote(), now_ms=NOW)
    assert result["used_margin_usdt"] > 0
    assert result["reserved_buy_margin_usdt"] > 0
    assert result["free_margin_usdt"] > 0
    assert result["equity_usdt"] <= 2_500
    assert result["private_api_enabled"] is False
    assert result["live_order_submission"] is False


def test_paper_state_recovers_without_crossing_unified_ledger(tmp_path):
    cfg = ComboConfig()
    one = ComboStrategy(cfg, root=tmp_path)
    one.on_quote(quote(), now_ms=NOW)
    qty = one.paper.position.qty
    orders = one.paper.snapshot()["active_orders"]

    two = ComboStrategy(cfg, root=tmp_path)
    assert two.paper.initialized
    assert two.paper.position.qty == pytest.approx(qty)
    assert two.paper.snapshot()["active_orders"] == orders
    assert two.paper.snapshot()["capital_usdt"] == 2_500
    assert two.paper.last_quote_ts_ms == NOW
    duplicate = two.on_quote(quote(ts=NOW), now_ms=NOW + 2)
    assert duplicate["status"] == "duplicate_quote"


def test_corrupt_or_mismatched_combo_ledger_fails_closed(tmp_path):
    cfg = ComboConfig()
    path = tmp_path / "combo_paper.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError):
        ComboPaperAdapter(cfg, root=tmp_path)

    path.unlink()
    paper = ComboPaperAdapter(cfg, root=tmp_path)
    paper.save()
    changed = ComboConfig(order_qty_btc=0.012)
    with pytest.raises(RuntimeError):
        ComboPaperAdapter(changed, root=tmp_path)


def test_okx_contract_metadata_normalizes_contracts_to_btc():
    meta = OKXInstrumentMetadata(
        inst_id="BTC-USDT-SWAP", contract_type="linear", contract_value=0.01,
        contract_value_ccy="BTC", tick_size=0.1, lot_size_contracts=0.01,
        min_size_contracts=0.01, state="live",
    )
    assert meta.contracts_to_btc(5.0) == pytest.approx(0.05)
    assert meta.btc_to_contracts(0.013) == pytest.approx(1.3)
    with pytest.raises(ValueError):
        OKXInstrumentMetadata(
            inst_id="BTC-USDT-SWAP", contract_type="inverse", contract_value=100,
            contract_value_ccy="USD", tick_size=0.1, lot_size_contracts=1,
            min_size_contracts=1, state="live",
        )


def test_websocket_listener_consumes_contract_size_and_converts_to_btc():
    class FakeFeed:
        def __init__(self):
            self.callback = None
            self.symbols = []
        def add_tick_listener(self, callback):
            self.callback = callback
        def set_symbols(self, symbols):
            self.symbols = symbols
        def status(self):
            return {"healthy": True, "connected": True}

    strategy = ComboStrategy()
    feed = FakeFeed()
    meta = OKXInstrumentMetadata(
        inst_id="BTC-USDT-SWAP", contract_type="linear", contract_value=0.01,
        contract_value_ccy="BTC", tick_size=0.1, lot_size_contracts=0.01,
        min_size_contracts=0.01, state="live",
    )
    listener = ComboWebSocketListener(strategy, feed=feed, instrument_meta=meta)
    listener.start()
    assert feed.symbols == ["BTC-USDT-SWAP"]
    now = int(time.time() * 1000)
    feed.callback({
        "exchange": "okx", "inst": "BTC-USDT-SWAP", "last": 78_000.0,
        "bid": 77_999.9, "ask": 78_000.1, "bid_size_contracts": 5,
        "ask_size_contracts": 5, "size_unit": "contracts_for_swap", "ts_ms": now,
    })
    assert listener.last_error is None
    assert listener.last_result["status"] == "base_opened"
    assert strategy.paper.position.qty == pytest.approx(0.013)
    assert listener.status()["execution_mode"] == "PAPER_ONLY"
