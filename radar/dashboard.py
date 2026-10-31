"""Tek merkezli statik dashboard üretimi.

site/index.html: Baş Trader birleşik paneli (tek ana giriş).
site/radar.html: eski Radar paneli, geçiş süresince salt-okunur arşiv.
site/birlesik.html: eski bağlantılar için uyumluluk alias'ı.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import store
from .config import load_config
from .unified.settings import discover_settings

TEMPLATES = Path(__file__).resolve().parent / "templates"
LEGACY_TEMPLATE = TEMPLATES / "index.html"
UNIFIED_TEMPLATE = TEMPLATES / "birlesik.html"


def write_site(site: Path, payload: dict) -> None:
    site = Path(site)
    site.mkdir(parents=True, exist_ok=True)
    clean = store.clean(payload)
    data = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    (site / "data.json").write_text(data, encoding="utf-8")

    # Parametre kataloğu keşif amaçlıdır; dashboard buradan config yazamaz.
    settings_dir = site / "birlesik"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings = discover_settings(load_config())
    (settings_dir / "ayarlar.json").write_text(
        json.dumps(settings, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    bt = site / "backtest.json"
    repo_bt = Path(__file__).resolve().parent.parent / "data" / "backtest.json"
    if repo_bt.exists():
        shutil.copyfile(repo_bt, bt)

    # Eski dashboard'u silmek yerine radar.html arşiv rotasında tut.
    legacy = LEGACY_TEMPLATE.read_text(encoding="utf-8")
    legacy = legacy.replace("/*__DATA__*/null", data.replace("</", "<\\/"))
    (site / "radar.html").write_text(legacy, encoding="utf-8")

    # Tek ana giriş: birleşik Baş Trader. Bu panel kendi data.json ve birlesik/* JSON'larını okur.
    unified = UNIFIED_TEMPLATE.read_text(encoding="utf-8")
    (site / "index.html").write_text(unified, encoding="utf-8")
    # Eski doğrudan bağlantıları kırmamak için alias dosyasını da koru.
    (site / "birlesik.html").write_text(unified, encoding="utf-8")
    polat = TEMPLATES / "polat.html"
    if polat.exists():
        shutil.copyfile(polat, site / "polat.html")
    (site / ".nojekyll").write_text("", encoding="utf-8")
