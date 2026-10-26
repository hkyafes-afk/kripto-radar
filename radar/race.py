"""v9 Faz 1 — 5 yıllık varyant yarışı (kanıt ile karar).

Her yöneticinin şu anki kuralı (taban), tek bir değişiklik yapılmış varyantlarına karşı aynı veride, aynı sert maliyetle
yarışır. Canlı ve test aynı fonksiyonları kullanır (team.apply_variant): kazanan neyse canlıda birebir o çalışır.

Ölçü (kullanıcı hedefi "parayı katla, batmadan"): bileşik büyüme (yıllık %), en büyük düşüş ve ikisinin oranı (MAR),
ayrıca işlem başına ortalama R. Benimseme şartları (hepsi birden):
  1) Doğrulama döneminde (son %30, eğitimde görülmemiş) ort. R tabandan en az +0,02 iyi
  2) Eğitim döneminde (ilk %70) tabandan kötü değil (−0,01R tolerans)
  3) 5 yılın bileşik büyümesi tabandan düşük değil ve MAR tabanın en az %90'ı
  4) Her iki dönemde de yeterli işlem (eğitim ≥ 30, doğrulama ≥ 15)
Aynı aileden (çıkış / giriş / stop) en fazla bir varyant seçilir; farklı ailelerden iki kazanan varsa birleşimi ayrıca
yarışır ve ancak tek başına en iyiden de iyiyse benimsenir. Bütün denemeler Deflated Sharpe ile düzeltilir (çok deneme
yapınca şans eseri iyi görünen sonucu cezalandırır).

Firma geneli iki kural da sınanır: (a) BTC 50 günlük ortalamanın altındayken long yarım boyut (mevcut) vs tamamen yasak,
(b) Genel Müdür'ün coin kanıt sınıfı (B yarım, C gölge) açık vs kapalı.
"""
from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import bt2, learn, quant, team
from .config import ROOT, load_config

FAMILY = {"cikis_iz": "çıkış", "cikis_iz_tp1": "çıkış", "cikis_hedef": "çıkış",
          "retest": "giriş", "adx25": "giriş", "ttm": "giriş", "fitil_stop": "stop"}
TRIALS = {
    "kemal": [[], ["adx25"]],
    "kaan": [[], ["cikis_iz"], ["cikis_iz_tp1"], ["cikis_hedef"], ["retest"], ["fitil_stop"]],
    "selin": [[], ["cikis_iz"], ["cikis_iz_tp1"], ["cikis_hedef"], ["ttm"]],
    "murat": [[], ["cikis_iz"], ["cikis_iz_tp1"], ["cikis_hedef"]],
}
LONG_ONLY = {"kaan", "selin"}          # canlıda short'ları gölgede: sermaye eğrisi long'larla
CLASS_MULT = {"A": 1.0, "B": 0.5, "C": 0.0}


def _avg(trs):
    return float(np.mean([t["r"] for t in trs])) if trs else float("nan")


def _r(x, d=3):
    return None if x is None or x != x else round(float(x), d)


