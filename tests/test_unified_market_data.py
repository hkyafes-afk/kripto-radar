import math

from radar.unified.market_data import normalize_bbo, normalize_okx_ticker


def test_okx_ticker_normalizer_preserves_exchange_timestamp_and_values():
    result = normalize_okx_ticker({
        "instId": "BTC-USDT-SWAP", "last": "100", "bidPx": "99.9",
        "askPx": "100.1", "ts": "1700000000000",
    })
    assert result.venue == "okx"
    assert result.symbol == "BTC-USDT-SWAP"
    assert result.last == 100
    assert result.bid == 99.9
    assert result.ask == 100.1
    assert result.event_ts_ms == 1_700_000_000_000
    assert result.valid is True


def test_normalizers_reject_nonfinite_locked_and_missing_timestamp_values():
    bad = normalize_okx_ticker({
        "instId": "BTC-USDT-SWAP", "last": "NaN", "bidPx": "100",
        "askPx": "100", "ts": None,
    })
    assert bad.valid is False
    assert bad.event_ts_ms is None
    assert bad.reason == "price_missing_or_non_finite"

    generic = normalize_bbo(
        venue="bitget", symbol="BTCUSDT", last=100, bid=101, ask=100,
        event_ts_ms="1700000000000",
    )
    assert generic.valid is False
    assert generic.reason == "bbo_locked_or_crossed"
    assert math.isfinite(generic.last)


def test_generic_normalizer_does_not_fabricate_timestamp():
    result = normalize_bbo(
        venue="okx", symbol="ETH-USDT-SWAP", last="2000", bid="1999",
        ask="2001", event_ts_ms=None,
    )
    assert result.valid is True
    assert result.event_ts_ms is None
