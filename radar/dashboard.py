"""Statik dashboard üretimi: site/index.html (veri gömülü) + site/data.json."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import store

TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"


def write_site(site: Path, payload: dict) -> None:
    site = Path(site)
    site.mkdir(parents=True, exist_ok=True)
    clean = store.clean(payload)
    data = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    (site / "data.json").write_text(data, encoding="utf-8")
    bt = site / "backtest.json"
    repo_bt = Path(__file__).resolve().parent.parent / "data" / "backtest.json"
    if repo_bt.exists():
        shutil.copyfile(repo_bt, bt)
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("/*__DATA__*/null", data.replace("</", "<\\/"))
    (site / "index.html").write_text(html, encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    for name in ("polat.html",                             # Polat sayfası (bağımsız ajan; veriyi kendi dalından okur)
                 "birlesik.html"):                         # Birleşik Baş Trader paneli (veriyi site/birlesik/'ten okur)
        src = TEMPLATE.parent / name
        if src.exists():
            shutil.copyfile(src, site / name)
