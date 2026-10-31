"""EffectivenessAuditor (radar/unified/effectiveness.py). İşlem listeleri SENTETİKTİR (sabit tohumlu rastgele R değerleri)."""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.unified.effectiveness import EffectivenessAuditor, deflated_sharpe, expected_max_sharpe  # noqa: E402

NOW = 1_800_000_000_000


def legacy(mgr, rs, shadow=False, regime=1, tf="1D"):
    return [{"mgr": mgr, "r": r, "pnl_usd": r * 5.0, "fee_usd": 0.4, "funding_usd": 0.0, "shadow": shadow,
             "regime": regime, "tf": tf, "exit_ts": NOW - i * 1000} for i, r in enumerate(rs)]


def sentetik_r(n, mean, sd=1.0, seed=1):
    rnd = random.Random(seed)
    return [rnd.gauss(mean, sd) for _ in range(n)]


def test_sentetik_kucuk_ornek_kanit_degil():
    rep = EffectivenessAuditor().audit(legacy("kemal", [2.0, 1.5, -1.0, 3.0, 0.5]), now_ms=NOW)
    k = rep["kapsamlar"]["strateji:swing_trend:legacy:paper"]
    assert k["n_trades"] == 5 and k["karar"] == "kanit_yetersiz" and "95 işlem daha" in k["karar_tr"]
    assert any("örnek küçük" in n for n in k["notes"])
    assert rep["notlar"] and "deneme kaydı yok" in rep["notlar"][0]


def test_sentetik_guclu_avantaj_terfi_adayi():
    rep = EffectivenessAuditor().audit(legacy("kemal", sentetik_r(200, 0.5)), now_ms=NOW)
    k = rep["kapsamlar"]["strateji:swing_trend:legacy:paper"]
    assert k["karar"] == "terfi_adayi" and k["ci_low_r"] > 0 and k["dsr"]["dsr"] >= 0.95
    assert rep["ozet"]["terfi_adayi"] == ["strateji:swing_trend:legacy:paper"]


def test_sentetik_negatif_strateji_kapat_adayi():
    rep = EffectivenessAuditor().audit(legacy("murat", sentetik_r(80, -0.6, seed=3)), now_ms=NOW)
    assert rep["kapsamlar"]["strateji:legacy_murat:legacy:paper"]["karar"] == "kapat_adayi"


def test_sentetik_cok_deneme_dsr_sikilasir(tmp_path):
    rs = sentetik_r(120, 0.12, seed=5)
    one = deflated_sharpe(rs, 1)
    many = deflated_sharpe(rs, 500)
    assert many["sharpe_esik"] > one["sharpe_esik"] == 0.0 and many["dsr"] < one["dsr"]
    a = EffectivenessAuditor(root=tmp_path)
    assert a.register_trials("swing_trend", 300) == 300 and a.register_trials("swing_trend", 200) == 500
    rep = a.audit(legacy("kemal", rs), now_ms=NOW)
    assert rep["kapsamlar"]["strateji:swing_trend:legacy:paper"]["dsr"]["denemeler"] == 500
    assert expected_max_sharpe(1, 0.01) == 0.0 and expected_max_sharpe(100, 0.01) > 0


def test_sentetik_golge_paper_ve_legacy_birlesik_karsilastirma():
    lc = legacy("kemal", sentetik_r(40, 0.3, seed=7)) + legacy("kemal", sentetik_r(35, 0.1, seed=8), shadow=True)
    uc = [{"strategy": "swing_trend", "r": r, "net_usd": r * 5, "fees_usd": 0.3, "funding_usd": 0.0,
           "horizon": "swing", "closed_ts": NOW} for r in sentetik_r(12, 0.4, seed=9)]
    rep = EffectivenessAuditor().audit(lc, uc, now_ms=NOW)
    cmp = rep["karsilastirma"]["swing_trend"]
    assert cmp["golge_paper"]["n_a"] == 35 and cmp["golge_paper"]["n_b"] == 40
    assert cmp["legacy_birlesik"]["n_b"] == 12 and "30+" in cmp["legacy_birlesik"]["not"]
    assert "sistem:birlesik:paper" in rep["kapsamlar"] and "ufuk:swing:legacy:paper" in rep["kapsamlar"]


