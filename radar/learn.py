"""Öğrenme: yöneticiler kendi işlemlerinden ders çıkarır ama tesadüfe kanmaz.

Üç katman:
  1) Ders (her kapanışta): işlemin en iyi/en kötü noktası (MFE/MAE), çıkış sebebi ve maliyet payından düz Türkçe ders.
  2) İşlem türü öğrenmesi (canlı, saatlik): her "yönetici|yön" türü için gerçek + gölge sonuçları. Gölgedeki tür
     30+ işlemde ort. ≥ +0,10R ve kâr faktörü ≥ 1,2 ise gerçeğe alınır; gerçek tür 30+ işlemde ort. ≤ −0,10R ya da
     kâr faktörü < 0,8 ise gölgeye alınır. (30 işlem: şansın etkisi azalmaya başladığı en küçük örnek.)
  3) Ayar öğrenmesi (haftalık, GitHub Actions): her yöneticinin stop ve iz süren stop katsayısı sınırlı bir ızgarada
     (0,8 / 1,0 / 1,25) 5 yıllık veride denenir. Eğitim (ilk %70) en iyisi, doğrulamada (son %30, hiç görülmemiş) da
     mevcut ayardan en az +0,02R iyiyse ve her iki dönemde pozitifse benimsenir; değilse ayar değişmez.
     Sonuç data/learned.json'a yazılır; canlı motor saatte bir okur.
"""
from __future__ import annotations

import copy
import itertools
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .config import ROOT

MIN_N = 30
DEFAULT_SHADOW = {"kaan|short", "selin|short"}     # 5 yıllık test: masraf sonrası ~0R → gölgeden başlar


def lesson(t: dict) -> str:
    """Kapanan işlem için ders (Türkçe, sade)."""
    r, mfe, mae = t.get("r", 0.0) or 0.0, t.get("mfe_r", 0.0) or 0.0, t.get("mae_r", 0.0) or 0.0
    out = t.get("outcome", "")
    fee, gross = abs(t.get("fee_usd", 0.0) or 0.0), t.get("gross_usd", 0.0) or 0.0
    parts = []
    if out == "hedef":
        parts.append(f"Plan işledi: hedefe ulaştı ({r:+.2f}R).")
    elif out in ("iz stop",) and r > 0:
        parts.append(f"Trend taşındı, iz süren stop kârı korudu ({r:+.2f}R; en iyi nokta +{mfe:.2f}R).")
    elif out in ("stop", "iz stop", "likidasyon"):
        if mfe >= 1.0:
            parts.append(f"Fiyat +{mfe:.2f}R'ye kadar gitti ama geri döndü; kâr alma ya da başabaş daha erken olabilirdi.")
        elif mfe < 0.3:
            parts.append(f"Giriş hemen ters gitti (en iyi nokta yalnızca +{mfe:.2f}R): sahte sinyal ya da kötü zamanlama.")
        else:
            parts.append(f"Stop oldu ({r:+.2f}R). İşlem +{mfe:.2f}R'ye kadar çıkabildi.")
    elif out == "kural çıkışı":
        parts.append(f"Kural gereği çıkıldı: {t.get('exit_note', '')} ({r:+.2f}R).")
    elif out == "süre":
        parts.append(f"Süre doldu ({r:+.2f}R); beklenen hareket gelmedi.")
    elif out == "manuel":
        parts.append(f"Elle kapatıldı ({r:+.2f}R).")
    else:
        parts.append(f"{out} ({r:+.2f}R).")
    if r > 0 and mae >= 0.8:
        parts.append(f"Kazandı ama önce −{mae:.2f}R'ye kadar zorlandı; stop'a çok yaklaştı.")
    if gross > 0 and fee > 0.25 * gross:
        parts.append(f"Komisyon brüt kârın %{fee / gross * 100:.0f}'ini yedi.")
    if abs(t.get("funding_usd", 0.0) or 0.0) >= 0.5 and gross:
        f = -(t.get("funding_usd") or 0.0)
        parts.append(f"Funding {'kazandırdı' if f > 0 else 'maliyet oldu'}: {f:+.2f}$.")
    return " ".join(parts)


