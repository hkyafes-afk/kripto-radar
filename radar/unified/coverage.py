"""Faktör kapsamı: kararda beklenen veriler var mı, taze mi, geçerli mi?

Kural (GOREV.md, Aşama 1): zorunlu faktör eksik, geçersiz ya da bayatsa
  - paper risk kullanılmaz (FactorCoverage.sufficient_for_paper = False),
  - fırsat kaydı silinmez, karar SHADOW olur (karar motoru, PR 2),
  - eksik faktörler reason_codes içinde görünür ve dashboard'a gider.
OI, tasfiye, emir defteri gibi verisi olmayan faktörler uydurulmaz; MISSING olarak ve sebebiyle listelenir.

Legacy köprüsü: `factors_from_legacy_ctx` mevcut motorun bağlam sözlüğünü (radar/engine.live_daily_ctx, team.extra_features,
quant.ctx_at) okur; hiçbir şeyi değiştirmez.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from .models import FactorCoverage, FactorValue, Horizon, Validity, is_finite_number


@dataclass(frozen=True, slots=True)
class FactorSpec:
    name: str
    required: bool = False
    max_age_s: float | None = None
    timeframe: str | None = None
    group: str = ""
    description: str = ""


H = 3600.0
DAY_AGE = 26 * H          # günlük mum: kapanıştan sonra en fazla ~1 gün + pay

SWING_SPECS: tuple[FactorSpec, ...] = (
    FactorSpec("price", True, 120, None, "fiyat", "canlı son fiyat"),
    FactorSpec("close_1D", True, DAY_AGE, "1D", "mum", "son günlük kapanış"),
    FactorSpec("atr_1D", True, DAY_AGE, "1D", "volatilite", "günlük ATR"),
    FactorSpec("sma50_1D", True, DAY_AGE, "1D", "trend", "50 günlük ortalama"),
    FactorSpec("btc_regime", True, DAY_AGE, "1D", "rejim", "BTC havası (boğa/ayı/yatay)"),
    FactorSpec("sma200_1D", False, DAY_AGE, "1D", "trend", "200 günlük ortalama (genç coinde yok)"),
    FactorSpec("rsi_1D", False, DAY_AGE, "1D", "momentum"),
    FactorSpec("adx_1D", False, DAY_AGE, "1D", "trend"),
    FactorSpec("vol_ratio_1D", False, DAY_AGE, "1D", "hacim", "günlük hacim / 20 günlük ortalama"),
    FactorSpec("funding_ann", False, 9 * H, None, "türev", "yıllık funding %"),
    FactorSpec("oi_change_24h", False, 2 * H, None, "türev", "24 saatlik açık pozisyon değişimi %"),
    FactorSpec("taker_imbalance", False, 600, None, "hacim", "agresif alış-satış dengesizliği"),
    FactorSpec("spread_bp", False, 60, None, "likidite"),
    FactorSpec("coin_age_days", False, None, None, "kalite"),
)

INTRADAY_SPECS: tuple[FactorSpec, ...] = (
    FactorSpec("price", True, 60, None, "fiyat"),
    FactorSpec("close_1H", True, 2 * H, "1H", "mum"),
    FactorSpec("atr_1H", True, 2 * H, "1H", "volatilite"),
    FactorSpec("close_4H", True, 5 * H, "4H", "mum", "üst zaman dilimi"),
    FactorSpec("btc_regime", True, DAY_AGE, "1D", "rejim"),
    FactorSpec("spread_bp", True, 30, None, "likidite", "gün içinde maliyet belirleyici"),
    FactorSpec("vol_ratio_1H", False, 2 * H, "1H", "hacim"),
    FactorSpec("funding_ann", False, 9 * H, None, "türev"),
    FactorSpec("book_depth_usd", False, 10, None, "likidite"),
    FactorSpec("taker_imbalance", False, 120, None, "hacim"),
)

MICRO_SPECS: tuple[FactorSpec, ...] = (
    FactorSpec("price", True, 5, None, "fiyat"),
    FactorSpec("spread_bp", True, 5, None, "likidite"),
    FactorSpec("book_depth_usd", True, 5, None, "likidite"),
    FactorSpec("book_imbalance", True, 5, None, "mikro yapı"),
    FactorSpec("trade_imbalance", True, 10, None, "mikro yapı"),
    FactorSpec("seq_ok", True, 5, None, "veri kalitesi", "emir defteri sıra boşluğu yok"),
)

CARRY_SPECS: tuple[FactorSpec, ...] = (
    FactorSpec("price", True, 120, None, "fiyat", "vadeli fiyat"),
    FactorSpec("spot_price", True, 120, None, "fiyat"),
    FactorSpec("funding_ann", True, 9 * H, None, "türev"),
    FactorSpec("funding_30d_avg", True, DAY_AGE, None, "türev"),
    FactorSpec("spread_bp", False, 60, None, "likidite"),
)

DEFAULT_SPECS: Mapping[Horizon, tuple[FactorSpec, ...]] = {
    Horizon.SWING: SWING_SPECS, Horizon.POSITION: SWING_SPECS, Horizon.INTRADAY: INTRADAY_SPECS,
    Horizon.MICRO: MICRO_SPECS, Horizon.CARRY: CARRY_SPECS,
}


def make_factor(name: str, value: Any, *, source: str, ts_ms: int | None = None, now_ms: int | None = None,
                timeframe: str | None = None, max_age_s: float | None = None, required: bool = False,
                confidence: float = 1.0, metadata: Mapping[str, Any] | None = None,
                validator: Callable[[Any], bool] | None = None, missing_reason: str | None = None) -> FactorValue:
    """Değer + zaman damgasından FactorValue: yaşını ve bayatlığını hesaplar, doğrulayıcı tutmazsa INVALID yapar."""
    age = (now_ms - ts_ms) / 1000.0 if (now_ms is not None and ts_ms is not None) else None
    if value is None:
        return FactorValue(name=name, value=None, source=source, timestamp_ms=ts_ms, age_seconds=age,
                           timeframe=timeframe, validity=Validity.MISSING, confidence=0.0,
                           missing_reason=missing_reason or "kaynakta değer yok", required=required,
                           metadata=metadata or {})
    if (isinstance(value, float) and value != value) or (validator is not None and not _safe(validator, value)):
        return FactorValue(name=name, value=value, source=source, timestamp_ms=ts_ms, age_seconds=age,
                           timeframe=timeframe, validity=Validity.INVALID, confidence=0.0,
                           missing_reason="geçerlilik kontrolü tutmadı", required=required, metadata=metadata or {})
    stale = bool(max_age_s is not None and age is not None and age > max_age_s)
    return FactorValue(name=name, value=value, source=source, timestamp_ms=ts_ms, age_seconds=age, timeframe=timeframe,
                       validity=Validity.STALE if stale else Validity.VALID, confidence=confidence,
                       missing_reason=(f"{age:.0f} sn eski (sınır {max_age_s:.0f} sn)" if stale else None),
                       stale=stale, required=required, metadata=metadata or {})


def _safe(fn: Callable[[Any], bool], v: Any) -> bool:
    try:
        return bool(fn(v))
    except Exception:  # noqa: BLE001
        return False


def compute_coverage(specs: Iterable[FactorSpec], factors: Mapping[str, FactorValue] | Iterable[FactorValue], *,
                     now_ms: int | None = None, min_ratio: float = 0.8) -> FactorCoverage:
    """Beklenen faktör listesine göre kapsam. Zorunlu faktörlerden biri bile sorunluysa paper'a yetersiz."""
    specs = tuple(specs)
    fmap = factors if isinstance(factors, Mapping) else {f.name: f for f in factors}
    if not specs:
        return FactorCoverage(expected_total=0, present=0, required_total=0, required_ok=0, coverage_ratio=0.0,
                              min_ratio=min_ratio, sufficient_for_paper=False, reason_codes=("KAPSAM_TANIMSIZ",))
    missing, stale, invalid = [], [], []
    req_missing, req_stale, req_invalid = [], [], []
    codes: list[str] = []
    ok = req_total = req_ok = 0
    for sp in specs:
        f = fmap.get(sp.name)
        req_total += int(sp.required)
        state = "ok"
        if f is None or f.validity is Validity.MISSING:
            state = "missing"
        elif f.validity is Validity.INVALID:
            state = "invalid"
        else:
            age = f.age_seconds
            if age is None and now_ms is not None and f.timestamp_ms is not None:
                age = (now_ms - f.timestamp_ms) / 1000.0
            if f.stale or f.validity is Validity.STALE or (sp.max_age_s is not None and age is not None and age > sp.max_age_s):
                state = "stale"
            elif sp.max_age_s is not None and age is None:
                codes.append(f"YAS_BILINMIYOR:{sp.name}")
        if state == "ok":
            ok += 1
            req_ok += int(sp.required)
            continue
        {"missing": missing, "stale": stale, "invalid": invalid}[state].append(sp.name)
        if sp.required:
            {"missing": req_missing, "stale": req_stale, "invalid": req_invalid}[state].append(sp.name)
            codes.append({"missing": "ZORUNLU_EKSIK", "stale": "ZORUNLU_BAYAT", "invalid": "ZORUNLU_GECERSIZ"}[state]
                         + f":{sp.name}")
    ratio = ok / len(specs)
    if ratio < min_ratio:
        codes.append(f"KAPSAM_DUSUK:{ratio:.2f}<{min_ratio:.2f}")
    sufficient = req_ok == req_total and ratio >= min_ratio
    return FactorCoverage(expected_total=len(specs), present=ok, required_total=req_total, required_ok=req_ok,
                          coverage_ratio=round(ratio, 4), min_ratio=min_ratio, missing=missing, stale=stale,
                          invalid=invalid, required_missing=req_missing, required_stale=req_stale,
                          required_invalid=req_invalid, sufficient_for_paper=sufficient, reason_codes=codes)


