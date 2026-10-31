from polat.market import Market


def _market():
    return Market(
        {"veri": {"zaman_dilimleri": ["1m"]}, "kurulum": {"duvar_kat": 3}},
        {"BTC-USDT-SWAP": {"ctVal": 1.0}},
    )


def test_market_exports_real_ticker_timestamp_quality_telemetry():
    market = _market()
    market.handle(
        {"arg": {"channel": "tickers"},
         "data": [{"instId": "BTC-USDT-SWAP", "last": "100", "bidPx": "99.9",
                   "askPx": "100.1", "ts": "1000"}]},
        recv_ms=1_125,
    )

    stats = market.stats()
    quality = stats["kalite_telemetrisi"]
    assert quality["kanallar"]["tickers"]["sembol_sayisi"] == 1
    assert quality["kanallar"]["tickers"]["durumlar"]["LIVE"] == 1
    assert "freshness strateji eşiği ayrıca uygulanır" in quality["anlam"]


def test_market_marks_order_book_sequence_gap_invalid_until_snapshot():
    market = _market()
    market.handle(
        {"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"}, "action": "snapshot",
         "data": [{"ts": "1000", "seqId": 100,
                   "bids": [["99", "2"]], "asks": [["101", "2"]]}]},
        recv_ms=1_010,
    )
    live = market.stats()["kalite_telemetrisi"]["kanallar"]["books"]
    assert live["durumlar"]["LIVE"] == 1

    market.handle(
        {"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"}, "action": "update",
         "data": [{"ts": "1010", "seqId": 103, "prevSeqId": 101,
                   "bids": [["99", "1"]], "asks": []}]},
        recv_ms=1_020,
    )
    invalid = market.stats()["kalite_telemetrisi"]["kanallar"]["books"]
    assert invalid["durumlar"]["INVALID"] == 1
    assert invalid["durumlar"]["LIVE"] == 0

    market.handle(
        {"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"}, "action": "snapshot",
         "data": [{"ts": "1020", "seqId": 200,
                   "bids": [["99", "2"]], "asks": [["101", "2"]]}]},
        recv_ms=1_030,
    )
    recovered = market.stats()["kalite_telemetrisi"]["kanallar"]["books"]
    assert recovered["durumlar"]["LIVE"] == 1
    assert recovered["durumlar"]["INVALID"] == 0