def _stats(rs: list[float]) -> dict:
    if not rs:
        return {"n": 0}
    a = np.array(rs, float)
    gl = -a[a <= 0].sum()
    return {"n": int(len(a)), "avg_r": round(float(a.mean()), 3),
            "pf": round(float(a[a > 0].sum() / gl), 2) if gl > 0 else None, "win": round(float((a > 0).mean()) * 100, 1)}


def segment_tick(desk, ledger: dict, since_ms: int, now_ms: int) -> list[str]:
    """İşlem türü öğrenmesi. Döner: Telegram mesajları. desk.st['segments'] günceller."""
    segs = desk.st.setdefault("segments", {})
    groups: dict[str, list] = {}
    for t in ledger["closed"]:
        if not t.get("mgr") or t.get("carry") or (t.get("exit_ts") or 0) < since_ms:
            continue
        groups.setdefault(f"{t['mgr']}|{t['side']}", []).append(t)
    msgs = []
    for key, trs in groups.items():
        cur = (segs.get(key) or {}).get("state")
        shadow_trs = [t for t in trs if t.get("shadow")]
        real_trs = [t for t in trs if not t.get("shadow")]
        mid, side = key.split("|")
        m = desk.by_id.get(mid)
        name = m.name if m else mid
        st_sh, st_re = _stats([t["r"] for t in shadow_trs]), _stats([t["r"] for t in real_trs])
        segs.setdefault(key, {}).update({"shadow": st_sh, "real": st_re, "checked": now_ms})
        is_shadow_now = cur == "golge" or (cur is None and key in DEFAULT_SHADOW)
        if is_shadow_now and st_sh.get("n", 0) >= MIN_N and (st_sh.get("avg_r") or 0) >= 0.10 and (st_sh.get("pf") or 0) >= 1.2:
            segs[key]["state"] = "gercek"
            segs[key]["since"] = now_ms
            msgs.append(f"📈 <b>Öğrenme</b>: {name} {side} işlemleri gölgede kendini kanıtladı "
                        f"({st_sh['n']} işlem, ort. {st_sh['avg_r']:+.2f}R, PF {st_sh['pf']}). Artık gerçek bütçeyle.")
        elif not is_shadow_now and st_re.get("n", 0) >= MIN_N and ((st_re.get("avg_r") or 0) <= -0.10 or (st_re.get("pf") or 9) < 0.8):
            segs[key]["state"] = "golge"
            segs[key]["since"] = now_ms
            msgs.append(f"📉 <b>Öğrenme</b>: {name} {side} işlemleri canlıda kaybettiriyor "
                        f"({st_re['n']} işlem, ort. {st_re['avg_r']:+.2f}R). Gölgeye alındı; kendini kanıtlarsa döner.")
    return msgs


