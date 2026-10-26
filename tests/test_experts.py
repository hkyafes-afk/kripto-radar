"""v9 uzman ajanlar: görüşler, karne ağırlığı, Analist-Hakem modeli, çeyrek Kelly."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import experts as ex  # noqa: E402
from radar import hakem as hk  # noqa: E402
from radar import team  # noqa: E402
from tests.test_director import OLD, _desk, _kaan  # noqa: E402
from tests.test_team import NOW, qctx  # noqa: E402


def test_gozcu_relative_strength_follows_evidence():
    i = _kaan()
    mom = {"gozcu": {"mode": "momentum", "top_r": 0.3, "bot_r": 0.1}}
    rev = {"gozcu": {"mode": "ters", "top_r": 0.05, "bot_r": 0.2}}
    assert ex.gozcu({"rs_rank": 0.9}, i, mom)["mult"] > 1, "kanıt momentum diyorsa güçlüde büyüt"
    assert ex.gozcu({"rs_rank": 0.2}, i, mom)["mult"] < 1
    assert ex.gozcu({"rs_rank": 0.1}, dict(i, side="short"), mom)["mult"] > 1, "short için en zayıf coin"
    assert ex.gozcu({"rs_rank": 0.9}, i, rev)["mult"] < 1, "kanıt tersini diyorsa güçlüde küçült"
    assert ex.gozcu({"rs_rank": 0.9}, i, None)["mult"] == 1.0, "kanıt yoksa dokunma"
    assert ex.gozcu({}, i, mom)["mult"] == 1.0


def test_balina_trend_and_capitulation():
    i = _kaan()
    good = ex.balina({"oi_chg24": 8.0, "chg24": 5.0, "funding_ann": -15.0}, i,
                     {"ls_acct": 1.2, "ls_top": 1.8, "liq_1h": {"short_usd": 2e6, "long_usd": 1e5}})
    bad = ex.balina({"oi_chg24": -8.0, "chg24": 5.0, "funding_ann": 80.0}, i, {"ls_acct": 3.2, "ls_top": 1.1})
    assert good["mult"] > 1.2 and good["action"] == "büyüt" and len(good["reasons"]) >= 3
    assert bad["mult"] < 0.7 and bad["action"] == "küçült"
    ay = dict(i, mgr="ayse")
    cap = ex.balina({"oi_chg24": -9.0, "funding_ann": -20.0}, ay, {"liq_1h": {"long_usd": 3e6}})
    none = ex.balina({"oi_chg24": 1.0, "funding_ann": 10.0}, ay, {})
    assert cap["mult"] >= 1.3 and none["mult"] <= 0.7, "gerçek kapitülasyonu sahtesinden ayırır"


def test_combine_weights_and_scorecard_learning():
    d, st, L = _desk()
    ops = [ex._op("gozcu", 1.2, ["x"]), ex._op("balina", 0.8, ["y"]), ex._op("hakem", 1.0, ["z"])]
    m, out = ex.combine(ops, d.st, "kaan")
    assert abs(m - (1 + 0.5 * 0.2) * (1 - 0.5 * 0.2)) < 1e-9, "kanıtsız uzman yarım söz"
    # Balina 60 işlemde haklı çıktı (küçült dediği kaybetti, büyüt dediği kazandı) → ağırlığı büyür
    trs = []
    for k in range(60):
        win = k % 2 == 0
        trs.append({"mgr": "kaan", "r": 1.5 if win else -1.0, "experts": [ex._op("balina", 1.3 if win else 0.7, []),
                                                                          ex._op("gozcu", 0.7 if win else 1.3, [])]})
    ex.score_closed(d.st, trs)
    wb, wg = ex.weight(d.st, "kaan", "balina"), ex.weight(d.st, "kaan", "gozcu")
    assert wb > 1.0 and wg < 0.2, (wb, wg)
    ex.score_closed(d.st, trs)
    assert d.st["experts"]["kaan|balina"]["n"] == 60, "aynı işlem iki kez sayılmaz"
    rep = ex.report(d.st)
    assert rep["kaan"]["balina"]["w"] == wb and rep["kaan"]["trader"]["n"] == 60


def test_kelly_scales_with_evidence():
    d, st, L = _desk()
    assert ex.kelly_mult(d, "kaan")[0] == 1.0, "30 işlemden önce dokunmaz"
    for k in range(40):
        L["closed"].append({"mgr": "kaan", "shadow": False, "r": 3.0 if k % 2 == 0 else -1.0})
    up, why = ex.kelly_mult(d, "kaan")
    assert up > 1.0 and "Kelly" in why
    L["closed"] = [{"mgr": "kaan", "shadow": False, "r": 0.2 if k % 3 == 0 else -1.0} for k in range(40)]
    assert ex.kelly_mult(d, "kaan")[0] == 0.5, "kaybettiren küçülür (taban 0,5×)"


def test_hakem_learns_signal_and_gates_noise():
    rng = np.random.default_rng(3)
    feats = list(team.FEAT_KEYS)
    ts = pd.date_range("2021-01-01", periods=1500, freq="1D", tz="UTC")

    def mk(signal: bool):
        trs = []
        for k in range(1500):
            f = {x: float(rng.normal()) for x in feats}
            r = (0.6 * f["vol_ratio"] if signal else 0.0) + rng.normal(0, 1)
            trs.append({"mgr": "kaan", "r": float(r), "feat": f, "entry_ts": ts[k]})
        return trs
    g = hk.fit(mk(True), feats)
    assert g["validated"] and g["val_corr"] > 0.1 and g["top"][0][0] == "vol_ratio"
    n = hk.fit(mk(False), feats)
    assert not n["validated"], "gürültüde söz hakkı yok"
    model = {"features": feats, "groups": {"kaan": g}}
    hi = ex.hakem({"mgr": "kaan", "feat": {**{x: 0.0 for x in feats}, "vol_ratio": 2.5}}, model)
    lo = ex.hakem({"mgr": "kaan", "feat": {**{x: 0.0 for x in feats}, "vol_ratio": -2.5}}, model)
    assert hi["mult"] > 1.1 and lo["mult"] < 0.9
    mute = ex.hakem({"mgr": "kaan", "feat": {"vol_ratio": 2.5}}, {"features": feats, "groups": {"kaan": n}})
    assert mute["mult"] == 1.0 and "söz hakkı yok" in mute["reasons"][0]


def test_desk_applies_expert_mult_and_keeps_opinions_on_trade():
    d, st, L = _desk()
    i = _kaan()
    _, base, _ = d.evaluate(i, NOW, meta=OLD)
    j = dict(i, exp_mult=0.6, experts=[ex._op("balina", 0.5, ["kalabalık"])], feat=team.features_for(qctx(), "long", 0.5))
    _, det, _ = d.evaluate(j, NOW, meta=OLD)
    assert abs(det["risk_pct"] - base["risk_pct"] * 0.6) < 0.05 and det["mults"]["uzman"] == 0.6
    o, _, _ = d.execute([j], NOW, meta=OLD)
    assert o and o[0]["experts"] and o[0]["feat"] and o[0]["mults"]["uzman"] == 0.6


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
