"""Birleşik Baş Trader — ortak alan modelleri (PR 1).

Bu modüldeki tipler, eski "her ajan kendi işlemini açar" yapısından tek merkezden karar veren yapıya geçişin ortak dilidir:
stratejiler yalnız `Opportunity` üretir; maliyet `CostEstimate`, karar `DecisionResult`, risk `RiskDecision`, yürütme
`ExecutionIntent` / `ExecutionResult` / `ExecutionEvent`, ölçüm `EffectivenessSnapshot` ile taşınır.

İlkeler
  - Modeller değişmez (frozen dataclass); koleksiyonlar tuple, sözlükler salt okunur (MappingProxyType).
  - Eksik veri sessizce uydurulmaz: değer yoksa `None` ve `Validity.MISSING`, sebep `missing_reason` alanında.
  - JSON'a güvenli dönüşüm: `to_jsonable()` (enum → değer, NaN/inf → None).
  - Bu sürümde hiçbir model emir göndermez; yürütme modu varsayılanı PAPER'dır (bkz. flags.py).

Hiçbiri canlı akışa bağlı değildir: legacy sistem (radar/team.py, desk.py, ledger.py) bu PR'da aynen çalışır.
"""
from __future__ import annotations

import enum
import hashlib
import math
from dataclasses import dataclass, field, fields, is_dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping

# ── yardımcılar ─────────────────────────────────────────────────────────


