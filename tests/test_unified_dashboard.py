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
    assert "birlesik/durum.json" in page and "birlesik/golge/durum.json" not in page and "data.json" in page and "kararlar-" in page
    assert "birlesik/hazirlik.json" in page
    assert page == alias
    assert "Kripto Radar" in legacy and "/*__DATA__*/null" not in legacy
    assert "Veri yok" in page and "Bayat" in page and "Bağlantı kesik" in page and "PAPER · TEK OTORİTE" in page
    assert "Ayarlar" in page and "renderSettings" in page and "birlesik/ayarlar.json" in page
    assert "Yeniden başlatma" in page and "doğrulanmadı" in page
    assert "Veri Sağlığı" in page and "renderDataHealth" in page and "tanilama" in page
    assert all(label in page for label in ("Fırsatlar", "Portföy", "Karar Merkezi", "Performans", "Deney Laboratuvarı"))
    assert "renderOpportunities" in page and "renderExperimentLab" in page
    assert "renderKarne(data && data.karne" in page
    assert "function sourceMeta" in page and "Kaynak güncellemesi" in page and "sourcebar" in page
    assert "birlesik/durum.json → PAPER defteri" in page
    assert "data.json → karne.yeni_golge" in page
    assert 'role="status"' in page and 'aria-live="polite"' in page
    assert 'id="modeTag"' in page and "Deneysel doğrulama sürüyor" in page
    assert (tmp_path / "polat.html").exists()
    settings = json.loads((tmp_path / "birlesik" / "ayarlar.json").read_text(encoding="utf-8"))
    assert settings["mode"] == "read_only_discovery"
    assert settings["write_enabled"] is False
    assert settings["write_ready_count"] == 0
    params = {item["path"]: item for item in settings["parameters"]}
    assert params["birlesik.paper.baslangic_bakiye"]["value"] == 100
    assert params["birlesik.yurutme_modu"]["protected"] is True


def test_panel_dis_kaynak_yuklemez():
    page = (Path(__file__).resolve().parent.parent / "radar" / "templates" / "birlesik.html").read_text(encoding="utf-8")
    assert "<script src" not in page and "http://" not in page
    assert page.count("https://") == 1
    assert "https://github.com/hkyafes-afk/kripto-radar/actions/workflows/settings-change-request.yml" in page


def test_sentetik_golge_ozeti_yazilir(tmp_path):
    s = ShadowRunner(CFG, tmp_path).run([kemal_ctx()], now_ms=NOW, force=True)
    o = json.loads((tmp_path / "ozet.json").read_text(encoding="utf-8"))
    assert o["mod"] == "shadow" and o["coin"] == s["coin"] == 1
