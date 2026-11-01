"""Ortak piyasa verisi kalite kapısı.

Polat'ın public OKX olayları ve UnifiedTrader'ın OKX WS/REST BBO karar yolu
bu çekirdeği kullanır. Her kanal kendi timestamp/freshness kuralıyla değerlendirilir.

Eksik veya güvenilmez kaynak verisi UNKNOWN/INVALID/STALE kalır; yerel alım zamanı
borsa olay zamanı yerine geçirilmez.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from math import ceil
from typing import Mapping


class QualityState(str, Enum):
    LIVE = "LIVE"
    STALE = "STALE"
    INVALID = "INVALID"
    DISCONNECTED = "DISCONNECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ChannelRule:
    # None: yalnız yapısal/sıra doğrulaması; freshness strateji kapısında ayrıca uygulanır.
    max_age_ms: int | None
    require_sequence: bool = False
    max_clock_skew_ms: int = 2_000

    def __post_init__(self):
        if self.max_age_ms is not None and self.max_age_ms <= 0:
            raise ValueError("max_age_ms pozitif olmalı veya None olmalı")
        if self.max_clock_skew_ms < 0:
            raise ValueError("max_clock_skew_ms negatif olamaz")


@dataclass(frozen=True)
class QualityObservation:
    symbol: str
    channel: str
    state: QualityState
    event_ts_ms: int | None
    receive_ts_ms: int
    age_ms: int | None
    seq_id: int | None = None
    reason_code: str | None = None


class MarketDataQualityEngine:
    """Sembol/kanal bazında timestamp, freshness ve sıra tutarlılığı denetler.

    max_age_ms=None yalnız yapısal geçerlilik bildirir; LIVE sonucu tek başına
    strateji için yeterli tazelik kanıtı değildir. Tazelik strategy_data_gate ile
    stratejinin kendi eşiğine göre yeniden denetlenmelidir.
    """

    def __init__(self, rules: Mapping[str, ChannelRule]):
        self.rules = dict(rules)
        self._last_seq: dict[tuple[str, str], int] = {}
        self._last_event_ts: dict[tuple[str, str], int] = {}

    def observe(
        self,
        *,
        symbol: str,
        channel: str,
        event_ts_ms: int | None,
        receive_ts_ms: int,
        seq_id: int | None = None,
        prev_seq_id: int | None = None,
        is_snapshot: bool = False,
        valid: bool = True,
    ) -> QualityObservation:
        key = (symbol, channel)
        rule = self.rules.get(channel)
        if rule is None:
            return QualityObservation(symbol, channel, QualityState.UNKNOWN, event_ts_ms,
                                      receive_ts_ms, None, seq_id, "channel_rule_missing")
        if not valid:
            return QualityObservation(symbol, channel, QualityState.INVALID, event_ts_ms,
                                      receive_ts_ms, None, seq_id, "payload_invalid")
        if event_ts_ms is None or event_ts_ms <= 0:
            return QualityObservation(symbol, channel, QualityState.UNKNOWN, None,
                                      receive_ts_ms, None, seq_id, "event_timestamp_missing")

        age = receive_ts_ms - event_ts_ms
        if age < -rule.max_clock_skew_ms:
            return QualityObservation(symbol, channel, QualityState.INVALID, event_ts_ms,
                                      receive_ts_ms, age, seq_id, "clock_skew_future")
        if rule.max_age_ms is not None and age > rule.max_age_ms:
            return QualityObservation(symbol, channel, QualityState.STALE, event_ts_ms,
                                      receive_ts_ms, age, seq_id, "event_stale")

        last_ts = self._last_event_ts.get(key)
        if last_ts is not None and event_ts_ms < last_ts:
            return QualityObservation(symbol, channel, QualityState.INVALID, event_ts_ms,
                                      receive_ts_ms, age, seq_id, "event_out_of_order")

        last_seq = self._last_seq.get(key)
        if rule.require_sequence:
            if seq_id is None:
                return QualityObservation(symbol, channel, QualityState.UNKNOWN, event_ts_ms,
                                          receive_ts_ms, age, None, "sequence_missing")
            if is_snapshot:
                pass
            elif last_seq is None:
                return QualityObservation(symbol, channel, QualityState.UNKNOWN, event_ts_ms,
                                          receive_ts_ms, seq_id, "snapshot_baseline_missing")
            elif prev_seq_id != last_seq:
                return QualityObservation(symbol, channel, QualityState.INVALID, event_ts_ms,
                                          receive_ts_ms, age, seq_id, "sequence_gap")
            elif seq_id <= last_seq:
                reason = "duplicate_sequence" if seq_id == last_seq else "sequence_out_of_order"
                return QualityObservation(symbol, channel, QualityState.INVALID, event_ts_ms,
                                          receive_ts_ms, age, seq_id, reason)
        elif seq_id is not None and last_seq is not None:
            if seq_id == last_seq:
                return QualityObservation(symbol, channel, QualityState.INVALID, event_ts_ms,
                                          receive_ts_ms, age, seq_id, "duplicate_sequence")
            if seq_id < last_seq:
                return QualityObservation(symbol, channel, QualityState.INVALID, event_ts_ms,
                                          receive_ts_ms, age, seq_id, "sequence_out_of_order")

        if seq_id is not None:
            self._last_seq[key] = seq_id
        self._last_event_ts[key] = event_ts_ms
        return QualityObservation(symbol, channel, QualityState.LIVE, event_ts_ms,
                                  receive_ts_ms, age, seq_id, None)

    def disconnected(self, *, symbol: str, channel: str, receive_ts_ms: int) -> QualityObservation:
        return QualityObservation(symbol, channel, QualityState.DISCONNECTED, None,
                                  receive_ts_ms, None, None, "connection_disconnected")


def strategy_data_gate(
    requirements: Mapping[str, int],
    observations: Mapping[str, QualityObservation],
    *,
    now_ms: int,
) -> dict:
    """Yalnız stratejinin zorunlu kanallarını kontrol eder; global kilit uygulamaz."""
    blocked: list[dict] = []
    for channel, max_age_ms in requirements.items():
        obs = observations.get(channel)
        if obs is None:
            blocked.append({"channel": channel, "state": QualityState.UNKNOWN.value,
                            "reason_code": "required_channel_missing"})
            continue
        if obs.state is not QualityState.LIVE:
            blocked.append({"channel": channel, "state": obs.state.value,
                            "reason_code": obs.reason_code or obs.state.value.lower()})
            continue
        age = now_ms - obs.event_ts_ms if obs.event_ts_ms is not None else None
        if age is None or age < 0 or age > max_age_ms:
            blocked.append({"channel": channel, "state": QualityState.STALE.value,
                            "reason_code": "strategy_freshness_limit"})
    return {"eligible": not blocked, "blocked_by": blocked}


class LatencyWindow:
    """Sabit boyutlu yerel gecikme örneklemi; yüzdelikler milisaniyedir."""

    def __init__(self, capacity: int = 5_000):
        if capacity <= 0:
            raise ValueError("capacity pozitif olmalı")
        self._samples: deque[float] = deque(maxlen=capacity)

    def add(self, latency_ms: float) -> None:
        if latency_ms < 0:
            raise ValueError("latency_ms negatif olamaz")
        self._samples.append(float(latency_ms))

    def snapshot(self) -> dict:
        values = sorted(self._samples)
        if not values:
            return {"samples": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}

        def percentile(p: float) -> float:
            # Nearest-rank: küçük örneklemlerde de deterministik.
            return values[max(0, ceil(p * len(values)) - 1)]

        return {
            "samples": len(values),
            "p50_ms": percentile(0.50),
            "p95_ms": percentile(0.95),
            "p99_ms": percentile(0.99),
            "max_ms": values[-1],
        }
