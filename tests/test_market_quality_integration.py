from polat.market import Market
from polat.okx import Feed


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



def test_disconnect_marks_only_channels_owned_by_failed_connection():
    market = _market()
    market.handle(
        {"arg": {"channel": "tickers"},
         "data": [{"instId": "BTC-USDT-SWAP", "last": "100", "bidPx": "99.9",
                   "askPx": "100.1", "ts": "1000"}]},
        recv_ms=1_010,
    )
    market.handle(
        {"arg": {"channel": "trades", "instId": "BTC-USDT-SWAP"},
         "data": [{"instId": "BTC-USDT-SWAP", "tradeId": "1", "px": "100",
                   "sz": "1", "side": "buy", "ts": "1001"}]},
        recv_ms=1_011,
    )
    market.disconnect_channels("piyasa", 1_020)
    channels = market.stats()["kalite_telemetrisi"]["kanallar"]
    assert channels["tickers"]["durumlar"]["DISCONNECTED"] == 1
    assert channels["trades"]["durumlar"]["LIVE"] == 1
    assert channels["trades"]["durumlar"]["DISCONNECTED"] == 0


def test_okx_feed_disconnect_callback_updates_market_quality():
    market = _market()
    market.handle(
        {"arg": {"channel": "tickers"},
         "data": [{"instId": "BTC-USDT-SWAP", "last": "100", "bidPx": "99.9",
                   "askPx": "100.1", "ts": "1000"}]},
        recv_ms=1_010,
    )
    feed = Feed(
        {"veri": {"ws_public": [], "ws_business": [], "zaman_dilimleri": ["1m"]}},
        market.handle,
        on_disconnect=market.disconnect_channels,
    )
    feed._disconnected("piyasa", 1_020)

    quality = market.stats()["kalite_telemetrisi"]["kanallar"]["tickers"]
    assert quality["durumlar"]["DISCONNECTED"] == 1
    assert quality["durumlar"]["LIVE"] == 0



def test_market_writes_raw_feed_events_to_bounded_journal(tmp_path):
    market = Market(_config(), {}, event_journal_path=tmp_path / "raw.jsonl")
    market.handle(
        {"arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
         "data": [{"instId": "BTC-USDT-SWAP", "last": "100", "bidPx": "99.9",
                   "askPx": "100.1", "ts": "1000"}]},
        recv_ms=1_010,
    )
    result = market.close()
    assert result["complete"] is True
    assert result["records_this_run"] == 1
    raw = next(market.event_journal.journal.records())
    assert raw["source"] == "okx_public"
    assert raw["channel"] == "tickers"
    assert raw["event_ts_ms"] == 1000
