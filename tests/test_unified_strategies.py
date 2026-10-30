"""Birleşik stratejiler (radar/unified/strategies). Bağlamlar SENTETİKTİR (elle yazılmış sayılar, gerçek piyasa değil).
Legacy eşleşme testi: sarmalayıcı, aynı bağlamda radar/team.ideas_for ile birebir aynı giriş/stop/hedefi üretmeli."""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import team  # noqa: E402
from radar.config import load_config  # noqa: E402
from radar.unified import coverage as cov  # noqa: E402
from radar.unified import edge  # noqa: E402
from radar.unified.factors import FactorEngine  # noqa: E402
from radar.unified.models import (CandleContext, Horizon, MarketContext, MarketStructure, Opportunity, Regime,  # noqa: E402
                                  RegimeContext, Side, StrategyAuthority, Trend)
from radar.unified.strategies import (REGISTRY, IntradayTrendStrategy, MicrostructureStrategy,  # noqa: E402
                                      PanicReversalStrategy, ShortTrendStrategy, SwingTrendStrategy, build_strategies)

CFG = load_config()
NOW = 1_800_000_000_000
DAY = 86_400_000


def kemal_ctx(**kw):
    """Sentetik günlük bağlam: Kemal'in long trend başlangıcı koşullarını sağlar."""
    c = {"inst": "X-USDT-SWAP", "sym": "X", "tf": "1D", "bar_ms": DAY, "price": 101.0, "open": 98.0, "high": 102.0,
         "low": 97.0, "close": 101.0, "volume": 5000.0, "atr": 3.0, "sma20": 97.0, "sma50": 95.0, "sma50_prev5": 94.0,
         "sma100": 90.0, "sma200": 80.0, "above50_prev": False, "below50_prev": False, "hc20": 100.0, "lc20": 85.0,
         "hh20": 100.0, "ll20": 88.0, "rsi": 60.0, "adx": 25.0, "vol_ratio": 1.4, "close_pos": 0.8,
         "btc_bull": True, "btc_bear": False, "w_trend": "boga", "high_vol": False, "funding_rate": 0.0001,
         "interval_h": 8, "funding_ann": 10.95, "oi_chg24": 3.0, "taker_imb": 0.2, "vol24_usd": 50e6, "regime": 1,
         "live_bar": True}
    c.update(kw)
    return c


def short_ctx(**kw):
    """Sentetik ayı rejimi + aşağı yapısal kırılım bağlamı."""
    c = kemal_ctx(close=80.0, price=80.0, open=84.0, high=85.0, low=79.0, sma20=86.0, sma50=88.0, sma50_prev5=90.0,
                  sma100=95.0, ll20=82.0, hh20=96.0, atr=3.0, w_trend="ayi", btc_bull=False, btc_bear=True,
                  funding_ann=-5.0, oi_chg24=2.0, vol24_usd=60e6, above50_prev=False, below50_prev=True)
    c.update(kw)
    return c


def mctx(raw):
    return FactorEngine(CFG).from_legacy_ctx(raw, now_ms=NOW, live_ts_ms=NOW)


def test_sentetik_kayit_ve_varsayilan_yetkiler():
    names = {cls.name: cls.default_authority for cls in REGISTRY}
    assert names["swing_trend"] is StrategyAuthority.PAPER
    assert names["panic_reversal"] is StrategyAuthority.SHADOW_ONLY
    assert names["intraday_trend"] is StrategyAuthority.SHADOW_ONLY
    assert names["microstructure"] is StrategyAuthority.SHADOW_ONLY
    assert names["legacy_murat"] is StrategyAuthority.SHADOW_ONLY
    assert "turbo" not in " ".join(names) and "bas" not in names        # bağımsız açma yetkileri yok


