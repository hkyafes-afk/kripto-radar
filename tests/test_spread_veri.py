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
    from radar.unified.quality import ChannelRule, MarketDataQualityEngine
    fe = Engine.__new__(Engine)
    fe.BBO_MAX_AGE_S = 15.0
    fe.book = {"X-USDT-SWAP": (100.0, 99.5, 100.5, __import__("time").time() - 2.0)}
    fe.bbo_quality = MarketDataQualityEngine({"ws_ticker": ChannelRule(max_age_ms=15_000)})
    fe.bbo_quality_latest = {}
    event_ms = int(fe.book["X-USDT-SWAP"][3] * 1000)
    receive_ms = int(__import__("time").time() * 1000)
    fe.bbo_quality_latest[("okx", "X-USDT-SWAP")] = fe.bbo_quality.observe(
        symbol="X-USDT-SWAP", channel="ws_ticker", event_ts_ms=event_ms,
        receive_ts_ms=receive_ms, valid=True,
    )
    b = fe._book_ctx("X-USDT-SWAP")
    assert b["bid"] == 99.5 and b["ask"] == 100.5 and 1.5 < b["book_age_s"] < 10
    y = fe._book_ctx("YOK-USDT-SWAP")
    assert y["bid"] is None and y["ask"] is None and y["book_age_s"] is None


INST_T = "X-USDT-SWAP"


def _fe_rest(site, tickers, book=None):
    from types import SimpleNamespace as NS
    import time as _t
    from test_golge_portfoy import fake_engine
    fe = fake_engine(site)
    fe.book = dict(book or {})
    fe.feeds = {}
    # OKX public ticker fixture'ları gerçek borsa zaman damgası taşır.
    for ticker in tickers:
        ticker.setdefault("ts", str(int(_t.time() * 1000)))
    fe.client = NS(tickers=lambda t: tickers)               # YALNIZ public tickers; başka çağrı varsa hata
    fe.bbo_rest = {}
    fe.meta[INST_T] = {**fe.meta.get(INST_T, {}), "ct_val": 1.0, "lot_sz": 0.001, "min_sz": 0.001, "max_lev": 20.0}
    return fe


def test_ws_defteri_bayatsa_rest_anlik_bbo_kullanilir(tmp_path):
    import time as _t
    fe = _fe_rest(tmp_path, [{"instId": "X-USDT-SWAP", "bidPx": "100.9", "askPx": "101.1"}],
                  book={"X-USDT-SWAP": (100.0, 99.0, 101.0, _t.time() - 60)})   # 60 sn bayat WS
    fe._bbo_snapshot({"X-USDT-SWAP"})
    b = fe._book_ctx("X-USDT-SWAP")
    assert b["bbo_kaynak"] == "rest" and b["bid"] == 100.9 and b["ask"] == 101.1 and b["book_age_s"] < 5


def test_bayat_ws_ve_rest_yoksa_spread_gecmez(tmp_path):
    import time as _t
    fe = _fe_rest(tmp_path, [], book={"X-USDT-SWAP": (100.0, 99.0, 101.0, _t.time() - 60)})
    fe._bbo_snapshot({"X-USDT-SWAP"})
    b = fe._book_ctx("X-USDT-SWAP")
    assert b["bid"] is None and b["ask"] is None and b["bbo_kaynak"] == "kalite_yok"


def test_tanilama_dosyasi_yazilir(tmp_path):
    import json as _j
    import time as _t
    from test_unified_strategies import kemal_ctx as _k
    fe = _fe_rest(tmp_path, [{"instId": "X-USDT-SWAP", "bidPx": "100.9", "askPx": "101.1"}])
    fe._unified_shadow([_k()])
    t = _j.loads((tmp_path / "birlesik" / "golge" / "durum.json").read_text(encoding="utf-8"))["tanilama"]
    for k in ("ws_healthy", "tick_count", "last_tick_age", "feed_symbols", "decision_coins", "book_entries",
              "spread_durum_sayim", "ornek"):
        assert k in t, k
    r = t["ornek"][0]
    for k in ("decision_inst", "feed_inst", "book_exists", "ws_bid", "ws_ask", "ws_book_age", "bid", "ask",
              "book_age", "bbo_kaynak", "spread_status"):
        assert k in r, k


