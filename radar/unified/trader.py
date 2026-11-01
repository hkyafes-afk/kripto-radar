"""Birleşik Baş Trader orkestratörü (yalnız `birlesik.karar_modu: unified` iken, yalnız PAPER).

Bir tur:
  1. fiyatlar → paper adaptörü (bekleyen limit/stop/TP emirleri)
  2. açık pozisyonların çıkış yönetimi (exits.evaluate_exits → reduce-only emirler, stop taşıma)
  3. bağlamlar → kararlar (UnifiedDecisionEngine) → günlük
  4. açılabilir kararlar → RiskGate → giriş emri; dolunca reduce-only koruyucu stop emri
  5. portföy (bakiye, zirve, gün başı, kayıp serisi) güncellenir, `durum.json` yazılır (panel için)

Legacy defter (`site/ledger.json`) ve legacy yöneticilerin pozisyonları bu modülden ETKİLENMEZ: birleşik pozisyonlar ayrı
bir paper defterinde (`site/birlesik/yurutme.json`) tutulur. Böylece iki sistem aynı dönemde karşılaştırılabilir.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .. import ledger as lg
from .decision import UnifiedDecisionEngine
from .carry import CarryPairPaper
from .execution.base import Quote
from .execution.paper import PaperExecutionAdapter
from .exits import evaluate_exits
from .factors import FactorEngine
from .flags import execution_mode, unified_cfg
from .journal import DecisionJournal
from . import hazirlik
from .targets import classify as classify_targets
from .models import (Decision, DecisionResult, ExecutionIntent, ExecutionMode, OrderStatus, OrderType, Regime, Side,
                     to_jsonable)
from .risk import MarketRisk, OpenPosition, PortfolioState, RiskGate

DAY_MS = 86_400_000


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


class UnifiedTrader:
    def __init__(self, cfg: Mapping | None, root: str | Path, engine: UnifiedDecisionEngine | None = None,
                 journal_root: str | Path | None = None, mode: str = "unified"):
        """mode="shadow": aynı paper bileşenleriyle AYRI bir gölge sanal defter (site/birlesik/golge/); gerçek emir yok."""
        self.cfg = dict(cfg or {})
        ex_mode, warn = execution_mode(self.cfg)
        assert ex_mode is ExecutionMode.PAPER                    # flags paper dışını zaten reddeder
        self.warning = warn
        self.root = Path(root)
        u = unified_cfg(self.cfg)
        self.start_equity = float((u.get("paper") or {}).get("baslangic_bakiye")
                                  or lg.demo_cfg(self.cfg)["baslangic_bakiye"])
        self.factors = FactorEngine(self.cfg)
        self.engine = engine or UnifiedDecisionEngine(self.cfg)
        self.risk = RiskGate(self.cfg)
        self.adapter = PaperExecutionAdapter(self.cfg, root=self.root)
        # Elif'in ayrı spot+perp defteri UnifiedTrader tarafından sahiplenilir; varsayılan kapalıdır.
        self.carry_paper = CarryPairPaper(self.cfg, root=self.root / "elif_carry")
        self.mode = mode
        self.journal_root = Path(journal_root) if journal_root else self.root
        self.journal = DecisionJournal(self.journal_root, keep_days=int(u.get("gunluk_saklama_gun", 14)))
        self.state_path = self.root / "portfoy.json"
        self.st = self._load()
        self.last_risk: list[dict] = []
        self.recovery_notes = self.adapter.reconcile()

    # ── portföy durumu ───────────────────────────────────────────
    def _load(self) -> dict:
        empty = {"start_equity": self.start_equity, "peak": None, "day": None, "day_start": None,
                 "issue_since": {}, "halted": None}
        try:
            raw = self.state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return empty
        except OSError as e:
            raise RuntimeError(f"Birleşik portföy durumu okunamadı: {self.state_path}: {e}") from e
        try:
            state = json.loads(raw)
        except ValueError as e:
            raise RuntimeError(f"Birleşik portföy durumu bozuk; güvenli başlatma iptal edildi: {self.state_path}") from e
        if not isinstance(state, dict):
            raise RuntimeError(f"Birleşik portföy durumu nesne değil; güvenli başlatma iptal edildi: {self.state_path}")

        # Eski shadow süreci 100.000 USDT varsayılanıyla çalışmış olabilir. 100 USDT
        # yapılandırmasına geçerken eski zirveyi taşımak hesabı sahte biçimde %99,9 düşüşte
        # gösterir. Yalnızca hiç emir/pozisyon/kapanmış işlem yoksa geçmiş özsermaye eğrisi
        # yeniden bazlanır. Herhangi bir işlem geçmişi varsa otomatik migrasyon YASAKTIR.
        baseline = state.get("start_equity", state.get("peak"))
        mismatch = False
        if baseline is not None:
            try:
                previous = float(baseline)
            except (TypeError, ValueError) as e:
                raise RuntimeError(f"Birleşik portföy başlangıç sermayesi geçersiz; güvenli başlatma iptal edildi: {self.state_path}") from e
            if previous != previous or abs(previous) == float("inf"):
                raise RuntimeError(f"Birleşik portföy başlangıç sermayesi sonlu değil; güvenli başlatma iptal edildi: {self.state_path}")
            mismatch = abs(previous - self.start_equity) > max(1e-8, abs(self.start_equity) * 1e-9)

        if mismatch:
            has_history = bool(self.adapter.positions or self.adapter.closed or self.adapter.open_orders())
            if has_history:
                raise RuntimeError(
                    f"Birleşik paper sermayesi {previous:g} → {self.start_equity:g} değişmiş ancak emir/pozisyon/geçmiş var; "
                    "otomatik sıfırlama yapılmadı. Önce defter migrasyonu gerekir."
                )
            return {**empty, "halted": state.get("halted"), "btc_hist": state.get("btc_hist") or [],
                    "eq_hist": [], "capital_migration": {"from": previous, "to": self.start_equity}}

        state["start_equity"] = self.start_equity
        state.setdefault("peak", None)
        state.setdefault("day", None)
        state.setdefault("day_start", None)
        state.setdefault("issue_since", {})
        state.setdefault("halted", None)
        return state

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.st["start_equity"] = self.start_equity
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.st, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.state_path)

    def equity(self, prices: Mapping[str, float] | None = None) -> float:
        eq = self.start_equity + self.adapter.closed_net_usd
        for inst, p in self.adapter.positions.items():
            px = (prices or {}).get(inst) or p.get("last") or p["entry"]
            sg = 1 if p["side"] == "long" else -1
            eq += p["realized_usd"] - p["fees_usd"] + p["funding_usd"] + sg * (px - p["entry"]) * p["qty"]
        return eq

    def loss_streak(self) -> int:
        return self.adapter.closed_loss_streak

    def portfolio(self, now_ms: int, prices=None, data_issue: str | None = None) -> PortfolioState:
        eq = self.equity(prices)
        day = _day(now_ms)
        if self.st.get("day") != day:
            self.st["day"], self.st["day_start"] = day, eq
        self.st["peak"] = max(float(self.st.get("peak") or eq), eq)
        pos = []
        for p in self.adapter.positions.values():
            stop = p.get("stop")
            sg = 1 if p["side"] == "long" else -1
            risk = max(0.0, sg * (p["entry"] - stop) * p["qty"]) if stop else float(p.get("risk_usd") or 0.0)
            pos.append(OpenPosition(inst=p["inst"], side=p["side"], risk_usd=risk, notional_usd=p["qty"] * p["entry"],
                                    margin_usd=p.get("margin_usd") or 0.0, cluster=p.get("cluster"),
                                    idempotency_key=p.get("decision_key") or ""))
        return PortfolioState(equity=eq, peak_equity=float(self.st["peak"]), day_start_equity=float(self.st["day_start"]),
                              positions=tuple(pos), loss_streak=self.loss_streak(), halted=self.st.get("halted"),
                              data_issue=data_issue)

    # ── yardımcılar ──────────────────────────────────────────────
    def _stop_order(self, inst: str) -> dict | None:
        return next((o for o in self.adapter.open_orders(inst) if o["intent"]["order_type"] in
                     (OrderType.STOP_MARKET.value, OrderType.TRAILING_STOP.value)), None)

    def _protect(self, pos: dict, now: int, quote: Quote | None) -> None:
        """Pozisyonun reduce-only koruyucu stop emri yoksa oluştur (ör. yeniden başlatma sonrası)."""
        if self._stop_order(pos["inst"]) or not pos.get("stop"):
            return
        n = sum(1 for o in self.adapter.orders.values() if o["intent"]["idempotency_key"] == pos["decision_key"])
        it = ExecutionIntent(idempotency_key=pos["decision_key"], symbol=pos["symbol"], inst=pos["inst"],
                             side=pos["side"], action="close", order_type=OrderType.STOP_MARKET, qty=pos["qty"],
                             created_ts_ms=now, stop_price=float(pos["stop"]), reduce_only=True, seq=n,
                             metadata={"why": "stop"})
        self.adapter.submit(it, quote, now)

    def _exit(self, pos: dict, qty: float, why: str, code: str, now: int, quote: Quote | None) -> None:
        n = sum(1 for o in self.adapter.orders.values() if o["intent"]["idempotency_key"] == pos["decision_key"])
        it = ExecutionIntent(idempotency_key=pos["decision_key"], symbol=pos["symbol"], inst=pos["inst"],
                             side=pos["side"], action="close" if qty >= pos["qty"] - 1e-12 else "reduce",
                             order_type=OrderType.MARKET, qty=min(qty, pos["qty"]), created_ts_ms=now, reduce_only=True,
                             seq=n, metadata={"why": f"{code}: {why}"})
        res = self.adapter.submit(it, quote, now)
        if res.status is OrderStatus.REJECTED and quote is None:
            # fiyat yok: çıkış emri doldurulamaz; koruyucu stop emri yerinde kalır, bir sonraki turda tekrar denenir
            self.adapter.orders.pop(it.client_order_id, None)

    # ── tur ──────────────────────────────────────────────────────
    def step(self, ctxs: Iterable[Mapping[str, Any]], quotes: Mapping[str, Quote], *, now_ms: int | None = None,
             meta: Mapping[str, Mapping] | None = None, ws_healthy: bool | None = None,
             global_issue: str | None = None, closed_bars: Mapping[str, Mapping] | None = None,
             clusters: Mapping[str, str] | None = None, funding_events: Iterable[tuple] = ()) -> dict:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        ctxs = list(ctxs)
        raw_by_inst = {c.get("inst"): c for c in ctxs}
        # 1) fiyatlar
        fills = []
        for inst, q in quotes.items():
            if inst in self.adapter.positions or self.adapter.open_orders(inst):
                fills += self.adapter.on_quote(q, now)
        # 2) bağlamlar
        mctxs = []
        for c in ctxs:
            m = (meta or {}).get(c.get("inst")) or {}
            try:
                mctxs.append(self.factors.from_legacy_ctx(
                    c, now_ms=now, live_ts_ms=now if c.get("live_bar") else None, data_issue=c.get("suspect"),
                    ws_healthy=ws_healthy, list_ts_ms=m.get("list_ts"), coin_class=c.get("coin_class")))
            except Exception as e:  # noqa: BLE001
                self.engine.errors.append(f"bağlam {c.get('sym')}: {type(e).__name__}: {e}")
        by_inst = {m.inst: m for m in mctxs}
        issues = self.st.setdefault("issue_since", {})
        for inst, m in by_inst.items():
            bad = m.data_quality is not None and not m.data_quality.ok
            if bad:
                issues.setdefault(inst, now)
            else:
                issues.pop(inst, None)
        for inst, rate, settle in funding_events:              # funding kesimleri (pozisyon açıkken olanlar)
            pos = self.adapter.positions.get(inst)
            if pos and settle > pos["opened_ts"] and settle not in pos.setdefault("fund_done", []):
                q = quotes.get(inst)
                self.adapter.apply_funding(inst, float(rate), q.last if q else pos.get("last") or pos["entry"], now)
                pos["fund_done"].append(settle)
        for inst, pos in self.adapter.positions.items():
            q = quotes.get(inst)
            if q is not None and q.valid:
                self._mark(pos, q.last, now)
        # 3) çıkışlar
        exits_done = []
        for inst, pos in list(self.adapter.positions.items()):
            q = quotes.get(inst)
            mc = by_inst.get(inst)
            price = q.last if q is not None and q.valid else None
            if price is None:
                continue
            regime = mc.regime.regime if mc and mc.regime else None
            acts = evaluate_exits(pos, price, now, self.cfg, atr=pos.get("atr"), regime=regime,
                                  closed_bar_ctx=(closed_bars or {}).get(inst),
                                  data_issue_since_ms=issues.get(inst), exit_rule=pos.get("exit_rule"))
            for a in acts:
                if inst not in self.adapter.positions:
                    break
                if a.kind == "move_stop":
                    so = self._stop_order(inst)
                    if so:
                        self.adapter.amend_stop(so["cid"], a.stop, now)
                    pos["stop"] = a.stop
                    pos.setdefault("stop_moves", []).append({"ts": now, "stop": a.stop, "code": a.code})
                else:
                    self._exit(pos, a.qty, a.why, a.code, now, q)
                    exits_done.append({"inst": inst, "code": a.code, "why": a.why})
                    so = self._stop_order(inst)
                    if so and inst in self.adapter.positions and so["intent"]["qty"] > self.adapter.positions[inst]["qty"]:
                        so["intent"]["qty"] = self.adapter.positions[inst]["qty"]      # stop miktarı kalan pozisyona
            if inst in self.adapter.positions:
                self._protect(self.adapter.positions[inst], now, q)
        # 4) kararlar
        decisions = self.engine.decide(mctxs, now)
        self.journal.write(decisions, now)
        self.journal.prune(now)
        # 5) açılışlar
        prices = {k: q.last for k, q in quotes.items() if q.valid}
        opened, risk_log = [], []
        gi = global_issue or (None if ws_healthy is not False else "canlı veri akışı kopuk")
        for d in sorted(decisions, key=lambda x: -(x.net_edge_bp or 0)):
            op = (d.plan or {}).get("operasyonel") or {}
            # YETKI_GOLGE operasyonel shadow: yalnız gölge modda, yalnız uygunluk kontrolünü geçen kararlar
            operational = self.mode == "shadow" and d.decision is Decision.SHADOW and bool(op.get("uygun"))
            if d.decision not in (Decision.OPEN, Decision.REDUCE_SIZE) and not operational:
                continue
            inst = d.plan.get("inst") if d.plan else None
            if not inst:
                continue
            if operational and d.idempotency_key in (self.st.get("op_keys") or []):
                continue                                             # aynı fırsat ikinci pozisyon açamaz
            if d.decision is Decision.SHADOW and not operational:
                continue
            if operational:
                # SHADOW kararının boyut çarpanı 0'dır; operasyonel yol OPEN ile aynı eşlemeyi (1.0) kullanır, yoksa
                # RiskGate boyutu 0 hesaplayıp LOT kuralıyla reddeder. Eşikler değişmez.
                rp = dict(d.risk_proposal or {})
                taban = float(rp.get("taban_risk_yuzde") or 0.5)
                rp.update({"boyut_carpani": 1.0, "risk_yuzde": round(taban * 1.0, 4),
                           "not": "operasyonel shadow: OPEN ile aynı boyut eşlemesi"})
                d_use = replace(d, decision=Decision.OPEN, targets=tuple(op.get("hedef") or ()), risk_proposal=rp)
            else:
                tv = classify_targets(d.side, d.entry, d.stop, d.targets)     # geçersiz (negatif/NaN) hedef asla yazılmaz
                d_use = replace(d, targets=tuple(tv["hedef"] + tv["mekanik"]))
            pf = self.portfolio(now, prices, data_issue=gi)
            raw = raw_by_inst.get(inst) or {}
            q = quotes.get(inst)
            # Karar motoru bağlam fiyatı üretmiş olsa bile, PAPER emri için güncel ve
            # tutarlı BBO zorunludur. Bu kapı yalnız yeni girişe uygulanır; yukarıdaki
            # açık pozisyon çıkış/koruma yönetimini durdurmaz.
            if (q is None or not q.valid or
                    not all(isinstance(v, (int, float)) and 0 < v < float("inf")
                            for v in (q.bid, q.ask, q.last)) or q.bid >= q.ask):
                risk_log.append({"sym": d.symbol, "strateji": d.strategy, "karar": Decision.REJECT.value,
                                 "kodlar": ["GIRIS_BBO_YOK"], "aciklama":
                                 "Yeni PAPER girişi için güncel, pozitif ve çapraz olmayan bid/ask gerekli.",
                                 "operasyonel": operational})
                continue
            mr = MarketRisk(inst=inst, price=q.last, vol24_usd=raw.get("vol24_usd"),
                            high_vol=bool(raw.get("high_vol")), cluster=(clusters or {}).get(inst),
                            meta=(meta or {}).get(inst))
            rd = self.risk.evaluate(d_use, pf, mr)
            risk_log.append({"sym": d.symbol, "strateji": d.strategy, "karar": rd.decision.value,
                             "kodlar": list(rd.reason_codes), "aciklama": rd.explanation_tr,
                             "operasyonel": operational})
            if not rd.approved:
                continue
            res = self._open(d_use, rd, inst, q, now, mr.cluster)
            if operational and res is not None:
                keys = self.st.setdefault("op_keys", [])
                keys.append(d.idempotency_key)
                del keys[:-500]
            if res is not None:
                opened.append({"sym": d.symbol, "durum": res.status.value, "dolan": res.filled_qty, "fiyat": res.avg_price,
                               "operasyonel": operational})
        self.last_risk = risk_log[-20:]
        op_neden: dict[str, int] = {}                          # YETKI_GOLGE adaylarının uygunsuzluk sebepleri (tanılama)
        for d in decisions:
            if d.decision is Decision.SHADOW and set(d.reason_codes or ()) == {"YETKI_GOLGE"}:
                for n in (((d.plan or {}).get("operasyonel") or {}).get("neden") or ["uygun"]):
                    op_neden[n] = op_neden.get(n, 0) + 1
        # 6) portföy
        pf = self.portfolio(now, prices)
        self._history(now, pf.equity, (raw_by_inst.get("BTC-USDT-SWAP") or {}).get("price")
                      or prices.get("BTC-USDT-SWAP"))
        self._save()
        self.adapter.save()
        counts: dict[str, int] = {}
        for d in decisions:
            counts[d.decision.value] = counts.get(d.decision.value, 0) + 1
        summary = {"ts": now, "mod": self.mode, "yurutme": "paper", "bakiye": round(pf.equity, 2),
                   "baslangic": self.start_equity, "gun_basi": round(float(self.st.get("day_start") or pf.equity), 2),
                   "karar": counts, "coin": len(mctxs),
                   "zirve": round(pf.peak_equity, 2), "dusus_yuzde": round(pf.drawdown_pct, 2),
                   "gun_kayip_yuzde": round(pf.day_loss_pct, 2), "kayip_serisi": pf.loss_streak,
                   "acik": len(self.adapter.positions), "acilan": opened, "cikislar": exits_done,
                   "dolumlar": len(fills), "risk": self.last_risk, "kararlar": len(decisions),
                   "hatalar": self.engine.errors[-5:], "kurtarma": self.recovery_notes,
                   "operasyonel_nedenler": op_neden}
        self.engine.errors.clear()
        self.recovery_notes = []
        self._write_status(summary)
        return summary

    def _open(self, d: DecisionResult, rd, inst: str, q: Quote | None, now: int, cluster: str | None):
        plan = dict(d.plan or {})
        etype = plan.get("entry_type") or "market"
        if etype == "limit" and plan.get("limit_price"):
            otype, price, exp = OrderType.LIMIT, float(plan["limit_price"]), now + 4 * 3_600_000
        else:
            otype, price, exp = OrderType.MARKET, None, None
        md = {"strategy": d.strategy, "strategies": list(d.strategies or (d.strategy,)), "stop": d.stop,
              "targets": list(d.targets), "risk_usd": rd.risk_usd,
              "leverage": rd.leverage, "horizon": plan.get("horizon"), "max_holding_hours": plan.get("max_holding_hours"),
              "cluster": cluster, "timeframe": d.timeframe,
              "execution_scope": "shadow" if self.mode == "shadow" else "unified"}
        it = ExecutionIntent(idempotency_key=d.idempotency_key, symbol=d.symbol, inst=inst, side=d.side, action="open",
                             order_type=otype, qty=rd.qty, price=price, created_ts_ms=now, expires_ts_ms=exp,
                             metadata=md)
        res = self.adapter.submit(it, q, now)
        pos = self.adapter.positions.get(inst)
        if pos is not None and pos.get("decision_key") == d.idempotency_key:
            pos["atr"] = plan.get("atr")
            pos["exit_rule"] = plan.get("exit_rule")
            pos["pp"] = plan.get("pp")
            pos["legacy_exits"] = bool(plan.get("exit_rule") or plan.get("pp"))
            pos["explanation_tr"] = d.explanation_tr
            pos["risk_text"] = rd.explanation_tr
            pos["strategies"] = list(d.strategies or (d.strategy,))
            pos["costs"] = to_jsonable(d.costs) if d.costs else None
            if self.mode == "shadow" and (plan.get("operasyonel") or {}).get("uygun"):
                op = plan["operasyonel"]
                pos.update(operational=True, karar="YETKI_GOLGE", stop_initial=pos["stop"], target_type=op.get("hedef_tipi"),
                           mechanical_reference=list(op.get("mekanik") or []), rejected_targets=list(op.get("reddedilen") or []),
                           exit_policy=op.get("cikis_politikasi"))
            pos["exit_plan"] = next((l for l in d.explanation_tr.split("\n") if l.startswith("Çıkış planı")), None)
            if q is not None and q.valid:
                self._mark(pos, q.last, now)
            if d.stop and pos["entry"] and abs(pos["entry"] - (d.entry or pos["entry"])) > 0:
                shift = pos["entry"] - d.entry                       # dolum farkı: stop/hedef aynı mesafede kalır
                pos["stop"] = pos["stop_init"] = d.stop + shift
                pos["targets"] = [t + shift for t in d.targets]
                sg = 1 if pos["side"] == "long" else -1
                pos["risk_usd"] = max(0.0, sg * (pos["entry"] - pos["stop"]) * pos["qty"])
            self._protect(pos, now, q)
        return res

    def _mark(self, pos: dict, price: float, now: int) -> None:
        """Canlı fiyatla pozisyonun anlık değerleri (net: ücret + funding dahil) ve fiyat geçmişi (sparkline)."""
        sg = 1 if pos["side"] == "long" else -1
        risk0 = float(pos.get("risk_usd") or 0) or None
        gross = sg * (price - pos["entry"]) * pos["qty"]
        unreal = gross + pos["realized_usd"] - pos["fees_usd"] + pos["funding_usd"]
        pos.update(current_price=price, last=price, updated_ts=now, unrealized_usd=unreal,
                   unrealized_pct=sg * (price / pos["entry"] - 1) * 100,
                   unrealized_r=(unreal / risk0) if risk0 else None)
        if pos["unrealized_r"] is not None:
            r = pos["unrealized_r"]
            pos["gross_r"] = gross / risk0
            pos["peak_r"] = max(float(pos.get("peak_r") or 0.0), r)
            pos["mfe_r"] = max(float(pos["mfe_r"]), r) if pos.get("mfe_r") is not None else r
            pos["mae_r"] = min(float(pos["mae_r"]), r) if pos.get("mae_r") is not None else r
            pos["giveback_r"] = max(0.0, pos["peak_r"] - r)
        h = pos.setdefault("hist", [])
        if not h or now - h[-1][0] >= 300_000:                 # 5 dakikada bir nokta, en fazla 288 (24 saat)
            h.append([now, price])
            del h[:-288]

    def _history(self, now: int, equity: float, btc: float | None) -> None:
        for key, val in (("eq_hist", equity), ("btc_hist", btc)):
            if val is None:
                continue
            h = self.st.setdefault(key, [])
            if not h or now - h[-1][0] >= 300_000:
                h.append([now, round(float(val), 6)])
                del h[:-576]                                   # 48 saat

    def _write_status(self, summary: dict) -> None:
        out = dict(summary)
        # BBO/WS tanılaması her yayında durum.json içinde bulunur (anahtar hep yazılır: yoksa kod eski demektir)
        out["tanilama"] = getattr(self, "diag", None) or {"hazir": False}
        out["pozisyonlar"] = [{k: p.get(k) for k in (
            "symbol", "inst", "side", "qty", "entry", "current_price", "stop", "stop_init", "targets", "strategy",
            "inst", "strategies", "opened_ts", "updated_ts", "realized_usd", "fees_usd", "funding_usd", "slip_usd", "risk_usd",
            "leverage", "unrealized_usd", "unrealized_pct", "unrealized_r", "peak_r", "costs", "exit_plan",
            "explanation_tr", "risk_text", "hist", "operational", "karar", "stop_initial", "target_type",
            "mechanical_reference", "rejected_targets", "exit_policy", "gross_r", "mfe_r", "mae_r", "giveback_r")}
            for p in self.adapter.positions.values()]
        out["kapananlar"] = [{k: c.get(k) for k in (
            "symbol", "side", "strategy", "strategies", "entry", "exit_px", "qty", "leverage", "net_usd", "r",
            "fees_usd", "slip_usd", "funding_usd", "realized_usd", "opened_ts", "closed_ts", "exit_reason",
            "operational", "karar", "exit_policy", "target_type", "mfe_r", "mae_r", "giveback_r", "peak_r")}
            for c in self.adapter.closed[-100:]]
        out["eq_hist"] = self.st.get("eq_hist") or []
        out["btc_hist"] = self.st.get("btc_hist") or []
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / "durum.tmp"
        tmp.write_text(json.dumps(to_jsonable(out), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.root / "durum.json")
        if self.journal_root != self.root:                    # gölge özeti (karar turu) karar günlüğünün yanında
            oz = {k: summary.get(k) for k in ("ts", "mod", "coin", "karar", "hatalar")}
            t2 = self.journal_root / "ozet.tmp"
            t2.write_text(json.dumps(to_jsonable(oz), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            t2.replace(self.journal_root / "ozet.json")
            try:                                             # İşleme hazır olma özeti (yalnız izleme)
                hazirlik.write_summary(self.journal_root, int(summary["ts"]))
            except Exception as e:  # noqa: BLE001
                self.engine.errors.append(f"hazırlık özeti: {type(e).__name__}: {e}")

    def halt(self, reason: str | None) -> None:
        """Elle durdurma (None → devam)."""
        self.st["halted"] = reason
        self._save()


def quote_from_book(inst: str, book_row: tuple | None, now_s: float | None = None, max_age_s: float = 15) -> Quote | None:
    """Engine.book satırı (last, bid, ask, alındığı an[sn]) → Quote. Bayat ya da eksikse None (dolum varsayılmaz)."""
    if not book_row:
        return None
    last, bid, ask, ts = book_row
    now_s = time.time() if now_s is None else now_s
    if now_s - ts > max_age_s or not (bid and ask and last):
        return None
    return Quote(inst=inst, bid=float(bid), ask=float(ask), last=float(last), ts_ms=int(ts * 1000))