def load_learned(path: Path | None = None) -> dict:
    p = path or (ROOT / "data" / "learned.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


# ── haftalık ayar öğrenmesi (GitHub Actions) ──────────────────────────

GRID = {"stop_k": [0.8, 1.0, 1.25], "trail_k": [0.8, 1.0, 1.25]}
LEARN_MGRS = ("kemal", "kaan", "selin", "murat", "ayse")


def run(client=None, cfg_path=None, n_coins=30, years=5.0, out: Path | None = None) -> dict:
    from . import bt2, quant, team
    from .config import load_config
    import pandas as pd
    t0 = time.time()
    base = load_config(cfg_path)
    prev = load_learned(out)
    cur_all = (prev.get("managers") or {})
    if client is None:
        from .okx import OKXClient
        client = OKXClient()
    daily, _, now_ms = bt2.load_data(client, base, n_coins, years, deniz_days=0)
    test_start = pd.Timestamp(now_ms - int(years * 365 * 86400_000), unit="ms", tz="UTC")
    split = test_start + (pd.Timestamp(now_ms, unit="ms", tz="UTC") - test_start) * 0.7
    BF = quant.features(daily["BTC"][1])
    wx = {BF.index[k]: quant.btc_weather(BF, k) for k in range(len(BF))}
    feats = {sym: quant.features(df) for sym, (inst, df, _) in daily.items()}
    mgrs = {m.id: m for m in team.roster(base) if m.id in LEARN_MGRS and m.analyze}
    result = {"managers": {}, "trials": {}}

    def trades_for(mid, ks, kt):
        cfg = copy.deepcopy(base)
        cfg["_learned"] = {"managers": {mid: {"stop_k": ks, "trail_k": kt}}}
        out_t = []
        for sym, (inst, df, vol) in daily.items():
            F = feats[sym]
            idx = F.index
            warm = max(int(np.searchsorted(idx, test_start)), 210)

            def extra(k, idx=idx):
                w = wx.get(idx[k]) or {}
                return {"btc_bear": w.get("btc_bear", False), "btc_bull": w.get("btc_bull", False),
                        "w_trend": w.get("trend"), "high_vol": w.get("high_vol"), "vol24_usd": vol}
            out_t += bt2.run_manager(mgrs[mid], F, sym, inst, 24.0, warm, extra, cfg)
        return out_t

    def avg(trs):
        return float(np.mean([t["r"] for t in trs])) if trs else float("nan")

    for mid in mgrs:
        cur = cur_all.get(mid) or {"stop_k": 1.0, "trail_k": 1.0}
        rows = []
        for ks, kt in itertools.product(GRID["stop_k"], GRID["trail_k"]):
            trs = trades_for(mid, ks, kt)
            tr = [t for t in trs if t["entry_ts"] < split]
            va = [t for t in trs if t["entry_ts"] >= split]
            a_tr, a_va = avg(tr), avg(va)
            rows.append({"stop_k": ks, "trail_k": kt, "n_train": len(tr), "train_r": round(a_tr, 4) if a_tr == a_tr else None,
                         "n_val": len(va), "val_r": round(a_va, 4) if a_va == a_va else None})
            print(f"  {mid} stop×{ks} iz×{kt}: eğitim {a_tr:+.3f}R ({len(tr)}) · doğrulama {a_va:+.3f}R ({len(va)})")
        result["trials"][mid] = rows
        cur_row = next((r for r in rows if r["stop_k"] == cur.get("stop_k", 1.0) and r["trail_k"] == cur.get("trail_k", 1.0)),
                       next(r for r in rows if r["stop_k"] == 1.0 and r["trail_k"] == 1.0))
        def v(x):
            return x if x is not None else -9.0
        ok = [r for r in rows if r["n_train"] >= MIN_N and r["n_val"] >= 15]
        best = max(ok, key=lambda r: v(r["train_r"])) if ok else cur_row
        adopt = (best is not cur_row and v(best["train_r"]) > v(cur_row["train_r"])
                 and v(best["val_r"]) >= v(cur_row["val_r"]) + 0.02 and v(best["train_r"]) > 0 and v(best["val_r"]) > 0)
        chosen = best if adopt else cur_row
        result["managers"][mid] = {"stop_k": chosen["stop_k"], "trail_k": chosen["trail_k"], "adopted": bool(adopt),
                                   "evidence": {"current": cur_row, "best_train": best},
                                   "why": ("eğitimde en iyi ve doğrulamada mevcut ayardan "
                                           f"{v(best['val_r']) - v(cur_row['val_r']):+.3f}R iyi → benimsendi") if adopt else
                                          "hiçbir değişiklik iki dönemde birden yeterince iyileştirmedi → ayar korundu"}
    result.update({"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "generated_at": now_ms,
                   "years": years, "split": str(split.date()), "n_coins": len(daily),
                   "grid": GRID, "duration_s": round(time.time() - t0, 1)})
    p = out or (ROOT / "data" / "learned.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# Haftalık ayar öğrenmesi — " + result["generated"], "",
          f"Izgara: stop × {GRID['stop_k']}, iz stop × {GRID['trail_k']} · eğitim {result['years']:g} yılın ilk %70'i, "
          f"doğrulama {result['split']} sonrası", "", "| Yönetici | Seçilen | Benimsendi mi | Gerekçe |", "|---|---|---|---|"]
    for mid, r in result["managers"].items():
        md.append(f"| {mid} | stop ×{r['stop_k']}, iz ×{r['trail_k']} | {'evet' if r['adopted'] else 'hayır'} | {r['why']} |")
    (p.parent / "OGRENME.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return result