def test_sentetik_shadow_open_ve_exit_zinciri(tmp_path):
    """spread_ok (REST anlık BBO) → operational PASS → RiskGate PASS → sanal OPEN → WS fiyatıyla stop → EXIT kaydı."""
    import json as _j
    import time as _t
    from test_golge_portfoy import golge, tick
    from test_unified_strategies import kemal_ctx as _k
    fe = _fe_rest(tmp_path, [{"instId": "X-USDT-SWAP", "bidPx": "100.9", "askPx": "101.1"}])
    fe._unified_shadow([_k()])                               # emir (limit) sanal defterde
    fe._unified_shadow([_k()])                               # REST BBO ile dolum
    d = golge(tmp_path)
    assert len(d["pozisyonlar"]) == 1, d.get("kararlar")
    p = d["pozisyonlar"][0]
    stop = p["stop"]
    fe.bbo_rest = {}
    tick(fe, stop * (1.02 if p["side"] == "short" else 0.98))   # stop'u geçen canlı WS fiyatı
    fe._unified_shadow([_k()])
    d = golge(tmp_path)
    kap = d.get("kapananlar") or []
    assert kap, "EXIT/kapanış kaydı yok"
    k = kap[-1]
    assert k["exit_reason"] and k["exit_px"] and k["closed_ts"] and k["net_usd"] is not None
    assert k["exit_reason"] and k["net_usd"] is not None



def test_yetki_golge_operasyonel_open_uctan_uca(tmp_path, monkeypatch):
    """YETKI_GOLGE (politika engeli YOK, deterministik) → spread_ok (REST anlık BBO) → operational PASS →
    RiskGate PASS → sanal OPEN (operasyonel) → EXIT. Gerçek emir/private çağrı yok (client yalnız tickers)."""
    import json as _j
    from radar.unified import decision as _dec
    from test_golge_portfoy import golge, tick
    from test_unified_strategies import kemal_ctx as _k
    from dataclasses import replace as _rep
    from radar.unified.models import StrategyAuthority as _SA
    monkeypatch.setattr(_dec.UnifiedDecisionEngine, "_policy", lambda self, ctx, o: None)
    orig_gates = _dec.UnifiedDecisionEngine._gates
    def gates_yetki(self, ctx, o, cost, ops):              # test: fırsatlar YETKI_GOLGE (SHADOW_ONLY) sayılır
        return orig_gates(self, ctx, _rep(o, authority=_SA.SHADOW_ONLY),
                          cost, [_rep(x, authority=_SA.SHADOW_ONLY) for x in ops])
    monkeypatch.setattr(_dec.UnifiedDecisionEngine, "_gates", gates_yetki)
    fe = _fe_rest(tmp_path, [{"instId": "X-USDT-SWAP", "bidPx": "100.9", "askPx": "101.1"}])
    for _ in range(3):
        fe._unified_shadow([_k()])
    d = golge(tmp_path)
    ops = [p for p in d["pozisyonlar"] if p.get("operational")]
    assert ops, {"karar": d.get("karar"), "kararlar": d.get("kararlar"), "acilan": d.get("acilan")}
    p = ops[0]
    assert p["karar"] == "YETKI_GOLGE" and p["stop"] and p["qty"] and p["risk_usd"] > 0
    fe.bbo_rest = {}
    tick(fe, p["stop"] * (1.02 if p["side"] == "short" else 0.98))
    fe._unified_shadow([_k()])
    d = golge(tmp_path)
    kap = [c for c in d.get("kapananlar") or [] if c.get("operational")]
    assert kap and kap[-1]["exit_reason"] and kap[-1]["net_usd"] is not None