def test_sentetik_yetki_config_ile_yalniz_daralir():
    cfg = copy.deepcopy(CFG)
    cfg["birlesik"] = {"stratejiler": {"swing_trend": {"yetki": "shadow_only"}, "panic_reversal": {"yetki": "paper"},
                                       "short_trend": {"yetki": "disabled"}, "compression_breakout": {"yetki": "xyz"},
                                       "intraday_trend": {"yetki": "paper"}}}
    by = {s.name: s.authority for s in build_strategies(cfg)}
    assert by["swing_trend"] is StrategyAuthority.SHADOW_ONLY
    assert by["panic_reversal"] is StrategyAuthority.SHADOW_ONLY       # genişletilemez
    assert "short_trend" not in by                                      # disabled → kurulmaz
    assert by["compression_breakout"] is StrategyAuthority.SHADOW_ONLY  # tanınmayan değer → güvenli taraf
    assert by["intraday_trend"] is StrategyAuthority.SHADOW_ONLY


def test_stratejiler_yurutme_modulu_import_etmez():
    root = Path(__file__).resolve().parent.parent / "radar" / "unified" / "strategies"
    forbidden = re.compile(r"^\s*(from|import)\s+[.\w]*\b(ledger|desk|okx|engine|notify|execution|store|lease|ws)\b",
                           re.M)
    for f in root.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert not forbidden.search(src), f"{f.name} yürütme/defter modülü import ediyor"
        assert "import team" in src or "team" not in re.findall(r"^\s*(?:from|import)\s+(\S+)", src, re.M)


