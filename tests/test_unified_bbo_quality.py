import time

from radar.engine import Engine
from radar.unified.quality import ChannelRule, MarketDataQualityEngine, QualityState


def _engine():
    engine = Engine.__new__(Engine)
    engine.BBO_MAX_AGE_S = 15.0
    engine.bbo_quality = MarketDataQualityEngine({
        "ws_ticker": ChannelRule(max_age_ms=15_000),
        "bitget_ticker": ChannelRule(max_age_ms=15_000),
        "rest_ticker": ChannelRule(max_age_ms=15_000),
    })
    engine.bbo_quality_latest = {}
    engine.book = {}
    engine.bg_book = {}
    engine.prices = {}
    engine.watch = {}
    engine.hw = {}
    engine.order_watch = {}
    engine.order_hits = {}
    engine._check_watch = lambda *args: None
    return engine


def test_unified_bbo_rejects_missing_exchange_timestamp():
    engine = _engine()
    inst = "BTC-USDT-SWAP"
    engine._on_tick("okx", inst, 100.0, 99.9, 100.1, None)

    observation = engine.bbo_quality_latest[("okx", inst)]
    book, source = engine._trusted_bbo(inst)

    assert observation.state is QualityState.UNKNOWN
    assert book is None
    assert source == "kalite_yok"


def test_unified_bbo_accepts_fresh_exchange_timestamp_and_valid_spread():
    engine = _engine()
    inst = "BTC-USDT-SWAP"
    now_ms = int(time.time() * 1000)
    engine._on_tick("okx", inst, 100.0, 99.9, 100.1, now_ms)

    book, source = engine._trusted_bbo(inst)
    assert source == "ws"
    assert book[1:3] == (99.9, 100.1)
    assert engine.bbo_quality_latest[("okx", inst)].state is QualityState.LIVE


def test_unified_bbo_rejects_stale_crossed_and_future_quotes():
    engine = _engine()
    inst = "BTC-USDT-SWAP"
    now_ms = int(time.time() * 1000)

    engine._on_tick("okx", inst, 100.0, 99.9, 100.1, now_ms - 16_000)
    assert engine.bbo_quality_latest[("okx", inst)].state is QualityState.STALE
    assert engine._trusted_bbo(inst)[0] is None

    engine._on_tick("okx", inst, 100.0, 101.0, 100.0, now_ms)
    assert engine.bbo_quality_latest[("okx", inst)].state is QualityState.INVALID
    assert engine._trusted_bbo(inst)[0] is None

    engine._on_tick("okx", inst, 100.0, 99.9, 100.1, now_ms + 10_000)
    assert engine.bbo_quality_latest[("okx", inst)].state is QualityState.INVALID
    assert engine._trusted_bbo(inst)[0] is None


def test_unified_bbo_does_not_use_unvalidated_rest_snapshot():
    engine = _engine()
    inst = "BTC-USDT-SWAP"
    engine.bbo_rest = {inst: (99.9, 100.1, time.time())}

    assert engine._trusted_bbo(inst) == (None, "kalite_yok")
