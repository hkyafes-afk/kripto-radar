"""Paper yürütme adaptörü: emir durum makinesi + pozisyon defteri (gerçek borsaya hiçbir şey gitmez).

Durumlar: pending → accepted → partially_filled → filled | cancelled | expired | rejected | unknown

Dolum modeli (muhafazakâr):
  - market      : emir defteri derinliği varsa VWAP; yoksa ask/bid + `varsayilan_kayma_bp`; taker ücreti.
                  Derinlik yetmezse dolan kısım kalır, kalan iptal edilir (kısmi dolum).
  - ioc_limit   : limit fiyatı veya daha iyisi kadar doldurulur, kalan anında iptal.
  - limit (GTC) : hemen eşleşebilen kısım taker; kalan bekler ve fiyat limitin ÖTESİNE geçince (son işlem limitten
                  daha iyi) maker ücretiyle dolar. Süresi dolunca expired.
  - stop_market / trailing_stop : son fiyat stop'u geçince tetiklenir, stop'tan `stop_kayma_bp` kötü fiyatla dolar.
  - take_profit_limit : son fiyat hedefe ulaşınca hedef fiyattan dolar (maker).
Reduce-only emir pozisyonu asla büyütmez ve ters çeviremez; pozisyon yoksa reddedilir.

İdempotency: client_order_id = ExecutionIntent.client_order_id (belirleyici). Aynı id ikinci kez gönderilirse yeni
emir oluşmaz, mevcut durum döner.

Kalıcılık: `root/yurutme.json` (emirler + pozisyonlar, atomik yazım) ve `root/olaylar-YYYY-MM-DD.jsonl` (olay günlüğü).
Yeniden başlatmada durum dosyadan yüklenir; `reconcile()` süresi dolan, sahipsiz ve tutarsız kayıtları düzeltir.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ... import ledger as lg
from ..flags import ensure_paper, unified_cfg
from ..models import (ExecutionEvent, ExecutionIntent, ExecutionMode, ExecutionResult, OrderStatus, OrderType, Side,
                      to_jsonable)
from .base import ExecutionAdapter, Quote

PAPER_DEFAULTS = {"kote_max_yas_sn": 15, "varsayilan_kayma_bp": 10.0, "stop_kayma_bp": 10.0, "kapali_sakla": 500}


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _is_buy(side: Side, action: str) -> bool:
    return (side is Side.LONG) == (action == "open")


class PaperExecutionAdapter(ExecutionAdapter):
    mode = ExecutionMode.PAPER

    def __init__(self, cfg: Mapping | None, root: str | Path | None = None):
        self.cfg = dict(cfg or {})
        ensure_paper(ExecutionMode.PAPER)
        p = dict(PAPER_DEFAULTS)
        p.update((unified_cfg(self.cfg).get("paper") or {}))
        self.p = p
        fc = lg.fees_cfg(self.cfg)
        self.taker = float(fc["taker_yuzde"]) / 100
        self.maker = float(fc["maker_yuzde"]) / 100
        self.root = Path(root) if root else None
        self.orders: dict[str, dict] = {}
        self.positions: dict[str, dict] = {}
        self.closed: list[dict] = []
        # `closed` yalnız son N işlemi tutar; muhasebe bu pencereden bağımsız olmalı.
        self.closed_net_usd = 0.0
        self.closed_loss_streak = 0
        self._pending_events: list[dict] = []
        self._load()

    # ── kalıcılık ────────────────────────────────────────────────
    @property
    def state_path(self) -> Path | None:
        return self.root / "yurutme.json" if self.root else None

    def _load(self) -> None:
        sp = self.state_path
        if not sp or not sp.exists():
            return
        try:
            st = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise RuntimeError(f"paper yürütme durumu okunamadı ({sp}): {e}") from e   # sessizce sıfırlama YOK
        self.orders = st.get("orders") or {}
        self.positions = st.get("positions") or {}
        self.closed = st.get("closed") or []
        # Eski durum dosyalarıyla uyumluluk: ilk açılışta eldeki geçmişten başlat.
        self.closed_net_usd = float(st.get("closed_net_usd", sum(c.get("net_usd") or 0.0 for c in self.closed)))
        self.closed_loss_streak = int(st.get("closed_loss_streak", self._trailing_loss_streak(self.closed)))

    @staticmethod
    def _trailing_loss_streak(closed: list[dict]) -> int:
        streak = 0
        for trade in reversed(closed):
            if float(trade.get("net_usd") or 0.0) < 0:
                streak += 1
            else:
                break
        return streak

    def save(self) -> None:
        if not self.root:
            self._pending_events.clear()
            return
        self.root.mkdir(parents=True, exist_ok=True)
        if self._pending_events:
            by_day: dict[str, list[str]] = {}
            for e in self._pending_events:
                by_day.setdefault(_day(e["ts_ms"]), []).append(json.dumps(e, ensure_ascii=False, separators=(",", ":")))
            for d, lines in by_day.items():
                with (self.root / f"olaylar-{d}.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
            self._pending_events.clear()
        term = [k for k, o in self.orders.items() if OrderStatus(o["status"]).terminal]
        keep = int(self.p["kapali_sakla"])
        if len(term) > keep:                                  # eski bitmiş emirler (olay günlüğünde zaten var)
            for k in sorted(term, key=lambda k: self.orders[k]["updated"])[:len(term) - keep]:
                del self.orders[k]
        self.closed = self.closed[-keep:]
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"v": 2, "orders": self.orders, "positions": self.positions, "closed": self.closed,
                                   "closed_net_usd": self.closed_net_usd,
                                   "closed_loss_streak": self.closed_loss_streak},
                                  ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.state_path)

    # ── yardımcılar ──────────────────────────────────────────────
    def _event(self, o: dict, kind: str, now: int, qty: float = 0.0, price: float | None = None, fee: float = 0.0,
               **payload) -> None:
        ev = {"ts_ms": now, "client_order_id": o["cid"], "kind": kind, "qty": qty, "price": price, "fee_usd": fee,
              "payload": payload}
        o["events"].append(ev)
        self._pending_events.append({**ev, "inst": o["intent"]["inst"]})

    def _result(self, o: dict) -> ExecutionResult:
        return ExecutionResult(
            client_order_id=o["cid"], status=OrderStatus(o["status"]), requested_qty=o["intent"]["qty"],
            filled_qty=o["filled"], avg_price=o["avg"], fee_usd=o["fee"], ts_ms=o["updated"], reason=o.get("reason", ""),
            events=tuple(ExecutionEvent(ts_ms=e["ts_ms"], client_order_id=e["client_order_id"], kind=e["kind"],
                                        qty=e["qty"], price=e["price"], fee_usd=e["fee_usd"], payload=e["payload"])
                         for e in o["events"]))

    def _set(self, o: dict, status: OrderStatus, now: int, reason: str = "") -> None:
        o["status"] = status.value
        o["updated"] = now
        if reason:
            o["reason"] = reason

    def _remaining(self, o: dict) -> float:
        return max(0.0, o["intent"]["qty"] - o["filled"])

    def _intent(self, o: dict) -> tuple[Side, str, OrderType]:
        it = o["intent"]
        return Side.parse(it["side"]), it["action"], OrderType(it["order_type"])

    # ── pozisyon defteri ─────────────────────────────────────────
    def _apply_fill(self, o: dict, qty: float, px: float, fee: float, now: int, maker: bool,
                    ref: float | None = None) -> None:
        if qty <= 0:
            return
        slip = abs(px - ref) * qty if ref else 0.0             # referansa (orta fiyat / stop) göre kayma, USD
        side, action, _ = self._intent(o)
        it = o["intent"]
        inst = it["inst"]
        prev_filled = o["filled"]
        o["avg"] = (o["avg"] * prev_filled + px * qty) / (prev_filled + qty) if prev_filled else px
        o["filled"] = prev_filled + qty
        o["fee"] += fee
        self._event(o, "fill" if self._remaining(o) <= 1e-12 else "partial_fill", now, qty, px, fee, maker=maker)
        pos = self.positions.get(inst)
        sg = 1 if side is Side.LONG else -1
        if action == "open":
            if pos is None:
                meta = dict(it.get("metadata") or {})
                self.positions[inst] = {
                    "inst": inst, "symbol": it["symbol"], "side": side.value, "qty": qty, "qty_init": qty,
                    "entry": px, "fees_usd": fee, "realized_usd": 0.0, "funding_usd": 0.0, "opened_ts": now,
                    "decision_key": it["idempotency_key"], "open_cid": o["cid"], "strategy": meta.get("strategy"),
                    "stop": meta.get("stop"), "stop_init": meta.get("stop"), "targets": meta.get("targets") or [],
                    "risk_usd": meta.get("risk_usd"), "leverage": meta.get("leverage"), "horizon": meta.get("horizon"),
                    "max_holding_hours": meta.get("max_holding_hours"), "best_px": px, "partials": [],
                    "notional_usd": qty * px, "margin_usd": qty * px / float(meta.get("leverage") or 1.0),
                    "cluster": meta.get("cluster"), "timeframe": meta.get("timeframe"), "slip_usd": slip,
                    "strategies": meta.get("strategies"), "execution_scope": meta.get("execution_scope", "unknown")}
            else:
                tot = pos["qty"] + qty
                pos["entry"] = (pos["entry"] * pos["qty"] + px * qty) / tot
                pos["qty"], pos["qty_init"] = tot, pos["qty_init"] + qty
                pos["fees_usd"] += fee
                pos["slip_usd"] = pos.get("slip_usd", 0.0) + slip
                pos["notional_usd"] = tot * pos["entry"]
            return
        # kapatma / azaltma
        if pos is None:
            return
        take = min(qty, pos["qty"])
        pnl = sg * (px - pos["entry"]) * take
        pos["realized_usd"] += pnl
        pos["fees_usd"] += fee
        pos["slip_usd"] = pos.get("slip_usd", 0.0) + slip
        pos["qty"] -= take
        pos["partials"].append({"ts": now, "qty": take, "px": px, "pnl_usd": pnl, "why": (it.get("metadata") or {}).get("why")})
        pos["notional_usd"] = pos["qty"] * pos["entry"]
        if pos["qty"] <= 1e-12:
            net = pos["realized_usd"] - pos["fees_usd"] + pos["funding_usd"]
            risk = pos.get("risk_usd") or None
            closed = {**pos, "qty": 0.0, "closed_ts": now, "exit_px": px, "net_usd": net,
                      "r": (net / risk) if risk else None, "exit_reason": (it.get("metadata") or {}).get("why")}
            self.closed.append(closed)
            self.closed_net_usd += net
            self.closed_loss_streak = self.closed_loss_streak + 1 if net < 0 else 0
            del self.positions[inst]
            for oo in self.orders.values():                  # sahipsiz kalan reduce-only emirleri iptal et
                if oo["intent"]["inst"] == inst and oo is not o and oo["intent"]["reduce_only"] and \
                        not OrderStatus(oo["status"]).terminal:
                    self._event(oo, "cancel", now, reason="pozisyon kapandı")
                    self._set(oo, OrderStatus.CANCELLED, now, "pozisyon kapandı")

    def apply_funding(self, inst: str, rate: float, price: float, now_ms: int) -> float:
        """Funding kesimi: long pozitif oranda öder. Döner: pozisyona yazılan tutar (+ gelir, − gider)."""
        pos = self.positions.get(inst)
        if not pos or not price or price <= 0:
            return 0.0
        sg = 1 if pos["side"] == "long" else -1
        amt = -sg * rate * pos["qty"] * price
        pos["funding_usd"] += amt
        self._pending_events.append({"ts_ms": now_ms, "client_order_id": pos["open_cid"], "kind": "funding",
                                     "qty": pos["qty"], "price": price, "fee_usd": 0.0, "inst": inst,
                                     "payload": {"rate": rate, "amount_usd": amt}})
        return amt

    # ── dolum hesapları ──────────────────────────────────────────
    def _walk(self, levels, qty: float, limit: float | None, buy: bool) -> tuple[float | None, float]:
        lv = [(float(p), float(s)) for p, s in levels
              if limit is None or (p <= limit if buy else p >= limit)]
        return lg.vwap_fill(lv, qty)

    def _taker_fill(self, o: dict, q: Quote, qty: float, limit: float | None, now: int) -> float:
        side, action, _ = self._intent(o)
        buy = _is_buy(side, action)
        levels = q.asks if buy else q.bids
        if levels:
            px, got = self._walk(levels, qty, limit, buy)
        else:
            slip = float(self.p["varsayilan_kayma_bp"]) / 1e4
            px = q.ask * (1 + slip) if buy else q.bid * (1 - slip)
            got = qty if limit is None or (px <= limit if buy else px >= limit) else 0.0
            if got == 0.0 and limit is not None and ((q.ask <= limit) if buy else (q.bid >= limit)):
                px, got = limit, qty                          # kayma tahmini limiti aşıyor: limitten dolar
        if not px or got <= 0:
            return 0.0
        self._apply_fill(o, got, px, got * px * self.taker, now, maker=False, ref=q.mid)
        return got

    # ── API ──────────────────────────────────────────────────────
    def submit(self, intent: ExecutionIntent, quote: Quote | None, now_ms: int | None = None) -> ExecutionResult:
        ensure_paper(intent.mode)
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        cid = intent.client_order_id
        if cid in self.orders:                                # idempotency: tekrar gönderim yeni emir açmaz
            return self._result(self.orders[cid])
        o = {"cid": cid, "intent": to_jsonable(intent), "status": OrderStatus.PENDING.value, "filled": 0.0, "avg": None,
             "fee": 0.0, "events": [], "created": now, "updated": now, "reason": ""}
        self.orders[cid] = o
        self._event(o, "submitted", now)
        try:
            self._process_new(o, intent, quote, now)
        finally:
            self.save()
        return self._result(o)

    def _process_new(self, o: dict, it: ExecutionIntent, q: Quote | None, now: int) -> None:
        pos = self.positions.get(it.inst)
        if it.action == "open":
            if it.reduce_only:
                return self._reject(o, now, "açılış emri reduce-only olamaz")
            if pos is not None and pos["side"] != it.side.value:
                return self._reject(o, now, "ters yönde açık pozisyon var (ters çevirme yok)")
        else:
            if not it.reduce_only:
                return self._reject(o, now, "kapatma/azaltma emri reduce-only olmalı")
            if pos is None or pos["side"] != it.side.value:
                return self._reject(o, now, "reduce-only: kapatılacak pozisyon yok")
            if it.qty > pos["qty"] + 1e-12:
                o["intent"]["qty"] = pos["qty"]               # reduce-only pozisyonu aşamaz → kırp
                self._event(o, "clipped", now, qty=pos["qty"])
        if it.expires_ts_ms is not None and now >= it.expires_ts_ms:
            return self._reject(o, now, "süresi dolmuş niyet")
        ot = it.order_type
        if ot in (OrderType.STOP_MARKET, OrderType.TRAILING_STOP, OrderType.TAKE_PROFIT_LIMIT):
            trig = it.stop_price if ot is not OrderType.TAKE_PROFIT_LIMIT else it.price
            if not trig or trig <= 0:
                return self._reject(o, now, "tetik fiyatı yok")
            o["trigger"] = float(trig)
            self._set(o, OrderStatus.ACCEPTED, now)
            self._event(o, "accepted", now, price=trig)
            if q is not None and q.valid:
                self._check_trigger(o, q, now)
            return None
        stale = q is None or not q.valid or (now - q.ts_ms) > float(self.p["kote_max_yas_sn"]) * 1000
        if stale:
            return self._reject(o, now, "fiyat yok ya da bayat (dolum varsayılmaz)")
        if ot is OrderType.MARKET:
            got = self._taker_fill(o, q, self._remaining(o), None, now)
            return self._finish_ioc(o, got, now)
        if ot is OrderType.IOC_LIMIT:
            got = self._taker_fill(o, q, self._remaining(o), it.price, now)
            return self._finish_ioc(o, got, now)
        # LIMIT GTC
        self._set(o, OrderStatus.ACCEPTED, now)
        self._event(o, "accepted", now, price=it.price)
        side, action, _ = self._intent(o)
        buy = _is_buy(side, action)
        if (q.ask <= it.price) if buy else (q.bid >= it.price):
            self._taker_fill(o, q, self._remaining(o), it.price, now)
        self._status_after_fill(o, now)
        return None

    def _reject(self, o: dict, now: int, reason: str) -> None:
        self._set(o, OrderStatus.REJECTED, now, reason)
        self._event(o, "reject", now, reason=reason)

    def _finish_ioc(self, o: dict, got: float, now: int) -> None:
        if self._remaining(o) <= 1e-12:
            self._set(o, OrderStatus.FILLED, now)
            return
        reason = "derinlik yetmedi; kalan iptal" if got > 0 else "dolum yok (fiyat/derinlik)"
        self._event(o, "cancel", now, qty=self._remaining(o), reason=reason)
        self._set(o, OrderStatus.CANCELLED, now, reason)

    def _status_after_fill(self, o: dict, now: int) -> None:
        if self._remaining(o) <= 1e-12:
            self._set(o, OrderStatus.FILLED, now)
        elif o["filled"] > 0:
            self._set(o, OrderStatus.PARTIALLY_FILLED, now)

    def _check_trigger(self, o: dict, q: Quote, now: int) -> bool:
        side, action, ot = self._intent(o)
        buy = _is_buy(side, action)
        trig = o["trigger"]
        last = q.last
        if ot is OrderType.TAKE_PROFIT_LIMIT:
            hit = last >= trig if not buy else last <= trig
            if not hit:
                return False
            qty = self._remaining(o)
            self._event(o, "triggered", now, price=last)
            self._apply_fill(o, qty, trig, qty * trig * self.maker, now, maker=True)
            self._set(o, OrderStatus.FILLED, now)
            return True
        hit = last <= trig if not buy else last >= trig       # stop: satış aşağı, alış yukarı kırılımda
        if not hit:
            return False
        slip = float(self.p["stop_kayma_bp"]) / 1e4
        base = min(last, trig) if not buy else max(last, trig)
        px = base * (1 - slip) if not buy else base * (1 + slip)
        qty = self._remaining(o)
        self._event(o, "triggered", now, price=last)
        self._apply_fill(o, qty, px, qty * px * self.taker, now, maker=False, ref=trig)
        if OrderStatus(o["status"]) is not OrderStatus.CANCELLED:
            self._set(o, OrderStatus.FILLED, now)
        return True

    def on_quote(self, quote: Quote, now_ms: int | None = None) -> list[ExecutionResult]:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        changed = []
        if not quote.valid:
            return changed
        pos = self.positions.get(quote.inst)
        if pos:
            pos["last"] = quote.last
            better = quote.last > pos["best_px"] if pos["side"] == "long" else quote.last < pos["best_px"]
            if better:
                pos["best_px"] = quote.last
        for o in sorted(self.orders.values(), key=lambda x: x["created"]):
            if o["intent"]["inst"] != quote.inst or OrderStatus(o["status"]).terminal:
                continue
            before = (o["status"], o["filled"])
            exp = o["intent"].get("expires_ts_ms")
            if exp is not None and now >= exp:
                self._event(o, "expire", now, qty=self._remaining(o))
                self._set(o, OrderStatus.EXPIRED, now, "süre doldu")
            elif "trigger" in o:
                self._check_trigger(o, quote, now)
            else:                                             # bekleyen limit: fiyat limitin ötesine geçerse maker dolum
                side, action, _ = self._intent(o)
                buy = _is_buy(side, action)
                lim = o["intent"]["price"]
                if (quote.last < lim) if buy else (quote.last > lim):
                    qty = self._remaining(o)
                    self._apply_fill(o, qty, lim, qty * lim * self.maker, now, maker=True)
                    self._status_after_fill(o, now)
            if (o["status"], o["filled"]) != before:
                changed.append(self._result(o))
        if changed or pos:
            self.save()
        return changed

    def cancel(self, client_order_id: str, now_ms: int | None = None, reason: str = "") -> ExecutionResult | None:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        o = self.orders.get(client_order_id)
        if o is None:
            return None
        if not OrderStatus(o["status"]).terminal:
            self._event(o, "cancel", now, qty=self._remaining(o), reason=reason)
            self._set(o, OrderStatus.CANCELLED, now, reason or "iptal")
            self.save()
        return self._result(o)

    def amend_stop(self, client_order_id: str, stop_price: float, now_ms: int | None = None) -> ExecutionResult | None:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        o = self.orders.get(client_order_id)
        if o is None or OrderStatus(o["status"]).terminal or "trigger" not in o:
            return None
        if o["trigger"] != stop_price:
            self._event(o, "amend", now, price=stop_price, old=o["trigger"])
            o["trigger"] = float(stop_price)
            o["intent"]["stop_price"] = float(stop_price)
            o["updated"] = now
            pos = self.positions.get(o["intent"]["inst"])
            if pos is not None and o["intent"]["reduce_only"]:
                pos["stop"] = float(stop_price)
            self.save()
        return self._result(o)

    def status(self, client_order_id: str) -> ExecutionResult | None:
        o = self.orders.get(client_order_id)
        return self._result(o) if o else None

    def open_orders(self, inst: str | None = None) -> list[dict]:
        return [o for o in self.orders.values() if not OrderStatus(o["status"]).terminal
                and (inst is None or o["intent"]["inst"] == inst)]

    def reconcile(self, now_ms: int | None = None) -> list[str]:
        """Yeniden başlatma sonrası uzlaştırma. Döner: yapılan düzeltmelerin Türkçe notları."""
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        notes = []
        for o in self.orders.values():
            st = OrderStatus(o["status"])
            if st.terminal:
                continue
            inst = o["intent"]["inst"]
            if st in (OrderStatus.PENDING, OrderStatus.UNKNOWN):
                # Gönderim yarıda kalmış: paper'da borsa tarafı yok, dolum kaydı varsa korunur, kalan iptal edilir.
                self._event(o, "reconcile", now, reason="onaysız emir")
                self._set(o, OrderStatus.CANCELLED, now, "yeniden başlatma: onaysız emir iptal")
                notes.append(f"{o['cid']}: onaysız emir iptal edildi")
                continue
            exp = o["intent"].get("expires_ts_ms")
            if exp is not None and now >= exp:
                self._event(o, "expire", now, qty=self._remaining(o))
                self._set(o, OrderStatus.EXPIRED, now, "süre doldu (uzlaştırma)")
                notes.append(f"{o['cid']}: süresi dolmuş emir kapatıldı")
                continue
            if o["intent"]["reduce_only"] and inst not in self.positions:
                self._event(o, "cancel", now, reason="pozisyon yok (uzlaştırma)")
                self._set(o, OrderStatus.CANCELLED, now, "pozisyon yok")
                notes.append(f"{o['cid']}: sahipsiz reduce-only emir iptal edildi")
        for inst, pos in list(self.positions.items()):
            if pos["qty"] <= 1e-12:
                del self.positions[inst]
                notes.append(f"{inst}: sıfır miktarlı pozisyon kaydı silindi")
        if notes:
            self.save()
        return notes

    def summary(self) -> dict[str, Any]:
        cl = self.closed
        return {"acik": len(self.positions), "kapali": len(cl),
                "net_usd": round(self.closed_net_usd, 4),
                "bekleyen_emir": len(self.open_orders())}
