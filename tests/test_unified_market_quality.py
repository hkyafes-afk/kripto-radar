from radar.unified.quality import (
    ChannelRule,
    LatencyWindow,
    MarketDataQualityEngine,
    QualityState,
    strategy_data_gate,
)


def test_freshness_and_clock_quality_states():
    engine = MarketDataQualityEngine({"trades": ChannelRule(max_age_ms=1_000)})
    live = engine.observe(symbol="BTC-USDT-SWAP", channel="trades",
                          event_ts_ms=10_000, receive_ts_ms=10_250)
    stale = engine.observe(symbol="ETH-USDT-SWAP", channel="trades",
                           event_ts_ms=1_000, receive_ts_ms=10_000)
    future = engine.observe(symbol="SOL-USDT-SWAP", channel="trades",
                            event_ts_ms=20_000, receive_ts_ms=10_000)
    missing = engine.observe(symbol="XRP-USDT-SWAP", channel="trades",
                             event_ts_ms=None, receive_ts_ms=10_000)

    assert live.state is QualityState.LIVE and live.age_ms == 250
    assert stale.state is QualityState.STALE and stale.reason_code == "event_stale"
    assert future.state is QualityState.INVALID and future.reason_code == "clock_skew_future"
    assert missing.state is QualityState.UNKNOWN


def test_order_book_sequence_gap_blocks_until_snapshot_recovers():
    engine = MarketDataQualityEngine({"books": ChannelRule(max_age_ms=2_000, require_sequence=True)})
    first = engine.observe(symbol="BTC-USDT-SWAP", channel="books", event_ts_ms=10_000,
                            receive_ts_ms=10_010, seq_id=100, is_snapshot=True)
    gap = engine.observe(symbol="BTC-USDT-SWAP", channel="books", event_ts_ms=10_100,
                         receive_ts_ms=10_110, seq_id=103, prev_seq_id=101)
    recovery = engine.observe(symbol="BTC-USDT-SWAP", channel="books", event_ts_ms=10_200,
                              receive_ts_ms=10_210, seq_id=200, is_snapshot=True)

    assert first.state is QualityState.LIVE
    assert gap.state is QualityState.INVALID and gap.reason_code == "sequence_gap"
    assert recovery.state is QualityState.LIVE


def test_strategy_gate_isolates_unrelated_channel_requirements():
    engine = MarketDataQualityEngine({
        "ticker": ChannelRule(max_age_ms=2_000),
        "funding": ChannelRule(max_age_ms=60_000),
    })
    ticker = engine.observe(symbol="BTC-USDT-SWAP", channel="ticker",
                            event_ts_ms=10_000, receive_ts_ms=10_100)
    funding = engine.observe(symbol="BTC-USDT-SWAP", channel="funding",
                             event_ts_ms=1_000, receive_ts_ms=10_100)
    observations = {"ticker": ticker, "funding": funding}

    ticker_only = strategy_data_gate({"ticker": 2_000}, observations, now_ms=10_100)
    funding_strategy = strategy_data_gate({"ticker": 2_000, "funding": 5_000},
                                           observations, now_ms=10_100)

    assert ticker_only["eligible"] is True
    assert funding_strategy["eligible"] is False
    assert funding_strategy["blocked_by"][0]["channel"] == "funding"


def test_latency_window_reports_percentiles_and_empty_state():
    window = LatencyWindow(capacity=4)
    assert window.snapshot()["samples"] == 0
    for value in (1, 2, 3, 4, 100):
        window.add(value)
    result = window.snapshot()
    assert result == {"samples": 4, "p50_ms": 3.0, "p95_ms": 100.0,
                      "p99_ms": 100.0, "max_ms": 100.0}
