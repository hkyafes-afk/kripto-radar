"""UnifiedDecisionEngine: bütün stratejilerin fırsatlarından coin başına TEK karar (PR 2: yalnız gölgede kaydedilir).

Akış (her coin bağlamı için):
  1. Stratejilerden fırsatları topla (hata veren strateji atlanır, diğerleri sürer).
  2. Aynı coin + yön + zaman ufku → tek fırsat: kanıtlar ve strateji listesi birleşir, tek risk bütçesi (ayrı pozisyon yok).
     Birincil fırsat: önce paper yetkili olan, sonra maliyet kararı (PASS > SHADOW > REJECT), sonra net avantaj, güven.
  3. Kapılar sırayla: veri sorunu (fail-closed) → veri kapsamı → yetki (hepsi gölge-yetkili mi) → v10 politika kapıları
     (radar/policy.gate: oynaklık, stop, kalabalık, Kaan-yatay, BTC rejim uyumu) → coin sınıfı C → maliyet (CostEngine).
  4. Aynı coinde long ve short ikisi de açılabilir çıkarsa çelişki: rejim izni, sonra net avantaj; kaybeden REJECT.
  5. DecisionResult + Türkçe açıklama (neden, kanıtlar, maliyet sonrası kalan, eksik veri, çıkış planı).
Risk miktarı burada yalnız ÖNERİDİR (risk_proposal); son söz PR 3'teki RiskGate'in. Bu modül emir göndermez.
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping

from .. import policy
from .costs import CostEngine
from .models import (CostEstimate, Decision, DecisionResult, Horizon, MarketContext, Opportunity, OrderType, Regime,
                     Side, StrategyAuthority, Tradability, is_finite_number)
from .strategies import Strategy, build_strategies
from .targets import classify

_AUTH = {StrategyAuthority.PAPER: 2, StrategyAuthority.SHADOW_ONLY: 1, StrategyAuthority.DISABLED: 0}
_TRAD = {Tradability.PASS: 2, Tradability.SHADOW: 1, Tradability.REJECT: 0}
STRATEGY_TR = {"swing_trend": "günlük trend", "compression_breakout": "sıkışmadan patlama",
               "controlled_breakout": "kontrollü kırılım", "panic_reversal": "panik dönüşü", "short_trend": "trend short'u",
               "intraday_trend": "gün içi trend", "microstructure": "mikro yapı", "legacy_murat": "geri çekilme sinyali (eski)"}
DECISION_TR = {Decision.OPEN: "AÇ (paper)", Decision.REDUCE_SIZE: "KÜÇÜK BOYUTLA AÇ (paper)",
               Decision.SHADOW: "GÖLGEDE ÖLÇ", Decision.REJECT: "REDDET"}
REASON_TR = (
    ("VERI_SORUNU", "veri sorunu: yeni pozisyon açılmaz"),
    ("ZORUNLU_EKSIK", "zorunlu veri eksik"),
    ("ZORUNLU_BAYAT", "zorunlu veri bayat"),
    ("ZORUNLU_GECERSIZ", "zorunlu veri geçersiz"),
    ("KAPSAM_DUSUK", "veri kapsamı düşük"),
    ("YETKI_GOLGE", "strateji yalnız gölgede ölçülüyor (kanıt birikene kadar)"),
    ("POLITIKA", "v10 politika kapısı"),
    ("SINIF_C", "1 yıldan genç coin: gerçek risk yok"),
    ("BRUT_AVANTAJ_BILINMIYOR", "beklenen avantaj bilinmiyor (geçmiş kanıt yok)"),
    ("MALIYET_SONRASI_AVANTAJ_YOK", "maliyetler avantajı siliyor"),
    ("AVANTAJ_TAMPONDAN_KUCUK", "maliyet sonrası avantaj belirsizlik tamponundan küçük"),
    ("MALIYET_GUVENI_DUSUK", "maliyet girdileri güvenilmez"),
    ("DERINLIK_YETERSIZ", "emir defteri derinliği yetersiz"),
    ("KISMI_DOLUM_YUKSEK", "emrin büyük kısmı dolmazdı"),
    ("TEK_KIRILIM", "yalnız kırılım kanıtı var (trend/sıkışma teyidi yok): boyut yarım"),
    ("CELISKI_KAYBETTI", "aynı coinde ters yönlü daha güçlü fırsat var"),
    ("CELISKI_REJIM_BELIRSIZ", "aynı coinde long ve short çelişiyor, rejim belirsiz"),
)


def reason_tr(code: str) -> str:
    for prefix, text in REASON_TR:
        if code.startswith(prefix):
            tail = code[len(prefix):].lstrip(":")
            return f"{text} ({tail})" if tail and prefix in ("ZORUNLU_EKSIK", "ZORUNLU_BAYAT", "ZORUNLU_GECERSIZ",
                                                              "KAPSAM_DUSUK", "POLITIKA", "VERI_SORUNU") else text
    return code


def _fmt(p: float | None) -> str:
    if p is None or not is_finite_number(p):
        return "-"
    return f"{p:.6g}"


class UnifiedDecisionEngine:
    def __init__(self, cfg: Mapping | None = None, strategies: Iterable[Strategy] | None = None,
                 costs: CostEngine | None = None):
        self.cfg = dict(cfg or {})
        self.strategies = list(strategies) if strategies is not None else build_strategies(self.cfg)
        self.costs = costs or CostEngine(self.cfg)
        self.base_risk_pct = float(((self.cfg.get("risk") or {}).get("risk_yuzde")) or 0.5)
        self.errors: list[str] = []

    # ── toplama ─────────────────────────────────────────────────────
    def collect(self, ctx: MarketContext) -> list[Opportunity]:
        out = []
        for s in self.strategies:
            try:
                out.extend(s.evaluate(ctx))
            except Exception as e:  # noqa: BLE001 — bir stratejinin hatası diğerlerini durdurmasın
                self.errors.append(f"{s.name} {ctx.symbol}: {type(e).__name__}: {e}")
        return out

    def cost_for(self, o: Opportunity, ctx: MarketContext) -> CostEstimate:
        atr = ctx.raw.get("atr") if ctx.raw else None
        if not is_finite_number(atr):
            c = ctx.candles.get(o.timeframe)
            atr = c.atr if c is not None else None
        return self.costs.estimate(
            side=o.side, horizon=o.horizon, entry_price=o.entry, gross_edge_bp=o.gross_edge_bp,
            entry_order=o.entry_type if o.entry_type in (OrderType.MARKET, OrderType.LIMIT, OrderType.IOC_LIMIT)
            else OrderType.MARKET,
            exit_order=OrderType.TAKE_PROFIT_LIMIT if o.targets else OrderType.MARKET,
            bid=ctx.liquidity.bid, ask=ctx.liquidity.ask, expected_holding_hours=o.expected_holding_hours,
            funding_rate=ctx.derivatives.funding_rate, funding_interval_h=ctx.derivatives.funding_interval_h,
            funding_ann_pct=ctx.derivatives.funding_ann_pct, atr=atr, timeframe=o.timeframe,
            coin_class=ctx.coin_class.value if ctx.coin_class else None)

    # ── karar ───────────────────────────────────────────────────────
    def decide(self, ctxs: Iterable[MarketContext], now_ms: int | None = None) -> list[DecisionResult]:
        out: list[DecisionResult] = []
        for ctx in ctxs:
            out.extend(self.decide_context(ctx, now_ms))
        return out

    def decide_context(self, ctx: MarketContext, now_ms: int | None = None) -> list[DecisionResult]:
        now = int(now_ms if now_ms is not None else ctx.ts_ms)
        opps = self.collect(ctx)
        if not opps:
            return []
        groups: dict[tuple[Side, Horizon], list[Opportunity]] = {}
        for o in opps:
            groups.setdefault((o.side, o.horizon), []).append(o)
        drafts = []
        for (side, hz), ops in groups.items():
            evals = [(o, self.cost_for(o, ctx)) for o in ops]
            evals.sort(key=lambda oc: (_AUTH[oc[0].authority], _TRAD[oc[1].tradability],
                                       oc[1].net_edge_bp if oc[1].net_edge_bp is not None else float("-inf"),
                                       oc[0].confidence), reverse=True)
            primary, cost = evals[0]
            decision, codes = self._gates(ctx, primary, cost, [o for o, _ in evals])
            drafts.append({"side": side, "horizon": hz, "primary": primary, "cost": cost, "ops": [o for o, _ in evals],
                           "decision": decision, "codes": codes})
        self._resolve_conflicts(ctx, drafts)
        return [self._result(ctx, d, now) for d in drafts]

    def _gates(self, ctx: MarketContext, o: Opportunity, cost: CostEstimate,
               ops: list[Opportunity]) -> tuple[Decision, list[str]]:
        codes: list[str] = []
        if ctx.data_quality.fail_closed:
            codes += [f"VERI_SORUNU:{c}" for c in ctx.data_quality.critical]
            return Decision.SHADOW, codes + list(cost.reason_codes)
        coverage = ctx.coverage_for(o.horizon)
        if coverage is not None and not coverage.sufficient_for_paper:
            codes += list(coverage.reason_codes)
            return Decision.SHADOW, codes + list(cost.reason_codes)
        # YETKI_GOLGE ilk engel olsa da politika ve yaş sınıfı da hesaplanır (kararı değiştirmez; izleme için)
        if all(x.authority is not StrategyAuthority.PAPER for x in ops):
            codes.append("YETKI_GOLGE")
        why = self._policy(ctx, o)
        if why:
            codes.append(f"POLITIKA:{why}")
        if ctx.coin_class is not None and ctx.coin_class.value == "C":
            codes.append("SINIF_C")
        if codes:
            return Decision.SHADOW, codes + list(cost.reason_codes)
        codes += list(cost.reason_codes)
        if cost.tradability is Tradability.REJECT:
            return Decision.REJECT, codes
        if cost.tradability is Tradability.SHADOW:
            return Decision.SHADOW, codes
        paper_strats = {x.strategy for x in ops if x.authority is StrategyAuthority.PAPER}
        if paper_strats == {"controlled_breakout"}:
            codes.append("TEK_KIRILIM")
            return Decision.REDUCE_SIZE, codes
        return Decision.OPEN, codes

    def _policy(self, ctx: MarketContext, o: Opportunity) -> str | None:
        """v10 politika kapıları (radar/policy.gate) — araştırmanın doğruladığı kurallar. Legacy fikirde özgün sözlük,
        yeni stratejide eşdeğer sözde-fikir kullanılır."""
        if o.legacy:
            idea = dict(o.legacy)
        else:
            idea = {"tf": o.timeframe, "kind": "trend", "mgr": o.strategy, "side": o.side.value, "entry": o.entry,
                    "sl": o.stop, "atr": (ctx.raw or {}).get("atr"),
                    "funding_rate": ctx.derivatives.funding_rate, "interval_h": ctx.derivatives.funding_interval_h}
        try:
            return policy.gate(idea, dict(ctx.raw or {}), self.cfg)
        except Exception as e:  # noqa: BLE001
            # Politika motoru hata verirse yeni pozisyon açmak güvenli değildir.
            self.errors.append(f"politika {ctx.symbol}: {type(e).__name__}: {e}")
            return f"POLITIKA_HATASI:{type(e).__name__}"

    def _resolve_conflicts(self, ctx: MarketContext, drafts: list[dict]) -> None:
        live = (Decision.OPEN, Decision.REDUCE_SIZE)
        by_hz: dict[Horizon, list[dict]] = {}
        for d in drafts:
            by_hz.setdefault(d["horizon"], []).append(d)
        for hz, ds in by_hz.items():
            L = [d for d in ds if d["side"] is Side.LONG and d["decision"] in live]
            S = [d for d in ds if d["side"] is Side.SHORT and d["decision"] in live]
            if not (L and S):
                continue
            reg = ctx.regime.regime
            if reg in (Regime.BULL, Regime.RECOVERY):
                win = L[0]
            elif reg in (Regime.BEAR, Regime.DETERIORATION):
                win = S[0]
            else:
                for d in L + S:
                    d["decision"] = Decision.SHADOW
                    d["codes"].append("CELISKI_REJIM_BELIRSIZ")
                continue
            for d in L + S:
                if d is not win:
                    d["decision"] = Decision.REJECT
                    d["codes"].append("CELISKI_KAYBETTI")

    def _result(self, ctx: MarketContext, d: dict, now: int) -> DecisionResult:
        o: Opportunity = d["primary"]
        cost: CostEstimate = d["cost"]
        ops: list[Opportunity] = d["ops"]
        decision: Decision = d["decision"]
        strategies = tuple(dict.fromkeys(x.strategy for x in ops))
        ev_for = tuple(dict.fromkeys(e for x in ops for e in x.evidence_for))
        ev_against = tuple(dict.fromkeys(e for x in ops for e in x.evidence_against))
        cov = ctx.coverage_for(d["primary"].horizon)
        missing = tuple(cov.missing) if cov else ()
        stale = tuple(cov.stale) if cov else ()
        mult = {Decision.OPEN: 1.0, Decision.REDUCE_SIZE: 0.5}.get(decision, 0.0)
        risk = {"taban_risk_yuzde": self.base_risk_pct, "boyut_carpani": mult,
                "risk_yuzde": round(self.base_risk_pct * mult, 4),
                "not": "öneri; son karar RiskGate'te (PR 3). Bu sürümde yalnız gölgede kaydedilir."}
        codes = tuple(dict.fromkeys(d["codes"]))
        return DecisionResult(
            decision=decision, symbol=ctx.symbol, side=o.side, strategy=o.strategy, timeframe=o.timeframe,
            confidence=o.confidence, gross_edge_bp=cost.gross_edge_bp, costs=cost, net_edge_bp=cost.net_edge_bp,
            entry=o.entry, stop=o.stop, targets=o.targets,
            expected_holding_hours=o.expected_holding_hours or o.max_holding_hours, risk_proposal=risk,
            evidence_for=ev_for, evidence_against=ev_against, missing_factors=missing, stale_factors=stale,
            reason_codes=codes, explanation_tr=self._explain(ctx, o, cost, decision, codes, strategies, ev_for,
                                                              ev_against, missing),
            ts_ms=now, idempotency_key=o.idempotency_key, strategies=strategies,
            plan={**self._plan(ctx, o), "operasyonel": self._operational(o, cost, decision, codes,
                                                                          ctx.liquidity.spread_durum)})

    @staticmethod
    def _operational(o: Opportunity, cost: CostEstimate, decision: Decision, codes: tuple,
                     spread_durum: str | None = None) -> dict:
        """YETKI_GOLGE → operasyonel shadow açılışı için uygunluk. Yalnız tek engel YETKI_GOLGE ise ve diğer tüm
        kapılar geçtiyse uygundur. PAPER/gerçek yetki VERİLMEZ; yalnız gölge sanal portföyde kullanılır."""
        neden = []
        if decision is not Decision.SHADOW:
            neden.append("karar gölge değil")
        if set(codes) != {"YETKI_GOLGE"}:
            neden.append("diğer engeller var: " + ", ".join(c for c in codes if c != "YETKI_GOLGE") if codes else "engel yok")
        if spread_durum != "spread_ok":                          # yalnız gerçek public BBO spread'i kabul edilir
            neden.append(spread_durum or "spread_missing")
        if cost.tradability is not Tradability.PASS:
            neden.append("maliyet kapısı geçmedi")
        if cost.net_edge_bp is None or cost.net_edge_bp <= 0:
            neden.append("net avantaj yok")
        geo_ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0
                     for v in (o.entry, o.stop))
        if not geo_ok or (o.entry == o.stop) or (o.side is Side.LONG and not o.stop < o.entry) \
                or (o.side is Side.SHORT and not o.stop > o.entry):
            neden.append("geçerli giriş/stop yok")
        if not (o.max_holding_hours and o.max_holding_hours > 0):
            neden.append("zaman çıkışı tanımlı değil")
        cl = classify(o.side, o.entry, o.stop, o.targets) if geo_ok else {"hedef": [], "mekanik": [], "reddedilen": list(o.targets)}
        return {"uygun": not neden, "neden": neden, "hedef": cl["hedef"], "mekanik": cl["mekanik"],
                "reddedilen": cl["reddedilen"], "hedef_tipi": "yapısal" if cl["hedef"] else "yok",
                "cikis_politikasi": "stop + iz + zaman" if (o.max_holding_hours and o.max_holding_hours > 0) else None}

    @staticmethod
    def _plan(ctx: MarketContext, o: Opportunity) -> dict:
        lg_ = o.legacy or {}
        c = ctx.candles.get(o.timeframe) if ctx.candles else None
        atr = lg_.get("atr") if isinstance(lg_.get("atr"), (int, float)) else (c.atr if c else None)
        rg = ctx.regime.regime if getattr(ctx, "regime", None) is not None else None
        return {"inst": o.inst, "horizon": o.horizon.value, "timeframe": o.timeframe, "entry_type": o.entry_type.value,
                "rejim": rg.value if rg is not None else None,
                "limit_price": o.limit_price, "max_holding_hours": o.max_holding_hours, "atr": atr,
                "exit_rule": dict(lg_["exit"]) if isinstance(lg_.get("exit"), dict) else None,
                "pp": dict(lg_["pp"]) if isinstance(lg_.get("pp"), dict) else None,
                "bar_ts_ms": o.bar_ts_ms, "coin_class": ctx.coin_class.value if ctx.coin_class else None}

    def _explain(self, ctx, o, cost, decision, codes, strategies, ev_for, ev_against, missing) -> str:
        side = "LONG" if o.side is Side.LONG else "SHORT"
        lines = [f"{ctx.symbol} {side} · {STRATEGY_TR.get(o.strategy, o.strategy)} · {o.timeframe} → "
                 f"{DECISION_TR.get(decision, decision.value)}"]
        why = [reason_tr(c) for c in codes if not c.startswith("KISMI_DOLUM:")]
        if why:
            lines.append("Neden: " + "; ".join(dict.fromkeys(why)) + ".")
        if len(strategies) > 1:
            lines.append("Aynı fırsatı bulan stratejiler: " + ", ".join(STRATEGY_TR.get(s, s) for s in strategies)
                         + " (tek pozisyon, tek risk bütçesi).")
        if ev_for:
            lines.append("Destekleyen: " + "; ".join(ev_for[:6]) + ".")
        if ev_against:
            lines.append("Karşı: " + "; ".join(ev_against[:6]) + ".")
        if cost.gross_edge_bp is None:
            lines.append(f"Maliyet: ~{cost.total_cost_bp:.0f} bp; beklenen avantaj bilinmiyor.")
        else:
            lines.append(f"Maliyet sonrası: brüt ~{cost.gross_edge_bp:.0f} bp − maliyet ~{cost.total_cost_bp:.0f} bp = "
                         f"{cost.edge_after_costs_bp:.0f} bp; belirsizlik tamponu {cost.uncertainty_buffer_bp:.0f} bp → "
                         f"net {cost.net_edge_bp:.0f} bp.")
        if missing:
            lines.append("Eksik veri: " + ", ".join(missing) + ".")
        if cost.missing_inputs:
            lines.append("Maliyette varsayılanlar: " + ", ".join(cost.missing_inputs) + ".")
        hold = o.max_holding_hours
        rule = exit_rule_tr((o.legacy or {}).get("exit"))
        pp = (o.legacy or {}).get("pp") or {}
        if rule and pp.get("trail_now") and not pp.get("partial_frac"):
            tgt = f"hedef yok (uzak emniyet seviyesi {_fmt(o.targets[-1])})" if o.targets else "hedef yok"
        else:
            tgt = "hedef " + (", ".join(_fmt(t) for t in o.targets) or "yok")
        plan = f"Çıkış planı: giriş {_fmt(o.entry)}, stop {_fmt(o.stop)}, {tgt}"
        if rule:
            plan += f"; kural çıkışı: {rule}"
        if pp.get("trail_atr"):
            plan += f"; iz süren stop {pp['trail_atr']:g} ATR"
        plan += (f"; en fazla {hold / 24:.0f} gün" if hold and hold >= 48 else f"; en fazla {hold:.0f} saat" if hold else "")
        lines.append(plan + ".")
        return "\n".join(lines)


def exit_rule_tr(rule) -> str | None:
    """Legacy kural çıkışının Türkçesi (radar/quant.exit_signal ile aynı anlam)."""
    if not isinstance(rule, Mapping):
        return None
    t, n = rule.get("type"), rule.get("n")
    return {"ma": f"günlük kapanış {n} günlük ortalamanın ters tarafına geçince",
            "chan": f"{n} günlük dip/tepe kırılınca", "mid": "fiyat orta banda dönünce (patlama başarısız)",
            "skor": "puan tablosu pozisyonun tersine dönünce", "mean": f"{n or 20} günlük ortalamaya dönüş tamamlanınca"
            }.get(t)
