"""Öğrenme ve dürüst ölçüm.

Gölge sinyaller: puanı eşiğe yakın ve tetikli HER sinyal, işlem açılsa da açılmasa da, aynı stop/hedef planıyla takip edilir.
Sonuç R'si MALİYETLER DÜŞÜLEREK hesaplanır: giriş + çıkış taker ücreti, spread/kayma tahmini, süre boyunca fonlama.

Kıyaslamalar (her Polat sinyalinin yanına aynı anda, aynı stop mesafesi ve aynı maliyetle):
  rastgele   → yönü yazı-tura
  trend_naif → yalnızca büyük zaman dilimi trend yönü ("sadece trende gir")
  ayrıca BTC al-tut (Polat başladığı andan beri)
Polat bunları geçemiyorsa okumaların bir değeri yok demektir; rapor bunu açıkça yazar.

Ağırlık öğrenme (walk-forward, örneklem dışı):
  örnekler zamana göre sıralanır; ilk %70 eğitim, son %30 doğrulama. Bir okumanın katsayısı ancak
  (a) eğitimde ve doğrulamada yeterli örnek varsa (min_ornek, min_ornek/3) ve
  (b) iki dönemde de etkisinin yönü aynıysa değişir. Aksi halde 1,0 kalır. Katsayı küçültülür (n/(n+50)).
"""
from __future__ import annotations

import json
import random
import uuid
from pathlib import Path


