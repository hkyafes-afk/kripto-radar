"""CostEngine: bir fırsatın maliyet sonrası avantajı ve işlem yapılabilirliği.

Formül (GOREV.md, Aşama 3), her kalem nominalin baz puanı (bp) cinsinden:

    edge_after_costs = gross_edge − entry_fee − exit_fee − spread − slippage − stop_slippage − funding − missed_fill
    net_edge         = edge_after_costs − execution_uncertainty_buffer

Karar:
    edge_after_costs ≤ 0                     → REJECT  (maliyetten sonra avantaj yok)
    0 < edge_after_costs ≤ buffer (net ≤ 0)  → SHADOW  (pozitif ama belirsizlik tamponundan küçük)
    net > 0 ve güven ≥ min_guven             → PASS    (RiskGate'e gider; son söz RiskGate'in)
    brüt avantaj bilinmiyorsa                → SHADOW  (BRUT_AVANTAJ_BILINMIYOR; avantaj uydurulmaz)
Kısa ufukta tampon daha büyük (mikro > gün içi > swing): daha sıkı eşik.

Mevcut sistemle tutarlılık: ücret oranları `ledger.fees_cfg` (config.yaml → ucret; v8 sert model: liste ücretinin 2 katı),
emir defteri dolumu `ledger.vwap_fill`, stop kayması `ledger.stop_slip_bp` aynen kullanılır; yeni bir ücret tablosu yok.
Girdi eksikse muhafazakâr varsayım kullanılır ve `missing_inputs` / `assumptions` alanlarına yazılır, güven düşer.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .. import ledger as lg
from .flags import unified_cfg
from .models import CostEstimate, CostLeg, Horizon, OrderType, Side, Tradability, is_finite_number

TAKER_ENTRY = (OrderType.MARKET, OrderType.IOC_LIMIT, OrderType.STOP_MARKET)
MAKER = (OrderType.LIMIT, OrderType.TAKE_PROFIT_LIMIT)

PENALTY = {"spread": 0.15, "derinlik": 0.15, "funding": 0.10, "atr": 0.10, "tutma_suresi": 0.10, "fiyat": 0.5}


def gross_edge_from_expectancy(expectancy_r: float, stop_bp: float) -> float:
    """Beklenen R (maliyet öncesi) → nominalin bp'si. Ör. +0,3R, stop %4 (400 bp) → 120 bp."""
    return float(expectancy_r) * float(stop_bp)


def bp_to_r(bp: float, stop_bp: float) -> float | None:
    return bp / stop_bp if stop_bp and stop_bp > 0 else None


def _spread_bp(bid: Any, ask: Any) -> float | None:
    if is_finite_number(bid) and is_finite_number(ask) and bid > 0 and ask >= bid:
        mid = (bid + ask) / 2
        return (ask - bid) / mid * 1e4
    return None


