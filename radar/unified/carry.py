"""Elif spot-long / perpetual-short carry PAPER ledger.

This is an isolated two-leg simulator. It never calls a venue API. A pair is
committed only after both legs pass freshness/size/edge checks; there is no
one-leg exposure in the ledger. New entries are disabled unless explicitly
enabled in config. It must be orchestrated only by UnifiedTrader.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Mapping

from .execution.base import Quote


class CarryPairPaper:
    def __init__(self, cfg: Mapping | None, root: str | Path):
        self.cfg = dict(cfg or {})
        u = self.cfg.get("birlesik") or {}
        c = u.get("elif_carry") or {}
        self.enabled = c.get("enabled") is True
        self.max_quote_age_ms = int(c.get("max_quote_age_ms", 5_000))
        self.max_notional_usd = float(c.get("max_notional_usd", 25.0))
        self.leverage = float(c.get("leverage", 1.0))
        self.slippage_bp = float(c.get("slippage_bp", 5.0))
        self.min_projected_net_usd = float(c.get("min_projected_net_usd", 0.10))
        if (self.max_quote_age_ms <= 0 or self.max_notional_usd <= 0 or self.leverage < 1
                or self.slippage_bp < 0 or self.min_projected_net_usd < 0):
            raise ValueError("elif_carry_config_invalid")
        fees = self.cfg.get("ucret") or {}
        self.spot_fee_rate = float(fees.get("spot_taker_yuzde", 0.10)) / 100.0
        self.perp_fee_rate = float(fees.get("taker_yuzde", 0.06)) / 100.0
        self.root = Path(root)
        self.path = self.root / "elif_carry_paper.json"
        self.positions: dict[str, dict] = {}
        self.closed: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        state = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema") != 1:
            raise RuntimeError("elif_carry_state_invalid_fail_closed")
        positions, closed = state.get("positions"), state.get("closed")
        if not isinstance(positions, dict) or not isinstance(closed, list):
            raise RuntimeError("elif_carry_ledger_invalid_fail_closed")
        for key, p in positions.items():
            if (not isinstance(key, str) or not isinstance(p, dict)
                    or p.get("status") != "open"
                    or not math.isclose(float(p.get("spot_qty", 0)), float(p.get("perp_qty", -1)), rel_tol=1e-9)):
                raise RuntimeError("elif_carry_unhedged_state_fail_closed")
        self.positions, self.closed = positions, closed

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".elif-carry-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"schema": 1, "positions": self.positions, "closed": self.closed[-500:]},
                          f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    def _fresh(self, q: Quote | None, now_ms: int) -> bool:
        return bool(q and q.valid and q.ts_ms > 0 and -1_000 <= now_ms - q.ts_ms <= self.max_quote_age_ms)

    @staticmethod
    def _depth_ok(q: Quote, side: str, qty: float) -> bool:
        levels = q.asks if side == "buy" else q.bids
        valid = [(float(px), float(size)) for px, size in levels
                 if math.isfinite(float(px)) and math.isfinite(float(size)) and float(px) > 0 and float(size) > 0]
        return bool(valid) and sum(size for _, size in valid) + 1e-12 >= qty

    def _costs(self, notional: float, spot: Quote, perp: Quote) -> float:
        slip = self.slippage_bp / 10_000
        # Four taker legs (spot/perp open and close) plus conservative full-spread crossing.
        fees = 2 * notional * (self.spot_fee_rate + self.perp_fee_rate)
        spreads = notional * ((spot.ask - spot.bid) / spot.mid + (perp.ask - perp.bid) / perp.mid)
        slippage = 4 * notional * slip
        return fees + spreads + slippage

    def open_pair(self, *, pair_id: str, symbol: str, spot_inst: str, perp_inst: str,
                  qty: float, spot: Quote | None, perp: Quote | None, funding_rate: float,
                  funding_interval_h: float, expected_holding_hours: float, now_ms: int) -> dict:
        if pair_id in self.positions:
            return dict(self.positions[pair_id])
        previous = next((row for row in self.closed if row.get("pair_id") == pair_id), None)
        if previous is not None:
            return dict(previous)
        if not self.enabled:
            return {"status": "blocked", "reason": "elif_carry_disabled"}
        numeric = (qty, funding_rate, funding_interval_h, expected_holding_hours)
        if (not pair_id or not symbol or not spot_inst or not perp_inst or spot_inst == perp_inst
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in numeric)
                or qty <= 0 or funding_rate <= 0 or funding_interval_h <= 0 or expected_holding_hours <= 0):
            return {"status": "rejected", "reason": "pair_parameters_invalid"}
        if not self._fresh(spot, now_ms) or not self._fresh(perp, now_ms):
            return {"status": "rejected", "reason": "spot_or_perp_quote_missing_stale_or_invalid"}
        if spot.inst != spot_inst or perp.inst != perp_inst:
            return {"status": "rejected", "reason": "instrument_mismatch"}
        if not self._depth_ok(spot, "buy", qty) or not self._depth_ok(perp, "sell", qty):
            return {"status": "rejected", "reason": "entry_orderbook_depth_insufficient_or_missing"}
        spot_notional, perp_notional = qty * spot.ask, qty * perp.bid
        notional = max(spot_notional, perp_notional)
        if notional > self.max_notional_usd:
            return {"status": "rejected", "reason": "carry_notional_limit"}
        projected_funding = perp_notional * funding_rate * (expected_holding_hours / funding_interval_h)
        estimated_cost = self._costs(notional, spot, perp)
        projected_net = projected_funding - estimated_cost
        if projected_net <= self.min_projected_net_usd:
            return {"status": "rejected", "reason": "carry_net_edge_below_minimum",
                    "projected_funding_usd": projected_funding, "estimated_four_leg_cost_usd": estimated_cost,
                    "projected_net_usd": projected_net}
        slip = self.slippage_bp / 10_000
        spot_fill, perp_fill = spot.ask * (1 + slip), perp.bid * (1 - slip)
        spot_fee, perp_fee = spot_fill * qty * self.spot_fee_rate, perp_fill * qty * self.perp_fee_rate
        # Both legs are validated and priced before the single atomic ledger commit.
        row = {
            "pair_id": pair_id, "symbol": symbol, "spot_inst": spot_inst, "perp_inst": perp_inst,
            "status": "open", "opened_ts_ms": now_ms, "spot_qty": qty, "perp_qty": qty,
            "spot_entry": spot_fill, "perp_entry": perp_fill,
            "spot_entry_fee_usd": spot_fee, "perp_entry_fee_usd": perp_fee,
            "fees_usd": spot_fee + perp_fee, "funding_usd": 0.0,
            "projected_funding_usd": projected_funding, "estimated_four_leg_cost_usd": estimated_cost,
            "projected_net_usd": projected_net, "funding_events": 0,
            "leverage": self.leverage, "scope": "unified_carry_paper",
        }
        self.positions[pair_id] = row
        self._save()
        return dict(row)

    def apply_funding(self, pair_id: str, *, rate: float, perp_mark: float, ts_ms: int) -> float:
        p = self.positions.get(pair_id)
        if not p or p["status"] != "open":
            return 0.0
        last_funding = int(p.get("last_funding_ts_ms") or p["opened_ts_ms"])
        if (type(rate) not in (int, float) or not math.isfinite(rate)
                or type(perp_mark) not in (int, float) or not math.isfinite(perp_mark)
                or perp_mark <= 0 or type(ts_ms) is not int or ts_ms <= last_funding):
            raise ValueError("funding_event_invalid_or_duplicate")
        # Short perp receives positive funding and pays negative funding.
        amount = p["perp_qty"] * perp_mark * rate
        p["funding_usd"] += amount
        p["funding_events"] += 1
        p["last_funding_ts_ms"] = ts_ms
        self._save()
        return amount

    def close_pair(self, pair_id: str, *, spot: Quote | None, perp: Quote | None, now_ms: int,
                   reason: str = "rule") -> dict:
        p = self.positions.get(pair_id)
        if not p:
            previous = next((row for row in self.closed if row.get("pair_id") == pair_id), None)
            return dict(previous) if previous is not None else {"status": "rejected", "reason": "carry_pair_not_found"}
        if p["status"] == "closed":
            return dict(p)
        if not self._fresh(spot, now_ms) or not self._fresh(perp, now_ms):
            return {"status": "blocked", "reason": "both_exit_quotes_required_fresh"}
        if spot.inst != p["spot_inst"] or perp.inst != p["perp_inst"]:
            return {"status": "rejected", "reason": "instrument_mismatch"}
        qty = p["spot_qty"]
        if not self._depth_ok(spot, "sell", qty) or not self._depth_ok(perp, "buy", qty):
            return {"status": "blocked", "reason": "exit_orderbook_depth_insufficient_or_missing"}
        slip = self.slippage_bp / 10_000
        spot_exit, perp_exit = spot.bid * (1 - slip), perp.ask * (1 + slip)
        spot_pnl = qty * (spot_exit - p["spot_entry"])
        perp_pnl = qty * (p["perp_entry"] - perp_exit)
        exit_fees = qty * spot_exit * self.spot_fee_rate + qty * perp_exit * self.perp_fee_rate
        fees = p["fees_usd"] + exit_fees
        net = spot_pnl + perp_pnl + p["funding_usd"] - fees
        closed = {**p, "status": "closed", "closed_ts_ms": now_ms, "close_reason": reason,
                  "spot_exit": spot_exit, "perp_exit": perp_exit, "spot_pnl_usd": spot_pnl,
                  "perp_pnl_usd": perp_pnl, "exit_fees_usd": exit_fees, "fees_usd": fees,
                  "net_usd": net, "holding_hours": (now_ms - p["opened_ts_ms"]) / 3_600_000,
                  "spot_qty": 0.0, "perp_qty": 0.0}
        self.closed.append(closed)
        del self.positions[pair_id]
        self._save()
        return dict(closed)

    def reconcile(self) -> dict:
        unhedged = [key for key, p in self.positions.items()
                    if not math.isclose(float(p.get("spot_qty", 0)), float(p.get("perp_qty", -1)), rel_tol=1e-9)]
        return {"ok": not unhedged, "open_pairs": len(self.positions), "unhedged_pair_ids": unhedged,
                "closed_pairs": len(self.closed)}


class ElifCarryStrategy:
    """Pair-only candidate evaluator; deliberately excluded from the single-leg Strategy registry."""

    def __init__(self, cfg: Mapping | None = None):
        self.cfg = dict(cfg or {})
        c = ((self.cfg.get("birlesik") or {}).get("elif_carry") or {})
        self.max_funding_age_ms = int(c.get("max_funding_age_ms", 9 * 3_600_000))
        self.max_history_age_ms = int(c.get("max_history_age_ms", 26 * 3_600_000))
        self.min_funding_30d = float(c.get("min_funding_30d_avg", 0.0))
        self.expected_holding_hours = float(c.get("expected_holding_hours", 72.0))
        self.min_projected_net_usd = float(c.get("min_projected_net_usd", 0.10))
        estimate_cfg = {**self.cfg, "birlesik": {**(self.cfg.get("birlesik") or {}),
                        "elif_carry": {**c, "enabled": False}}}
        self.paper = CarryPairPaper(estimate_cfg, Path(tempfile.gettempdir()) / "kripto-radar-carry-estimate")

    def evaluate_pair(self, *, symbol: str, spot: Quote | None, perp: Quote | None,
                      funding_rate: float | None, funding_ts_ms: int | None,
                      funding_30d_avg: float | None, history_ts_ms: int | None,
                      funding_interval_h: float, now_ms: int) -> dict:
        if not self.paper._fresh(spot, now_ms) or not self.paper._fresh(perp, now_ms):
            return {"status": "blocked", "reason": "both_spot_and_perp_quotes_must_be_fresh"}
        if (type(funding_ts_ms) is not int or now_ms - funding_ts_ms < -1_000
                or now_ms - funding_ts_ms > self.max_funding_age_ms):
            return {"status": "blocked", "reason": "funding_rate_missing_or_stale"}
        if (type(history_ts_ms) is not int or now_ms - history_ts_ms < -1_000
                or now_ms - history_ts_ms > self.max_history_age_ms):
            return {"status": "blocked", "reason": "funding_30d_history_missing_or_stale"}
        if (type(funding_rate) not in (int, float) or not math.isfinite(funding_rate)
                or type(funding_30d_avg) not in (int, float) or not math.isfinite(funding_30d_avg)
                or funding_rate <= 0 or funding_30d_avg <= self.min_funding_30d
                or type(funding_interval_h) not in (int, float) or funding_interval_h <= 0):
            return {"status": "rejected", "reason": "funding_not_positive_and_stable"}
        notional = min(self.paper.max_notional_usd, 100.0)
        projected_funding = notional * funding_rate * (self.expected_holding_hours / funding_interval_h)
        estimated_cost = self.paper._costs(notional, spot, perp)
        projected_net = projected_funding - estimated_cost
        if projected_net <= self.min_projected_net_usd:
            return {"status": "rejected", "reason": "carry_net_edge_below_minimum",
                    "projected_funding_usd": projected_funding, "estimated_four_leg_cost_usd": estimated_cost,
                    "projected_net_usd": projected_net}
        return {"status": "eligible", "strategy": "carry", "manager": "elif",
                "direction": "spot_long_perp_short", "symbol": symbol,
                "spot_inst": spot.inst, "perp_inst": perp.inst,
                "funding_rate": float(funding_rate), "funding_30d_avg": float(funding_30d_avg),
                "funding_interval_h": float(funding_interval_h),
                "expected_holding_hours": self.expected_holding_hours,
                "reference_notional_usd": notional, "projected_funding_usd": projected_funding,
                "estimated_four_leg_cost_usd": estimated_cost, "projected_net_usd": projected_net,
                "paper_execution_required": True}
