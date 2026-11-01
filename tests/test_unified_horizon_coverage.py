from radar.config import load_config
from radar.unified.factors import FactorEngine
from radar.unified.models import Horizon


NOW = 1_800_000_000_000


def _daily_context():
    return {
        "sym": "BTC", "inst": "BTC-USDT-SWAP", "tf": "1D",
        "open": 100.0, "high": 105.0, "low": 98.0, "close": 103.0,
        "price": 103.0, "volume": 1_000_000.0, "atr": 3.0,
        "sma50": 95.0, "sma50_prev5": 94.0, "sma200": 80.0,
        "rsi": 58.0, "adx": 25.0, "vol_ratio": 1.2,
        "w_trend": "boga", "funding_ann": 12.0, "oi_chg24": 2.0,
        "taker_imb": 0.1, "spread_bp": 2.0,
        "bid": 102.9, "ask": 103.1, "book_age_s": 1.0,
    }


def test_factor_engine_builds_independent_coverage_for_each_horizon():
    context = FactorEngine(load_config()).from_legacy_ctx(
        _daily_context(), now_ms=NOW, live_ts_ms=NOW, ws_healthy=True,
    )

    swing = context.coverage_for(Horizon.SWING)
    intraday = context.coverage_for(Horizon.INTRADAY)
    micro = context.coverage_for(Horizon.MICRO)
    carry = context.coverage_for(Horizon.CARRY)

    assert swing is not None and swing.sufficient_for_paper
    assert intraday is not None and not intraday.sufficient_for_paper
    assert {"close_1H", "atr_1H", "close_4H"} <= set(intraday.required_missing)
    assert micro is not None and not micro.sufficient_for_paper
    assert {"book_depth_usd", "book_imbalance", "trade_imbalance", "seq_ok"} <= set(micro.required_missing)
    assert carry is not None and not carry.sufficient_for_paper
    assert {"spot_price", "funding_30d_avg"} <= set(carry.required_missing)


def test_legacy_context_coverage_fallback_remains_compatible():
    context = FactorEngine(load_config()).from_legacy_ctx(
        _daily_context(), now_ms=NOW, live_ts_ms=NOW, ws_healthy=True,
    )
    assert context.coverage is context.coverage_for(Horizon.SWING)