# ── legacy bağlamı → faktörler ─────────────────────────────────────────

def _pos(v: Any) -> bool:
    return is_finite_number(v) and v > 0


def _pct(v: Any) -> bool:
    return is_finite_number(v) and 0 <= v <= 100


def _fin(v: Any) -> bool:
    return is_finite_number(v)


# kanonik ad → (legacy anahtar, doğrulayıcı, kaynak grubu: "bar" mum kapanışı | "live" canlı akış | "static")
LEGACY_MAP: Mapping[str, tuple[str, Callable[[Any], bool], str]] = {
    "price": ("price", _pos, "live"),
    "close_1D": ("close", _pos, "bar"),
    "atr_1D": ("atr", _pos, "bar"),
    "sma50_1D": ("sma50", _pos, "bar"),
    "sma200_1D": ("sma200", _pos, "bar"),
    "rsi_1D": ("rsi", _pct, "bar"),
    "adx_1D": ("adx", _pct, "bar"),
    "vol_ratio_1D": ("vol_ratio", lambda v: is_finite_number(v) and v >= 0, "bar"),
    "funding_ann": ("funding_ann", _fin, "live"),
    "oi_change_24h": ("oi_chg24", _fin, "live"),
    "taker_imbalance": ("taker_imb", lambda v: is_finite_number(v) and -1.0 <= v <= 1.0, "live"),
    "spread_bp": ("spread_bp", lambda v: is_finite_number(v) and v >= 0, "live"),
    "coin_age_days": ("coin_age_days", lambda v: is_finite_number(v) and v >= 0, "static"),
}

