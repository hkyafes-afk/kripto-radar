"""RiskGate: Baş Trader'ın "aç" dediği kararın son kontrolü ve boyutlandırması (yalnız paper).

Sıra (ilk sert ihlal karar verir):
  1. karar açılabilir mi (OPEN / REDUCE_SIZE)          → değilse aynen geçer
  2. devre kesiciler: elle durdurma, maksimum düşüş, günlük kayıp, veri sorunu
  3. coin başına tek pozisyon, aynı fırsat tekrarı (idempotency)
  4. giriş/stop geçerliliği
  5. risk bütçesi: taban risk × karar çarpanı × kayıp serisi çarpanı
  6. sınırlar: toplam açık risk, aynı yön (adet + risk), küme (aynı sektör/korelasyon grubu)
  7. nominal = risk / stop mesafesi, tek pozisyon nominal tavanı
  8. kaldıraç = gereken teminatı karşılayan EN DÜŞÜK değer; yüksek oynaklık / düşük likiditede tavan düşer;
     likidasyon stop'tan en az `likidasyon_tampon_kat` × stop mesafesi uzakta olmalı (sağlanamazsa nominal küçülür)
  9. kontrat yuvarlama (OKX lot kuralları) → son risk; istenenin `min_risk_orani`'ndan azsa REJECT

Değerler `risk:` (legacy, değiştirilmedi) + `birlesik.risk:` (yeni anahtarlar) bloklarından okunur.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ledger as lg
from .models import Decision, DecisionResult, RiskDecision, Side, is_finite_number

RISK_DEFAULTS: dict[str, Any] = {
    "likidasyon_tampon_kat": 2.0,          # |likidasyon − giriş| ≥ bu kat × |stop − giriş|
    "max_kume": 2,                         # aynı kümede (ör. meme, L1) en fazla açık pozisyon
    "yuksek_oynaklik_max_kaldirac": 2,     # kriz/yüksek oynaklık rejiminde kaldıraç tavanı
    "dusuk_likidite_usd": 20e6,            # 24s hacim bunun altındaysa ...
    "dusuk_likidite_max_kaldirac": 2,      # ... kaldıraç tavanı
    "min_risk_orani": 0.25,                # sınırlar yüzünden risk istenenin bu oranının altına inerse açma
    "teminat_kullanim_orani": 0.8,         # boştaki teminatın en fazla bu kadarı tek pozisyona bağlanır
}


def risk_settings(cfg: Mapping | None) -> dict:
    cfg = dict(cfg or {})
    r = lg.risk_cfg(cfg)
    r.update(RISK_DEFAULTS)
    r.update(((cfg.get("birlesik") or {}).get("risk")) or {})
    r["mmr_yuzde"] = lg.fees_cfg(cfg)["mmr_yuzde"]
    return r


@dataclass(frozen=True, slots=True)
class OpenPosition:
    inst: str
    side: Side
    risk_usd: float
    notional_usd: float = 0.0
    margin_usd: float = 0.0
    cluster: str | None = None
    idempotency_key: str = ""

    def __post_init__(self):
        object.__setattr__(self, "side", Side.parse(self.side))

    @classmethod
    def from_ledger(cls, t: Mapping) -> "OpenPosition":
        return cls(inst=t["inst"], side=t["side"], risk_usd=float(lg.open_risk(dict(t))),
                   notional_usd=float(t.get("notional_usd") or 0.0), margin_usd=float(t.get("margin_usd") or 0.0),
                   cluster=t.get("cluster"), idempotency_key=t.get("decision_key") or "")


@dataclass(frozen=True, slots=True)
class PortfolioState:
    equity: float
    peak_equity: float
    day_start_equity: float
    positions: tuple[OpenPosition, ...] = ()
    loss_streak: int = 0
    halted: str | None = None                 # elle durdurma / dış devre kesici nedeni
    data_issue: str | None = None             # genel veri sorunu (WS kopuk, borsa durumu…)

    def __post_init__(self):
        object.__setattr__(self, "positions", tuple(self.positions))

    @property
    def drawdown_pct(self) -> float:
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity * 100) if self.peak_equity > 0 else 0.0

    @property
    def day_loss_pct(self) -> float:
        return max(0.0, (self.day_start_equity - self.equity) / self.day_start_equity * 100) \
            if self.day_start_equity > 0 else 0.0

    @property
    def used_margin(self) -> float:
        return sum(p.margin_usd for p in self.positions)


@dataclass(frozen=True, slots=True)
class MarketRisk:
    """Pozisyon boyutunu etkileyen anlık piyasa bilgisi. Bilinmeyen alan None bırakılır (uydurulmaz)."""
    inst: str
    price: float | None = None
    vol24_usd: float | None = None
    high_vol: bool = False
    cluster: str | None = None
    meta: Mapping[str, Any] | None = field(default=None)   # OKX ct_val / lot_sz / min_sz


class RiskGate:
    def __init__(self, cfg: Mapping | None):
        self.cfg = dict(cfg or {})
        self.r = risk_settings(self.cfg)

    # ── yardımcılar ─────────────────────────────────────────────
    def _reject(self, decision: Decision, codes, checked, hit, text) -> RiskDecision:
        return RiskDecision(decision=decision, limits_checked=tuple(checked), limits_hit=tuple(hit),
                            reason_codes=tuple(codes), explanation_tr=text)

    def max_leverage(self, m: MarketRisk) -> float:
        cap = float(self.r["max_kaldirac"])
        if m.high_vol:
            cap = min(cap, float(self.r["yuksek_oynaklik_max_kaldirac"]))
        if m.vol24_usd is None or m.vol24_usd < float(self.r["dusuk_likidite_usd"]):
            cap = min(cap, float(self.r["dusuk_likidite_max_kaldirac"]))     # bilinmeyen likidite = düşük sayılır
        return max(1.0, cap)

    def liq_ok(self, side: Side, entry: float, stop: float, lev: float) -> tuple[bool, float | None, float | None]:
        liq = lg.liq_price(side.value, entry, lev, self.r["mmr_yuzde"])
        if liq is None:
            return True, None, None
        stop_d = abs(entry - stop)
        liq_d = abs(liq - entry)
        beyond = (liq < stop) if side is Side.LONG else (liq > stop)
        buf_pct = (abs(liq - stop) / entry * 100) if beyond else -abs(liq - stop) / entry * 100
        return beyond and liq_d >= float(self.r["likidasyon_tampon_kat"]) * stop_d, liq, buf_pct

    # ── ana kapı ─────────────────────────────────────────────────
    def evaluate(self, d: DecisionResult, pf: PortfolioState, m: MarketRisk) -> RiskDecision:
        r = self.r
        checked: list[str] = []
        if d.decision not in (Decision.OPEN, Decision.REDUCE_SIZE):
            return self._reject(d.decision, ("KARAR_ACMA_DEGIL",), checked, (), "Baş Trader açma kararı vermedi.")
        checked.append("devre_kesici")
        if pf.halted:
            return self._reject(Decision.REJECT, ("DEVRE_DURDURULDU",), checked, ("devre_kesici",),
                                f"Sistem durduruldu: {pf.halted}. Yeni pozisyon açılmaz.")
        if pf.drawdown_pct >= float(r["max_dusus_yuzde"]):
            return self._reject(Decision.REJECT, (f"DEVRE_MAX_DUSUS:{pf.drawdown_pct:.1f}",), checked,
                                ("max_dusus",), f"Zirveden düşüş %{pf.drawdown_pct:.1f} ≥ sınır "
                                f"%{float(r['max_dusus_yuzde']):.1f}: yeni pozisyon açılmaz.")
        if pf.day_loss_pct >= float(r["gunluk_kayip_yuzde"]):
            return self._reject(Decision.REJECT, (f"DEVRE_GUNLUK_KAYIP:{pf.day_loss_pct:.1f}",), checked,
                                ("gunluk_kayip",), f"Bugünkü kayıp %{pf.day_loss_pct:.1f} ≥ sınır "
                                f"%{float(r['gunluk_kayip_yuzde']):.1f}: bugün yeni pozisyon yok.")
        if pf.data_issue:
            return self._reject(Decision.SHADOW, ("DEVRE_VERI",), checked, ("veri",),
                                f"Veri sorunu ({pf.data_issue}): pozisyon açılmaz, karar gölgede ölçülür.")
        checked.append("coin_tekrar")
        if any(p.inst == m.inst for p in pf.positions):
            return self._reject(Decision.REJECT, ("COIN_ACIK",), checked, ("coin_tekrar",),
                                "Bu coinde zaten açık pozisyon var (coin başına tek pozisyon).")
        if d.idempotency_key and any(p.idempotency_key == d.idempotency_key for p in pf.positions):
            return self._reject(Decision.REJECT, ("TEKRAR_FIRSAT",), checked, ("coin_tekrar",),
                                "Aynı fırsat zaten işlendi.")
        side, entry, stop = d.side, d.entry, d.stop
        if side is None or not (is_finite_number(entry) and is_finite_number(stop) and entry > 0 and stop > 0):
            return self._reject(Decision.REJECT, ("GIRIS_STOP_GECERSIZ",), checked, (), "Giriş ya da stop geçersiz.")
        stop_frac = abs(entry - stop) / entry
        if stop_frac <= 0 or (side is Side.LONG and stop >= entry) or (side is Side.SHORT and stop <= entry):
            return self._reject(Decision.REJECT, ("GIRIS_STOP_GECERSIZ",), checked, (), "Stop yanlış tarafta.")
        if pf.equity <= 0:
            return self._reject(Decision.REJECT, ("BAKIYE_YOK",), checked, (), "Bakiye yok.")

        # ── risk bütçesi ──
        mult = float(d.risk_proposal.get("boyut_carpani", 1.0 if d.decision is Decision.OPEN else 0.5))
        codes: list[str] = []
        if d.decision is Decision.REDUCE_SIZE:
            codes.append("KARAR_YARIM_BOYUT")
        if pf.loss_streak >= int(r["kayip_serisi"]):
            mult *= float(r["seri_risk_carpan"])
            codes.append(f"KAYIP_SERISI:{pf.loss_streak}")
        want_pct = float(r["risk_yuzde"]) * mult
        want = pf.equity * want_pct / 100
        allowed, hit = want, []
        checked += ["toplam_risk", "ayni_yon", "kume"]
        heat_left = pf.equity * float(r["max_isi_yuzde"]) / 100 - sum(p.risk_usd for p in pf.positions)
        if heat_left < allowed:
            allowed = max(0.0, heat_left)
            hit.append("toplam_risk")
        same = [p for p in pf.positions if p.side is side]
        if len(same) >= int(r["max_ayni_yon"]):
            return self._reject(Decision.REJECT, (f"AYNI_YON_ADET:{len(same)}",), checked, ("ayni_yon",),
                                f"Aynı yönde zaten {len(same)} pozisyon var (sınır {int(r['max_ayni_yon'])}).")
        side_left = pf.equity * float(r["max_ayni_yon_risk_yuzde"]) / 100 - sum(p.risk_usd for p in same)
        if side_left < allowed:
            allowed = max(0.0, side_left)
            hit.append("ayni_yon")
        cl = m.cluster
        if cl and sum(1 for p in pf.positions if p.cluster == cl) >= int(r["max_kume"]):
            return self._reject(Decision.REJECT, (f"KUME_DOLU:{cl}",), checked, ("kume",),
                                f"'{cl}' kümesinde zaten {int(r['max_kume'])} pozisyon var (birlikte hareket ederler).")

        # ── nominal, kaldıraç, likidasyon ──
        checked += ["nominal_tavan", "kaldirac", "likidasyon_tamponu"]
        notional = allowed / stop_frac
        cap_notional = pf.equity * float(r["max_pozisyon_oran"])
        if notional > cap_notional:
            notional = cap_notional
            hit.append("nominal_tavan")
        free_margin = max(0.0, pf.equity - pf.used_margin) * float(r["teminat_kullanim_orani"])
        lev_cap = self.max_leverage(m)
        if free_margin <= 0:
            return self._reject(Decision.REJECT, ("TEMINAT_YOK",), checked, ("kaldirac",), "Boşta teminat yok.")
        lev = max(1.0, float(math.ceil(notional / free_margin - 1e-9)))
        if lev > lev_cap:
            lev, notional = lev_cap, free_margin * lev_cap
            hit.append("kaldirac")
        ok, liq, buf = self.liq_ok(side, entry, stop, lev)
        while not ok and lev > 1:
            lev -= 1.0
            notional = min(notional, free_margin * lev)
            if "likidasyon_tamponu" not in hit:
                hit.append("likidasyon_tamponu")
            ok, liq, buf = self.liq_ok(side, entry, stop, lev)
        if not ok:
            return self._reject(Decision.REJECT, ("LIKIDASYON_TAMPONU",), checked, ("likidasyon_tamponu",),
                                "Stop ile likidasyon arasında yeterli tampon sağlanamadı.")

        # ── kontrat yuvarlama ──
        contracts, qty, err = lg.round_contracts(notional, entry, dict(m.meta) if m.meta else None)
        if err or qty <= 0:
            return self._reject(Decision.REJECT, ("LOT_UYGUN_DEGIL",), checked, ("lot",), f"Lot kuralı: {err or 'miktar 0'}.")
        notional = qty * entry
        risk_usd = qty * abs(entry - stop)
        if risk_usd > want * 1.0001 or risk_usd > allowed * 1.5 + 1e-9:
            return self._reject(Decision.REJECT, ("LOT_RISK_ASIMI",), checked, ("lot",),
                                "En küçük lot bile risk sınırını aşıyor.")
        if risk_usd < want * float(r["min_risk_orani"]):
            return self._reject(Decision.REJECT, ("RISK_COK_KUCULDU",) + tuple(f"SINIR:{h}" for h in hit), checked,
                                hit, f"Sınırlar ({', '.join(hit) or 'lot'}) yüzünden risk istenenin "
                                f"%{risk_usd / want * 100:.0f}'ine indi; açılmaz.")
        final = Decision.OPEN if (d.decision is Decision.OPEN and risk_usd >= want * 0.999 and not hit) \
            else Decision.REDUCE_SIZE
        codes += [f"SINIR:{h}" for h in hit]
        margin = notional / lev
        text = (f"Risk {risk_usd:.2f}$ (bakiyenin %{risk_usd / pf.equity * 100:.2f}'i) · nominal {notional:.2f}$ · "
                f"kaldıraç {lev:.0f}x · teminat {margin:.2f}$"
                + (f" · likidasyon {liq:.6g} (stop'tan %{buf:.1f} ötede)" if liq else " · likidasyon yok (1x)")
                + (f". Küçültüldü: {', '.join(hit)}." if hit else ".")
                + (" Kayıp serisi nedeniyle risk yarıya indi." if any(c.startswith("KAYIP_SERISI") for c in codes) else ""))
        return RiskDecision(decision=final, risk_usd=risk_usd, risk_pct=risk_usd / pf.equity * 100,
                            notional_usd=notional, qty=qty, leverage=lev, margin_usd=margin, liq_price=liq,
                            stop_liq_buffer_pct=buf, limits_checked=tuple(dict.fromkeys(checked)),
                            limits_hit=tuple(hit), reason_codes=tuple(codes), explanation_tr=text)


def portfolio_from_ledger(positions: Sequence[Mapping], equity: float, peak: float, day_start: float,
                          loss_streak: int = 0, halted: str | None = None, data_issue: str | None = None) -> PortfolioState:
    return PortfolioState(equity=equity, peak_equity=peak, day_start_equity=day_start,
                          positions=tuple(OpenPosition.from_ledger(t) for t in positions),
                          loss_streak=loss_streak, halted=halted, data_issue=data_issue)