class Learner:
    def __init__(self, cfg: dict, root: Path):
        self.cfg = cfg
        self.L = cfg["ogrenme"]
        self.root = Path(root)
        self.active: list[dict] = []
        self.last_rec: dict[str, int] = {}
        self.mult: dict[str, float] = {}
        self.cat_mult: dict[str, float] = {}
        self.meta: dict = {"guncelleme": 0, "ornek": 0, "uygulanan": 0, "reddedilen": 0}
        self.rng = random.Random(1907)
        self.resolved_file = self.root / "gunluk" / "golge.jsonl"
        self.weights_file = self.root / "ogrenme.json"
        self.bench: dict = {"btc_baslangic": None, "baslangic_ts": None}
        if self.weights_file.exists():
            js = json.loads(self.weights_file.read_text(encoding="utf-8"))
            self.mult, self.cat_mult, self.meta = js.get("carpan", {}), js.get("kategori_carpan", {}), js.get("meta", self.meta)
            self.bench = js.get("kiyas", self.bench)

    # --- durum ------------------------------------------------------------------------------
    def to_state(self):
        return {"active": self.active, "last_rec": self.last_rec}

    def load_state(self, st: dict | None):
        if st:
            self.active = st.get("active", [])
            self.last_rec = st.get("last_rec", {})

    def save_weights(self):
        self.weights_file.parent.mkdir(parents=True, exist_ok=True)
        self.weights_file.write_text(json.dumps({"carpan": self.mult, "kategori_carpan": self.cat_mult, "meta": self.meta,
                                                 "kiyas": self.bench}, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- maliyet ----------------------------------------------------------------------------
    def cost_r(self, entry: float, r_dist: float, spread_bp: float | None, funding: float | None, hours: float, d: int) -> float:
        y = self.cfg["yurutme"]
        fee = 2 * y["ucret_taker"] * entry
        slip = 2 * entry * ((spread_bp or 2.0) / 2 + 2.0) / 1e4        # her tarafta yarım spread + 2 bp kayma
        fund = (funding or 0.0) * entry * (hours / 8.0) * d            # long pozitif fonlamada öder
        return (fee + slip + fund) / r_dist if r_dist else 0.0

    # --- kayıt ------------------------------------------------------------------------------
    def record(self, c, dec: dict, plan: dict, taken: bool, now_ms: int, trend_dir: int):
        d = dec["yon"]
        if not d or abs(dec["S"]) < self.L["golge_min_puan"] or not dec.get("tetik_yonde"):
            return
        key = f"{c.inst}|{d}"
        if now_ms - self.last_rec.get(key, 0) < self.L["golge_aralik_dk"] * 60_000 and not taken:
            return
        self.last_rec[key] = now_ms
        entry, sl = plan["giris"], plan["stop"]
        r = abs(entry - sl)
        if not r:
            return
        base = {"inst": c.inst, "entry": entry, "r": r, "ts": now_ms, "spread_bp": c.spread_bp(),
                "funding": c.funding, "mfe": 0.0}
        tp_pct = self.cfg["cikis"]["hedef_yuzde"] / 100
        def mk(tip, dd, extra):
            return {**base, "id": uuid.uuid4().hex[:10], "tip": tip, "d": dd, "sl": entry - dd * r,
                    "tp": entry * (1 + dd * tp_pct), **extra}
        self.active.append(mk("polat", d, {"S": dec["S"], "oylar": dec["oylar"], "kategoriler": dec["kategoriler"],
                                           "tetik": dec.get("tetik_yonde"), "taken": taken, "engel": dec.get("engel")}))
        self.active.append(mk("rastgele", self.rng.choice((1, -1)), {}))
        if trend_dir:
            self.active.append(mk("trend_naif", trend_dir, {}))

    def on_price(self, inst: str, px: float, ts: int) -> list[dict]:
        done = []
        ufuk = self.L["golge_ufuk_saat"] * 3_600_000
        keep = []
        for x in self.active:
            if x["inst"] != inst:
                keep.append(x)
                continue
            d = x["d"]
            x["mfe"] = max(x["mfe"], (px - x["entry"]) * d / x["r"])
            hours = (ts - x["ts"]) / 3.6e6
            gross = None
            why = None
            if (px - x["sl"]) * d <= 0:
                gross, why = -1.0, "stop"
            elif (px - x["tp"]) * d >= 0:
                gross, why = (x["tp"] - x["entry"]) * d / x["r"], "hedef"
            elif ts - x["ts"] >= ufuk:
                gross, why = (px - x["entry"]) * d / x["r"], "ufuk"
            if gross is None:
                keep.append(x)
                continue
            x["R"] = round(gross - self.cost_r(x["entry"], x["r"], x["spread_bp"], x["funding"], hours, d), 4)
            x["R_brut"] = round(gross, 4)
            x["sonuc"] = why
            x["kapanis_ts"] = ts
            done.append(x)
        self.active = keep
        if done:
            self.resolved_file.parent.mkdir(parents=True, exist_ok=True)
            with self.resolved_file.open("a", encoding="utf-8") as f:
                for x in done:
                    f.write(json.dumps(x, ensure_ascii=False) + "\n")
        return done

    def resolved(self) -> list[dict]:
        if not self.resolved_file.exists():
            return []
        out = []
        for line in self.resolved_file.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return out

    # --- walk-forward ağırlık güncellemesi ----------------------------------------------------
    @staticmethod
    def _edge(rows, key, getter):
        agree, against = [], []
        for x in rows:
            v = getter(x, key)
            if v is None:
                continue
            a = v * x["d"]
            if a >= 0.3:
                agree.append(x["R"])
            elif a <= -0.3:
                against.append(x["R"])
        if not agree or not against:
            return None, len(agree), len(against)
        return sum(agree) / len(agree) - sum(against) / len(against), len(agree), len(against)

    def update(self, now_ms: int) -> dict:
        rows = sorted((x for x in self.resolved() if x["tip"] == "polat"), key=lambda x: x["ts"])
        self.meta.update(guncelleme=now_ms, ornek=len(rows))
        mn = self.L["min_ornek"]
        if len(rows) < mn * 2:
            self.save_weights()
            return {"durum": f"örnek yetersiz ({len(rows)} < {mn * 2})"}
        cut = int(len(rows) * (1 - self.L["dogrulama_orani"]))
        tr, va = rows[:cut], rows[cut:]
        feats = set()
        for x in rows:
            feats.update(x.get("oylar", {}).keys())
        new, applied, rejected = {}, 0, 0
        lo, hi = self.L["carpan_alt"], self.L["carpan_ust"]
        get_v = lambda x, k: (x.get("oylar") or {}).get(k)
        for f in feats:
            e_tr, a1, b1 = self._edge(tr, f, get_v)
            e_va, a2, b2 = self._edge(va, f, get_v)
            n_tr, n_va = min(a1, b1), min(a2, b2)
            if e_tr is None or e_va is None or n_tr < mn or n_va < max(mn // 3, 5):
                continue
            if (e_tr > 0) != (e_va > 0):
                rejected += 1                       # eğitimde işe yarayıp doğrulamada yaramıyor → aşırı uydurma, uygulanmaz
                continue
            shrink = n_tr / (n_tr + 50)
            new[f] = round(max(lo, min(hi, 1 + max(-0.75, min(1.0, e_tr)) * shrink)), 3)
            applied += 1
        get_c = lambda x, k: (x.get("kategoriler") or {}).get(k)
        cats = {}
        for cat in ("trend", "seviye", "formasyon", "momentum", "hacim", "uyusmazlik", "turev", "akis", "piyasa"):
            e_tr, a1, b1 = self._edge(tr, cat, get_c)
            e_va, a2, b2 = self._edge(va, cat, get_c)
            if e_tr is None or e_va is None or min(a1, b1) < mn or min(a2, b2) < max(mn // 3, 5):
                continue
            if (e_tr > 0) == (e_va > 0):
                n = min(a1, b1)
                cats[cat] = round(max(lo, min(hi, 1 + max(-0.75, min(1.0, e_tr)) * n / (n + 50))), 3)
        self.mult, self.cat_mult = new, cats
        self.meta.update(uygulanan=applied, reddedilen=rejected, egitim=len(tr), dogrulama=len(va))
        self.save_weights()
        return {"durum": "güncellendi", "uygulanan": applied, "reddedilen": rejected}

    # --- kıyas raporu -----------------------------------------------------------------------
    def benchmark_report(self, btc_px: float | None, polat_ret_pct: float | None) -> dict:
        rows = self.resolved()
        def st(xs):
            if not xs:
                return {"n": 0}
            rs = [x["R"] for x in xs]
            return {"n": len(xs), "ort_R_net": round(sum(rs) / len(rs), 3), "kazanma": round(sum(r > 0 for r in rs) / len(rs) * 100, 1),
                    "toplam_R_net": round(sum(rs), 2)}
        out = {"polat_tum_sinyaller": st([x for x in rows if x["tip"] == "polat"]),
               "polat_girilen": st([x for x in rows if x["tip"] == "polat" and x.get("taken")]),
               "polat_girilmeyen": st([x for x in rows if x["tip"] == "polat" and not x.get("taken")]),
               "rastgele": st([x for x in rows if x["tip"] == "rastgele"]),
               "trend_naif": st([x for x in rows if x["tip"] == "trend_naif"])}
        b0 = self.bench.get("btc_baslangic")
        out["btc_al_tut_yuzde"] = round((btc_px / b0 - 1) * 100, 2) if b0 and btc_px else None
        out["polat_ozsermaye_yuzde"] = round(polat_ret_pct, 2) if polat_ret_pct is not None else None
        n = out["polat_tum_sinyaller"]["n"]
        k = self.L["kanit_islem"]
        if n < k:
            out["hukum"] = f"kanıt yetersiz: {n}/{k} sonuçlanmış sinyal. Bu sayıdan önce hiçbir sonuç anlamlı değildir."
        else:
            p = out["polat_tum_sinyaller"]["ort_R_net"]
            rr = out["rastgele"].get("ort_R_net")
            tn = out["trend_naif"].get("ort_R_net")
            beats = [nm for nm, v in (("rastgele", rr), ("trend_naif", tn)) if v is not None and p > v]
            out["hukum"] = (f"Polat net {p}R; geçtiği kıyaslar: {', '.join(beats) or 'hiçbiri'}"
                            + ("" if len(beats) == 2 else " → okumaların üstünlüğü kanıtlanmadı"))
        return out
