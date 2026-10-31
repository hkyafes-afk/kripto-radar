from __future__ import annotations

import threading

from radar.engine import Engine


def _engine():
    e = Engine.__new__(Engine)
    e.cfg = {"birlesik": {"karar_modu": "unified"}}
    e.lock = threading.RLock()
    e.pending_open = [{"inst": "X-USDT-SWAP"}]
    e.pending_ideas = [{"mgr": "kemal", "sym": "X", "tf": "1D", "side": "long"}]
    e.order_hits = {"legacy-order": (100.0, 1)}
    e.ledger = {
        "orders": [{"oid": "legacy-order", "mgr": "kemal", "idea": {"sym": "X", "tf": "1D", "side": "long"}}],
        "open": [], "closed": []
    }
    e.desk = None
    e.order_watch = {}
    e.dirty = False
    e.stats_counter = {}
    e.pending_carry = []
    e.rebuild_watch = lambda: None
    return e


def test_unified_mod_legacy_market_entries_are_blocked():
    e = _engine()
    assert e.open_positions([{"inst": "X-USDT-SWAP"}]) == []
    assert e.pending_open == []
    assert e.pending_ideas == []
    assert e.order_hits == {}
    assert e.ledger["orders"] == []


def test_unified_mod_legacy_manager_ideas_are_challengers_not_entries():
    e = _engine()
    e.process_ideas()
    assert e.pending_ideas == []
    assert e.stats_counter["legacy_blocked"] == 1
    assert e.ledger["open"] == []


def test_unified_mod_cancels_legacy_pending_order_before_fill():
    e = _engine()
    e.process_orders()
    assert e.ledger["orders"] == []
    assert e.order_hits == {}
    assert e.ledger["open"] == []


def test_unified_mod_keeps_legacy_carry_closes_but_blocks_new_carry():
    e = _engine()
    candidate = ({"inst": "X-USDT-SWAP", "sym": "X"}, 1.0, 1.0, 1.0, False)
    e.pending_carry = [([candidate], [])]
    e.process_carry()
    assert e.ledger["open"] == []
