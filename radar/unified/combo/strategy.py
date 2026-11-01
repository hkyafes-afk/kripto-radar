"""Bitsgap tarzı tek COMBO: alt seviyede DCA BUY, üst seviyede reduce-only GRID SELL."""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from .config import ComboConfig
from .grid import GridBuilder
from .models import ComboOrderStatus, ComboQuote
from .paper import ComboPaperAdapter


class ComboStrategy:
    def __init__(self, cfg: ComboConfig | None = None, root: str | Path | None = None,
                 paper: ComboPaperAdapter | None = None):
        self.cfg = cfg or ComboConfig()
        self.grid = GridBuilder(self.cfg)
        self.paper = paper or ComboPaperAdapter(self.cfg, root=root)
        self.last_data_issue: str | None = None

    def _tp_price(self) -> float | None:
        avg = self.paper.position.avg_entry_price
        if avg is None:
            return None
        raw = avg * (1.0 + self.cfg.take_profit_pct)
        return math.ceil(raw / self.cfg.price_tick) * self.cfg.price_tick

    def _refresh_tp(self, now_ms: int) -> None:
        qty = self.paper.position.qty
        if qty <= 1e-12:
            self.paper.tp_order = None
        elif not self.paper.tp_triggered:
            avg = self.paper.position.avg_entry_price
            self.paper.tp_order = {
                "order_type": "TAKE_PROFIT_LIMIT", "side": "sell", "reduce_only": True,
                "avg_entry_price": avg, "price": self._tp_price(), "qty": qty,
                "updated_ts_ms": now_ms, "status": "active",
            }
        elif self.paper.tp_order:
            # TP tetiklendikten sonra grid emirleri iptal edilir; kalan miktar aynı limitte bekler.
            self.paper.tp_order["qty"] = qty
            self.paper.tp_order["status"] = "partially_filled" if qty > 0 else "filled"
        self.paper.save()

    def _arm_sell_for_lot(self, lot_id: str, buy_level_index: int | None, now_ms: int) -> None:
        if buy_level_index is None:
            level = self.grid.above(self.paper.position.lots[lot_id].entry_price)
        else:
            level = self.grid.next_above(buy_level_index)
        if level is None:
            self.paper._event("grid_edge", "Lot için üst SELL grid çizgisi bulunmuyor", now_ms,
                              qty=self.paper.position.available_qty(lot_id))
            return
        lot_qty = self.paper.position.available_qty(lot_id)
        self.paper.place_limit(
            side="sell", price=level.price, qty=lot_qty, role="sell_grid",
            level_index=level.index, lot_id=lot_id, reduce_only=True, now_ms=now_ms,
        )

    def _arm_buy_level(self, level_index: int, now_ms: int,
                       parent_order_id: str | None = None) -> None:
        level = self.grid.by_index(level_index)
        if level is None or (parent_order_id is None and self.paper.has_active_buy_at(level_index)):
            return
        role = "sell_counter_buy" if parent_order_id else "dca_buy"
        self.paper.place_limit(
            side="buy", price=level.price, qty=self.cfg.order_qty_btc, role=role,
            level_index=level.index, lot_id=None, reduce_only=False, now_ms=now_ms,
            parent_order_id=parent_order_id,
        )

    def _seed_grid(self, quote: ComboQuote, base_lot_id: str, now_ms: int,
                   anchor_price: float | None = None) -> None:
        anchor = anchor_price if anchor_price is not None else quote.last
        for level in reversed(self.grid.below(anchor)):
            self._arm_buy_level(level.index, now_ms)
        above = self.grid.above(anchor)
        if above is not None:
            self._arm_sell_for_lot(base_lot_id, None, now_ms)

    def _reconcile_order_intents(self, now_ms: int) -> bool:
        """Recover a crash between fill persistence and its paired counter-order write."""
        if self.paper.halted or self.paper.tp_triggered:
            return False
        changed = False
        for lot in list(self.paper.position.lots.values()):
            if not self.paper.has_active_sell_for_lot(lot.lot_id):
                self._arm_sell_for_lot(lot.lot_id, lot.level_index, now_ms)
                changed = True
        for order in list(self.paper.orders.values()):
            if (order.side == "sell" and order.role == "sell_grid"
                    and order.status is ComboOrderStatus.FILLED and order.level_index is not None):
                has_child = any(
                    child.side == "buy" and child.parent_order_id == order.order_id
                    and child.status not in (child.status.CANCELLED, child.status.REJECTED)
                    for child in self.paper.orders.values()
                )
                if not has_child:
                    self._arm_buy_level(order.level_index - 1, now_ms,
                                        parent_order_id=order.order_id)
                    changed = True
        return changed

    def _tp_needs_refresh(self) -> bool:
        qty = self.paper.position.qty
        current = self.paper.tp_order
        if self.paper.tp_triggered:
            return False
        if qty <= 1e-12:
            return current is not None
        if current is None or current.get("status") != "active":
            return True
        avg = self.paper.position.avg_entry_price
        return (
            not math.isclose(float(current.get("qty", 0.0)), qty, rel_tol=0.0, abs_tol=1e-12)
            or avg is None
            or not math.isclose(float(current.get("avg_entry_price", 0.0)), avg,
                                rel_tol=0.0, abs_tol=1e-8)
        )

    def _process_counter_orders(self, fills, now_ms: int) -> None:
        for fill in fills:
            if fill.side == "buy":
                # BUY kısmi dolumlarının her biri yalnız kendi oluşan lotuna SELL bağlar.
                self._arm_sell_for_lot(fill.lot_id, fill.level_index, now_ms)
            elif fill.side == "sell" and fill.level_index is not None:
                # 0.013 BTC SELL tamamen dolmadan tam boy yeni BUY kurma; aksi halde
                # küçük bir partial fill, daha büyük karşı DCA hacmi açabilir.
                order = self.paper.orders.get(fill.order_id)
                if order is not None and order.status is ComboOrderStatus.FILLED:
                    self._arm_buy_level(fill.level_index - 1, now_ms,
                                        parent_order_id=fill.order_id)

    def _handle_hard_stop(self, quote: ComboQuote, now_ms: int) -> None:
        if not self.paper.halted:
            self.paper.halted = True
            self.paper.halt_reason = "HARD_STOP"
            self.paper.cancel_all_orders(now_ms, "hard stop tetiklendi; yeni grid girişi kapatıldı")
            self.paper.tp_order = None
        if self.paper.position.qty > 1e-12:
            self.paper.market_stop_reduce(quote, now_ms)
        self.paper.save()

    def _handle_tp(self, quote: ComboQuote, now_ms: int) -> None:
        if not self.paper.tp_triggered:
            self.paper.tp_triggered = True
            self.paper.cancel_all_orders(now_ms, "ana TP tetiklendi; bütün GRID/DCA emirleri iptal edildi")
            if self.paper.tp_order:
                self.paper.tp_order["status"] = "triggered"
        self.paper.limit_tp_reduce(quote, now_ms)
        if self.paper.position.qty <= 1e-12:
            self.paper.halted = True
            self.paper.halt_reason = "MAIN_TP"
            if self.paper.tp_order:
                self.paper.tp_order["qty"] = 0.0
                self.paper.tp_order["status"] = "filled"
        else:
            self._refresh_tp(now_ms)
        self.paper.save()

    def on_quote(self, quote: ComboQuote, now_ms: int | None = None) -> dict[str, Any]:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        if quote.symbol != self.cfg.symbol:
            return {"status": "ignored_symbol", **self.paper.snapshot()}
        if not quote.valid:
            self.last_data_issue = "BBO geçersiz"
            self.paper._event("quote_rejected", self.last_data_issue, now)
            self.paper.save()
            return {"status": "invalid_quote", **self.paper.snapshot()}
        age = now - quote.ts_ms
        if age < -1_000 or age > self.cfg.quote_max_age_ms:
            self.last_data_issue = f"quote yaş sınırını aştı: {age} ms"
            self.paper._event("quote_stale", self.last_data_issue, now)
            self.paper.save()
            return {"status": "stale_quote", **self.paper.snapshot()}
        if self.paper.last_quote_ts_ms is not None and quote.ts_ms <= self.paper.last_quote_ts_ms:
            self.paper._event("quote_duplicate", "Eski/tekrarlanan WebSocket tick'i işlenmedi", now)
            self.paper.save()
            return {"status": "duplicate_quote", **self.paper.snapshot()}
        self.paper.last_quote_ts_ms = quote.ts_ms
        self.last_data_issue = None
        self.paper.last_mark = quote.last

        if self.paper.halt_reason == "HARD_STOP":
            self._handle_hard_stop(quote, now)
            return {"status": "halted_hard_stop", **self.paper.snapshot()}

        if self.paper.halted:
            return {"status": "halted", **self.paper.snapshot()}

        if not self.paper.initialized:
            if not (self.cfg.low_price <= quote.last <= self.cfg.high_price
                    and self.cfg.low_price <= quote.ask <= self.cfg.high_price):
                self.paper._event("base_wait", "Piyasa fiyatı grid bandı dışında; Base Order açılmadı", now,
                                  price=quote.last)
                self.paper.save()
                return {"status": "waiting_inside_grid_band", **self.paper.snapshot()}
            lot = self.paper.open_base_market(quote, now)
            if lot is None:
                return {"status": "waiting_base_liquidity", **self.paper.snapshot()}
            self._seed_grid(quote, lot.lot_id, now, anchor_price=self.paper.grid_anchor_price)
            self.paper.grid_seeded = True
            self._refresh_tp(now)
            return {"status": "base_opened", **self.paper.snapshot()}

        if quote.last <= self.cfg.hard_stop_price:
            self._handle_hard_stop(quote, now)
            return {"status": "halted_hard_stop", **self.paper.snapshot()}

        if self.paper.tp_triggered:
            self._handle_tp(quote, now)
            return {"status": "tp_reducing", **self.paper.snapshot()}

        if not self.paper.grid_seeded:
            base_lot = next(
                (lot for lot in self.paper.position.lots.values() if lot.level_index is None), None
            )
            if base_lot is None:
                self.paper.halted = True
                self.paper.halt_reason = "GRID_RECOVERY_ORPHAN"
                self.paper._event("recovery_halted", "Grid seed yarım; Base Order lotu bulunamadı", now)
                self.paper.save()
                return {"status": "halted_grid_recovery", **self.paper.snapshot()}
            anchor = self.paper.grid_anchor_price or base_lot.entry_price
            self._seed_grid(quote, base_lot.lot_id, now, anchor_price=anchor)
            self.paper.grid_seeded = True
            self._refresh_tp(now)
            return {"status": "grid_recovered", **self.paper.snapshot()}

        self._reconcile_order_intents(now)
        if self._tp_needs_refresh():
            self._refresh_tp(now)

        tp = self.paper.tp_order
        if tp and quote.bid >= float(tp["price"]):
            self._handle_tp(quote, now)
            return {"status": "tp_triggered", **self.paper.snapshot()}

        fills = self.paper.match_limit_orders(quote, now)
        if fills:
            self._process_counter_orders(fills, now)
            self._refresh_tp(now)
        tp = self.paper.tp_order
        if tp and quote.bid >= float(tp["price"]):
            self._handle_tp(quote, now)
            return {"status": "tp_triggered", **self.paper.snapshot()}

        self.paper.save()
        return {"status": "active", **self.paper.snapshot()}