MISSING_REASONS: Mapping[str, str] = {
    "oi_change_24h": "OI verisi bu coin için alınamadı (OKX OI akışı yok ya da gecikmeli)",
    "taker_imbalance": "taker (agresif taraf) verisi yok",
    "spread_bp": "anlık alış/satış fiyatı yok",
    "sma200_1D": "200 günden kısa geçmiş",
    "funding_ann": "funding oranı alınamadı",
    "coin_age_days": "listeleme tarihi bilinmiyor",
}


def factors_from_legacy_ctx(ctx: Mapping[str, Any], *, now_ms: int, specs: Iterable[FactorSpec] = SWING_SPECS,
                            bar_close_ms: int | None = None, live_ts_ms: int | None = None,
                            static_ts_ms: int | None = None) -> dict[str, FactorValue]:
    """Legacy bağlam sözlüğünden kanonik faktörler. Zaman damgası verilmeyen gruplarda yaş bilinmez (bayat sayılmaz,
    compute_coverage YAS_BILINMIYOR kodu ekler). Bağlamda olmayan faktör MISSING ve sebebiyle döner."""
    out: dict[str, FactorValue] = {}
    w = ctx.get("w_trend")
    for sp in specs:
        if sp.name == "btc_regime":
            val = w if w in ("boga", "ayi", "yatay") else None
            out[sp.name] = make_factor(sp.name, val, source="legacy:weather", ts_ms=bar_close_ms, now_ms=now_ms,
                                       timeframe="1D", max_age_s=sp.max_age_s, required=sp.required,
                                       missing_reason="BTC havası hesaplanamadı")
            continue
        m = LEGACY_MAP.get(sp.name)
        if m is None:
            out[sp.name] = FactorValue.missing(sp.name, "legacy bağlamında bu faktör yok", required=sp.required,
                                               timeframe=sp.timeframe)
            continue
        key, validator, grp = m
        v = ctx.get(key)
        ts = {"bar": bar_close_ms, "live": live_ts_ms, "static": static_ts_ms}[grp]
        out[sp.name] = make_factor(sp.name, v, source=f"legacy:{key}", ts_ms=ts, now_ms=now_ms, timeframe=sp.timeframe,
                                   max_age_s=sp.max_age_s, required=sp.required, validator=validator,
                                   missing_reason=MISSING_REASONS.get(sp.name))
    return out


def coverage_for(horizon: Horizon, factors: Mapping[str, FactorValue], *, now_ms: int | None = None,
                 min_ratio: float = 0.8) -> FactorCoverage:
    return compute_coverage(DEFAULT_SPECS[horizon], factors, now_ms=now_ms, min_ratio=min_ratio)
