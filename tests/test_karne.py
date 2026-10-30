"""Tek ajan karnesi (radar/karne.py) ve güven aralığı yardımcıları (radar/ci.py). İşlemler SENTETİKTİR."""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import ci, karne  # noqa: E402
from radar.commands import cmd_karne  # noqa: E402


def tr(mgr, r, shadow=False, **kw):
    return {"mgr": mgr, "r": r, "pnl_usd": r * 10, "shadow": shadow, **kw}


def sentetik(mgr, n, mu, seed, **kw):
    rnd = random.Random(seed)
    return [tr(mgr, rnd.gauss(mu, 1.0), **kw) for _ in range(n)]


def test_sentetik_bas_trader_toplami_ve_kaynaklar():
    closed = sentetik("kemal", 40, 0.4, 1) + sentetik("murat", 10, -0.3, 2) + sentetik("kemal", 12, 0.1, 3, shadow=True)
    k = karne.build(closed)
    assert k["bas_trader"]["n"] == 50 and k["bas_trader_golge"]["n"] == 12
    by = {r["kaynak"]: r for r in k["kaynaklar"]}
    assert by["swing_trend"]["etiket"] == "Trend" and by["swing_trend"]["gercek"]["n"] == 40
    assert by["swing_trend"]["golge"]["n"] == 12 and by["legacy_murat"]["golge"]["n"] == 0
    assert by["legacy_murat"]["gercek"]["durum"].startswith("kanıt yetersiz")
    s = by["swing_trend"]["gercek"]
    assert s["ga_alt"] is not None and s["ga_alt"] < s["ort_r"] < s["ga_ust"]
    assert {"isabet", "ort_r", "toplam_r", "pf"} <= set(s)


def test_sentetik_filtre_karnesi():
    sh = [tr("kaan", r, shadow=True, filtered="spread çok geniş", filter_kind="spread") for r in [-1.0] * 25 + [-0.8] * 10]
    sh += [tr("selin", 0.5, shadow=True)]                                         # filtresiz gölge (sınıf/yetki)
    k = karne.build(sh)
    f = {r["filtre"]: r for r in k["filtreler"]}
    assert f["spread"]["n"] == 35 and f["spread"]["karar"].startswith("filtre haklı")
    assert f["sınıf C / yetki gölgesi"]["karar"].startswith("kanıt yetersiz")


def test_veri_yoksa_uydurmaz():
    k = karne.build([])
    assert k["bas_trader"] == {"n": 0, "durum": "veri yok"} and k["kaynaklar"] == [] and k["filtreler"] == []
    md = karne.to_md(k, "Karne")
    assert "veri yok" in md and "Baş Trader" in md


def test_sentetik_birlesik_paper_dahil_ve_telegram():
    uc = [{"strategy": "swing_trend", "r": 1.0, "net_usd": 5.0}]
    k = karne.build(sentetik("kemal", 5, 0.2, 4), uc)
    assert k["bas_trader"]["n"] == 6
    txt = cmd_karne({"karne": k, "ledger": {"stats": {}}})
    assert "Baş Trader" in txt and "Trend" in txt and "Kemal" not in txt


def test_ci_yardimcilari():
    assert ci.mean_ci([1, 2]) == (None, None)
    lo, hi = ci.mean_ci([0.5] * 20)
    assert lo == hi == 0.5
    lo, hi = ci.diff_ci([1.0] * 10 + [1.2] * 10, [0.0] * 10 + [0.2] * 10)
    assert lo > 0.8 and hi < 1.2
    base = [(i, -1.0 if i < 10 else 1.0) for i in range(30)]
    lo, hi, n = ci.subset_improvement_ci(base, {i for i in range(10, 30)})
    assert n == 10 and hi == -1.0                                                  # elenenlerin hepsi kaybeden
