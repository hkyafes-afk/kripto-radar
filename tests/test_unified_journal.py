"""Karar günlüğü, gölge çalıştırıcı ve motor kancası. Bağlamlar SENTETİKTİR (elle yazılmış sayılar, gerçek piyasa değil)."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from radar.config import load_config  # noqa: E402
from radar.unified.decision import UnifiedDecisionEngine  # noqa: E402
from radar.unified.journal import DAY_MS, DecisionJournal  # noqa: E402
from radar.unified.models import Decision  # noqa: E402
from radar.unified.shadow import ShadowRunner  # noqa: E402
from radar.unified.strategies import SwingTrendStrategy  # noqa: E402
from test_unified_decision import mctx  # noqa: E402
from test_unified_strategies import kemal_ctx  # noqa: E402

CFG = load_config()
NOW = 1_800_000_000_000


def _decisions(**kw):
    return UnifiedDecisionEngine(CFG, strategies=[SwingTrendStrategy(CFG)]).decide([mctx(**kw)], NOW)


def _lines(j):
    return [json.loads(x) for f in j.files() for x in f.read_text(encoding="utf-8").splitlines()]


def test_sentetik_gunluk_ayni_karari_tekrar_yazmaz(tmp_path):
    j = DecisionJournal(tmp_path)
    ds = _decisions()
    assert j.write(ds, NOW) == 1
    assert j.write(_decisions(), NOW + 60_000) == 0                      # değişmeyen karar → satır yok
    assert DecisionJournal(tmp_path).write(_decisions(), NOW + 120_000) == 0   # yeniden başlatma sonrası da
    rec = _lines(j)[0]
    assert rec["decision"] == "OPEN" and rec["strategy"] == "swing_trend" and rec["kayit_ms"] == NOW


def test_sentetik_gunluk_karar_degisince_yazar(tmp_path):
    j = DecisionJournal(tmp_path)
    j.write(_decisions(), NOW)
    ds = _decisions(suspect="Bitget ile fiyat farkı %4")
    assert ds[0].decision is Decision.SHADOW
    assert j.write(ds, NOW + 60_000) == 1
    assert [r["decision"] for r in _lines(j)] == ["OPEN", "SHADOW"]


def test_sentetik_gunluk_dondurme_ve_budama(tmp_path):
    j = DecisionJournal(tmp_path, keep_days=14)
    j.write(_decisions(), NOW)
    j.write(_decisions(suspect="x"), NOW + 20 * DAY_MS)
    assert len(j.files()) == 2                                            # her gün ayrı dosya
    removed = j.prune(NOW + 20 * DAY_MS)
    assert len(removed) == 1 and len(j.files()) == 1
    idx = json.loads((tmp_path / "son_durum.json").read_text())
    assert all(v[1] >= NOW + 17 * DAY_MS for v in idx.values())          # dizin yalnız son 3 gün


def test_sentetik_golge_calistirici_ozet_ve_aralik(tmp_path):
    r = ShadowRunner(CFG, tmp_path, interval_s=300)
    s = r.run([kemal_ctx()], now_ms=NOW, force=True)
    assert s["coin"] == 1 and s["yeni_satir"] >= 1 and isinstance(s["karar"], dict)
    assert r.run([kemal_ctx()], now_ms=NOW) is None                       # 5 dk dolmadan tekrar çalışmaz
    assert (tmp_path / "son_durum.json").exists()


def test_sentetik_golge_bozuk_baglam_digerlerini_durdurmaz(tmp_path):
    r = ShadowRunner(CFG, tmp_path)
    s = r.run([{"inst": "BOZUK", "sym": "BOZUK", "close": "x"}, kemal_ctx()], now_ms=NOW, force=True)
    assert s is not None and s["coin"] >= 1


def _fake_engine(cfg, site):
    """Gerçek Engine'i ağ/defter kurmadan oluşturur (yalnız kancanın kullandığı alanlar)."""
    from radar.engine import Engine
    fe = Engine.__new__(Engine)
    fe.cfg, fe.site, fe.meta, fe.book, fe.halt, fe.ledger = cfg, site, {}, {}, None, {"closed": []}
    return fe


def _hook(cfg, site, ctxs):
    fe = _fake_engine(cfg, site)
    fe._unified_shadow(ctxs)
    return fe


def test_motor_kancasi_legacy_modda_calismaz(tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["birlesik"]["karar_modu"] = "legacy"
    fe = _hook(cfg, tmp_path, [kemal_ctx()])
    assert not hasattr(fe, "unified_shadow") and not (tmp_path / "birlesik").exists()


def test_sentetik_motor_kancasi_shadow_modda_gunluk_yazar(tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["birlesik"]["karar_modu"] = "shadow"
    fe = _hook(cfg, tmp_path, [kemal_ctx()])
    assert fe.unified_shadow["coin"] == 1
    assert list((tmp_path / "birlesik").glob("kararlar-*.jsonl"))
    assert (tmp_path / "birlesik" / "etkinlik.json").exists()            # etkinlik denetçisi de çalıştı


def test_motor_kancasi_hata_yutar(tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["birlesik"]["karar_modu"] = "shadow"
    _hook(cfg, tmp_path, None)                                            # None bağlam → hata yutulur, istisna yok
