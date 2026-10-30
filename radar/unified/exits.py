"""Çıkış yöneticisi (birleşik paper pozisyonları). Saf fonksiyon: pozisyon + fiyat + bağlam → yapılacaklar listesi.
Emir göndermez; orkestratör listeyi yürütme adaptörüne çevirir.

Kurallar (legacy `risk:` bloğundaki değerlerle, legacy ile aynı anlam):
  - stop / felaket stop        : adaptördeki reduce-only stop emri tetikler (burada yalnız yoksa kapat)
  - kısmi kâr                  : +`kismi_kar_r` R'de pozisyonun `kismi_kar_oran`'ı kapanır
  - başabaş                    : kısmi kârdan sonra stop giriş + ücretlere çekilir
  - kâr kilidi                 : tepe kâr `baslangic_r` R'yi geçince stop tepe kârın `koru` oranını korur
  - iz süren stop              : kısmi kârdan sonra en iyi fiyattan `iz_suren_atr` ATR geride
  - hedef                      : son hedefe ulaşınca kapat (hedefi olan stratejilerde)
  - kural çıkışı               : stratejinin kural çıkışı (ör. Kemal: günlük kapanış 50 günlük ortalamanın altında);
                                 yalnız TAMAMLANMIŞ günlük mumla (legacy ile aynı: quant.exit_signal)
  - zaman                      : `max_holding_hours` aşıldı
  - rejim                      : long pozisyon kriz/ayı rejimine, short boğaya döndü
  - veri sorunu                : coin verisi `veri_sorunu_cikis_dk` dakikadan uzun süredir güvenilmez → kapat
Stop yalnız kâr yönüne taşınır (asla gevşemez).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .. import ledger as lg
from .models import Regime

EXIT_DEFAULTS = {"veri_sorunu_cikis_dk": 60, "rejim_cikisi": True}


@dataclass(frozen=True, slots=True)
class ExitAction:
    kind: str              # close | reduce | move_stop
    why: str               # Türkçe sebep
    code: str              # STOP | KISMI_KAR | BASABAS | KAR_KILIDI | IZ_STOP | HEDEF | KURAL | ZAMAN | REJIM | VERI
    qty: float = 0.0
    stop: float | None = None


def exit_settings(cfg: Mapping | None) -> dict:
    cfg = dict(cfg or {})
    r = lg.risk_cfg(cfg)
    s = dict(EXIT_DEFAULTS)
    s.update(((cfg.get("birlesik") or {}).get("cikis")) or {})
    s["risk"] = r
    s["taker"] = float(lg.fees_cfg(cfg)["taker_yuzde"]) / 100
    return s


def _tighter(side: str, cur: float | None, new: float) -> bool:
    if cur is None:
        return True
    return new > cur if side == "long" else new < cur


def evaluate_exits(pos: Mapping[str, Any], price: float, now_ms: int, cfg: Mapping | None, *,
                   atr: float | None = None, regime: Regime | None = None, closed_bar_ctx: Mapping | None = None,
                   data_issue_since_ms: int | None = None, exit_rule: Mapping | None = None) -> list[ExitAction]:
    s = exit_settings(cfg)
    r = s["risk"]
    side = pos["side"]
    sg = 1 if side == "long" else -1
    entry, qty = float(pos["entry"]), float(pos["qty"])
    stop0 = pos.get("stop_init")
    stop = pos.get("stop")
    out: list[ExitAction] = []
    if qty <= 0 or not price or price <= 0:
        return out
    risk_px = abs(entry - stop0) if stop0 else None

    # 1) koruyucu stop (adaptörde emir olmalı; yine de fiyat ötesindeyse kapat)
    if stop is not None and sg * (price - stop) <= 0:
        return [ExitAction("close", f"stop {stop:.6g} geçildi (fiyat {price:.6g})", "STOP", qty)]
    # 2) veri sorunu
    if data_issue_since_ms is not None and now_ms - data_issue_since_ms >= float(s["veri_sorunu_cikis_dk"]) * 60_000:
        return [ExitAction("close", f"veri {int((now_ms - data_issue_since_ms) / 60_000)} dakikadır güvenilmez", "VERI", qty)]
    # 3) zaman
    mh = pos.get("max_holding_hours")
    if mh and now_ms - int(pos["opened_ts"]) >= float(mh) * 3_600_000:
        return [ExitAction("close", f"azami tutma süresi ({float(mh):.0f} saat) doldu", "ZAMAN", qty)]
    # 4) rejim
    if s["rejim_cikisi"] and regime is not None:
        if (side == "long" and regime is Regime.CRISIS) or (side == "short" and regime is Regime.BULL):
            return [ExitAction("close", f"rejim pozisyonun aleyhine döndü ({regime.value})", "REJIM", qty)]
    # 5) hedef
    targets = [float(t) for t in (pos.get("targets") or []) if t]
    if targets and sg * (price - targets[-1]) >= 0:
        return [ExitAction("close", f"son hedef {targets[-1]:.6g} görüldü", "HEDEF", qty)]
    # 6) kural çıkışı (tamamlanmış günlük mum)
    if exit_rule and closed_bar_ctx:
        try:
            from .. import quant
            why = quant.exit_signal(dict(exit_rule), dict(closed_bar_ctx), side)
        except Exception:  # noqa: BLE001 - eksik bağlam kural çıkışını tetiklemez
            why = None
        if why:
            return [ExitAction("close", f"kural çıkışı: {why}", "KURAL", qty)]
    if not risk_px:
        return out
    best = float(pos.get("best_px") or entry)
    if sg * (price - best) > 0:
        best = price
    peak_r = sg * (best - entry) / risk_px
    now_r = sg * (price - entry) / risk_px
    new_stop = stop
    # 7) kısmi kâr + başabaş. Legacy kaynaklı pozisyonda legacy ile aynı: kısmi kâr yalnız ara hedef (tp1) varsa ve
    #    o fiyatta; plan (pp) değerleri varsayılanları ezer. Birleşik stratejide +kismi_kar_r R.
    pp = dict(pos.get("pp") or {})
    legacy_exits = bool(pos.get("legacy_exits"))
    frac = float(pp.get("partial_frac", r["kismi_kar_oran"]))
    be_on = bool(pp.get("basabas", r.get("basabas", True)))
    partial_done = bool(pos.get("partials")) or bool(pp.get("trail_now"))
    if len(targets) >= 2:
        trig_hit = sg * (price - targets[0]) >= 0
        trig_txt = f"ara hedef {targets[0]:.6g}"
    elif legacy_exits:
        trig_hit, trig_txt = False, ""
    else:
        trig_hit = now_r >= float(r["kismi_kar_r"])
        trig_txt = f"+{now_r:.2f}R"
    if not partial_done and trig_hit:
        partial_done = True
        if frac > 0:
            out.append(ExitAction("reduce", f"{trig_txt}: kısmi kâr (%{frac * 100:.0f})", "KISMI_KAR", qty * frac))
        if be_on:
            be = entry * (1 + sg * 2 * s["taker"])
            if _tighter(side, new_stop, be):
                new_stop = be
                out.append(ExitAction("move_stop", "stop başabaşa (+ücretler) çekildi", "BASABAS", stop=be))
    # 8) kâr kilidi
    kk = r.get("kar_kilidi") or {}
    if kk.get("aktif") and peak_r >= float(kk.get("baslangic_r", 0.5)):
        lock = entry + sg * float(kk.get("koru", 0.6)) * peak_r * risk_px
        if _tighter(side, new_stop, lock):
            new_stop = lock
            out.append(ExitAction("move_stop", f"kâr kilidi: tepe +{peak_r:.2f}R'nin %{float(kk.get('koru', 0.6)) * 100:.0f}'ı korunur",
                                  "KAR_KILIDI", stop=lock))
    # 9) iz süren stop (kısmi kârdan sonra)
    k = float(pp.get("trail_atr", r.get("iz_suren_atr")) or 0)
    if partial_done and k > 0 and atr and atr > 0:
        tr = best - sg * k * atr
        if _tighter(side, new_stop, tr) and sg * (price - tr) > 0:
            new_stop = tr
            out.append(ExitAction("move_stop", f"iz süren stop: en iyi fiyattan {k:g} ATR geride", "IZ_STOP", stop=tr))
    # aynı turda birden çok stop taşıması → yalnız en sıkı olanı bırak
    moves = [a for a in out if a.kind == "move_stop"]
    if len(moves) > 1:
        keep = moves[-1]
        out = [a for a in out if a.kind != "move_stop" or a is keep]
    return out