def _frozen_map(m: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if isinstance(m, MappingProxyType):
        return m
    return MappingProxyType(dict(m or {}))


def _tuple(x: Iterable | None) -> tuple:
    if x is None:
        return ()
    if isinstance(x, (str, bytes)):
        return (x,)
    return tuple(x)


def is_finite_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def stable_key(*parts: Any, n: int = 20) -> str:
    """Belirleyici (deterministik) anahtar: aynı girdiler → aynı anahtar. İdempotency ve client order id için."""
    raw = "|".join("" if p is None else (p.value if isinstance(p, enum.Enum) else str(p)) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:n]


def to_jsonable(obj: Any) -> Any:
    """Dataclass/enum/mapping/tuple ağacını JSON'a güvenli yapıya çevirir (NaN/inf → None)."""
    if isinstance(obj, enum.Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


# ── sabit listeler ──────────────────────────────────────────────────────

TIMEFRAMES = ("1m", "5m", "15m", "1H", "4H", "1D", "1W")
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1H": 3600, "4H": 14_400, "1D": 86_400, "1W": 604_800}


# ── enum'lar ─────────────────────────────────────────────────────────────


class Side(enum.StrEnum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1

    @classmethod
    def parse(cls, v: Any) -> "Side":
        if isinstance(v, Side):
            return v
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0:
            return cls.LONG if v > 0 else cls.SHORT
        s = str(v).strip().lower()
        if s in ("long", "buy", "al", "l"):
            return cls.LONG
        if s in ("short", "sell", "sat", "s"):
            return cls.SHORT
        raise ValueError(f"bilinmeyen yön: {v!r}")


class Horizon(enum.StrEnum):
    MICRO = "micro"          # 1m–15m, mikro yapı
    INTRADAY = "intraday"    # 15m–4H bağlamı, saatler
    SWING = "swing"          # 4H–1D, günler–haftalar
    POSITION = "position"    # 1W
    CARRY = "carry"          # funding/basis, yönsüz

    @classmethod
    def from_timeframe(cls, tf: str | None) -> "Horizon":
        return {"1m": cls.MICRO, "5m": cls.MICRO, "15m": cls.INTRADAY, "1H": cls.INTRADAY,
                "4H": cls.SWING, "1D": cls.SWING, "1W": cls.POSITION}.get(tf or "", cls.SWING)


class Validity(enum.StrEnum):
    VALID = "valid"
    MISSING = "missing"
    STALE = "stale"
    INVALID = "invalid"


class Trend(enum.StrEnum):
    UP = "up"
    DOWN = "down"
    RANGE = "range"
    UNKNOWN = "unknown"


class Regime(enum.StrEnum):
    BULL = "bull"
    RECOVERY = "recovery"
    RANGE = "range"
    DETERIORATION = "deterioration"
    BEAR = "bear"
    CRISIS = "crisis"
    UNKNOWN = "unknown"


class CoinClass(enum.StrEnum):
    A = "A"   # 3 yıl+ geçmiş (radar/director.py ile aynı anlam)
    B = "B"   # 1–3 yıl
    C = "C"   # 1 yıldan yeni


class Decision(enum.StrEnum):
    OPEN = "OPEN"
    REDUCE_SIZE = "REDUCE_SIZE"
    SHADOW = "SHADOW"
    REJECT = "REJECT"
    HOLD = "HOLD"
    EXIT = "EXIT"
    REDUCE_POSITION = "REDUCE_POSITION"


class Tradability(enum.StrEnum):
    REJECT = "REJECT"        # maliyet sonrası avantaj yok
    SHADOW = "SHADOW"        # avantaj var ama tampondan küçük ya da girdiler güvenilmez → gölgede ölç
    PASS = "PASS"            # RiskGate'e gidebilir


class StrategyAuthority(enum.StrEnum):
    PAPER = "paper"              # şartları geçerse paper risk kullanabilir
    SHADOW_ONLY = "shadow_only"  # yalnız gölgede ölçülür (yeni/kanıtsız/veri eksik stratejiler)
    DISABLED = "disabled"        # hiç fırsat üretmez (ör. Deniz)


class ExecutionMode(enum.StrEnum):
    PAPER = "paper"
    OKX_DEMO = "okx_demo"        # gelecekte: OKX demo API (bu sürümde kapalı)
    OKX_LIVE = "okx_live"        # gerçek para: bu çalışmada KESİNLİKLE kapalı


class DecisionMode(enum.StrEnum):
    LEGACY = "legacy"            # mevcut ajan sistemi karar verir (varsayılan)
    SHADOW = "shadow"            # birleşik motor yalnız gölgede karar üretir, legacy işlem açar
    UNIFIED = "unified"          # birleşik motor karar verir (PR 2–3 sonrası)


class OrderType(enum.StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    IOC_LIMIT = "ioc_limit"
    STOP_MARKET = "stop_market"
    TAKE_PROFIT_LIMIT = "take_profit_limit"
    TRAILING_STOP = "trailing_stop"


class OrderStatus(enum.StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    UNKNOWN = "unknown"

    @property
    def terminal(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED)


# ── faktörler ve veri kapsamı ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FactorValue:
    """Tek bir faktörün değeri ve güvenilirliği. Eksik/geçersiz değer uydurulmaz; sebep yazılır."""
    name: str
    value: Any = None
    source: str = ""
    timestamp_ms: int | None = None
    age_seconds: float | None = None
    timeframe: str | None = None
    validity: Validity = Validity.VALID
    confidence: float = 1.0
    missing_reason: str | None = None
    stale: bool = False
    required: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "metadata", _frozen_map(self.metadata))
        c = self.confidence if is_finite_number(self.confidence) else 0.0
        object.__setattr__(self, "confidence", min(1.0, max(0.0, float(c))))
        v, val = self.value, self.validity
        if val is Validity.VALID:
            if v is None:
                val = Validity.MISSING
            elif isinstance(v, float) and not math.isfinite(v):
                val = Validity.INVALID
            elif self.stale:
                val = Validity.STALE
        if val is Validity.STALE and not self.stale:
            object.__setattr__(self, "stale", True)
        if val is not Validity.VALID and self.missing_reason is None:
            object.__setattr__(self, "missing_reason", {Validity.MISSING: "değer yok", Validity.INVALID: "geçersiz değer",
                                                         Validity.STALE: "bayat"}[val])
        object.__setattr__(self, "validity", val)

    @property
    def usable(self) -> bool:
        return self.validity is Validity.VALID and not self.stale

    @classmethod
    def missing(cls, name: str, reason: str, *, required: bool = False, source: str = "",
                timeframe: str | None = None) -> "FactorValue":
        return cls(name=name, value=None, source=source, timeframe=timeframe, validity=Validity.MISSING,
                   confidence=0.0, missing_reason=reason, required=required)


@dataclass(frozen=True, slots=True)
class FactorCoverage:
    """Beklenen faktörlerin ne kadarının kullanılabilir olduğu ve paper işlem için yeterliliği (coverage.py hesaplar)."""
    expected_total: int
    present: int
    required_total: int
    required_ok: int
    coverage_ratio: float
    min_ratio: float
    missing: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()
    invalid: tuple[str, ...] = ()
    required_missing: tuple[str, ...] = ()
    required_stale: tuple[str, ...] = ()
    required_invalid: tuple[str, ...] = ()
    sufficient_for_paper: bool = False
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self):
        for f_ in ("missing", "stale", "invalid", "required_missing", "required_stale", "required_invalid", "reason_codes"):
            object.__setattr__(self, f_, _tuple(getattr(self, f_)))

    @property
    def required_all_ok(self) -> bool:
        return self.required_ok == self.required_total

    def summary_tr(self) -> str:
        s = f"veri kapsamı %{self.coverage_ratio * 100:.0f} ({self.present}/{self.expected_total})"
        bad = list(self.required_missing) + list(self.required_stale) + list(self.required_invalid)
        if bad:
            s += f"; zorunlu sorunlu: {', '.join(bad)}"
        return s


# ── piyasa bağlamı ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CandleContext:
    """Tek mumun geometrisi. Mum isimleri (engulfing vb.) tek başına işlem nedeni değildir; FactorEngine (PR 2)
    bağlamla birlikte değerlendirir. `complete=False` olan mum swing/gün içi stratejilerde kesin sinyal sayılmaz."""
    timeframe: str
    open_ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    complete: bool = True
    atr: float | None = None
    prev_high: float | None = None
    prev_low: float | None = None

    @property
    def valid(self) -> bool:
        px = (self.open, self.high, self.low, self.close)
        if not all(is_finite_number(p) and p > 0 for p in px):
            return False
        return self.high >= max(self.open, self.close) and self.low <= min(self.open, self.close)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def direction(self) -> int:
        return 1 if self.close > self.open else -1 if self.close < self.open else 0

    @property
    def body_ratio(self) -> float | None:
        return self.body / self.range if self.range > 0 else None

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def upper_wick_ratio(self) -> float | None:
        return self.upper_wick / self.range if self.range > 0 else None

    @property
    def lower_wick_ratio(self) -> float | None:
        return self.lower_wick / self.range if self.range > 0 else None

    @property
    def close_pos(self) -> float | None:
        """Kapanışın mum aralığındaki konumu: 0 = dipte, 1 = tepede."""
        return (self.close - self.low) / self.range if self.range > 0 else None

    @property
    def range_atr(self) -> float | None:
        return self.range / self.atr if is_finite_number(self.atr) and self.atr and self.atr > 0 else None

    @property
    def inside(self) -> bool | None:
        if self.prev_high is None or self.prev_low is None:
            return None
        return self.high <= self.prev_high and self.low >= self.prev_low

    @property
    def outside(self) -> bool | None:
        if self.prev_high is None or self.prev_low is None:
            return None
        return self.high > self.prev_high and self.low < self.prev_low


@dataclass(frozen=True, slots=True)
class MarketStructure:
    timeframe: str
    trend: Trend = Trend.UNKNOWN
    ema_fast: float | None = None
    ema_slow: float | None = None
    ema_slope: float | None = None
    adx: float | None = None
    donchian_high: float | None = None
    donchian_low: float | None = None
    prev_day_high: float | None = None
    prev_day_low: float | None = None
    prev_week_high: float | None = None
    prev_week_low: float | None = None
    prev_month_high: float | None = None
    prev_month_low: float | None = None
    swing_high: float | None = None
    swing_low: float | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "notes", _tuple(self.notes))


@dataclass(frozen=True, slots=True)
class VolumeContext:
    timeframe: str | None = None
    volume: float | None = None
    avg_volume: float | None = None
    rel_volume: float | None = None
    quote_volume_24h: float | None = None
    trades: float | None = None
    taker_buy_ratio: float | None = None      # 0..1; yoksa None (uydurulmaz)


@dataclass(frozen=True, slots=True)
class LiquidityContext:
    bid: float | None = None
    ask: float | None = None
    bid_depth_usd: float | None = None
    ask_depth_usd: float | None = None
    imbalance: float | None = None            # −1..1, alış derinliği ağırsa +
    est_slippage_bp: float | None = None
    book_ts_ms: int | None = None
    book_age_s: float | None = None
    spread_durum: str | None = None           # spread_ok / spread_missing / spread_stale / spread_invalid
    ws_healthy: bool | None = None
    seq_gap: bool | None = None

    @property
    def mid(self) -> float | None:
        if is_finite_number(self.bid) and is_finite_number(self.ask) and self.bid > 0 and self.ask >= self.bid:
            return (self.bid + self.ask) / 2
        return None

    @property
    def spread_bp(self) -> float | None:
        m = self.mid
        return (self.ask - self.bid) / m * 1e4 if m else None


@dataclass(frozen=True, slots=True)
class DerivativesContext:
    funding_rate: float | None = None         # kesim başına oran (ör. 0.0001 = %0,01)
    funding_interval_h: float | None = None
    funding_ann_pct: float | None = None      # yıllık %; verilmezse orandan hesaplanır
    next_funding_ts_ms: int | None = None
    open_interest: float | None = None
    oi_change_24h_pct: float | None = None
    basis_bp: float | None = None
    mark_index_bp: float | None = None
    long_short_ratio: float | None = None
    liquidation_long_usd: float | None = None
    liquidation_short_usd: float | None = None

    def __post_init__(self):
        if self.funding_ann_pct is None and is_finite_number(self.funding_rate):
            iv = self.funding_interval_h if is_finite_number(self.funding_interval_h) and self.funding_interval_h else 8.0
            object.__setattr__(self, "funding_ann_pct", float(self.funding_rate) * (8760.0 / iv) * 100.0)


@dataclass(frozen=True, slots=True)
class RegimeContext:
    """Piyasa rejimi. Rejim bir puan değildir: strateji yetkisi, boyut, long/short izni ve çıkışı belirler (PR 2–3)."""
    regime: Regime = Regime.UNKNOWN
    btc_trend_long: Trend = Trend.UNKNOWN
    btc_trend_mid: Trend = Trend.UNKNOWN
    btc_trend_short: Trend = Trend.UNKNOWN
    btc_vol_pct: float | None = None
    breadth: float | None = None              # yükselen coin oranı 0..1
    confidence: float = 0.0
    source: str = ""

    @classmethod
    def from_legacy_weather(cls, w: Mapping[str, Any] | None) -> "RegimeContext":
        """radar/quant.btc_weather çıktısından (trend: boga/ayi/yatay, high_vol) muhafazakâr eşleme.
        Ayı + yüksek oynaklık → CRISIS. Bilinmiyorsa UNKNOWN (uydurulmaz)."""
        w = w or {}
        t = w.get("trend")
        hv = bool(w.get("high_vol"))
        if t == "boga":
            reg, tr = Regime.BULL, Trend.UP
        elif t == "ayi":
            reg, tr = (Regime.CRISIS if hv else Regime.BEAR), Trend.DOWN
        elif t == "yatay":
            reg, tr = Regime.RANGE, Trend.RANGE
        else:
            return cls(source="legacy_weather")
        return cls(regime=reg, btc_trend_mid=tr, confidence=0.6, source="legacy_weather")


@dataclass(frozen=True, slots=True)
class DataQualityContext:
    """Veri sağlığı. Kritik sorun → fail-closed (yeni paper pozisyon açılmaz)."""
    issues: tuple[str, ...] = ()
    critical: tuple[str, ...] = ()
    last_candle_age_s: float | None = None
    ws_healthy: bool | None = None
    book_age_s: float | None = None
    seq_gap: bool | None = None
    clock_skew_ms: float | None = None
    history_bars: int | None = None

    def __post_init__(self):
        object.__setattr__(self, "issues", _tuple(self.issues))
        object.__setattr__(self, "critical", _tuple(self.critical))

    @property
    def fail_closed(self) -> bool:
        return bool(self.critical)

    @property
    def ok(self) -> bool:
        return not self.critical

    @classmethod
    def from_legacy_issue(cls, issue: str | None, **kw) -> "DataQualityContext":
        """engine._data_issue() çıktısı: metin varsa kritik sayılır (legacy de o coinde işlem açmıyor)."""
        return cls(issues=(issue,) if issue else (), critical=(issue,) if issue else (), **kw)


@dataclass(frozen=True, slots=True)
class MarketContext:
    """Bir coinin karar anındaki bütün bilgisi. Stratejiler yalnız bunu okur, emir gönderemez."""
    symbol: str
    inst: str
    ts_ms: int
    timeframe: str
    candles: Mapping[str, CandleContext] = field(default_factory=dict)
    structure: Mapping[str, MarketStructure] = field(default_factory=dict)
    volume: VolumeContext = field(default_factory=VolumeContext)
    liquidity: LiquidityContext = field(default_factory=LiquidityContext)
    derivatives: DerivativesContext = field(default_factory=DerivativesContext)
    regime: RegimeContext = field(default_factory=RegimeContext)
    data_quality: DataQualityContext = field(default_factory=DataQualityContext)
    coin_class: CoinClass | None = None
    coin_age_days: float | None = None
    factors: Mapping[str, FactorValue] = field(default_factory=dict)
    coverage: FactorCoverage | None = None
    coverage_by_horizon: Mapping[Horizon, FactorCoverage] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)   # legacy bağlam (salt okunur); yalnız legacy sarmalayıcılar okur

    def __post_init__(self):
        for f_ in ("candles", "structure", "factors", "coverage_by_horizon", "raw"):
            object.__setattr__(self, f_, _frozen_map(getattr(self, f_)))

    def coverage_for(self, horizon: Horizon) -> FactorCoverage | None:
        """Use the opportunity horizon's own required/optional factor contract."""
        return self.coverage_by_horizon.get(horizon, self.coverage)

    def factor(self, name: str) -> FactorValue | None:
        return self.factors.get(name)

    def value(self, name: str, default: Any = None):
        f = self.factors.get(name)
        return f.value if f is not None and f.usable else default


# ── fırsat, maliyet, karar ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Opportunity:
    """Bir stratejinin önerisi. Risk miktarı, kaldıraç ya da emir içermez (onları RiskGate ve yürütme belirler)."""
    symbol: str
    inst: str
    side: Side
    strategy: str
    setup: str
    timeframe: str
    horizon: Horizon
    created_ts_ms: int
    entry: float
    stop: float
    targets: tuple[float, ...] = ()
    bar_ts_ms: int | None = None
    entry_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    expected_holding_hours: float | None = None
    max_holding_hours: float | None = None
    gross_edge_bp: float | None = None        # stratejinin maliyet ÖNCESİ beklenen avantajı (nominalin bp'si); bilinmiyorsa None
    confidence: float = 0.0
    htf_direction: Trend = Trend.UNKNOWN      # üst zaman dilimi yönü
    decision_setup: str = ""                  # karar zaman dilimindeki kurulum
    ltf_entry_quality: float | None = None    # alt zaman dilimi giriş kalitesi 0..1
    evidence_for: tuple[str, ...] = ()
    evidence_against: tuple[str, ...] = ()
    factors_used: tuple[str, ...] = ()
    authority: StrategyAuthority = StrategyAuthority.PAPER
    source: str = "unified"                   # unified | legacy
    legacy: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "side", Side.parse(self.side))
        for f_ in ("targets", "evidence_for", "evidence_against", "factors_used"):
            object.__setattr__(self, f_, _tuple(getattr(self, f_)))
        object.__setattr__(self, "legacy", _frozen_map(self.legacy))
        c = self.confidence if is_finite_number(self.confidence) else 0.0
        object.__setattr__(self, "confidence", min(1.0, max(0.0, float(c))))
        if not (is_finite_number(self.entry) and self.entry > 0 and is_finite_number(self.stop) and self.stop > 0):
            raise ValueError(f"{self.symbol}: giriş/stop geçersiz ({self.entry!r}, {self.stop!r})")
        if self.side.sign * (self.entry - self.stop) <= 0:
            raise ValueError(f"{self.symbol}: stop yanlış tarafta ({self.side.value}, giriş {self.entry}, stop {self.stop})")
        for t in self.targets:
            if not is_finite_number(t) or self.side.sign * (t - self.entry) <= 0:
                raise ValueError(f"{self.symbol}: hedef yanlış tarafta ya da geçersiz ({t!r})")

    @property
    def stop_distance(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def stop_bp(self) -> float:
        return self.stop_distance / self.entry * 1e4

    @property
    def rr(self) -> float | None:
        """Son hedefe göre ödül/risk."""
        return abs(self.targets[-1] - self.entry) / self.stop_distance if self.targets else None

    @property
    def dedup_key(self) -> str:
        """Aynı coin + yön + kurulum + zaman ufku → aynı fırsat (UnifiedDecisionEngine birleştirir)."""
        return f"{self.symbol}|{self.side.value}|{self.setup}|{self.horizon.value}"

    @property
    def idempotency_key(self) -> str:
        """Aynı mumda aynı fırsatın iki kez işleme dönüşmesini engeller."""
        return stable_key(self.symbol, self.side, self.strategy, self.setup, self.timeframe,
                          self.bar_ts_ms if self.bar_ts_ms is not None else self.created_ts_ms)


@dataclass(frozen=True, slots=True)
class CostLeg:
    """Çok bacaklı işlemlerde (carry: spot al + swap short, açılış + kapanış) tek bacağın maliyeti."""
    name: str
    instrument: str                           # "swap" | "spot"
    order_type: OrderType
    fee_bp: float
    spread_bp: float
    slippage_bp: float

    @property
    def total_bp(self) -> float:
        return self.fee_bp + self.spread_bp + self.slippage_bp


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """Maliyet kalemleri nominalin baz puanı (bp) cinsinden. Pozitif = maliyet. funding_bp negatifse gelir."""
    horizon: Horizon
    notional_usd: float | None
    entry_fee_bp: float
    exit_fee_bp: float
    spread_bp: float
    entry_slippage_bp: float
    exit_slippage_bp: float
    stop_slippage_bp: float                   # stop'la çıkma olasılığıyla ağırlıklı beklenen ek kayma
    stop_slippage_raw_bp: float               # stop'ta gerçekleşirse kayma
    funding_bp: float
    missed_fill_buffer_bp: float              # limit/IOC emrinde kaçırma ve kısmi dolum tamponu
    uncertainty_buffer_bp: float
    total_cost_bp: float
    gross_edge_bp: float | None
    net_edge_bp: float | None                 # brüt − maliyetler − belirsizlik tamponu (GOREV formülü)
    edge_after_costs_bp: float | None = None  # brüt − maliyetler (tampon hariç); ≤ 0 → REJECT
    fill_ratio: float = 1.0
    unfilled_ratio: float = 0.0
    confidence: float = 1.0
    missing_inputs: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    tradability: Tradability = Tradability.SHADOW
    reason_codes: tuple[str, ...] = ()
    legs: tuple[CostLeg, ...] = ()

    def __post_init__(self):
        for f_ in ("missing_inputs", "assumptions", "reason_codes", "legs"):
            object.__setattr__(self, f_, _tuple(getattr(self, f_)))

    def cost_r(self, stop_bp: float) -> float | None:
        """Toplam maliyetin R cinsinden karşılığı (stop mesafesine göre)."""
        return self.total_cost_bp / stop_bp if stop_bp and stop_bp > 0 else None

    def net_edge_r(self, stop_bp: float) -> float | None:
        if self.net_edge_bp is None or not stop_bp or stop_bp <= 0:
            return None
        return self.net_edge_bp / stop_bp


@dataclass(frozen=True, slots=True)
class DecisionResult:
    decision: Decision
    symbol: str
    side: Side | None
    strategy: str
    timeframe: str
    confidence: float
    gross_edge_bp: float | None
    costs: CostEstimate | None
    net_edge_bp: float | None
    entry: float | None
    stop: float | None
    targets: tuple[float, ...] = ()
    expected_holding_hours: float | None = None
    risk_proposal: Mapping[str, Any] = field(default_factory=dict)
    evidence_for: tuple[str, ...] = ()
    evidence_against: tuple[str, ...] = ()
    missing_factors: tuple[str, ...] = ()
    stale_factors: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    explanation_tr: str = ""
    ts_ms: int = 0
    idempotency_key: str = ""
    strategies: tuple[str, ...] = ()          # aynı fırsatı bulan bütün stratejiler (kanıt yoğunluğu)
    plan: Mapping[str, Any] = field(default_factory=dict)   # yürütme planı: inst, ufuk, giriş tipi, çıkış kuralı, ATR (PR 3)

    def __post_init__(self):
        if self.side is not None:
            object.__setattr__(self, "side", Side.parse(self.side))
        for f_ in ("targets", "evidence_for", "evidence_against", "missing_factors", "stale_factors", "reason_codes",
                   "strategies"):
            object.__setattr__(self, f_, _tuple(getattr(self, f_)))
        object.__setattr__(self, "risk_proposal", _frozen_map(self.risk_proposal))
        object.__setattr__(self, "plan", _frozen_map(self.plan))

    @property
    def evidence_count(self) -> int:
        return len(self.strategies)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """RiskGate'in son sözü. Baş Trader açmak istese bile burada reddedilebilir."""
    decision: Decision                        # OPEN | REDUCE_SIZE | SHADOW | REJECT
    risk_usd: float = 0.0
    risk_pct: float = 0.0
    notional_usd: float = 0.0
    qty: float = 0.0
    leverage: float = 1.0
    margin_usd: float = 0.0
    liq_price: float | None = None
    stop_liq_buffer_pct: float | None = None
    limits_checked: tuple[str, ...] = ()
    limits_hit: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    explanation_tr: str = ""

    def __post_init__(self):
        for f_ in ("limits_checked", "limits_hit", "reason_codes"):
            object.__setattr__(self, f_, _tuple(getattr(self, f_)))

    @property
    def approved(self) -> bool:
        return self.decision in (Decision.OPEN, Decision.REDUCE_SIZE)


# ── yürütme ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    """Yürütme adaptörüne giden emir niyeti. Varsayılan ve bu sürümde tek izinli mod PAPER."""
    idempotency_key: str
    symbol: str
    inst: str
    side: Side
    action: str                               # open | close | reduce
    order_type: OrderType
    qty: float
    created_ts_ms: int
    price: float | None = None
    stop_price: float | None = None
    reduce_only: bool = False
    time_in_force: str = "GTC"                # GTC | IOC
    expires_ts_ms: int | None = None
    mode: ExecutionMode = ExecutionMode.PAPER
    seq: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "side", Side.parse(self.side))
        object.__setattr__(self, "metadata", _frozen_map(self.metadata))
        if not (is_finite_number(self.qty) and self.qty > 0):
            raise ValueError(f"{self.symbol}: emir miktarı geçersiz ({self.qty!r})")
        if self.order_type in (OrderType.LIMIT, OrderType.IOC_LIMIT, OrderType.TAKE_PROFIT_LIMIT) and not (
                is_finite_number(self.price) and self.price > 0):
            raise ValueError(f"{self.symbol}: limit emirde fiyat gerekli")

    @property
    def client_order_id(self) -> str:
        """Belirleyici: aynı niyet → aynı id (tekrar gönderimde çift emir oluşmaz). OKX clOrdId sınırına uygun (≤32, alfanümerik)."""
        return "kr" + stable_key(self.idempotency_key, self.action, self.seq, n=30)


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    ts_ms: int
    client_order_id: str
    kind: str                                 # submitted | accepted | fill | partial_fill | cancel | expire | reject | funding | ...
    qty: float = 0.0
    price: float | None = None
    fee_usd: float = 0.0
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "payload", _frozen_map(self.payload))


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    client_order_id: str
    status: OrderStatus
    requested_qty: float
    filled_qty: float = 0.0
    avg_price: float | None = None
    fee_usd: float = 0.0
    ts_ms: int = 0
    reason: str = ""
    events: tuple[ExecutionEvent, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "events", _tuple(self.events))

    @property
    def fill_ratio(self) -> float:
        return self.filled_qty / self.requested_qty if self.requested_qty > 0 else 0.0

    @property
    def is_terminal(self) -> bool:
        return self.status.terminal