class CostEngine:
    def __init__(self, cfg: Mapping | None = None):
        self.cfg = cfg or {}
        self.fc = lg.fees_cfg(dict(self.cfg))
        self.U = unified_cfg(self.cfg)["maliyet"]

    # ── oranlar ─────────────────────────────────────────────────────
    def fee_bp(self, order_type: OrderType, instrument: str = "swap") -> float:
        """Emir türüne göre ücret (bp). Spot için config'te maker yoksa taker kullanılır (muhafazakâr)."""
        if instrument == "spot":
            taker = float(self.fc.get("spot_taker_yuzde", 0.10)) * 100
            maker = float(self.fc.get("spot_maker_yuzde", self.fc.get("spot_taker_yuzde", 0.10))) * 100
        else:
            taker = float(self.fc["taker_yuzde"]) * 100
            maker = float(self.fc["maker_yuzde"]) * 100
        return maker if order_type in MAKER else taker

    def _h(self, key: str, horizon: Horizon) -> float:
        return float(self.U[key][horizon.value])

    # ── tek bacaklı yönlü işlem ─────────────────────────────────────
    def estimate(self, *, side: Side | str, horizon: Horizon, entry_price: float, gross_edge_bp: float | None,
                 notional_usd: float | None = None, entry_order: OrderType = OrderType.MARKET,
                 exit_order: OrderType = OrderType.MARKET, bid: float | None = None, ask: float | None = None,
                 book_levels: Sequence[tuple[float, float]] | None = None,
                 expected_holding_hours: float | None = None, funding_rate: float | None = None,
                 funding_interval_h: float | None = None, funding_ann_pct: float | None = None,
                 atr: float | None = None, timeframe: str | None = None, coin_class: str | None = None,
                 stop_probability: float | None = None) -> CostEstimate:
        """book_levels: alınacak taraftaki emir defteri [(fiyat, coin miktarı), ...] en iyiden kötüye
        (long için ask'ler, short için bid'ler). Verilmezse kayma varsayılır."""
        side = Side.parse(side)
        missing: list[str] = []
        assumptions: list[str] = []
        codes: list[str] = []
        conf = 1.0
        if not (is_finite_number(entry_price) and entry_price > 0):
            missing.append("fiyat")
            conf -= PENALTY["fiyat"]
            codes.append("FIYAT_GECERSIZ")
            entry_price = 1.0

        p_stop = stop_probability if is_finite_number(stop_probability) else float(self.U["stop_olasiligi"])
        p_stop = min(1.0, max(0.0, float(p_stop)))
        if not is_finite_number(stop_probability):
            assumptions.append(f"stop'la çıkma olasılığı {p_stop:.2f} varsayıldı")

        # Ücretler: giriş emir türüne göre; çıkış = stop (taker) olasılığı + planlanan çıkış türü
        entry_fee = self.fee_bp(entry_order)
        planned_exit_fee = self.fee_bp(exit_order)
        exit_fee = p_stop * self.fee_bp(OrderType.STOP_MARKET) + (1 - p_stop) * planned_exit_fee

        # Spread: her taker geçişinde yarım spread
        spread = _spread_bp(bid, ask)
        if spread is None:
            spread = self._h("varsayilan_spread_bp", horizon)
            missing.append("spread")
            conf -= PENALTY["spread"]
            assumptions.append(f"spread {spread:g} bp varsayıldı")
        entry_cross = 1.0 if entry_order not in MAKER else 0.0
        exit_cross = p_stop * 1.0 + (1 - p_stop) * (0.0 if exit_order in MAKER else 1.0)
        spread_cost = spread / 2 * (entry_cross + exit_cross)

        # Giriş kayması: emir defteri derinliğiyle VWAP (ledger.vwap_fill), yoksa varsayım
        fill_ratio, unfilled = 1.0, 0.0
        entry_slip = 0.0
        if entry_order not in MAKER:
            if book_levels and is_finite_number(notional_usd) and notional_usd > 0:
                qty = notional_usd / entry_price
                vwap, got = lg.vwap_fill(list(book_levels), qty)
                best = book_levels[0][0]
                if vwap is None or not best:
                    missing.append("derinlik")
                    conf -= PENALTY["derinlik"]
                    entry_slip = self._h("varsayilan_kayma_bp", horizon)
                else:
                    entry_slip = abs(vwap / best - 1) * 1e4
                    fill_ratio = min(1.0, got / qty) if qty > 0 else 1.0
                    unfilled = 1.0 - fill_ratio
                    if unfilled > 1e-9:
                        if entry_order is OrderType.IOC_LIMIT:
                            codes.append(f"KISMI_DOLUM:{fill_ratio:.2f}")
                            if unfilled > float(self.U["kismi_dolum_shadow_orani"]):
                                codes.append("KISMI_DOLUM_YUKSEK")
                        else:
                            codes.append("DERINLIK_YETERSIZ")
            else:
                entry_slip = self._h("varsayilan_kayma_bp", horizon)
                missing.append("derinlik")
                conf -= PENALTY["derinlik"]
                assumptions.append(f"giriş kayması {entry_slip:g} bp varsayıldı (emir defteri yok)")
        # Çıkış kayması: taker çıkışlarda girişle simetrik varsayılır
        exit_slip = exit_cross * (entry_slip if entry_order not in MAKER else self._h("varsayilan_kayma_bp", horizon))
        if entry_order in MAKER and exit_cross > 0:
            assumptions.append("maker girişte çıkış kayması varsayılan değerle")

        # Stop kayması (ledger.stop_slip_bp: taban + ATR payı, C sınıfında ×1,5)
        if not (is_finite_number(atr) and atr > 0):
            missing.append("atr")
            conf -= PENALTY["atr"]
            atr_for = 0.0
        else:
            atr_for = float(atr)
        stop_raw = lg.stop_slip_bp({"atr": atr_for, "entry": entry_price, "tf": timeframe, "coin_class": coin_class},
                                   self.fc)
        stop_exp = p_stop * stop_raw

        # Funding: beklenen tutma süresi boyunca (long + oran → maliyet)
        hours = expected_holding_hours
        if not (is_finite_number(hours) and hours >= 0):
            hours = self._h("varsayilan_tutma_saat", horizon)
            missing.append("tutma_suresi")
            conf -= PENALTY["tutma_suresi"]
            assumptions.append(f"tutma süresi {hours:g} saat varsayıldı")
        if is_finite_number(funding_rate):
            iv = funding_interval_h if is_finite_number(funding_interval_h) and funding_interval_h else 8.0
            per_hour = float(funding_rate) / float(iv)
        elif is_finite_number(funding_ann_pct):
            per_hour = float(funding_ann_pct) / 100.0 / 8760.0
        else:
            per_hour = float(self.U["varsayilan_funding_yillik_yuzde"]) / 100.0 / 8760.0
            if horizon is not Horizon.MICRO:
                missing.append("funding")
                conf -= PENALTY["funding"]
            assumptions.append(f"funding yıllık %{self.U['varsayilan_funding_yillik_yuzde']:g} varsayıldı "
                               "(long öder, short alır)")
        funding = side.sign * per_hour * float(hours) * 1e4
        if funding < 0 and not self.U["funding_gelir_kredisi"]:
            assumptions.append(f"funding geliri ({-funding:.1f} bp) avantaja sayılmadı")
            funding = 0.0

        missed = float(self.U["kacirma_tamponu_bp"]) if entry_order in MAKER + (OrderType.IOC_LIMIT,) else 0.0

        buffer = self._h("belirsizlik_tamponu_bp", horizon) + float(self.U["eksik_veri_tampon_bp"]) * len(missing)
        total = entry_fee + exit_fee + spread_cost + entry_slip + exit_slip + stop_exp + funding + missed
        conf = max(0.0, min(1.0, conf))
        return self._decide(horizon=horizon, notional_usd=notional_usd, gross=gross_edge_bp, total=total,
                            buffer=buffer, conf=conf, missing=missing, assumptions=assumptions, codes=codes,
                            parts=dict(entry_fee_bp=entry_fee, exit_fee_bp=exit_fee, spread_bp=spread_cost,
                                       entry_slippage_bp=entry_slip, exit_slippage_bp=exit_slip,
                                       stop_slippage_bp=stop_exp, stop_slippage_raw_bp=stop_raw, funding_bp=funding,
                                       missed_fill_buffer_bp=missed),
                            fill_ratio=fill_ratio, unfilled=unfilled)

    # ── carry: spot long + swap short, açılış ve kapanış (4 bacak) ─────
    def estimate_carry(self, *, notional_usd: float | None, expected_holding_hours: float | None,
                       funding_rate: float | None = None, funding_interval_h: float | None = None,
                       funding_ann_pct: float | None = None, perp_bid: float | None = None,
                       perp_ask: float | None = None, spot_bid: float | None = None, spot_ask: float | None = None,
                       hedge_buffer_bp: float = 0.0) -> CostEstimate:
        """Brüt avantaj = beklenen funding geliri (swap short alır). Maliyet = 4 bacağın ücret + yarım spread + kayma.
        Ayrı muhasebe (yönlü işlemlerden bağımsız); hedge sapması tamponu isteğe bağlı."""
        h = Horizon.CARRY
        missing: list[str] = []
        assumptions: list[str] = []
        conf = 1.0
        hours = expected_holding_hours
        if not (is_finite_number(hours) and hours > 0):
            hours = self._h("varsayilan_tutma_saat", h)
            missing.append("tutma_suresi")
            conf -= PENALTY["tutma_suresi"]
            assumptions.append(f"tutma süresi {hours:g} saat varsayıldı")
        if is_finite_number(funding_rate):
            iv = funding_interval_h if is_finite_number(funding_interval_h) and funding_interval_h else 8.0
            ann = float(funding_rate) * (8760.0 / float(iv)) * 100.0
        elif is_finite_number(funding_ann_pct):
            ann = float(funding_ann_pct)
        else:
            ann = None
            missing.append("funding")
            conf -= 0.5
        gross = ann / 100.0 * float(hours) / 8760.0 * 1e4 if ann is not None else None

        def leg(name: str, inst: str, bid, ask) -> CostLeg:
            sp = _spread_bp(bid, ask)
            if sp is None:
                sp = self._h("varsayilan_spread_bp", h)
                if f"spread_{inst}" not in missing:
                    missing.append(f"spread_{inst}")
            return CostLeg(name=name, instrument=inst, order_type=OrderType.MARKET,
                           fee_bp=self.fee_bp(OrderType.MARKET, inst), spread_bp=sp / 2,
                           slippage_bp=self._h("varsayilan_kayma_bp", h))

        legs = (leg("spot al (açılış)", "spot", spot_bid, spot_ask), leg("swap short (açılış)", "swap", perp_bid, perp_ask),
                leg("spot sat (kapanış)", "spot", spot_bid, spot_ask), leg("swap kapat (kapanış)", "swap", perp_bid, perp_ask))
        conf -= 0.1 * sum(1 for m in missing if m.startswith("spread_"))
        assumptions.append("bacak kayması varsayılan değerle (emir defteri yok)")
        total = sum(L.total_bp for L in legs) + float(hedge_buffer_bp)
        buffer = self._h("belirsizlik_tamponu_bp", h) + float(self.U["eksik_veri_tampon_bp"]) * len(missing)
        fees = sum(L.fee_bp for L in legs)
        spreads = sum(L.spread_bp for L in legs)
        slips = sum(L.slippage_bp for L in legs)
        return self._decide(horizon=h, notional_usd=notional_usd, gross=gross, total=total, buffer=buffer,
                            conf=max(0.0, min(1.0, conf)), missing=missing, assumptions=assumptions, codes=[],
                            parts=dict(entry_fee_bp=fees / 2, exit_fee_bp=fees / 2, spread_bp=spreads,
                                       entry_slippage_bp=slips / 2, exit_slippage_bp=slips / 2, stop_slippage_bp=0.0,
                                       stop_slippage_raw_bp=0.0, funding_bp=0.0,
                                       missed_fill_buffer_bp=float(hedge_buffer_bp)),
                            fill_ratio=1.0, unfilled=0.0, legs=legs)

    # ── ortak karar ───────────────────────────────────────────────────
    def _decide(self, *, horizon, notional_usd, gross, total, buffer, conf, missing, assumptions, codes, parts,
                fill_ratio, unfilled, legs=()) -> CostEstimate:
        codes = list(codes)
        if gross is None or not is_finite_number(gross):
            after, net, trad = None, None, Tradability.SHADOW
            codes.append("BRUT_AVANTAJ_BILINMIYOR")
        else:
            after = float(gross) - total
            net = after - buffer
            if after <= 0:
                trad = Tradability.REJECT
                codes.append("MALIYET_SONRASI_AVANTAJ_YOK")
            elif net <= 0:
                trad = Tradability.SHADOW
                codes.append("AVANTAJ_TAMPONDAN_KUCUK")
            else:
                trad = Tradability.PASS
            if trad is Tradability.PASS:
                if conf < float(self.U["min_guven"]):
                    trad = Tradability.SHADOW
                    codes.append(f"MALIYET_GUVENI_DUSUK:{conf:.2f}")
                if any(c in ("DERINLIK_YETERSIZ", "KISMI_DOLUM_YUKSEK", "FIYAT_GECERSIZ") for c in codes):
                    trad = Tradability.SHADOW
        return CostEstimate(horizon=horizon, notional_usd=notional_usd, total_cost_bp=round(total, 4),
                            uncertainty_buffer_bp=round(buffer, 4), gross_edge_bp=gross,
                            net_edge_bp=None if net is None else round(net, 4),
                            edge_after_costs_bp=None if after is None else round(after, 4), fill_ratio=fill_ratio,
                            unfilled_ratio=unfilled, confidence=round(conf, 3), missing_inputs=missing,
                            assumptions=assumptions, tradability=trad, reason_codes=codes, legs=legs,
                            **{k: round(v, 4) for k, v in parts.items()})
