"""UnifiedTrader orkestratörü ve motor kancasının unified modu. Bağlamlar ve fiyatlar SENTETİKTİR."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from radar.config import load_config  # noqa: E402
from radar.unified.decision import UnifiedDecisionEngine  # noqa: E402
from radar.unified.execution import Quote  # noqa: E402
from radar.unified.models import OrderStatus  # noqa: E402
from radar.unified.strategies import SwingTrendStrategy  # noqa: E402
from radar.unified.trader import UnifiedTrader, quote_from_book  # noqa: E402
from test_unified_strategies import kemal_ctx  # noqa: E402

CFG = load_config()
NOW = 1_800_000_000_000
INST = "X-USDT-SWAP"
META = {INST: {"list_ts": NOW - 2000 * 86_400_000}}


def q(last, ts=NOW):
    return Quote(inst=INST, bid=last * 0.9997, ask=last * 1.0003, last=last, ts_ms=ts)


def trader(tmp_path, cfg=CFG):
    return UnifiedTrader(cfg, tmp_path, engine=UnifiedDecisionEngine(cfg, strategies=[SwingTrendStrategy(cfg)]))


def test_sentetik_tam_dongu_ac_koru_kapat(tmp_path):
    t = trader(tmp_path)
    s = t.step([kemal_ctx()], {INST: q(101.0)}, now_ms=NOW, meta=META)
    assert len(s["acilan"]) == 1 and s["acilan"][0]["durum"] == "filled" and s["acik"] == 1
    pos = t.adapter.positions[INST]
    assert pos["strategy"] == "swing_trend" and pos["exit_rule"] == {"type": "ma", "n": 50} and pos["legacy_exits"]
    so = t._stop_order(INST)
    assert so and so["intent"]["reduce_only"] and abs(so["trigger"] - pos["stop"]) < 1e-9     # koruyucu stop emri
    assert abs(pos["risk_usd"] - s["bakiye"] * 0.005) / (s["bakiye"] * 0.005) < 0.05          # ~%0,5 risk
    # aynı fırsat bir sonraki turda tekrar açılmaz
    s2 = t.step([kemal_ctx()], {INST: q(101.2, NOW + 60_000)}, now_ms=NOW + 60_000, meta=META)
    assert s2["acilan"] == [] and len([o for o in t.adapter.orders.values()
                                      if o["intent"]["action"] == "open"]) == 1
    # fiyat stop'a iner → stop emri tetiklenir, pozisyon kapanır, zarar ~1R
    stop = pos["stop"]
    s3 = t.step([], {INST: q(stop * 0.999, NOW + 120_000)}, now_ms=NOW + 120_000, meta=META)
    assert s3["acik"] == 0 and t.adapter.closed and -1.4 < t.adapter.closed[-1]["r"] < -0.9
    assert s3["bakiye"] < t.start_equity and s3["kayip_serisi"] == 1
    st = json.loads((tmp_path / "durum.json").read_text())
    assert st["mod"] == "unified" and st["yurutme"] == "paper" and st["kapananlar"]


def test_sentetik_100_usdt_butce_gercek_trader_entegrasyonu(tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg.setdefault("birlesik", {}).setdefault("paper", {})["baslangic_bakiye"] = 100.0
    t = trader(tmp_path, cfg)
    assert t.start_equity == 100.0
    # Küçük kontrat: 100 USDT hesabın %0,5 risk bütçesine göre emir açılabilir.
    s = t.step([kemal_ctx()], {INST: q(101.0)}, now_ms=NOW,
               meta={INST: {**META[INST], "ct_val": 0.001, "lot_sz": 1, "min_sz": 1}})
    assert 99.0 < s["bakiye"] < 100.0 and s["acik"] == 1  # açılış taker ücreti anında düşülür
    pos = t.adapter.positions[INST]
    assert pos["notional_usd"] < 20.0
    assert pos["risk_usd"] <= 0.5 * 1.05


def test_sentetik_100_usdt_minimum_kontrat_buyukse_islem_yok(tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg.setdefault("birlesik", {}).setdefault("paper", {})["baslangic_bakiye"] = 100.0
    t = trader(tmp_path, cfg)
    s = t.step([kemal_ctx()], {INST: q(101.0)}, now_ms=NOW,
               meta={INST: {**META[INST], "ct_val": 1.0, "lot_sz": 1, "min_sz": 1}})
    assert s["bakiye"] == 100.0 and s["acik"] == 0
    assert not t.adapter.positions
    assert any("LOT_UYGUN_DEGIL" in r["kodlar"] for r in s["risk"])


def test_sentetik_fiyat_yoksa_acilmaz(tmp_path):
    t = trader(tmp_path)
    s = t.step([kemal_ctx()], {}, now_ms=NOW, meta=META)
    assert s["acik"] == 0 and s["acilan"] == []
    assert any("GIRIS_BBO_YOK" in r["kodlar"] for r in s["risk"])


def test_crossed_bbo_rejects_new_entry(tmp_path):
    t = trader(tmp_path)
    crossed = Quote(inst=INST, bid=102.0, ask=101.0, last=101.5, ts_ms=NOW)
    s = t.step([kemal_ctx()], {INST: crossed}, now_ms=NOW, meta=META)
    assert s["acik"] == 0 and s["acilan"] == []
    assert any("GIRIS_BBO_YOK" in r["kodlar"] for r in s["risk"])


def test_locked_bbo_rejects_new_entry(tmp_path):
    t = trader(tmp_path)
    locked = Quote(inst=INST, bid=101.0, ask=101.0, last=101.0, ts_ms=NOW)
    s = t.step([kemal_ctx()], {INST: locked}, now_ms=NOW, meta=META)
    assert s["acik"] == 0 and s["acilan"] == []
    assert any("GIRIS_BBO_YOK" in r["kodlar"] for r in s["risk"])


def test_sentetik_veri_sorununda_acilmaz(tmp_path):
    t = trader(tmp_path)
    s = t.step([kemal_ctx(suspect="Bitget farkı %4")], {INST: q(101.0)}, now_ms=NOW, meta=META)
    assert s["acik"] == 0 and s["acilan"] == []
    s = t.step([kemal_ctx()], {INST: q(101.0)}, now_ms=NOW, meta=META, ws_healthy=False)
    assert s["acik"] == 0 and s["risk"] and s["risk"][-1]["karar"] in ("SHADOW", "REJECT")


def test_sentetik_elle_durdurma(tmp_path):
    t = trader(tmp_path)
    t.halt("kullanıcı durdurdu")
    s = t.step([kemal_ctx()], {INST: q(101.0)}, now_ms=NOW, meta=META)
    assert s["acik"] == 0 and s["risk"][0]["kodlar"] == ["DEVRE_DURDURULDU"]


def test_bozuk_portfoy_durumu_sessizce_sifirlanmaz(tmp_path):
    (tmp_path / "portfoy.json").write_text("{bozuk", encoding="utf-8")
    import pytest
    with pytest.raises(RuntimeError, match="güvenli başlatma iptal edildi"):
        trader(tmp_path)


def test_sentetik_yeniden_baslatma_pozisyon_ve_stop_korunur(tmp_path):
    t = trader(tmp_path)
    t.step([kemal_ctx()], {INST: q(101.0)}, now_ms=NOW, meta=META)
    t2 = trader(tmp_path)
    assert INST in t2.adapter.positions and t2._stop_order(INST)
    # stop emri bir şekilde kaybolursa bir sonraki turda yeniden kurulur
    so = t2._stop_order(INST)
    so["status"] = OrderStatus.CANCELLED.value
    t2.step([], {INST: q(101.5, NOW + 60_000)}, now_ms=NOW + 60_000, meta=META)
    assert t2._stop_order(INST)


def test_sentetik_kural_cikisi_tamamlanmis_mumla(tmp_path):
    t = trader(tmp_path)
    t.step([kemal_ctx()], {INST: q(101.0)}, now_ms=NOW, meta=META)
    bar = {"inst": INST, "close": 99.0, "sma50": 99.5, "atr": 3.0}
    s = t.step([], {INST: q(99.0, NOW + 60_000)}, now_ms=NOW + 60_000, meta=META, closed_bars={INST: bar})
    assert s["acik"] == 0 and s["cikislar"][0]["code"] == "KURAL"


def test_quote_from_book_bayat():
    assert quote_from_book(INST, (100.0, 99.9, 100.1, 1000.0), now_s=1005.0).bid == 99.9
    assert quote_from_book(INST, (100.0, 99.9, 100.1, 1000.0), now_s=1100.0) is None
    assert quote_from_book(INST, None) is None


def test_sentetik_motor_kancasi_unified_modu_ayri_defter(tmp_path):
    from radar.engine import Engine
    cfg = copy.deepcopy(CFG)
    cfg["birlesik"]["karar_modu"] = "unified"
    fe = Engine.__new__(Engine)                                          # ağ/defter kurmadan
    fe.cfg, fe.site, fe.meta, fe.book, fe.halt, fe.ledger = cfg, tmp_path, dict(META), {}, None, {"closed": []}
    fe._unified_shadow([kemal_ctx()])
    assert fe.unified_shadow["mod"] == "unified" and (tmp_path / "birlesik" / "durum.json").exists()
    assert not (tmp_path / "ledger.json").exists()                       # legacy deftere dokunulmaz
    assert fe.unified_audit["islem_sayisi"] == 0


def test_eski_100k_shadow_portfoyu_islemsizse_100_usdtye_guvenle_rebase_edilir(tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg.setdefault("birlesik", {}).setdefault("paper", {})["baslangic_bakiye"] = 100.0
    old_btc = [[NOW, 77500.0]]
    (tmp_path / "portfoy.json").write_text(json.dumps({
        "peak": 100000.0, "day": "2026-10-31", "day_start": 100000.0,
        "issue_since": {}, "halted": None, "eq_hist": [[NOW, 100000.0]], "btc_hist": old_btc
    }), encoding="utf-8")

    t = trader(tmp_path, cfg)
    assert t.start_equity == 100.0
    assert t.st["start_equity"] == 100.0
    assert t.st["peak"] is None
    assert t.st["eq_hist"] == []
    assert t.st["btc_hist"] == old_btc
    assert t.portfolio(NOW).equity == 100.0
    t._save()
    assert json.loads((tmp_path / "portfoy.json").read_text())["start_equity"] == 100.0


def test_sermaye_degisiminde_islem_gecmisi_varsa_migrasyon_fail_closed(tmp_path):
    import pytest

    cfg = copy.deepcopy(CFG)
    cfg.setdefault("birlesik", {}).setdefault("paper", {})["baslangic_bakiye"] = 100.0
    t = trader(tmp_path, cfg)
    t.adapter.closed = [{"net_usd": 1.0}]
    t.adapter.closed_net_usd = 1.0
    t.adapter.save()
    (tmp_path / "portfoy.json").write_text(json.dumps({
        "peak": 100000.0, "day": "2026-10-31", "day_start": 100000.0,
        "issue_since": {}, "halted": None, "eq_hist": [[NOW, 100000.0]]
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="otomatik sıfırlama yapılmadı"):
        trader(tmp_path, cfg)