# ── ölçüm ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EffectivenessSnapshot:
    """Bir kapsamın (strateji, rejim, tüm sistem…) gerçekleşen performansı. Güven aralığı bootstrap ile (sabit tohum)."""
    scope: str
    ts_ms: int
    n_trades: int
    win_rate: float | None = None
    avg_r: float | None = None
    median_r: float | None = None
    sum_r: float | None = None
    profit_factor: float | None = None
    ci_low_r: float | None = None
    ci_high_r: float | None = None
    top3_share: float | None = None           # toplam kârın en iyi 3 işlemden gelen payı (yoğunlaşma)
    net_pnl_usd: float | None = None
    cost_usd: float | None = None
    funding_usd: float | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "notes", _tuple(self.notes))

    @property
    def proven_positive(self) -> bool:
        """Güven aralığının alt sınırı sıfırın üstünde mi? (gerçek paraya geçişin asgari şartı, tek başına yetmez)"""
        return self.ci_low_r is not None and self.ci_low_r > 0

    @classmethod
    def from_r(cls, scope: str, ts_ms: int, r_values: Iterable[float], *, n_boot: int = 2000, alpha: float = 0.05,
               seed: int = 7, **extra) -> "EffectivenessSnapshot":
        import numpy as np
        rs = np.array([float(r) for r in r_values if is_finite_number(r)], dtype=float)
        n = len(rs)
        if n == 0:
            return cls(scope=scope, ts_ms=ts_ms, n_trades=0, notes=("işlem yok",), **extra)
        wins, losses = rs[rs > 0], rs[rs < 0]
        pf = float(wins.sum() / -losses.sum()) if losses.size and losses.sum() < 0 else None
        lo = hi = None
        if n >= 5:
            rng = np.random.default_rng(seed)
            means = rs[rng.integers(0, n, size=(n_boot, n))].mean(axis=1)
            lo, hi = float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))
        pos = np.sort(rs[rs > 0])[::-1]
        top3 = float(pos[:3].sum() / pos.sum()) if pos.size and pos.sum() > 0 else None
        notes = () if n >= 30 else (f"örnek küçük ({n} işlem): sonuç kanıt sayılmaz",)
        return cls(scope=scope, ts_ms=ts_ms, n_trades=n, win_rate=float((rs > 0).mean()), avg_r=float(rs.mean()),
                   median_r=float(np.median(rs)), sum_r=float(rs.sum()), profit_factor=pf, ci_low_r=lo, ci_high_r=hi,
                   top3_share=top3, notes=notes, **extra)


