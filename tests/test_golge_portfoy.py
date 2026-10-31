"""Gölge sanal portföy (karar_modu: shadow): Baş Trader'ın açma kararı → canlı fiyatla sanal pozisyon → stop/hedef →
karne; yeniden başlatmada korunma; gerçek emir yasağı; panel durum etiketleri. Bağlamlar ve fiyatlar SENTETİKTİR."""
from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from radar import karne  # noqa: E402
from radar.config import load_config  # noqa: E402
from radar.engine import Engine  # noqa: E402
from radar.unified.quality import ChannelRule, MarketDataQualityEngine  # noqa: E402
from test_unified_strategies import kemal_ctx  # noqa: E402

CFG = load_config()
INST = "X-USDT-SWAP"


def fake_engine(site, cfg=CFG):
    fe = Engine.__new__(Engine)                          # ağ/defter kurmadan
    fe.cfg, fe.site, fe.halt = copy.deepcopy(cfg), site, None
    fe.cfg.setdefault("birlesik", {})["karar_modu"] = "shadow"
    fe.meta = {INST: {"list_ts": (time.time() - 2000 * 86400) * 1000}}
    fe.book, fe.ledger, fe.golge_fund_events = {}, {"closed": [], "open": []}, []
    fe.BBO_MAX_AGE_S = 15.0
    fe.bbo_quality = MarketDataQualityEngine({"ws_ticker": ChannelRule(max_age_ms=15_000)})
    fe.bbo_quality_latest = {}
    return fe


def tick(fe, px):
    now = time.time()
    receive_ms = int(now * 1000)
    fe.book[INST] = (px, px * 0.9997, px * 1.0003, now)
    fe.bbo_quality_latest[("okx", INST)] = fe.bbo_quality.observe(
        symbol=INST, channel="ws_ticker", event_ts_ms=receive_ms,
        receive_ts_ms=receive_ms, valid=True,
    )


def golge(site):
    return json.loads((site / "birlesik" / "golge" / "durum.json").read_text(encoding="utf-8"))


def test_config_golge_modunda():
    cfg = copy.deepcopy(CFG)
    cfg["birlesik"]["karar_modu"] = "shadow"
    assert cfg["birlesik"]["karar_modu"] == "shadow"


def test_sentetik_golge_ac_guncelle_stop_karne(tmp_path):
    fe = fake_engine(tmp_path)
    tick(fe, 101.0)
    fe._unified_shadow([kemal_ctx()])
    d = golge(tmp_path)
    assert d["mod"] == "shadow" and len(d["pozisyonlar"]) == 1
    p = d["pozisyonlar"][0]
    for k in ("symbol", "side", "strategies", "entry", "current_price", "stop", "targets", "qty", "leverage", "risk_usd",
              "opened_ts", "updated_ts", "costs", "unrealized_usd", "unrealized_pct", "unrealized_r", "peak_r", "exit_plan"):
        assert k in p and p[k] is not None or k in ("targets",), k
    assert p["strategies"] == ["swing_trend"]
    # legacy / unified paper defterlerine dokunulmadı
    assert not (tmp_path / "ledger.json").exists() and not (tmp_path / "birlesik" / "yurutme.json").exists()
    # aynı fırsat tekrar açılmaz
    tick(fe, 103.0)
    fe._unified_shadow([kemal_ctx()])
    d = golge(tmp_path)
    assert len(d["pozisyonlar"]) == 1 and d["pozisyonlar"][0]["current_price"] == 103.0
    assert d["pozisyonlar"][0]["unrealized_usd"] > 0 and d["pozisyonlar"][0]["peak_r"] > 0
    # stop → sanal kapanış, maliyetler düşülmüş net R
    stop = d["pozisyonlar"][0]["stop"]
    tick(fe, stop * 0.995)
    fe._unified_shadow([])
    d = golge(tmp_path)
    assert d["pozisyonlar"] == [] and len(d["kapananlar"]) == 1
    c = d["kapananlar"][0]
    assert c["r"] < 0 and c["fees_usd"] > 0 and c["slip_usd"] > 0 and c["exit_reason"]
    k = karne.build([], [], golge_closed=fe._golge_trader.adapter.closed)
    assert k["yeni_golge"]["n"] == 1 and k["yeni_golge"]["pnl_usd"] < 0 and k["bas_trader"]["n"] == 0


def test_sentetik_hedefte_kapanir(tmp_path):
    fe = fake_engine(tmp_path)
    tick(fe, 101.0)
    fe._unified_shadow([kemal_ctx()])
    t = golge(tmp_path)["pozisyonlar"][0]["targets"]
    tick(fe, t[-1] * 1.01)
    fe._unified_shadow([])
    c = golge(tmp_path)["kapananlar"]
    assert c and c[0]["r"] > 0


