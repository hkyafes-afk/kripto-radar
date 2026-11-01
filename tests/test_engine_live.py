"""Canlı motor: WebSocket fiyatı → stop/hedef tetik → gerçekçi kapanış, funding kesimi, elle kapatma."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from radar import ledger as lg  # noqa: E402
from radar import ws  # noqa: E402
from radar.demo import DemoClient  # noqa: E402
from radar.engine import Engine  # noqa: E402
from radar.config import load_config  # noqa: E402
from test_ws import FakeServer, _frame  # noqa: E402


def _engine(d):
    cfg = load_config()
    cfg.setdefault("birlesik", {})["karar_modu"] = "legacy"  # bu dosya legacy motor davranışını test eder
    cfg_path = Path(d) / "legacy-test.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    e = Engine(Path(d), cfg_path=cfg_path, client=DemoClient(), notify=False, demo=True)
    e.run_once()
    e.rebuild_watch()
    return e


def test_tick_triggers_stop_and_target():
    with tempfile.TemporaryDirectory() as d:
        e = _engine(d)
        assert e.ledger["open"], "demo taramada işlem açılmalıydı"
        t = e.ledger["open"][0]
        n0 = len(e.ledger["closed"])
        sgn = 1 if t["side"] == "long" else -1
        # hedefe tam dokunmak limit emri doldurmaz
        tp_px = t["tp"]
        e._on_tick("okx", t["inst"], tp_px, tp_px * 0.9999, tp_px * 1.0001, int(time.time() * 1000))
        assert t["id"] not in e.hits
        e.hits.clear()          # (bu dokunuş +1R kısmi kârını tetikler; bu testte sadece tam kapanışa bakıyoruz)
        # stop seviyesini geçen fiyat → bid/ask'tan (daha kötü) dolum
        px = t["sl"] - sgn * t["entry"] * 0.002
        bid, ask = px - 0.0001 * px, px + 0.0001 * px
        e._on_tick("okx", t["inst"], px, bid, ask, int(time.time() * 1000))
        assert e.hits[t["id"]][0] == "stop"
        e.process_hits()
        # (aynı coinde başka yöneticinin, ör. Baş Trader'ın, pozisyonu da aynı fiyatta stop olabilir)
        c = next(x for x in e.ledger["closed"][n0:] if x["id"] == t["id"])
        assert len(e.ledger["closed"]) >= n0 + 1
        assert c["outcome"] == ("iz stop" if t.get("trail") and t.get("tp1_done") else "stop")
        slip = lg.stop_slip_bp(t, lg.fees_cfg(e.cfg)) / 1e4            # v9: oynaklığa bağlı stop kayması
        exp_exit = (bid if sgn > 0 else ask) * (1 - sgn * slip)
        assert abs(c["exit"] - exp_exit) < 1e-9 * max(1, exp_exit) and c["exit_liq"] == "taker"
        assert c["pnl_usd"] < 0 and t["inst"] not in e.watch or all(w[0] != t["id"] for w in e.watch.get(t["inst"], ()))
        # ikinci işlem hedefin içinden geçince maker dolum
        t2 = e.ledger["open"][0]
        s2 = 1 if t2["side"] == "long" else -1
        px2 = t2["tp"] + s2 * t2["entry"] * 0.001
        e._on_tick("okx", t2["inst"], px2, px2 * 0.9999, px2 * 1.0001, int(time.time() * 1000))
        e.process_hits()
        c2 = next(x for x in reversed(e.ledger["closed"]) if x["id"] == t2["id"])
        assert c2["outcome"] == "hedef" and c2["exit"] == t2["tp"] and c2["exit_liq"] == "maker" and c2["pnl_usd"] > 0


def test_funding_settlement_and_manual_close():
    with tempfile.TemporaryDirectory() as d:
        e = _engine(d)
        t = e.ledger["open"][0]
        now = int(time.time() * 1000)
        e._set_fund(t["inst"], 0.0005, now + 1000, "ws")
        e._set_fund(t["inst"], 0.0001, now + 8 * 3600_000, "ws")   # dönem değişti → önceki kesim olayı
        e.prices[t["inst"]] = t["entry"]
        e.process_funding()   # kesim zamanı gelmeden olay işlenir (giriş kesimden önce)
        assert t["funding_events"] == 1
        sgn = 1 if t["side"] == "long" else -1
        assert abs(t["funding_usd"] - sgn * t["qty"] * t["entry"] * 0.0005) < 1e-6
        e.process_funding()
        assert t["funding_events"] == 1, "aynı kesim iki kez uygulanmamalı"
        # elle kapatma: sembolle
        e.book[t["inst"]] = (t["entry"], t["entry"] * 0.999, t["entry"] * 1.001, time.time())
        msg = e.on_close_request(t["sym"])
        assert "kapatılıyor" in msg
        e.process_manual()
        c = next(x for x in reversed(e.ledger["closed"]) if x["id"] == t["id"])
        assert c["outcome"] == "manuel" and c["exit"] == (t["entry"] * 0.999 if sgn > 0 else t["entry"] * 1.001)
        assert "yok" in e.on_close_request("YOKCOIN")


def test_feed_to_engine_end_to_end():
    """Sahte OKX WebSocket sunucusu stop'u geçen fiyat yayınlar; motor işlemi kapatır."""
    with tempfile.TemporaryDirectory() as d:
        e = _engine(d)
        t = e.ledger["open"][0]
        sgn = 1 if t["side"] == "long" else -1
        px = t["sl"] - sgn * t["entry"] * 0.003

        def beh(conn, recv):
            recv()
            conn.sendall(_frame(json.dumps({"arg": {"channel": "tickers", "instId": t["inst"]},
                                            "data": [{"instId": t["inst"], "last": str(px), "bidPx": str(px * 0.9999),
                                                      "askPx": str(px * 1.0001), "ts": str(int(time.time() * 1000))}]}).encode()))
            time.sleep(1.5)

        srv = FakeServer(beh)
        srv.start()
        f = ws.OKXFeed(e._on_tick, e._on_funding)
        f.urls = [f"ws://127.0.0.1:{srv.port}/"]
        e.feeds["okx"] = f
        e._feed_symbols()
        f.start()
        t0 = time.time()
        while t["id"] not in e.hits and time.time() - t0 < 5:
            time.sleep(0.05)
        latency = time.time() - t0
        e.process_hits()
        f.stop()
        cx = next((x for x in e.ledger["closed"] if x["id"] == t["id"]), None)
        assert cx and cx["outcome"] in ("stop", "iz stop")
        assert latency < 3, latency
        p = e.build_payload()
        assert p["feeds"]["okx"]["ticks"] >= 1


