"""Spread altyapısı: motorun public BBO'su karar bağlamına gerçek veri olarak taşınır; eksik/bayat/geçersiz →
operasyonel açılış yok. Gerçek emir/private endpoint yok. Veriler SENTETİKTİR."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from radar.config import load_config  # noqa: E402
from radar.engine import Engine  # noqa: E402
from radar.unified.factors import FactorEngine  # noqa: E402
from radar.unified.liquidity import spread_status  # noqa: E402
from test_unified_strategies import kemal_ctx  # noqa: E402

NOW = 1_793_373_000_000


def test_spread_durumu_ayrimlari():
    st, bp = spread_status(99.0, 101.0, 1.0)
    assert st == "spread_ok" and abs(bp - 200.0) < 1e-6              # (101−99)/100·1e4 = 200 bp
    assert spread_status(None, 101.0, 1.0)[0] == "spread_missing"
    assert spread_status(99.0, 101.0, 60.0)[0] == "spread_stale"
    assert spread_status(None, None, 1.0)[0] == "spread_missing"
    assert spread_status(101.0, 99.0, 1.0)[0] == "spread_invalid"     # ask < bid
    assert spread_status(0.0, 101.0, 1.0)[0] == "spread_invalid"
    assert spread_status(float("nan"), 101.0, 1.0)[0] == "spread_invalid"


def _ctx(**kw):
    c = dict(kemal_ctx())
    c.update(kw)
    return c


def test_gercek_bid_ask_karar_baglamina_tasinir():
    fe = FactorEngine(load_config())
    m = fe.from_legacy_ctx(_ctx(bid=99.0, ask=101.0, book_age_s=1.0), now_ms=NOW)
    assert m.liquidity.spread_durum == "spread_ok"
    assert m.liquidity.spread_bp is not None and abs(m.liquidity.spread_bp - 200.0) < 1e-6


def test_eksik_ve_bayat_bid_ask_gerçek_spread_olmaz():
    fe = FactorEngine(load_config())
    m = fe.from_legacy_ctx(_ctx(), now_ms=NOW)                        # bid/ask yok
    assert m.liquidity.spread_durum == "spread_missing" and m.liquidity.spread_bp is None
    m = fe.from_legacy_ctx(_ctx(bid=99.0, ask=101.0, book_age_s=120.0), now_ms=NOW)
    assert m.liquidity.spread_durum == "spread_stale" and m.liquidity.spread_bp is None


def test_motor_defterinden_bbo_okunur():
    fe = Engine.__new__(Engine)
    fe.book = {"X-USDT-SWAP": (100.0, 99.5, 100.5, __import__("time").time() - 2.0)}
    b = fe._book_ctx("X-USDT-SWAP")
    assert b["bid"] == 99.5 and b["ask"] == 100.5 and 1.5 < b["book_age_s"] < 10
    assert fe._book_ctx("YOK-USDT-SWAP") == {"bid": None, "ask": None, "book_age_s": None}
