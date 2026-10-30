"""İşleme hazır olma göstergesi, engel özeti, olasılık eşiği ve canlı fiyat (OKX public WebSocket) testleri.
Hiçbir kapı gevşetilmez; testler yalnız sunum/izleme katmanını doğrular. Veriler SENTETİKTİR."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.unified import hazirlik as hz  # noqa: E402
from radar.unified.trader import UnifiedTrader  # noqa: E402

PAGE = Path(__file__).resolve().parent.parent / "radar" / "templates" / "birlesik.html"
NOW = int(time.time() * 1000)
TODAY = datetime.fromtimestamp(NOW / 1000, timezone.utc).strftime("%Y-%m-%d")


def _rec(codes, decision="SHADOW", **kw):
    base = {"decision": decision, "strategy": "swing_trend", "side": "long", "reason_codes": codes,
            "net_edge_bp": 4.0, "costs": {"total_cost_bp": 9.0, "notional_usd": 500.0},
            "plan": {"coin_class": "A", "rejim": "bull"}, "kayit_ms": NOW}
    base.update(kw)
    return base


# ── 1) kapı durumları (yalnız okuma; kapı mantığı değişmez) ──────────────────────────────
def test_yetki_golge_hard_block_ve_olculen_kapilar():
    h = hz.evaluate(_rec(["YETKI_GOLGE"]))
    st = {k["ad"]: k["durum"] for k in h["kapilar"]}
    assert st["yetki"] == "GÖLGE"
    assert st["politika"] == "VERİ YOK" and st["sinif"] == "VERİ YOK"      # önceki kapıda engellendi → ölçülmedi
    assert h["engel_metni"] == "İşlem açılamaz: YETKI_GOLGE"
    assert "Canlı kanıt/yetki yetersiz" in h["nedenler"]
    assert h["olculen"] == h["gecen"] + sum(1 for k in h["kapilar"] if k["durum"] == "GÖLGE")
    assert h["skor"] == round(h["gecen"] / h["olculen"] * 100)


def test_politika_atr_etiketi_ve_nedeni():
    code = "POLITIKA:oynaklık kapısı: günlük ATR fiyatın %8.8'i (> %5); 5 yıllık testte bu kovada kazanç yok"
    h = hz.evaluate(_rec([code]))
    assert h["engel"] == "POLITIKA:ATR"
    assert h["nedenler"][0] == "Günlük oynaklık %5 sınırını aşıyor"
    assert h["engel_metni"] == "İşlem açılamaz: POLITIKA:ATR"


def test_sinif_c_ve_maliyet_nedeni_sayisal():
    h = hz.evaluate(_rec(["MALIYET_SONRASI_AVANTAJ_YOK"], net_edge_bp=-3.2))
    assert h["engel_aile"] == "MALIYET"
    assert any("net avantaj -3.2 bp" in n for n in h["nedenler"])
    h2 = hz.evaluate(_rec(["SINIF_C"]))
    assert h2["nedenler"][0] == "Coin 1 yıldan genç"


def test_veri_sorunu_sonrasi_kapilar_olculmez():
    h = hz.evaluate(_rec(["VERI_SORUNU:bayat"]))
    st = {k["ad"]: k["durum"] for k in h["kapilar"]}
    assert st["veri_kalite"] == "GÖLGE"
    assert st["veri_kapsam"] == st["yetki"] == st["politika"] == "VERİ YOK"
    assert h["engel_aile"] == "VERI"


def test_red_karari_engel_metni_ve_celiski():
    h = hz.evaluate(_rec(["CELISKI_KAYBETTI"], decision="REJECT"))
    st = {k["ad"]: k["durum"] for k in h["kapilar"]}
    assert st["rejim"] == "RED"
    assert h["engel_metni"].startswith("İşlem açılamaz (red):")


def test_acik_karar_engel_yok_ve_riskgate_gecti():
    h = hz.evaluate(_rec([], decision="OPEN"))
    assert h["engel"] is None and h["engel_metni"] is None
    st = {k["ad"]: k["durum"] for k in h["kapilar"]}
    assert st["riskgate"] == "GEÇTİ" and h["skor"] == 100


def test_hazirlik_yuzdesi_yalniz_olculen_kapilardan():
    h = hz.evaluate(_rec(["YETKI_GOLGE"]))
    assert h["olculen"] < len(hz.GATES)                    # VERİ YOK kapılar paydaya girmez
    assert h["skor"] == round(h["gecen"] / h["olculen"] * 100)


# ── 2) özet ve olasılık eşiği ───────────────────────────────────────────────────────────
def _write_journal(root: Path, rows):
    root.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    (root / f"kararlar-{TODAY}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _row(key, codes, decision="SHADOW", strategy="swing_trend", side="long", rejim="bull", t=None):
    r = _rec(codes, decision=decision, strategy=strategy, side=side, idempotency_key=key,
             kayit_ms=t or NOW - 60_000, symbol="X")
    r["plan"] = {"coin_class": "A", "rejim": rejim}
    return r


def test_engel_ozeti_24_saat_ve_olasilik_esigi(tmp_path):
    rows = [_row("k1", ["YETKI_GOLGE"]), _row("k2", ["POLITIKA:oynaklık kapısı: x"]),
            _row("k3", ["MALIYET_SONRASI_AVANTAJ_YOK"], decision="REJECT"),
            _row("eski", ["YETKI_GOLGE"], t=NOW - 3 * 86_400_000)]
    # 30 gözlemlik grup: ilk kayıt SHADOW (YETKI); 6'sı sonradan OPEN'a dönüştü → %20
    for i in range(30):
        rows.append(_row(f"g{i}", ["YETKI_GOLGE"], strategy="controlled_breakout", side="short", rejim="bear"))
    for i in range(6):
        rows.append(_row(f"g{i}", [], decision="OPEN", strategy="controlled_breakout", side="short", rejim="bear",
                         t=NOW - 1000))
    # az gözlemli grup → kanıt yetersiz
    rows += [_row(f"s{i}", ["SINIF_C"], strategy="panic_reversal") for i in range(3)]
    _write_journal(tmp_path, rows)
    out = hz.write_summary(tmp_path, NOW)
    e = out["engel_24s"]
    assert e["YETKI_GOLGE"] == 31            # k1 + 30 gölge kaydı (eski kayıt 24 saat dışında)
    assert e["POLITIKA"] == 1 and e["MALIYET"] == 1 and e["RED"] == 1
    assert e["toplam"] == 42                 # 3 + 30 + 6 + 3 (24 saat içinde)
    g_big = out["gruplar"]["controlled_breakout|short|bear|YETKI"]
    assert g_big == {"n": 30, "acik": 6}     # ilk kayda göre gruplanır; n ≥ 30 → oran hesaplanabilir
    g_small = out["gruplar"]["panic_reversal|long|bull|SINIF"]
    assert g_small["n"] == 3 < out["esik"]                        # kanıt yetersiz (3/30) yazılır, yüzde yok
    assert (tmp_path / "hazirlik.json").exists()


# ── 3) canlı fiyat matematiği: server formülü ile istemci formülü aynı, ücret/funding tek kez düşülür ────
def _pos(side, entry, qty, risk, fees, funding, realized=0.0, price=None):
    p = {"symbol": "AAA", "inst": "AAA-USDT-SWAP", "side": side, "entry": entry, "qty": qty, "risk_usd": risk,
         "fees_usd": fees, "funding_usd": funding, "realized_usd": realized, "stop": entry * 0.95,
         "targets": [entry * 1.1], "strategies": ["swing_trend"], "opened_ts": NOW - 3_600_000, "updated_ts": NOW,
         "leverage": 2, "hist": [], "peak_r": 0.0, "unrealized_usd": 0.0, "unrealized_pct": 0.0}
    UnifiedTrader._mark(None, p, price or entry, NOW)              # sunucu formülü (değişmeden kullanılır)
    return p


def test_server_formulu_ucret_ve_funding_bir_kez(tmp_path):
    p = _pos("long", 100.0, 2.0, 20.0, fees=1.0, funding=-0.5)
    assert p["unrealized_usd"] == pytest.approx(-1.5)              # gross 0 − ücret 1 − funding 0.5: bir kez
    q = _pos("short", 100.0, 2.0, 20.0, fees=1.0, funding=-0.5, price=105.0)
    assert q["unrealized_usd"] == pytest.approx(-11.5)
    assert q["unrealized_pct"] == pytest.approx(-5.0)


# ── 4) tarayıcı testleri (Playwright) ───────────────────────────────────────────────────
def _browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright yok")
    return sync_playwright


FAKE_WS = """
window.__BT_CFG = Object.assign({}, window.__BT_CFG || {}, __CFG__);
window.__wsInst = []; window.__wsSent = []; window.__autoPong = __AUTO__;
class FakeWS {
  constructor(u) { this.url = u; this.readyState = 0; window.__wsInst.push(this);
    setTimeout(() => { if (this.readyState !== 0) return; this.readyState = 1; this.onopen && this.onopen({}); }, 5); }
  send(m) { window.__wsSent.push(m); if (m === 'ping' && window.__autoPong) setTimeout(() => this.onmessage && this.onmessage({data: 'pong'}), 5); }
  close() { if (this.readyState === 3) return; this.readyState = 3; this.onclose && this.onclose({}); }
}
FakeWS.CONNECTING = 0; FakeWS.OPEN = 1; FakeWS.CLOSING = 2; FakeWS.CLOSED = 3;
window.WebSocket = FakeWS;
"""


def _session(routes, width=390, cfg=None, action=None, autopong=False):
    sp = _browser()
    html = PAGE.read_text(encoding="utf-8")
    out = {}
    with sp() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": width, "height": 900})
        page.add_init_script(FAKE_WS.replace("__CFG__", json.dumps(cfg or {})).replace("__AUTO__", "true" if autopong else "false"))

        def handle(route):
            url = route.request.url.split("?")[0]
            if url.endswith("/birlesik.html"):
                return route.fulfill(status=200, body=html, content_type="text/html")
            for key, body in routes.items():
                if key == "kararlar-" and "kararlar-" in url:
                    return route.fulfill(status=200, body=body, content_type="text/plain")
                if key != "kararlar-" and url.endswith(key):
                    return route.fulfill(status=200, body=body, content_type="application/json")
            return route.fulfill(status=404, body="")
        page.route("**/*", handle)
        page.goto("http://panel.test/birlesik.html")
        page.wait_for_timeout(500)
        if action:
            out.update(action(page) or {})
        out["text"] = page.inner_text("body")
        out["sw"] = page.evaluate("document.documentElement.scrollWidth")
        b.close()
    return out


def _durum(pos):
    return json.dumps({"ts": NOW, "mod": "shadow", "bakiye": 100000.0, "baslangic": 100000.0, "gun_basi": 100000.0,
                       "hatalar": [], "pozisyonlar": pos, "kapananlar": [], "eq_hist": [], "btc_hist": [],
                       "coin": 2, "karar": {}})


def _data():
    return json.dumps({"generated_at": NOW, "feeds": {"okx": {"healthy": True, "last_msg_age_s": 0.2}},
                       "btc": {"price": 77000.5, "chg24": -1.2}, "weather": {"trend": "boga"}})


def _tick(page, inst, last):
    msg = {"arg": {"channel": "tickers", "instId": inst}, "data": [{"instId": inst, "last": str(last)}]}
    page.evaluate("m => window.__wsInst[window.__wsInst.length - 1].onmessage({data: JSON.stringify(m)})", msg)
    page.wait_for_timeout(120)


def test_karar_karti_hazirlik_ve_engel(tmp_path):
    rows = [_row("d1", ["YETKI_GOLGE"]), _row("d2", ["POLITIKA:oynaklık kapısı: x"])]
    dec = "\n".join(json.dumps({**r, "hazirlik": hz.evaluate(r), "explanation_tr": "Neden: test"}) for r in rows)
    haz = {"ts": NOW, "esik": 30, "engel_24s": {"YETKI_GOLGE": 1, "POLITIKA": 1, "MALIYET": 0, "VERI": 0, "RED": 0, "toplam": 2},
           "gruplar": {rows[0]["strategy"] + "|long|bull|YETKI": {"n": 3, "acik": 0}}}

    def act(page):
        page.click("text=Son Kararlar")
        page.wait_for_timeout(200)
        return {}
    o = _session({"/data.json": _data(), "/golge/durum.json": _durum([]),
                  "kararlar-": dec, "/hazirlik.json": json.dumps(haz)}, action=act)
    t = o["text"]
    assert "Son 24 saat engel özeti" in t
    assert "İşlem açılamaz: YETKI_GOLGE" in t and "Canlı kanıt/yetki yetersiz" in t
    assert "Hazırlık: %" in t and "ölçülebilir kapı geçti" in t
    assert "kanıt yetersiz (3/30)" in t
    assert "Günlük oynaklık %5 sınırını aşıyor" in t
    assert o["sw"] <= 390


def test_canli_fiyat_usd_pct_r_ve_bir_kez_dusum():
    p_long = _pos("long", 100.0, 2.0, 10.0, fees=1.0, funding=-0.5)
    p_long["symbol"], p_long["inst"] = "AAA", "AAA-USDT-SWAP"
    p_short = _pos("short", 100.0, 2.0, 10.0, fees=1.0, funding=-0.5)
    p_short["symbol"], p_short["inst"] = "BBB", "BBB-USDT-SWAP"
    dur = _durum([p_long, p_short])

    def act(page):
        # Sunucu fiyatı girişte: ücret+funding bir kez düşülmüş (−1.50$)
        base = page.evaluate("() => window.__BT.cardState({inst: 'AAA-USDT-SWAP', entry: 100, qty: 2, side: 'long', "
                             "risk_usd: 20, fees_usd: 1, funding_usd: -0.5, realized_usd: 0}, Date.now()).mode")
        assert base == "srv"
        _tick(page, "AAA-USDT-SWAP", 100.0)                 # giriş fiyatı → aynı −1.50$ olmalı
        _tick(page, "BBB-USDT-SWAP", 100.0)
        at_entry = page.evaluate("() => [...document.querySelectorAll('[data-inst=\"AAA-USDT-SWAP\"] [data-lv=\"usd\"]')].map(e => e.textContent)")
        assert at_entry == ["-$1.50"]
        _tick(page, "AAA-USDT-SWAP", 105.0)                # long: +10 − 1.5 = +8.50
        _tick(page, "BBB-USDT-SWAP", 105.0)                # short: −10 − 1.5 = −11.50
        return {}
    o = _session({"/data.json": _data(), "/golge/durum.json": dur, "kararlar-": "", "/hazirlik.json": "{}"}, action=act)
    t = o["text"]
    assert "+$8.50" in t and "+5.00%" in t and "+0.85R" in t
    assert "-$11.50" in t and "-5.00%" in t and "-1.15R" in t
    assert "Canlı" in t and o["sw"] <= 390


def test_baglanti_kesilince_fiyat_yeniymis_gibi_gosterilmez():
    p = _pos("long", 100.0, 2.0, 20.0, fees=1.0, funding=0.0)
    p["symbol"], p["inst"] = "AAA", "AAA-USDT-SWAP"

    def act(page):
        _tick(page, "AAA-USDT-SWAP", 105.0)
        page.evaluate("() => window.__wsInst[window.__wsInst.length - 1].close()")
        page.wait_for_timeout(150)
        return {"kesik": page.evaluate("() => [...document.querySelectorAll('[data-inst=\"AAA-USDT-SWAP\"] [data-lv=\"usd\"]')].map(e => e.textContent)")}
    o = _session({"/data.json": _data(), "/golge/durum.json": _durum([p]), "kararlar-": "", "/hazirlik.json": "{}"},
                 action=act)
    assert o["kesik"] == ["—"]
    assert "Bağlantı kesik" in o["text"]


def test_ping_yanit_yoksa_yeniden_baglan():
    p = _pos("long", 100.0, 2.0, 20.0, fees=0.0, funding=0.0)
    p["symbol"], p["inst"] = "AAA", "AAA-USDT-SWAP"
    cfg = {"pingMs": 300, "pongMs": 300, "maxBackoffMs": 300, "watchMs": 100, "retryMs": 100}

    def act(page):
        page.wait_for_timeout(1800)
        sent = page.evaluate("() => window.__wsSent.slice()")
        n = page.evaluate("() => window.__wsInst.length")
        return {"sent": sent, "n": n}
    o = _session({"/data.json": _data(), "/golge/durum.json": _durum([p]), "kararlar-": "", "/hazirlik.json": "{}"},
                 cfg=cfg, action=act)
    assert "ping" in o["sent"] and o["n"] >= 2                 # pong gelmedi → kapatıp yeniden bağlandı


def test_ping_pong_geldiginde_baglanti_korunur():
    p = _pos("long", 100.0, 2.0, 20.0, fees=0.0, funding=0.0)
    p["symbol"], p["inst"] = "AAA", "AAA-USDT-SWAP"
    cfg = {"pingMs": 300, "pongMs": 800, "maxBackoffMs": 300, "watchMs": 100, "retryMs": 100}

    def act(page):
        page.wait_for_timeout(1500)
        return {"n": page.evaluate("() => window.__wsInst.length"), "sent": page.evaluate("() => window.__wsSent.slice()")}
    o = _session({"/data.json": _data(), "/golge/durum.json": _durum([p]), "kararlar-": "", "/hazirlik.json": "{}"},
                 cfg=cfg, action=act, autopong=True)
    assert "ping" in o["sent"] and o["n"] == 1


def test_bayat_fiyat_etiketi():
    p = _pos("long", 100.0, 2.0, 20.0, fees=0.0, funding=0.0)
    p["symbol"], p["inst"] = "AAA", "AAA-USDT-SWAP"

    def act(page):
        _tick(page, "AAA-USDT-SWAP", 105.0)
        page.wait_for_timeout(900)
        return {"card": page.inner_text('[data-inst="AAA-USDT-SWAP"] [data-lv="state"]'),
                "usd": page.inner_text('[data-inst="AAA-USDT-SWAP"] [data-lv="usd"]')}
    o = _session({"/data.json": _data(), "/golge/durum.json": _durum([p]), "kararlar-": "", "/hazirlik.json": "{}"},
                 cfg={"staleMs": 400, "pingMs": 60000, "watchMs": 100}, action=act)
    assert "Bayat" in o["card"] and o["usd"] == "—"


def test_mobil_tasma_yok_ve_karar_ayrintisi():
    p = _pos("short", 1.2345, 10.0, 30.0, fees=0.2, funding=-0.1)
    p["symbol"], p["inst"] = "XYZ", "XYZ-USDT-SWAP"
    rows = [_row("m1", ["YETKI_GOLGE", "POLITIKA:oynaklık kapısı: x"] * 1)]
    dec = "\n".join(json.dumps({**r, "hazirlik": hz.evaluate(r), "explanation_tr": "Neden: uzun"}) for r in rows)
    for w in (390, 1280):
        def act(pg):
            pg.click("text=Son Kararlar")
            pg.wait_for_timeout(150)
            return {}
        o = _session({"/data.json": _data(), "/golge/durum.json": _durum([p]), "kararlar-": dec,
                      "/hazirlik.json": json.dumps({"ts": NOW, "esik": 30, "engel_24s": {"toplam": 1},
                                                    "gruplar": {}})}, width=w, action=act)
        assert o["sw"] <= w, (w, o["sw"])
        assert "Kemal" not in o["text"] and "Selin" not in o["text"]
