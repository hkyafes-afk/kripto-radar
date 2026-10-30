"""PaperExecutionAdapter durum makinesi, OKX demo stub'ı ve çıkış yöneticisi. Fiyatlar ve kitaplar SENTETİKTİR."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.config import load_config  # noqa: E402
from radar.unified.execution import OKXDemoAdapter, PaperExecutionAdapter, Quote, make_adapter  # noqa: E402
from radar.unified.exits import evaluate_exits  # noqa: E402
from radar.unified.flags import ExecutionModeError  # noqa: E402
from radar.unified.models import ExecutionIntent, ExecutionMode, OrderStatus, OrderType, Regime  # noqa: E402

CFG = load_config()
NOW = 1_800_000_000_000
INST = "X-USDT-SWAP"


def q(last=100.0, bid=99.95, ask=100.05, ts=NOW, asks=(), bids=()):
    return Quote(inst=INST, bid=bid, ask=ask, last=last, ts_ms=ts, asks=asks, bids=bids)


def intent(action="open", ot=OrderType.MARKET, qty=1.0, side="long", price=None, stop=None, key="k1", seq=0,
           reduce_only=None, exp=None, md=None):
    return ExecutionIntent(idempotency_key=key, symbol="X", inst=INST, side=side, action=action, order_type=ot, qty=qty,
                           created_ts_ms=NOW, price=price, stop_price=stop, seq=seq, expires_ts_ms=exp,
                           reduce_only=(action != "open") if reduce_only is None else reduce_only,
                           metadata=md or {"stop": 95.0, "targets": [110.0], "risk_usd": 5.0, "strategy": "swing_trend"})


def test_sentetik_market_dolum_ve_pozisyon(tmp_path):
    a = PaperExecutionAdapter(CFG, tmp_path)
    r = a.submit(intent(), q(), NOW)
    assert r.status is OrderStatus.FILLED and r.filled_qty == 1.0
    assert r.avg_price > 100.05                                            # derinlik yok → ask + varsayılan kayma
    assert r.fee_usd > 0 and [e.kind for e in r.events] == ["submitted", "fill"]
    p = a.positions[INST]
    assert p["side"] == "long" and p["qty"] == 1.0 and p["stop"] == 95.0
    assert list(tmp_path.glob("olaylar-*.jsonl")) and (tmp_path / "yurutme.json").exists()


def test_sentetik_idempotency_ayni_niyet_tek_emir(tmp_path):
    a = PaperExecutionAdapter(CFG, tmp_path)
    r1 = a.submit(intent(), q(), NOW)
    r2 = a.submit(intent(), q(), NOW + 1000)
    assert r1.client_order_id == r2.client_order_id and len(a.orders) == 1 and a.positions[INST]["qty"] == 1.0
    b = PaperExecutionAdapter(CFG, tmp_path)                              # yeniden başlatma sonrası da
    b.submit(intent(), q(), NOW + 2000)
    assert len(b.orders) == 1 and b.positions[INST]["qty"] == 1.0


def test_sentetik_kismi_dolum_derinlik_yetmez(tmp_path):
    a = PaperExecutionAdapter(CFG, tmp_path)
    r = a.submit(intent(qty=3.0), q(asks=((100.05, 1.0), (100.10, 1.0))), NOW)
    assert r.status is OrderStatus.CANCELLED and abs(r.filled_qty - 2.0) < 1e-12
    assert abs(r.avg_price - 100.075) < 1e-9
    assert [e.kind for e in r.events] == ["submitted", "partial_fill", "cancel"]
    assert a.positions[INST]["qty"] == 2.0


def test_sentetik_ioc_limit(tmp_path):
    a = PaperExecutionAdapter(CFG, tmp_path)
    r = a.submit(intent(ot=OrderType.IOC_LIMIT, price=100.06, qty=2.0), q(asks=((100.05, 1.0), (100.2, 5.0))), NOW)
    assert r.status is OrderStatus.CANCELLED and r.filled_qty == 1.0     # yalnız limit içindeki seviye
    r = a.submit(intent(ot=OrderType.IOC_LIMIT, price=99.0, key="k2"), q(asks=((100.05, 1.0),)), NOW)
    assert r.status is OrderStatus.CANCELLED and r.filled_qty == 0.0


def test_sentetik_limit_bekler_dolar_ve_suresi_dolar(tmp_path):
    a = PaperExecutionAdapter(CFG, tmp_path)
    r = a.submit(intent(ot=OrderType.LIMIT, price=99.0, exp=NOW + 60_000), q(), NOW)
    assert r.status is OrderStatus.ACCEPTED and INST not in a.positions
    assert a.on_quote(q(last=99.0, bid=98.95, ask=99.05, ts=NOW + 1000), NOW + 1000) == []   # limite değmek yetmez
    ch = a.on_quote(q(last=98.9, bid=98.85, ask=98.95, ts=NOW + 2000), NOW + 2000)
    assert ch and ch[0].status is OrderStatus.FILLED and ch[0].avg_price == 99.0
    b = PaperExecutionAdapter(CFG, tmp_path / "b")
    b.submit(intent(ot=OrderType.LIMIT, price=90.0, exp=NOW + 60_000, key="k3"), q(), NOW)
    ch = b.on_quote(q(ts=NOW + 61_000), NOW + 61_000)
    assert ch[0].status is OrderStatus.EXPIRED


def test_sentetik_bayat_fiyatta_dolum_yok(tmp_path):
    a = PaperExecutionAdapter(CFG, tmp_path)
    r = a.submit(intent(), q(ts=NOW - 60_000), NOW)
    assert r.status is OrderStatus.REJECTED and "bayat" in r.reason and not a.positions
    assert a.submit(intent(key="k9"), None, NOW).status is OrderStatus.REJECTED


def test_sentetik_reduce_only_kurallari(tmp_path):
    a = PaperExecutionAdapter(CFG, tmp_path)
    r = a.submit(intent(action="close"), q(), NOW)
    assert r.status is OrderStatus.REJECTED and "pozisyon yok" in r.reason
    a.submit(intent(), q(), NOW)
    r = a.submit(intent(action="reduce", qty=5.0, key="k1", seq=1), q(), NOW)   # pozisyonu aşamaz → kırpılır
    assert r.status is OrderStatus.FILLED and r.filled_qty == 1.0 and INST not in a.positions
    assert a.closed and a.closed[-1]["net_usd"] < 0                     # ücret + kayma: anında kapatma zarar
    r = a.submit(intent(side="short", key="k5"), q(), NOW)
    assert r.status is OrderStatus.FILLED
    r = a.submit(intent(side="long", key="k6"), q(), NOW)
    assert r.status is OrderStatus.REJECTED and "ters" in r.reason        # ters çevirme yok


def test_sentetik_stop_emri_tetiklenir_ve_kayar(tmp_path):
    a = PaperExecutionAdapter(CFG, tmp_path)
    a.submit(intent(), q(), NOW)
    s = a.submit(intent(action="close", ot=OrderType.STOP_MARKET, stop=95.0, seq=1), q(), NOW)
    assert s.status is OrderStatus.ACCEPTED
    assert a.amend_stop(s.client_order_id, 96.0, NOW + 1).status is OrderStatus.ACCEPTED
    assert a.positions[INST]["stop"] == 96.0
    ch = a.on_quote(q(last=95.5, bid=95.45, ask=95.55, ts=NOW + 5000), NOW + 5000)
    assert ch[0].status is OrderStatus.FILLED and ch[0].avg_price < 95.5   # stop'tan kötü dolum (kayma)
    assert INST not in a.positions and a.closed[-1]["r"] < 0


def test_sentetik_kar_al_ve_sahipsiz_emir_iptali(tmp_path):
    a = PaperExecutionAdapter(CFG, tmp_path)
    a.submit(intent(), q(), NOW)
    st = a.submit(intent(action="close", ot=OrderType.STOP_MARKET, stop=95.0, seq=1), q(), NOW)
    tp = a.submit(intent(action="close", ot=OrderType.TAKE_PROFIT_LIMIT, price=110.0, seq=2), q(), NOW)
    ch = a.on_quote(q(last=110.5, bid=110.4, ask=110.6, ts=NOW + 9000), NOW + 9000)
    assert any(c.client_order_id == tp.client_order_id and c.status is OrderStatus.FILLED for c in ch)
    assert a.status(st.client_order_id).status is OrderStatus.CANCELLED     # pozisyon kapandı → stop iptal
    assert a.closed[-1]["net_usd"] > 0


def test_sentetik_yeniden_baslatma_uzlastirma(tmp_path):
    a = PaperExecutionAdapter(CFG, tmp_path)
    a.submit(intent(), q(), NOW)
    a.submit(intent(action="close", ot=OrderType.STOP_MARKET, stop=95.0, seq=1), q(), NOW)
    lim = a.submit(intent(ot=OrderType.LIMIT, price=90.0, key="k2", exp=NOW + 1000), q(), NOW)
    # çökme benzetimi: bir emir "pending" kalmış, pozisyon kaydı dışarıdan silinmiş
    a.orders["krSAHTE"] = {**a.orders[lim.client_order_id], "cid": "krSAHTE", "status": "pending", "events": []}
    a.save()
    b = PaperExecutionAdapter(CFG, tmp_path)
    assert INST in b.positions and len(b.orders) == 4
    del b.positions[INST]
    notes = b.reconcile(NOW + 5000)
    assert any("onaysız" in n for n in notes) and any("süresi" in n for n in notes) and any("sahipsiz" in n for n in notes)
    assert not b.open_orders()


def test_sentetik_bozuk_durum_dosyasi_sessizce_sifirlanmaz(tmp_path):
    (tmp_path / "yurutme.json").write_text("{bozuk")
    with pytest.raises(RuntimeError):
        PaperExecutionAdapter(CFG, tmp_path)


def test_sentetik_funding(tmp_path):
    a = PaperExecutionAdapter(CFG, tmp_path)
    a.submit(intent(), q(), NOW)
    amt = a.apply_funding(INST, 0.0001, 100.0, NOW + 1)
    assert amt < 0 and a.positions[INST]["funding_usd"] == amt          # long pozitif oranda öder


def test_paper_disi_mod_kapali(tmp_path):
    with pytest.raises(ExecutionModeError):
        OKXDemoAdapter()
    it = ExecutionIntent(idempotency_key="k", symbol="X", inst=INST, side="long", action="open",
                         order_type=OrderType.MARKET, qty=1.0, created_ts_ms=NOW, mode=ExecutionMode.OKX_LIVE)
    with pytest.raises(ExecutionModeError):
        PaperExecutionAdapter(CFG, tmp_path).submit(it, q(), NOW)
    cfg = copy.deepcopy(CFG)
    cfg["birlesik"]["yurutme_modu"] = "okx_live"
    assert isinstance(make_adapter(cfg, tmp_path / "m"), PaperExecutionAdapter)   # istense de paper


# ── çıkış yöneticisi ───────────────────────────────────────────────

def pos(**kw):
    p = {"inst": INST, "symbol": "X", "side": "long", "qty": 2.0, "entry": 100.0, "stop": 95.0, "stop_init": 95.0,
         "targets": [], "opened_ts": NOW, "best_px": 100.0, "partials": [], "max_holding_hours": None}
    p.update(kw)
    return p


def test_sentetik_cikis_stop_zaman_rejim_veri():
    assert evaluate_exits(pos(), 94.9, NOW, CFG)[0].code == "STOP"
    assert evaluate_exits(pos(max_holding_hours=10), 100.0, NOW + 11 * 3_600_000, CFG)[0].code == "ZAMAN"
    assert evaluate_exits(pos(), 100.0, NOW, CFG, regime=Regime.CRISIS)[0].code == "REJIM"
    assert evaluate_exits(pos(side="short", stop=105.0, stop_init=105.0), 100.0, NOW, CFG, regime=Regime.BULL)[0].code == "REJIM"
    assert evaluate_exits(pos(), 100.0, NOW, CFG, data_issue_since_ms=NOW - 61 * 60_000)[0].code == "VERI"
    assert evaluate_exits(pos(), 100.0, NOW, CFG, data_issue_since_ms=NOW - 5 * 60_000) == []


def test_sentetik_cikis_kismi_kar_basabas_kilit():
    acts = evaluate_exits(pos(), 105.0, NOW, CFG)                       # +1R
    kinds = {a.code: a for a in acts}
    assert kinds["KISMI_KAR"].qty == 1.0                                 # %50
    stops = [a for a in acts if a.kind == "move_stop"]
    assert len(stops) == 1 and stops[0].stop > 100.0                    # başabaş ya da daha sıkı kâr kilidi


def test_sentetik_cikis_legacy_kural_ve_ara_hedef():
    # legacy (Kemal) pozisyonu: ara hedef yok → kısmi kâr yok; kural çıkışı tamamlanmış günlük mumla
    p = pos(legacy_exits=True)
    assert not any(a.code == "KISMI_KAR" for a in evaluate_exits(p, 105.0, NOW, CFG))
    bar = {"close": 99.0, "sma50": 100.5, "atr": 2.0}
    acts = evaluate_exits(p, 99.0, NOW, CFG, closed_bar_ctx=bar, exit_rule={"type": "ma", "n": 50})
    assert acts and acts[0].code == "KURAL"
    p2 = pos(legacy_exits=True, targets=[104.0, 115.0])
    assert any(a.code == "KISMI_KAR" for a in evaluate_exits(p2, 104.5, NOW, CFG))
    assert evaluate_exits(p2, 115.0, NOW, CFG)[0].code == "HEDEF"


def test_sentetik_stop_asla_gevsemez():
    acts = evaluate_exits(pos(stop=104.0, partials=[{"qty": 1}]), 106.0, NOW, CFG, atr=5.0)
    assert all(a.stop > 104.0 for a in acts if a.kind == "move_stop")