def test_tick_partial_trailing_and_circuit_breakers():
    with tempfile.TemporaryDirectory() as d:
        e = _engine(d)
        # (kısmi kâr lot altına düşmesin: en az 4 kontratlık işlem; 1 lotluk küçük pozisyon bölünemez)
        t = next((x for x in e.ledger["open"] if x.get("tp1") and (x.get("pp") or {}).get("partial_frac")
                  and (x.get("contracts") or 0) >= 4), None)
        if t is None:      # v8 demo taramasında sadece hedefsiz trend işlemleri açıldıysa: kısmi kârlı bir işlem kur
            o0 = e.ledger["open"][0]
            px0 = o0["last"] if o0.get("last") else o0["entry"]
            atr = px0 * 0.02
            idea = {"mgr": "kaan", "inst": o0["inst"], "sym": o0["sym"], "tf": "1D", "side": "long", "entry": px0,
                    "sl": px0 - 2 * atr, "tp": px0 + 6 * atr, "tp1": px0 + 2 * atr, "atr": atr, "max_saat": 720,
                    "pp": {"partial_frac": 0.3, "trail_atr": 2.5, "basabas": True}, "conviction": 0.5}
            e.ledger["open"] = [x for x in e.ledger["open"] if x["inst"] != o0["inst"]]
            t = lg.build_trade(idea, px0, 1.0, 0, 3.0, e.cfg, int(time.time() * 1000), 140.0)
            e.ledger["open"].append(t)
            e.rebuild_watch()
        sgn = 1 if t["side"] == "long" else -1
        # +1R'yi geçen fiyat → kısmi kâr, stop başabaşa
        px = t["tp1"] + sgn * t["entry"] * 0.0005
        e._on_tick("okx", t["inst"], px, px * 0.9999, px * 1.0001, int(time.time() * 1000))
        e.process_hits()
        assert t["tp1_done"] and t["partials"] and t["qty_open"] < t["qty"]
        assert sgn * (t["sl"] - t["entry"]) > 0, "stop başabaşın kâr tarafında olmalı"
        # fiyat daha da iyi → iz süren stop ilerler
        sl0 = t["sl"]
        best = t["entry"] + sgn * 1.8 * abs(t["entry"] - t["sl_init"])   # hedefin (2R) hemen öncesi
        e._on_tick("okx", t["inst"], best, best, best, int(time.time() * 1000))
        e.process_trailing()
        assert sgn * (t["sl"] - sl0) > 0
        # geri dönüş → iz stop ile kârla kapanış
        back = t["sl"] - sgn * t["entry"] * 0.001
        e._on_tick("okx", t["inst"], back, back, back, int(time.time() * 1000))
        e.process_hits()
        c = next(x for x in e.ledger["closed"] if x["id"] == t["id"])
        assert c["outcome"] == "iz stop" and c["pnl_usd"] > 0
        # devre kesici: günlük kayıp (gerçek paraya geçişte açılacak; demo modunda kapalı)
        if e.desk:
            e.desk.fc["durdurma_aktif"] = True
        e.risk_tick()
        rs = e.state["risk"]
        lim = e.risk_limits()["gunluk_kayip_yuzde"] / 100      # ekip modunda firma limiti (%3)
        rs["day_start"] = rs["equity"] / (1 - lim - 0.005)
        rs["peak"] = rs["day_start"]
        e.risk_tick()
        assert e.halt == "gunluk"
        assert not e.open_positions([dict(s) for s in e.signals_by_tf.get("1H", [])[:3]])
        # zirveden düşüş → durdur, /devam ile aç
        rs["halt"] = None
        rs["peak"] = rs["equity"] * 1.2
        e.risk_tick()
        assert e.halt == "dusus"
        assert "kaldırıldı" in e.resume() and e.halt is None
        txt = e.panel_text()
        assert "canlı panel" in txt and "Bakiye" in txt