def test_sentetik_swing_trend_legacy_ile_birebir():
    raw = kemal_ctx()
    legacy = team.ideas_for(dict(raw), [team.BY_ID["kemal"]], CFG)
    assert len(legacy) == 1 and legacy[0]["side"] == "long"
    opps = SwingTrendStrategy(CFG).evaluate(mctx(raw))
    assert len(opps) == 1
    o, i = opps[0], legacy[0]
    assert o.side is Side.LONG and o.entry == i["entry"] and o.stop == i["sl"]
    assert o.targets[-1] == i["tp"] and o.strategy == "swing_trend" and o.source == "legacy"
    assert o.authority is StrategyAuthority.PAPER
    assert abs(o.gross_edge_bp - edge.prior_r("swing_trend") * o.stop_bp) < 1e-9
    assert o.bar_ts_ms == (NOW // DAY) * DAY                            # canlı günün açılışı


def test_sentetik_swing_trend_baglam_yoksa_bos():
    m = mctx(kemal_ctx())
    empty = MarketContext(symbol="X", inst="X", ts_ms=NOW, timeframe="1D")
    assert SwingTrendStrategy(CFG).evaluate(empty) == []
    assert SwingTrendStrategy(CFG).evaluate(m.__class__(**{**{f: getattr(m, f) for f in m.__dataclass_fields__},
                                                           "timeframe": "1H"})) == []


def test_sentetik_short_trend_kosullar():
    s = ShortTrendStrategy(CFG)
    opps = s.evaluate(mctx(short_ctx()))
    assert len(opps) == 1
    o = opps[0]
    assert o.side is Side.SHORT and o.stop > o.entry and o.targets[0] < o.entry
    assert o.authority is StrategyAuthority.SHADOW_ONLY and o.gross_edge_bp is None
    assert s.evaluate(mctx(short_ctx(w_trend="boga"))) == []                 # boğada short yok
    assert s.evaluate(mctx(short_ctx(funding_ann=-35.0))) == []              # short kalabalık
    assert s.evaluate(mctx(short_ctx(oi_chg24=15.0, funding_ann=-15.0))) == []   # squeeze riski
    assert s.evaluate(mctx(short_ctx(close=70.0, price=70.0, low=69.0))) == []  # 50g ortalamadan 6 ATR uzak: kaçmış
    assert s.evaluate(mctx(short_ctx(vol24_usd=5e6))) == []                  # likidite yetersiz
    assert s.evaluate(mctx(short_ctx(close=83.0, price=83.0))) == []         # 20 günlük dip kırılmadı
    kriz = s.evaluate(mctx(short_ctx(high_vol=True)))
    assert kriz and any("squeeze" in a for a in kriz[0].evidence_against)


def _intraday_ctx(trend=Trend.UP, complete=True, vr=0.8, regime=Regime.BULL):
    c1 = CandleContext("1H", NOW - 3_600_000, open=100.1, high=100.6, low=99.9, close=100.5, atr=1.0, complete=complete)
    f = {"vol_ratio_1H": cov.make_factor("vol_ratio_1H", vr, source="sentetik", ts_ms=NOW, now_ms=NOW)}
    return MarketContext(symbol="Y", inst="Y-USDT-SWAP", ts_ms=NOW, timeframe="1H", candles={"1H": c1},
                         structure={"4H": MarketStructure("4H", trend=trend), "1H": MarketStructure("1H", ema_fast=100.0)},
                         regime=RegimeContext(regime=regime), factors=f)


def test_sentetik_gun_ici_trend_golgede():
    s = IntradayTrendStrategy(CFG)
    opps = s.evaluate(_intraday_ctx())
    assert len(opps) == 1 and opps[0].side is Side.LONG and opps[0].horizon is Horizon.INTRADAY
    assert opps[0].authority is StrategyAuthority.SHADOW_ONLY and opps[0].htf_direction is Trend.UP
    assert s.evaluate(_intraday_ctx(complete=False)) == []                   # tamamlanmamış mumda sinyal yok
    assert s.evaluate(_intraday_ctx(vr=1.3)) == []                           # hacim daralmadı
    assert s.evaluate(_intraday_ctx(trend=Trend.RANGE)) == []
    assert s.evaluate(MarketContext(symbol="Y", inst="Y", ts_ms=NOW, timeframe="1H")) == []   # veri yok


def _micro_ctx(**over):
    vals = {"price": 100.0, "spread_bp": 1.0, "book_depth_usd": 2e5, "book_imbalance": 0.5, "trade_imbalance": 0.4,
            "seq_ok": True}
    vals.update(over)
    f = {k: cov.make_factor(k, v, source="sentetik", ts_ms=NOW, now_ms=NOW) for k, v in vals.items() if v is not None}
    return MarketContext(symbol="Z", inst="Z-USDT-SWAP", ts_ms=NOW, timeframe="1m", factors=f)


def test_sentetik_mikro_yapi_veri_ister():
    s = MicrostructureStrategy(CFG)
    o = s.evaluate(_micro_ctx())
    assert len(o) == 1 and o[0].side is Side.LONG and o[0].authority is StrategyAuthority.SHADOW_ONLY
    assert s.evaluate(_micro_ctx(seq_ok=False)) == []
    assert s.evaluate(_micro_ctx(book_depth_usd=None)) == []                # zorunlu veri yok → fırsat yok
    assert s.evaluate(_micro_ctx(spread_bp=8.0)) == []
    assert s.evaluate(_micro_ctx(book_imbalance=-0.5, trade_imbalance=-0.4))[0].side is Side.SHORT


def test_sentetik_panik_donusu_karsi_kanitlar():
    s = PanicReversalStrategy(CFG)
    m = FactorEngine(CFG).from_legacy_ctx(kemal_ctx(w_trend="ayi", high_vol=True), now_ms=NOW, live_ts_ms=NOW)
    o = Opportunity(symbol="X", inst="X", side="long", strategy="panic_reversal", setup="ayse", timeframe="1D",
                    horizon=Horizon.SWING, created_ts_ms=NOW, entry=100.0, stop=95.0)
    against = s.extra_against(m, o)
    assert any("kriz" in a for a in against) and any("spread" in a for a in against)
    assert s.authority is StrategyAuthority.SHADOW_ONLY


def test_sentetik_avantaj_onseli():
    assert abs(edge.prior_r("swing_trend") - 0.292 * 252 / 302) < 1e-12
    assert edge.prior_r("controlled_breakout") == edge.CAP_R                 # 1,14R → üst sınır 0,5R
    assert edge.prior_r("short_trend") is None and edge.gross_edge_bp("short_trend", 500) is None
    assert edge.gross_edge_bp("swing_trend", 0) is None
