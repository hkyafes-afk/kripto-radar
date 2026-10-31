"""Birleşik Baş Trader paneli: site üretimi ve gölge özeti. Bağlamlar SENTETİKTİR."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from radar import dashboard  # noqa: E402
from radar.config import load_config  # noqa: E402
from radar.unified.shadow import ShadowRunner  # noqa: E402
from test_unified_strategies import kemal_ctx  # noqa: E402

CFG = load_config()
NOW = 1_800_000_000_000


def test_site_birlesik_panelini_kopyalar_ve_baglar(tmp_path):
    dashboard.write_site(tmp_path, {"x": 1})
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    alias = (tmp_path / "birlesik.html").read_text(encoding="utf-8")
    legacy = (tmp_path / "radar.html").read_text(encoding="utf-8")
    assert "birlesik/golge/durum.json" in page and "birlesik/durum.json" in page and "data.json" in page and "kararlar-" in page
    assert page == alias
    assert "Kripto Radar" in legacy and "/*__DATA__*/null" not in legacy
    assert "Veri yok" in page and "Bayat" in page and "Bağlantı kesik" in page and "PAPER · TEK OTORİTE" in page
    assert 'id="modeTag"' in page and "Deneysel doğrulama sürüyor" in page
    assert (tmp_path / "polat.html").exists()


def test_panel_dis_kaynak_yuklemez():
    page = (Path(__file__).resolve().parent.parent / "radar" / "templates" / "birlesik.html").read_text(encoding="utf-8")
    assert "<script src" not in page and "http://" not in page and "https://" not in page


def test_sentetik_golge_ozeti_yazilir(tmp_path):
    s = ShadowRunner(CFG, tmp_path).run([kemal_ctx()], now_ms=NOW, force=True)
    o = json.loads((tmp_path / "ozet.json").read_text(encoding="utf-8"))
    assert o["mod"] == "shadow" and o["coin"] == s["coin"] == 1