def test_sentetik_maliyet_payi_ve_carry_ayri():
    lc = legacy("kemal", [1.0] * 10)
    lc.append({"mgr": "elif", "r": 0.2, "pnl_usd": 1.0, "fee_usd": 0.5, "carry": True, "exit_ts": NOW})
    rep = EffectivenessAuditor().audit(lc, now_ms=NOW)
    k = rep["kapsamlar"]["strateji:swing_trend:legacy:paper"]
    assert abs(k["maliyet_payi"] - 4.0 / 54.0) < 1e-9
    assert "carry" in rep["kapsamlar"] and "strateji:carry:legacy:paper" not in rep["kapsamlar"]


def test_gecersiz_r_atlanir_ve_yazim(tmp_path):
    a = EffectivenessAuditor(root=tmp_path)
    rep = a.audit([{"mgr": "kemal", "r": None}, {"mgr": "kemal", "r": float("nan")}], now_ms=NOW)
    assert rep["ozet"]["islem_sayisi"] == 0
    assert a.write(rep).exists()


def test_sentetik_yaris_csv_ve_komut(tmp_path):
    import json

    import pandas as pd

    from radar import race
    from radar.__main__ import main
    from radar.unified.effectiveness import audit_race
    split = pd.Timestamp("2024-01-01")
    trades = {"kemal": [{"sym": "X", "side": "long", "entry_ts": pd.Timestamp("2023-06-01") + pd.Timedelta(days=i),
                         "exit_ts": pd.Timestamp("2023-06-05") + pd.Timedelta(days=i), "r": r, "hours": 96,
                         "why": "sentetik"} for i, r in enumerate(sentetik_r(40, 0.3, seed=11))],
              "kaan": [{"sym": "Y", "side": "short", "entry_ts": pd.Timestamp("2024-03-01"), "exit_ts": pd.Timestamp("2024-03-02"),
                        "r": -1.0, "hours": 24}]}
    p = tmp_path / "race_islemler.csv"
    assert race.write_trades_csv(p, trades, split) == 41
    rep = audit_race(p)
    assert rep["kapsamlar"]["strateji:swing_trend:legacy:paper"]["n_trades"] == 40
    assert "rejim:egitim:legacy:paper" in rep["kapsamlar"] and "rejim:dogrulama:legacy:paper" in rep["kapsamlar"]
    out = tmp_path / "etkinlik.json"
    assert main(["etkinlik", "--yaris", str(p), "--cikti", str(out)]) == 0
    assert json.loads(out.read_text())["yaris"]["ozet"]["islem_sayisi"] == 41
    assert main(["etkinlik", "--cikti", str(out)]) == 2



def test_unified_paper_promotion_requires_fourteen_days_of_forward_evidence():
    same_day = [
        {"strategy": "swing_trend", "r": 0.5 + (i % 3) * 0.05,
         "net_usd": 2.5, "fees_usd": 0.1, "funding_usd": 0.0,
         "horizon": "swing", "closed_ts": NOW}
        for i in range(120)
    ]
    report = EffectivenessAuditor().audit(unified_closed=same_day, now_ms=NOW)
    item = report["kapsamlar"]["strateji:swing_trend:birlesik:paper"]
    assert item["n_trades"] == 120
    assert item["evidence_window_days"] == 0
    assert item["karar"] == "kanit_yetersiz"
    assert "14 gün" in item["karar_tr"]

    forward = [
        {**row, "closed_ts": NOW - (119 - i) * 12 * 60 * 60 * 1000}
        for i, row in enumerate(same_day)
    ]
    report = EffectivenessAuditor().audit(unified_closed=forward, now_ms=NOW)
    item = report["kapsamlar"]["strateji:swing_trend:birlesik:paper"]
    assert item["evidence_window_days"] >= 14
    assert item["karar"] == "terfi_adayi"