def test_team_breaker_uses_firm_limits():
    """Ekip modunda devre kesici risk müdürünün limitlerini (%3 / %15) kullanır; eski %2 ile konmuş günlük
    durdurma bir kez düzeltilir; gerçek limit aşıldıktan sonra toparlanma durdurmayı kaldırmaz."""
    from datetime import datetime, timezone
    with tempfile.TemporaryDirectory() as d:
        e = _engine(d)
        if not e.desk:
            e.ensure_team()
        e.desk.fc["durdurma_aktif"] = True        # bu test durdurmayı ölçer (demo modunda kapalı)
        assert e.desk, "ekip başlamalıydı"
        rc = e.risk_limits()
        assert rc["gunluk_kayip_yuzde"] == 3.0 and rc["max_dusus_yuzde"] == 15.0 and rc["kaynak"] == "firma"
        e.risk_tick()
        rs = e.state["risk"]
        eq = rs["equity"]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        def at(frac):                       # günün başına göre kayıp oranı
            rs.update(day=today, day_start=eq / (1 - frac), peak=eq)
            e.risk_tick()

        rs.update(halt="gunluk")            # eski sürümün %2 limitiyle konmuş durdurma (halt_lim yok)
        at(0.024)
        assert rs.get("halt") is None and e.halt is None, rs
        at(0.025)                           # %3'ün içinde: açık kalır
        assert e.halt is None
        at(0.031)                           # %3 aşıldı → durur
        assert e.halt == "gunluk" and rs["halt_lim"] == 3.0
        at(0.025)                           # toparlanma durdurmayı kaldırmaz
        assert e.halt == "gunluk"
        assert "–" in e.risk_limits()["risk_yuzde"]       # yöneticilerin taban–tavan risk aralığı


def test_user_extra_daily_allowance():
    """/devam günlük limitte: onay ister, 'evet' ile günde 1 kez %1,5 ek hak; ek pay bitince yine durur."""
    from datetime import datetime, timezone
    with tempfile.TemporaryDirectory() as d:
        e = _engine(d)
        if not e.desk:
            e.ensure_team()
        e.desk.fc["durdurma_aktif"] = True        # bu test durdurmayı ölçer (demo modunda kapalı)
        e.risk_tick()
        rs = e.state["risk"]
        eq = rs["equity"]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        def at(loss):
            rs.update(day=today, day_start=eq + loss, peak=eq)
            e.risk_tick()

        at(eq * 0.035)                                  # %3,5 kayıp → durur
        assert e.halt == "gunluk"
        msg = e.resume()
        assert "/devam evet" in msg and e.halt == "gunluk", "onaysız açılmamalı"
        msg = e.resume("evet")
        assert "Ek kayıp hakkı" in msg and e.halt is None
        e.risk_tick()
        assert e.halt is None, "ek hak verilince hemen tekrar durmamalı"
        start = rs["day_start"]
        at(eq * 0.035 + start * 0.01)                   # %1 daha kayıp: ek payın içinde
        assert e.halt is None
        at(eq * 0.035 + rs["day_start"] * 0.02)         # ek pay (%1,5) da bitti → durur
        assert e.halt == "gunluk"
        assert "kullanıldı" in e.resume("evet") and e.halt == "gunluk", "günde 1 kez"