# ── legacy köprüsü ───────────────────────────────────────────────────

# Eski ajan → yeni strateji karşılığı (GOREV.md "Eski ajan dönüşümü"). Geçmiş kayıtlar silinmez; yalnız eşlenir.
LEGACY_STRATEGY = {
    "kemal": ("swing_trend", StrategyAuthority.PAPER),
    "selin": ("compression_breakout", StrategyAuthority.PAPER),
    "kaan": ("controlled_breakout", StrategyAuthority.PAPER),
    "ayse": ("panic_reversal", StrategyAuthority.SHADOW_ONLY),
    "elif": ("carry", StrategyAuthority.PAPER),
    "murat": ("legacy_murat", StrategyAuthority.SHADOW_ONLY),     # legacy gölge karşılaştırma
    "deniz": ("legacy_deniz", StrategyAuthority.DISABLED),        # devre dışı; mikro araştırma referansı
    "turbo": ("legacy_turbo", StrategyAuthority.SHADOW_ONLY),     # bağımsız işlem açma yetkisi kalkar
    "ortak": ("legacy_ortak", StrategyAuthority.SHADOW_ONLY),     # bağımsız işlem açma yetkisi kalkar
    "bas": ("bas_trader_legacy", StrategyAuthority.SHADOW_ONLY),  # yerini UnifiedDecisionEngine alır
}


