from polat.market import Market
from polat.okx import Feed


def _market(event_journal_path=None):
    return Market(
        {"veri": {"zaman_dilimleri": ["1m"]}, "kurulum": {"duvar_kat": 3}},
        {"BTC-USDT-SWAP": {"ctVal": 1.0}},
        event_journal_path=event_journal_path,
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
    assert "Her kanal timestamp/sequence ve kanal tazelik eşiğiyle izlenir" in quality["anlam"]
    assert stats["gecikme_ornek_sayisi"] == 1
    assert stats["gecikme_ms_p99"] == 125
    assert stats["gecikme_ms_max"] == 125


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
    market = _market(event_journal_path=tmp_path / "raw.jsonl")
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



def test_ticker_with_missing_or_stale_exchange_timestamp_never_updates_price_state():
    import time

    market = _market()
    now = int(time.time() * 1000)
    market.handle(
        {"arg": {"channel": "tickers"},
         "data": [{"instId": "BTC-USDT-SWAP", "last": "100", "bidPx": "99.9",
                   "askPx": "100.1"}]},
        recv_ms=now,
    )
    assert "BTC-USDT-SWAP" not in market.coins

    market.handle(
        {"arg": {"channel": "tickers"},
         "data": [{"instId": "BTC-USDT-SWAP", "last": "100", "bidPx": "99.9",
                   "askPx": "100.1", "ts": str(now - 20_000)}]},
        recv_ms=now,
    )
    assert "BTC-USDT-SWAP" not in market.coins
    quality = market.stats()["kalite_telemetrisi"]["kanallar"]["tickers"]
    assert quality["durumlar"]["STALE"] == 1


def test_market_tracks_candle_quality_and_candle_connection_disconnect():
    import time

    market = _market()
    coin = market.coin("BTC-USDT-SWAP")
    now = int(time.time() * 1000)
    candle_ts = now - 30_000
    market._candle(
        "BTC-USDT-SWAP", "candle1m",
        [[str(candle_ts), "100", "102", "99", "101", "10", "1000", "1", "1"]],
    )
    channel = market.stats()["kalite_telemetrisi"]["kanallar"]["candle_1m"]
    assert channel["durumlar"]["LIVE"] == 1
    market.disconnect_channels("mum", now + 1)
    channel = market.stats()["kalite_telemetrisi"]["kanallar"]["candle_1m"]
    assert channel["durumlar"]["DISCONNECTED"] == 1


def test_market_rejects_invalid_mark_price_and_funding_values():
    import time

    market = _market()
    now = int(time.time() * 1000)
    market.handle(
        {"arg": {"channel": "mark-price"},
         "data": [{"instId": "BTC-USDT-SWAP", "markPx": "-1", "ts": str(now)}]},
        recv_ms=now,
    )
    market.handle(
        {"arg": {"channel": "funding-rate"},
         "data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "5", "ts": str(now)}]},
        recv_ms=now,
    )
    coin = market.coins["BTC-USDT-SWAP"]
    assert coin.mark is None
    assert coin.funding is None
    channels = market.stats()["kalite_telemetrisi"]["kanallar"]
    assert channels["mark-price"]["durumlar"]["INVALID"] == 1
    assert channels["funding-rate"]["durumlar"]["INVALID"] == 1



def test_malformed_orderbook_levels_fail_closed_without_crashing():
    market = _market()
    market.handle(
        {"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"}, "action": "snapshot",
         "data": [{"ts": "1000", "seqId": 10,
                   "bids": [["NaN", "2"]], "asks": [["101", "2"]]}]},
        recv_ms=1_010,
    )
    coin = market.coins["BTC-USDT-SWAP"]
    assert coin.book.ok is False
    quality = market.stats()["kalite_telemetrisi"]["kanallar"]["books"]
    assert quality["durumlar"]["INVALID"] == 1



def test_market_counts_orderbook_sequence_loss_duplicates_and_disconnects():
    market = _market()
    market.handle(
        {"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"}, "action": "snapshot",
         "data": [{"ts": "1000", "seqId": 100,
                   "bids": [["99", "2"]], "asks": [["101", "2"]]}]},
        recv_ms=1_010,
    )
    market.handle(
        {"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"}, "action": "update",
         "data": [{"ts": "1010", "seqId": 105, "prevSeqId": 104,
                   "bids": [["99", "1"]], "asks": []}]},
        recv_ms=1_020,
    )
    counters = market.stats()["kalite_telemetrisi"]["kanal_sayaclari"]["books"]
    assert counters["sequence_gap"] == 1
    assert counters["dropped_sequence_updates"] == 4

    market.handle(
        {"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"}, "action": "snapshot",
         "data": [{"ts": "1020", "seqId": 200,
                   "bids": [["99", "2"]], "asks": [["101", "2"]]}]},
        recv_ms=1_030,
    )
    market.handle(
        {"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"}, "action": "update",
         "data": [{"ts": "1030", "seqId": 200, "prevSeqId": 200,
                   "bids": [["99", "1"]], "asks": []}]},
        recv_ms=1_040,
    )
    counters = market.stats()["kalite_telemetrisi"]["kanal_sayaclari"]["books"]
    assert counters["duplicate"] == 1
    assert counters["live"] == 2
    assert counters["invalid"] == 2

    market.disconnect_channels("defter", 1_050)
    counters = market.stats()["kalite_telemetrisi"]["kanal_sayaclari"]["books"]
    assert counters["disconnected"] == 1


def test_market_counts_stale_and_out_of_order_channel_events():
    market = _market()
    market.handle(
        {"arg": {"channel": "tickers"},
         "data": [{"instId": "BTC-USDT-SWAP", "last": "100", "bidPx": "99.9",
                   "askPx": "100.1", "ts": "1000"}]},
        recv_ms=1_010,
    )
    market.handle(
        {"arg": {"channel": "tickers"},
         "data": [{"instId": "BTC-USDT-SWAP", "last": "100", "bidPx": "99.9",
                   "askPx": "100.1", "ts": "900"}]},
        recv_ms=1_020,
    )
    counters = market.stats()["kalite_telemetrisi"]["kanal_sayaclari"]["tickers"]
    assert counters["live"] == 1
    assert counters["out_of_order"] == 1
    assert counters["invalid"] == 1


def test_feed_exports_connection_telemetry_container():
    feed = Feed(
        {"veri": {"ws_public": [], "ws_business": [], "zaman_dilimleri": ["1m"]}},
        lambda _msg, _ts: None,
    )
    assert feed.stats["baglanti_telemetrisi"] == {}
    assert set(feed.conns) == {"piyasa", "defter", "tape", "mum", "mum_genel"}