def test_sentetik_yeniden_baslatmada_korunur(tmp_path):
    fe = fake_engine(tmp_path)
    tick(fe, 101.0)
    fe._unified_shadow([kemal_ctx()])
    fe2 = fake_engine(tmp_path)                          # workflow yeniden başladı
    tick(fe2, 102.0)
    fe2._unified_shadow([kemal_ctx()])
    d = golge(tmp_path)
    assert len(d["pozisyonlar"]) == 1 and d["pozisyonlar"][0]["current_price"] == 102.0


def test_sentetik_gercek_emir_cagrisi_yok(tmp_path, monkeypatch):
    from radar import okx
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("gerçek borsa çağrısı yapılmamalı")
    for name in dir(okx.OKXClient):
        if not name.startswith("__") and callable(getattr(okx.OKXClient, name)):
            monkeypatch.setattr(okx.OKXClient, name, boom)
    monkeypatch.setattr(okx.OKXClient, "__init__", boom)
    fe = fake_engine(tmp_path)
    tick(fe, 101.0)
    fe._unified_shadow([kemal_ctx()])
    tick(fe, 80.0)
    fe._unified_shadow([])
    assert not calls and golge(tmp_path)["kapananlar"]
    src = "\n".join(f.read_text(encoding="utf-8") for f in (Path(__file__).resolve().parent.parent / "radar" / "unified").rglob("*.py"))
    assert "OKXClient" not in src and "/api/v5/trade" not in src


# ── panel DOM / mobil (Playwright) ─────────────────────────────────────

PAGE = Path(__file__).resolve().parent.parent / "radar" / "templates" / "birlesik.html"


def _browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright yok")
    return sync_playwright


def _open(routes, width=390):
    sp = _browser()
    with sp() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": width, "height": 900})
        html = PAGE.read_text(encoding="utf-8")

        def handle(route):
            url = route.request.url.split("?")[0]
            if url.endswith("/birlesik.html"):
                return route.fulfill(status=200, body=html, content_type="text/html")
            for key, (status, body) in routes.items():
                if url.endswith(key):
                    if status == 0:
                        return route.abort()
                    return route.fulfill(status=status, body=body, content_type="application/json")
            return route.fulfill(status=404, body="")
        page.route("**/*", handle)
        page.goto("http://panel.test/birlesik.html")
        page.wait_for_timeout(600)
        out = {"text": page.inner_text("body"), "sw": page.evaluate("document.documentElement.scrollWidth")}
        b.close()
    return out


def test_panel_veri_yok():
    o = _open({})
    assert "Veri yok" in o["text"] and o["sw"] <= 390


def test_panel_bayat_ve_baglanti_kesik():
    old = json.dumps({"generated_at": int(time.time() * 1000) - 3 * 3600_000, "feeds": {"okx": {"healthy": False}}})
    o = _open({"/data.json": (200, old)})
    assert "Bayat" in o["text"] and "Bağlantı kesik" in o["text"]
    o = _open({"/data.json": (0, "")})
    assert "Bağlantı kesik" in o["text"]


def test_sentetik_panel_kart_icerigi_ve_tasma():
    now = int(time.time() * 1000)
    durum = {"ts": now, "mod": "unified", "bakiye": 99.95, "baslangic": 100.0, "gun_basi": 100.01, "hatalar": [],
             "pozisyonlar": [{"symbol": "XYZ", "side": "short", "leverage": 3, "entry": 1.2345, "current_price": 1.2001,
                              "stop": 1.3, "targets": [1.1], "opened_ts": now - 7200_000, "unrealized_usd": 12.5,
                              "unrealized_pct": 2.8, "unrealized_r": 0.4, "peak_r": 0.6, "strategies": ["short_trend"],
                              "hist": [[now - 600_000, 1.23], [now - 300_000, 1.21], [now, 1.2]]}],
             "kapananlar": [{"symbol": "ABC", "side": "long", "leverage": 1, "entry": 10, "exit_px": 9.5, "net_usd": -5.2,
                             "r": -1.04, "fees_usd": 0.1, "slip_usd": 0.05, "funding_usd": -0.01, "exit_reason": "STOP: stop",
                             "opened_ts": now - 9e6, "closed_ts": now - 1e6, "strategies": ["swing_trend"]}],
             "eq_hist": [[now - 600_000, 100.0], [now - 300_000, 99.98], [now, 99.95]], "btc_hist": []}
    data = {"generated_at": now, "feeds": {"okx": {"healthy": True, "last_msg_age_s": 0.2}}, "btc": {"price": 77000.5, "chg24": -1.2},
            "karne": karne.build([], [])}
    o = _open({"/data.json": (200, json.dumps(data)), "/birlesik/durum.json": (200, json.dumps(durum))})
    t = o["text"]
    assert "XYZ" in t and "SHORT · 3x" in t and "Short trend" in t and "+$12.50" in t and "+0.40R" in t and "tepe" in t
    assert "Kemal" not in t and "Selin" not in t and "Kaan" not in t
    assert o["sw"] <= 390
    assert _open({"/data.json": (200, json.dumps(data)), "/golge/durum.json": (200, json.dumps(durum))}, width=1280)["sw"] <= 1280