def opportunity_from_legacy_idea(idea: Mapping[str, Any], now_ms: int | None = None) -> Opportunity:
    """radar/team._idea() sözlüğünden Opportunity. Legacy'yi DEĞİŞTİRMEZ; birleşik motorun gölgede karşılaştırması içindir.

    Brüt avantaj legacy'de tahmin edilmediği için `gross_edge_bp=None` kalır (uydurulmaz). Hedefler sırayla tp1, tp.
    `max_saat` beklenen değil EN UZUN tutma süresidir → `max_holding_hours`."""
    mgr = str(idea.get("mgr") or "")
    strategy, authority = LEGACY_STRATEGY.get(mgr, (f"legacy_{mgr or 'bilinmeyen'}", StrategyAuthority.SHADOW_ONLY))
    side = Side.parse(idea["side"])
    entry = float(idea.get("entry") or idea.get("market_px"))
    raw_targets = [idea.get("tp1"), idea.get("tp")]
    targets = []
    for t in raw_targets:
        if is_finite_number(t) and side.sign * (float(t) - entry) > 0 and float(t) not in targets:
            targets.append(float(t))
    targets.sort(key=lambda x: side.sign * x)
    checks = idea.get("checks") or []
    ev_for = tuple(str(n) for n, ok in checks if ok)
    ev_against = tuple(str(n) for n, ok in checks if not ok)
    tf = str(idea.get("tf") or "1D")
    kind = idea.get("kind")
    horizon = Horizon.CARRY if kind == "carry" else Horizon.from_timeframe(tf)
    ts = int(now_ms if now_ms is not None else idea.get("created_ms") or 0)
    return Opportunity(
        symbol=str(idea.get("sym") or idea.get("inst") or ""), inst=str(idea.get("inst") or ""), side=side,
        strategy=strategy, setup=str(idea.get("setup") or mgr), timeframe=tf, horizon=horizon, created_ts_ms=ts,
        entry=entry, stop=float(idea["sl"]), targets=tuple(targets),
        entry_type=OrderType.LIMIT if idea.get("entry_type") == "limit" else OrderType.MARKET,
        limit_price=idea.get("limit_px") if idea.get("entry_type") == "limit" else None,
        max_holding_hours=float(idea["max_saat"]) if is_finite_number(idea.get("max_saat")) else None,
        gross_edge_bp=None, confidence=float(idea.get("conviction") or 0.0), decision_setup=str(idea.get("core") or ""),
        evidence_for=ev_for, evidence_against=ev_against, factors_used=tuple(sorted((idea.get("feat") or {}).keys())),
        authority=authority, source="legacy", legacy=dict(idea))