def run(client=None, cfg_path=None, n_coins=30, years=5.0, out_dir: Path | None = None) -> dict:
    t0 = time.time()
    base = load_config(cfg_path)
    base["_learned"] = learn.load_learned()
    base.setdefault("ekip", {})["varyant"] = {}          # taban = varyantsız (yarış kendi denemesini verir)
    if client is None:
        from .okx import OKXClient
        client = OKXClient()
    daily, _, now_ms = bt2.load_data(client, base, n_coins, years, deniz_days=0)
    test_start = pd.Timestamp(now_ms - int(years * 365 * 86400_000), unit="ms", tz="UTC")
    end = pd.Timestamp(now_ms, unit="ms", tz="UTC")
    split = test_start + (end - test_start) * 0.7
    days = pd.date_range(test_start.normalize(), end.normalize(), freq="1D", tz="UTC")
    BF = quant.features(daily["BTC"][1])
    wx = {BF.index[k]: quant.btc_weather(BF, k) for k in range(len(BF))}
    feats = {sym: quant.features(df) for sym, (inst, df, _) in daily.items()}
    mgrs = {m.id: m for m in team.roster(base) if m.analyze}
    ages = {sym: bt2.coin_first_ts(client, sym, inst) for sym, (inst, df, _) in daily.items()}
    print("  coin yaşları: " + ", ".join(f"{s} {a.date() if a is not None else '?'}" for s, a in sorted(ages.items())))

    def trades_for(mid, var):
        cfg = copy.deepcopy(base)
        cfg["_variant"] = {mid: list(var)}
        out = []
        for sym, (inst, df, vol) in daily.items():
            F = feats[sym]
            idx = F.index
            warm = max(int(np.searchsorted(idx, test_start)), 210)

            def extra(k, idx=idx):
                w = wx.get(idx[k]) or {}
                return {"btc_bear": w.get("btc_bear", False), "btc_bull": w.get("btc_bull", False),
                        "w_trend": w.get("trend"), "high_vol": w.get("high_vol"), "vol24_usd": vol}
            out += bt2.run_manager(mgrs[mid], F, sym, inst, 24.0, warm, extra, cfg, first_ts=ages.get(sym))
        return out

    def wmult_fn(bear_long=0.5, classes=None):
        def f(t):
            m = quant.weather_mult({"trend": t.get("weather_trend") or "yatay", "high_vol": t.get("high_vol")},
                                   t["mgr"], t["side"], bear_long=bear_long)
            if bear_long == 0.0 and t["side"] == "long" and t.get("btc_bear") and t["mgr"] != "ayse":
                m = 0.0
            if classes:
                m *= classes.get(t.get("coin_class") or "A", 1.0)
            return m
        return f

    def measure(mid, trs, mult=None):
        eq_trs = [t for t in trs if t["side"] == "long"] if mid in LONG_ONLY else trs
        tr = [t for t in eq_trs if t["entry_ts"] < split]
        va = [t for t in eq_trs if t["entry_ts"] >= split]
        ec = bt2.equity_curve(eq_trs, mgrs[mid].base_risk, days=days, mult_fn=mult or wmult_fn())
        p = bt2.perf(ec["curve"]) if ec else {}
        mar = (p.get("cagr_pct") or 0) / abs(p.get("max_dd_pct") or 1e-9) if p else None
        st = bt2.r_stats(eq_trs)
        return {"n": len(eq_trs), "avg_r": _r(_avg(eq_trs)), "pf": st.get("pf"), "win_pct": st.get("win_pct"),
                "avg_hours": st.get("avg_hours"), "n_train": len(tr), "train_r": _r(_avg(tr)), "n_val": len(va),
                "val_r": _r(_avg(va)), "cagr_pct": p.get("cagr_pct"), "max_dd_pct": p.get("max_dd_pct"),
                "sharpe": p.get("sharpe"), "mar": _r(mar, 2), "_sr": p.get("sr_daily"), "_T": p.get("T"),
                "_skew": p.get("skew"), "_kurt": p.get("kurt"),
                "long_r": _r(_avg([t for t in trs if t["side"] == "long"])),
                "short_r": _r(_avg([t for t in trs if t["side"] == "short"]))}

    def better(c, b):
        def v(x, d=-9.0):
            return d if x is None else x
        return (c["n_train"] >= 30 and c["n_val"] >= 15
                and v(c["val_r"]) >= v(b["val_r"]) + 0.02
                and v(c["train_r"]) >= v(b["train_r"]) - 0.01
                and v(c["cagr_pct"]) >= v(b["cagr_pct"])
                and v(c["mar"]) >= 0.9 * v(b["mar"]))

    results, all_trades, srs = {}, {}, []
    for mid, trials in TRIALS.items():
        if mid not in mgrs:
            continue
        rows = []
        for var in trials:
            trs = trades_for(mid, var)
            m = measure(mid, trs)
            m["variant"] = list(var)
            rows.append(m)
            if m.get("_sr") is not None:
                srs.append(m["_sr"])
            if not var:
                all_trades[mid] = trs
            print(f"  {mid} {'+'.join(var) or 'taban'}: {m['n']} işlem, ort {m['avg_r']}R "
                  f"(eğitim {m['train_r']} / doğrulama {m['val_r']}), yıllık %{m['cagr_pct']}, düşüş %{m['max_dd_pct']}, MAR {m['mar']}")
        b = rows[0]
        winners = {}
        for r in rows[1:]:
            if better(r, b):
                fam = FAMILY[r["variant"][0]]
                if fam not in winners or (r["val_r"] or -9) > (winners[fam]["val_r"] or -9):
                    winners[fam] = r
        chosen = None
        if len(winners) >= 2:
            combo = sorted(v for w in winners.values() for v in w["variant"])
            trs = trades_for(mid, combo)
            m = measure(mid, trs)
            m["variant"] = combo
            m["combo"] = True
            rows.append(m)
            if m.get("_sr") is not None:
                srs.append(m["_sr"])
            best_single = max(winners.values(), key=lambda r: r["val_r"] or -9)
            chosen = m if better(m, b) and (m["val_r"] or -9) >= (best_single["val_r"] or -9) \
                and (m["cagr_pct"] or -99) >= (best_single["cagr_pct"] or -99) else best_single
            print(f"  {mid} birleşim {'+'.join(combo)}: doğrulama {m['val_r']}R, yıllık %{m['cagr_pct']}")
        elif winners:
            chosen = next(iter(winners.values()))
        results[mid] = {"rows": rows, "chosen": chosen["variant"] if chosen else [],
                        "why": (f"{'+'.join(chosen['variant'])}: doğrulama {chosen['val_r']:+.3f}R (taban {b['val_r']:+.3f}R), "
                                f"yıllık %{chosen['cagr_pct']} (taban %{b['cagr_pct']}), MAR {chosen['mar']} (taban {b['mar']})"
                                if chosen else "hiçbir varyant dört şartı birden geçmedi → mevcut kural korunur")}
    # Deflated Sharpe: bütün denemelere göre
    for mid, res in results.items():
        for r in res["rows"]:
            if r.get("_sr") is not None and r.get("_T"):
                r["dsr"], _ = bt2.deflated_sharpe(r["_sr"], r["_T"], r["_skew"] or 0, r["_kurt"] or 3, srs)
    # firma geneli kurallar (taban işlemleriyle)
    firm = {}
    pool = [t for mid, trs in all_trades.items() for t in trs
            if not (mid in LONG_ONLY and t["side"] == "short")]
    risk = {mid: mgrs[mid].base_risk for mid in all_trades}
    C_ONLY = {"A": 1.0, "B": 1.0, "C": 0.0}
    for name, fn in (("taban (ayıda long yarım, sınıf kuralı yok)", wmult_fn(0.5)),
                     ("ayıda long YASAK", wmult_fn(0.0)),
                     ("Genel Müdür kanıt sınıfı açık (B yarım, C gölge)", wmult_fn(0.5, CLASS_MULT)),
                     ("ayıda long yasak + kanıt sınıfı", wmult_fn(0.0, CLASS_MULT)),
                     ("sadece C gölge (B tam boyut)", wmult_fn(0.5, C_ONLY)),
                     ("ayıda long yasak + sadece C gölge", wmult_fn(0.0, C_ONLY))):
        ec = bt2.equity_curve(pool, risk, days=days, mult_fn=fn)
        p = bt2.perf(ec["curve"]) if ec else {}
        tr = [t for t in ec["taken"] if t["entry_ts"] < split] if ec else []
        va = [t for t in ec["taken"] if t["entry_ts"] >= split] if ec else []
        firm[name] = {"cagr_pct": p.get("cagr_pct"), "max_dd_pct": p.get("max_dd_pct"), "sharpe": p.get("sharpe"),
                      "mar": _r((p.get("cagr_pct") or 0) / abs(p.get("max_dd_pct") or 1e-9), 2),
                      "taken": len(ec["taken"]) if ec else 0, "train_r": _r(_avg(tr)), "val_r": _r(_avg(va))}
        print(f"  firma · {name}: yıllık %{p.get('cagr_pct')}, düşüş %{p.get('max_dd_pct')}, {firm[name]['taken']} işlem")
    cls_counts = {c: sum(1 for t in pool if t.get("coin_class") == c) for c in "ABC"}
    cls_r = {c: _r(_avg([t for t in pool if t.get("coin_class") == c])) for c in "ABC"}
    for mid, res in results.items():
        for r in res["rows"]:
            for k in [k for k in r if k.startswith("_")]:
                r.pop(k)
    out = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "years": years,
           "n_coins": len(daily), "coins": sorted(daily), "test_start": str(test_start.date()), "split": str(split.date()),
           "n_trials": len(srs), "managers": results, "firm": firm, "class_counts": cls_counts, "class_avg_r": cls_r,
           "cost": {**bt2.COST, "stop_kayma": "taban 5 bp + günlük ATR'nin %5'i (genç coinde ×1,5)"},
           "duration_s": round(time.time() - t0, 1)}
    d = out_dir or (ROOT / "data")
    d.mkdir(parents=True, exist_ok=True)
    (d / "race.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (d / "YARIS.md").write_text(report_md(out), encoding="utf-8")
    print(report_md(out))
    return out


def report_md(o: dict) -> str:
    L = [f"# v9 Faz 1 — 5 yıllık varyant yarışı ({o['generated']})", "",
         f"{o['n_coins']} coin · {o['test_start']} → bugün · eğitim/doğrulama ayrımı {o['split']} · {o['n_trials']} deneme "
         f"(Deflated Sharpe bütün denemelere göre) · {o['duration_s']:.0f} sn", "",
         "Benimseme: doğrulamada ≥ +0,02R iyi, eğitimde kötü değil, bileşik büyüme düşmüyor, MAR ≥ %90 taban, yeterli işlem.", ""]
    for mid, res in o["managers"].items():
        L += [f"## {mid}", "", "| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |",
              "|---|---|---|---|---|---|---|---|---|"]
        for r in res["rows"]:
            name = "+".join(r["variant"]) or "taban (mevcut)"
            L.append(f"| {name}{' (birleşim)' if r.get('combo') else ''} | {r['n']} | {r['avg_r']} | {r['train_r']} | "
                     f"{r['val_r']} | {r['cagr_pct']} | {r['max_dd_pct']} | {r['mar']} | {r.get('dsr')} |")
        L += ["", f"**Karar:** {res['why']}", ""]
    L += ["## Firma geneli kurallar (taban işlemleriyle, tek havuz)", "", "| Kural | Yıllık % | Maks. düşüş % | MAR | İşlem | Eğitim R | Doğrulama R |",
          "|---|---|---|---|---|---|---|"]
    for k, v in o["firm"].items():
        L.append(f"| {k} | {v['cagr_pct']} | {v['max_dd_pct']} | {v['mar']} | {v['taken']} | {v['train_r']} | {v['val_r']} |")
    L += ["", f"Kanıt sınıfına göre işlemler: A {o['class_counts'].get('A')} (ort. {o['class_avg_r'].get('A')}R), "
              f"B {o['class_counts'].get('B')} (ort. {o['class_avg_r'].get('B')}R), C {o['class_counts'].get('C')} "
              f"(ort. {o['class_avg_r'].get('C')}R)", "",
          "Uyarı: coin evreni bugün hâlâ işlem gören coinler (batanlar yok) → sonuçlar iyimser tarafta.", ""]
    return "\n".join(L)
