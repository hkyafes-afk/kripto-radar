"""COMBO için bağımsız PAPER emir ve cüzdan defteri.

Gerçek borsa emir adaptörü yoktur. Reduce-only satış hem emir kabulünde hem dolumda
lot miktarıyla yeniden doğrulanır. BBO derinliği yoksa emir doldurulmuş varsayılmaz.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from .config import ComboConfig
from .models import ComboEvent, ComboFill, ComboOrder, ComboOrderStatus, ComboQuote
from .position import ComboPosition, LongLot


class ComboPaperAdapter:
    def __init__(self, cfg: ComboConfig, root: str | Path | None = None):
        self.cfg = cfg
        self.root = Path(root) if root is not None else None
        self.position = ComboPosition()
        self.orders: dict[str, ComboOrder] = {}
        self.fills: list[ComboFill] = []
        self.events: list[ComboEvent] = []
        self.realized_gross_usdt = 0.0
        self.fees_paid_usdt = 0.0
        self.last_mark: float | None = None
        self.last_quote_ts_ms: int | None = None
        self.initialized = False
        self.grid_seeded = False
        self.grid_anchor_price: float | None = None
        self.halted = False
        self.halt_reason: str | None = None
        self.tp_triggered = False
        self.tp_order: dict[str, Any] | None = None
        self.next_order_id = 1
        self._load()

    @property
    def state_path(self) -> Path | None:
        return self.root / "combo_paper.json" if self.root else None

    def _signature(self) -> dict[str, Any]:
        # Tüm ayarlar defter kimliğine dahildir: ücret/stop/tick değişikliği eski defteri sessizce yorumlamasın.
        return asdict(self.cfg)

    def _load(self) -> None:
        path = self.state_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"COMBO PAPER defteri okunamadı; sıfırdan başlatılmadı: {exc}") from exc
        if raw.get("schema_version") != 1 or raw.get("config_signature") != self._signature():
            raise RuntimeError("COMBO PAPER defteri sürüm/ayar uyuşmazlığı; otomatik sıfırlama yapılmadı")
        self.position = ComboPosition.from_dict(raw.get("lots") or [])
        self.orders = {
            item["order_id"]: ComboOrder(
                **{**item, "status": ComboOrderStatus(item["status"])}
            ) for item in raw.get("orders", [])
        }
        self.fills = [ComboFill(**item) for item in raw.get("fills", [])]
        self.events = [ComboEvent(**item) for item in raw.get("events", [])]
        self.realized_gross_usdt = float(raw.get("realized_gross_usdt", 0.0))
        self.fees_paid_usdt = float(raw.get("fees_paid_usdt", 0.0))
        self.last_mark = raw.get("last_mark")
        self.last_quote_ts_ms = raw.get("last_quote_ts_ms")
        self.initialized = bool(raw.get("initialized", False))
        self.grid_seeded = bool(raw.get("grid_seeded", False))
        self.grid_anchor_price = raw.get("grid_anchor_price")
        self.halted = bool(raw.get("halted", False))
        self.halt_reason = raw.get("halt_reason")
        self.tp_triggered = bool(raw.get("tp_triggered", False))
        self.tp_order = raw.get("tp_order")
        self.next_order_id = int(raw.get("next_order_id", 1))

    def save(self) -> None:
        path = self.state_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = {
            "schema_version": 1, "config_signature": self._signature(), "lots": self.position.to_dict(),
            "orders": [{**asdict(order), "status": order.status.value} for order in self.orders.values()],
            "fills": [asdict(fill) for fill in self.fills[-5000:]],
            "events": [asdict(event) for event in self.events[-2000:]],
            "realized_gross_usdt": self.realized_gross_usdt, "fees_paid_usdt": self.fees_paid_usdt,
            "last_mark": self.last_mark, "last_quote_ts_ms": self.last_quote_ts_ms,
            "initialized": self.initialized, "grid_seeded": self.grid_seeded,
            "grid_anchor_price": self.grid_anchor_price, "halted": self.halted,
            "halt_reason": self.halt_reason, "tp_triggered": self.tp_triggered,
            "tp_order": self.tp_order, "next_order_id": self.next_order_id,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        tmp.replace(path)

    def _event(self, kind: str, message: str, now_ms: int, order_id: str | None = None,
               qty: float | None = None, price: float | None = None) -> None:
        self.events.append(ComboEvent(now_ms, kind, message, order_id, qty, price))
        self.events = self.events[-2000:]

    def _new_id(self) -> str:
        value = f"combo-{self.next_order_id:08d}"
        self.next_order_id += 1
        return value

    def _mark(self, mark: float | None = None) -> float:
        return float(mark or self.last_mark or self.position.avg_entry_price or 0.0)

    def unrealized_gross_usdt(self, mark: float | None = None) -> float:
        px = self._mark(mark)
        return self.position.unrealized_gross(px) if px > 0 else 0.0

    def equity_usdt(self, mark: float | None = None) -> float:
        return (self.cfg.paper_capital_usdt + self.realized_gross_usdt
                + self.unrealized_gross_usdt(mark) - self.fees_paid_usdt)

    def used_margin_usdt(self, mark: float | None = None) -> float:
        px = self._mark(mark)
        notional = self.position.qty * px
        return notional / self.cfg.leverage_simulation if px > 0 else 0.0

    def reserved_buy_margin_usdt(self) -> float:
        return sum(
            order.price * order.remaining_qty / self.cfg.leverage_simulation
            for order in self.orders.values() if order.active and order.side == "buy"
        )

    def free_margin_usdt(self, mark: float | None = None) -> float:
        return self.equity_usdt(mark) - self.used_margin_usdt(mark) - self.reserved_buy_margin_usdt()

    def _reserved_for_lot(self, lot_id: str) -> float:
        return sum(
            order.remaining_qty for order in self.orders.values()
            if order.active and order.side == "sell" and order.reduce_only and order.lot_id == lot_id
        )

    def has_active_buy_at(self, level_index: int) -> bool:
        return any(order.active and order.side == "buy" and order.level_index == level_index
                   for order in self.orders.values())

    def has_active_sell_for_lot(self, lot_id: str) -> bool:
        return any(order.active and order.side == "sell" and order.lot_id == lot_id
                   for order in self.orders.values())

    def place_limit(self, *, side: str, price: float, qty: float, role: str, level_index: int | None,
                    now_ms: int, lot_id: str | None = None, reduce_only: bool = False,
                    parent_order_id: str | None = None) -> ComboOrder | None:
        if side not in ("buy", "sell") or price <= 0 or qty <= 0:
            raise ValueError("COMBO limit emri yönü, fiyatı ve miktarı geçerli olmalı")
        if side == "sell":
            if not reduce_only or not lot_id:
                raise ValueError("Bütün SELL emirleri lot bağlı ve reduce-only olmalı")
            available = self.position.available_qty(lot_id) - self._reserved_for_lot(lot_id)
            if qty > available + 1e-12:
                self._event("order_rejected", "reduce-only satış lot miktarını aşardı", now_ms,
                            qty=qty, price=price)
                return None
            if self.has_active_sell_for_lot(lot_id):
                return next(o for o in self.orders.values()
                            if o.active and o.side == "sell" and o.lot_id == lot_id)
        else:
            if reduce_only or lot_id is not None:
                raise ValueError("BUY DCA emirleri reduce-only veya lot bağlı olamaz")
            if level_index is not None and parent_order_id is None and self.has_active_buy_at(level_index):
                return next(o for o in self.orders.values()
                            if o.active and o.side == "buy" and o.level_index == level_index)
            if parent_order_id is not None:
                existing_child = next((o for o in self.orders.values()
                                       if o.side == "buy" and o.parent_order_id == parent_order_id
                                       and o.status not in (ComboOrderStatus.CANCELLED, ComboOrderStatus.REJECTED)), None)
                if existing_child is not None:
                    return existing_child
                unclaimed = next((o for o in self.orders.values()
                                  if o.active and o.side == "buy" and o.level_index == level_index
                                  and o.parent_order_id is None), None)
                if unclaimed is not None:
                    unclaimed.parent_order_id = parent_order_id
                    unclaimed.updated_ts_ms = now_ms
                    self.save()
                    return unclaimed
            px = self._mark()
            required_margin = price * qty / self.cfg.leverage_simulation
            projected_notional = (self.position.qty * px + self.reserved_buy_margin_usdt()
                                  * self.cfg.leverage_simulation + price * qty)
            if self.free_margin_usdt(px) + 1e-9 < required_margin:
                self._event("order_rejected", "izole PAPER serbest teminatı yetersiz", now_ms,
                            qty=qty, price=price)
                return None
            if projected_notional > self.cfg.max_gross_notional_usdt + 1e-9:
                self._event("order_rejected", "10x PAPER nominal maruziyet sınırı aşılırdı", now_ms,
                            qty=qty, price=price)
                return None
        order = ComboOrder(
            order_id=self._new_id(), side=side, price=price, qty=qty, role=role,
            level_index=level_index, lot_id=lot_id, reduce_only=reduce_only,
            created_ts_ms=now_ms, updated_ts_ms=now_ms, parent_order_id=parent_order_id,
        )
        self.orders[order.order_id] = order
        self._event("order_accepted", f"{side.upper()} {role} limit PAPER emri kabul edildi",
                    now_ms, order.order_id, qty, price)
        self.save()
        return order

    def cancel_all_orders(self, now_ms: int, reason: str) -> None:
        for order in self.orders.values():
            if order.active:
                order.status = ComboOrderStatus.CANCELLED
                order.updated_ts_ms = now_ms
                order.reason = reason
                self._event("order_cancelled", reason, now_ms, order.order_id,
                            order.remaining_qty, order.price)
        self.save()

    def open_base_market(self, quote: ComboQuote, now_ms: int) -> LongLot | None:
        if self.initialized:
            return None
        qty = self.cfg.order_qty_btc
        if quote.ask_size is None or quote.ask_size + 1e-12 < qty:
            self._event("base_wait", "Base Order için yeterli gerçek BBO ask miktarı yok", now_ms,
                        qty=qty, price=quote.ask)
            self.save()
            return None
        required_margin = quote.ask * qty / self.cfg.leverage_simulation
        if self.free_margin_usdt(quote.last) + 1e-9 < required_margin:
            self._event("base_rejected", "Base Order için izole PAPER teminatı yetersiz", now_ms,
                        qty=qty, price=quote.ask)
            self.save()
            return None
        order_id = self._new_id()
        fee = quote.ask * qty * self.cfg.taker_fee_rate
        lot_id = f"{order_id}-lot-1"
        lot = LongLot(lot_id, quote.ask, qty, None, now_ms)
        self.position.add(lot)
        self.fees_paid_usdt += fee
        fill = ComboFill(order_id, "buy", "base_market", None, lot_id, qty, quote.ask, fee, now_ms)
        self.fills.append(fill)
        self._event("base_filled", "PAPER market Base Order doldu; LONG lot açıldı",
                    now_ms, order_id, qty, quote.ask)
        self.initialized = True
        self.grid_anchor_price = quote.last
        self.grid_seeded = False
        self.last_mark = quote.last
        self.save()
        return lot

    def match_limit_orders(self, quote: ComboQuote, now_ms: int) -> list[ComboFill]:
        """BBO miktarını aynı quote içinde emirler arasında bir kez tüketir; kısmi dolumu destekler."""
        if quote.ask_size is None or quote.bid_size is None:
            self._event("fill_blocked", "BBO miktarı eksik; varsayımsal dolum yapılmadı", now_ms)
            self.save()
            return []
        ask_left = float(quote.ask_size)
        bid_left = float(quote.bid_size)
        fills: list[ComboFill] = []
        buy_orders = sorted(
            (o for o in self.orders.values() if o.active and o.side == "buy" and quote.ask <= o.price),
            key=lambda o: (-o.price, o.created_ts_ms, o.order_id),
        )
        sell_orders = sorted(
            (o for o in self.orders.values() if o.active and o.side == "sell" and quote.bid >= o.price),
            key=lambda o: (o.price, o.created_ts_ms, o.order_id),
        )
        for order in buy_orders:
            if ask_left <= 1e-12:
                break
            qty = min(order.remaining_qty, ask_left)
            px = min(order.price, quote.ask)
            fee = qty * px * self.cfg.maker_fee_rate
            lot_id = f"{order.order_id}-lot-{len([f for f in self.fills if f.order_id == order.order_id]) + 1}"
            self.position.add(LongLot(lot_id, px, qty, order.level_index, now_ms))
            self.fees_paid_usdt += fee
            order.filled_qty += qty
            order.updated_ts_ms = now_ms
            order.status = (ComboOrderStatus.FILLED if order.remaining_qty <= 1e-12
                            else ComboOrderStatus.PARTIALLY_FILLED)
            fill = ComboFill(order.order_id, "buy", order.role, order.level_index, lot_id, qty, px, fee, now_ms)
            fills.append(fill)
            self.fills.append(fill)
            self._event("order_fill", "BUY limit PAPER dolumu", now_ms, order.order_id, qty, px)
            ask_left -= qty
        for order in sell_orders:
            if bid_left <= 1e-12:
                break
            if not order.active:
                continue
            if not order.reduce_only or not order.lot_id:
                order.status = ComboOrderStatus.REJECTED
                order.reason = "SELL reduce-only invariant ihlali"
                self._event("order_rejected", order.reason, now_ms, order.order_id)
                continue
            lot_available = self.position.available_qty(order.lot_id)
            qty = min(order.remaining_qty, bid_left, lot_available)
            if qty <= 1e-12:
                order.status = ComboOrderStatus.CANCELLED
                order.reason = "reduce-only lot kalmadı"
                self._event("order_cancelled", order.reason, now_ms, order.order_id)
                continue
            px = max(order.price, quote.bid)
            fee = qty * px * self.cfg.maker_fee_rate
            self.realized_gross_usdt += self.position.close(order.lot_id, qty, px)
            self.fees_paid_usdt += fee
            order.filled_qty += qty
            order.updated_ts_ms = now_ms
            order.status = (ComboOrderStatus.FILLED if order.remaining_qty <= 1e-12
                            else ComboOrderStatus.PARTIALLY_FILLED)
            fill = ComboFill(order.order_id, "sell", order.role, order.level_index,
                             order.lot_id, qty, px, fee, now_ms)
            fills.append(fill)
            self.fills.append(fill)
            self._event("order_fill", "SELL reduce-only PAPER dolumu", now_ms, order.order_id, qty, px)
            bid_left -= qty
        if fills:
            self.fills = self.fills[-5000:]
            self.save()
        return fills

    def reduce_all(self, *, quote: ComboQuote, qty_available: float, price: float,
                   fee_rate: float, role: str, now_ms: int) -> float:
        if qty_available <= 0 or self.position.qty <= 1e-12:
            return 0.0
        qty, gross = self.position.close_fifo(min(qty_available, self.position.qty), price)
        if qty <= 0:
            return 0.0
        fee = qty * price * fee_rate
        self.realized_gross_usdt += gross
        self.fees_paid_usdt += fee
        order_id = self._new_id()
        self.fills.append(ComboFill(order_id, "sell", role, None, None, qty, price, fee, now_ms))
        self._event("position_reduced", f"{role} reduce-only PAPER kapanışı", now_ms,
                    order_id, qty, price)
        self.fills = self.fills[-5000:]
        self.save()
        return qty

    def market_stop_reduce(self, quote: ComboQuote, now_ms: int) -> float:
        if quote.bid_size is None:
            self._event("stop_wait", "Stop tetiklendi fakat BBO bid miktarı yok; dolum varsayılmadı", now_ms)
            self.save()
            return 0.0
        px = quote.bid * (1.0 - self.cfg.stop_slippage_bps / 10_000.0)
        return self.reduce_all(quote=quote, qty_available=quote.bid_size, price=px,
                               fee_rate=self.cfg.taker_fee_rate, role="hard_stop_market", now_ms=now_ms)

    def limit_tp_reduce(self, quote: ComboQuote, now_ms: int) -> float:
        if quote.bid_size is None or not self.tp_order:
            return 0.0
        tp_price = float(self.tp_order["price"])
        if quote.bid < tp_price:
            return 0.0
        px = max(tp_price, quote.bid)
        return self.reduce_all(quote=quote, qty_available=quote.bid_size, price=px,
                               fee_rate=self.cfg.maker_fee_rate, role="main_take_profit", now_ms=now_ms)

    def snapshot(self) -> dict[str, Any]:
        mark = self.last_mark
        avg = self.position.avg_entry_price
        tp_status = (self.tp_order or {}).get("status")
        tp_active = tp_status in ("active", "triggered", "partially_filled")
        active_grid_orders = sum(o.active for o in self.orders.values())
        return {
            "mode": "PAPER_ONLY", "symbol": self.cfg.symbol,
            "capital_usdt": self.cfg.paper_capital_usdt,
            "equity_usdt": self.equity_usdt(mark),
            "net_after_fees_usdt": self.realized_gross_usdt - self.fees_paid_usdt,
            "unrealized_gross_usdt": self.unrealized_gross_usdt(mark),
            "fees_paid_usdt": self.fees_paid_usdt,
            "position_qty_btc": self.position.qty, "avg_entry_price": avg,
            "mark_price": mark, "tp_order": self.tp_order, "tp_active": tp_active,
            "hard_stop_price": self.cfg.hard_stop_price,
            "used_margin_usdt": self.used_margin_usdt(mark),
            "reserved_buy_margin_usdt": self.reserved_buy_margin_usdt(),
            "free_margin_usdt": self.free_margin_usdt(mark),
            "leverage_simulation": self.cfg.leverage_simulation,
            "initialized": self.initialized, "halted": self.halted,
            "halt_reason": self.halt_reason,
            "active_grid_orders": active_grid_orders,
            "active_orders": active_grid_orders + int(tp_active),
            "order_count": len(self.orders), "fill_count": len(self.fills),
            "private_api_enabled": False, "live_order_submission": False,
        }