def test_continuous_daily_evaluation():
    """Günlük yöneticiler gece yarısını beklemez: canlı mumla her an değerlendirilir; aynı fikir tekrarlanmaz."""
    with tempfile.TemporaryDirectory() as d:
        e = _engine(d)
        if not e.desk:
            e.ensure_team()
        r = e.universe[0]
        e.prices[r["inst"]] = 12345.0
        c = e.live_daily_ctx(r)
        assert c and c["live_bar"] and c["close"] == 12345.0 and c["tf"] == "1D"
        assert c["high"] >= 12345.0, "bugünün mumu canlı fiyatla güncellenmeli"
        # her coin için canlı değerlendirme; fikir çıkarsa ikinci turda tekrar önerilmez
        for x in e.universe:
            e.prices.setdefault(x["inst"], x["price"])
        e.pending_ideas.clear()
        e.scan_live_daily()
        n1 = len(e.pending_ideas)
        e.scan_live_daily()
        assert len(e.pending_ideas) == n1, "aynı fikir 30 dk içinde tekrar önerilmemeli"
        assert e.stats_counter["live_coins"] >= len(e.universe) // 2


def test_kaan_short_is_shadow():
    from radar import team
    c = {"inst": "X-USDT-SWAP", "sym": "X", "tf": "1D", "bar_ms": 86_400_000, "price": 80.0, "close": 80.0, "atr": 4.0,
         "open": 84.0, "high": 85.0, "low": 79.5, "hh20": 99.0, "ll20": 82.0, "hh10": 88.0, "ll10": 83.0,
         "sma50": 90.0, "sma100": 95.0, "sma200": 99.0, "vol_ratio": 2.0, "close_pos": 0.1, "btc_bear": True}
    idea = team.a_kaan(c, team.BY_ID["kaan"], e_cfg())
    assert idea and idea["side"] == "short" and idea["shadow_only"]


def e_cfg():
    from radar.config import load_config
    return load_config()


def test_data_issue_scales_with_volatility():
    """Kapanmış 4H mumdan bu yana sert ama gerçek hareket 'bozuk veri' sayılmamalı; kaba hatalar yakalanmalı."""
    import pandas as pd
    with tempfile.TemporaryDirectory() as d:
        e = Engine(Path(d), client=DemoClient(), notify=False, demo=True)
        now = pd.Timestamp.now(tz="UTC").floor("4h")
        idx = pd.date_range(end=now - pd.Timedelta(hours=4), periods=50, freq="4h")
        df = pd.DataFrame({"close": [0.0015] * 50}, index=idx)
        # oynak meme coin: 4H ATR %8 → %31 sapma eşik içinde (5 ATR = %40)
        assert e._data_issue(df, "4H", 0.0015 * 1.31, "DOOD", atr=0.0015 * 0.08) is None
        # aynı coinde 2 kat fark (yanlış enstrüman / ondalık hatası) → şüpheli
        assert "mum farkı" in (e._data_issue(df, "4H", 0.0030, "DOOD", atr=0.0015 * 0.08) or "")
        # sakin coin (ATR %1): %10 sapma → şüpheli (taban eşik %8)
        assert "eşik %8" in (e._data_issue(df, "4H", 0.0015 * 1.10, "BTC", atr=0.0015 * 0.01) or "")
        # ATR bilinmiyorsa eski davranış (%8)
        assert e._data_issue(df, "4H", 0.0015 * 1.05, "X") is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)


def test_risk_tick_ekip_setupinda_cokmez():
    """Regresyon (30.10.2026 canlı döngü çöküşü): 30+ kapanmış ekip işleminin setup'ı yönetici kimliği ('kemal') ve
    beklentisi negatifken risk_tick SETUP_LABELS KeyError ile çöküyordu. Kill switch yalnız eski sinyal setup'larına bakar.
    İşlemler SENTETİKTİR."""
    with tempfile.TemporaryDirectory() as d:
        e = _engine(d)
        e.ledger["closed"] += [{"real": True, "setup": "kemal", "tf": "1D", "r": -0.5, "exit_ts": i, "mgr": "kemal"}
                               for i in range(40)]
        e.risk_tick()                                   # KeyError olmamalı
        assert ("kemal", "1D") not in e.blocked
