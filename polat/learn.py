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
import math
import random
import time
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
        self.online: dict[str, float] = {}
        self.paths: dict[str, dict] = {}
        self.ab = {"ogrenilmis": [], "temel": []}
        self.adjust: dict = {}
        self.risk = None
        self.lessons_file = self.root / "gunluk" / "dersler.jsonl"
        if self.weights_file.exists():
            js = json.loads(self.weights_file.read_text(encoding="utf-8"))
            self.mult, self.cat_mult, self.meta = js.get("carpan", {}), js.get("kategori_carpan", {}), js.get("meta", self.meta)
            self.bench = js.get("kiyas", self.bench)
            self.online, self.paths = js.get("cevrimici", {}), self._migrate_paths(js.get("yollar", {}))
            self.ab = js.get("ab", self.ab)
            self.adjust = js.get("ayar", {})

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
                                                 "kiyas": self.bench, "cevrimici": self.online, "yollar": self.paths,
                                                 "ab": self.ab, "ayar": self.adjust}, ensure_ascii=False, indent=1),
                                     encoding="utf-8")

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
            sl_ = entry - dd * r
            return {**base, "id": uuid.uuid4().hex[:10], "tip": tip, "d": dd, "sl": sl_,
                    "tp": entry * (1 + dd * tp_pct), "kalan": 1.0, "R_kismi": 0.0,
                    "p": {"side": "long" if dd > 0 else "short", "entry": entry, "sl": sl_, "r_dist": r, "opened": now_ms},
                    **extra}
        self.active.append(mk("polat", d, {"S": dec["S"], "S_temel": dec.get("S_temel"), "oylar": dec["oylar"],
                                           "kategoriler": dec["kategoriler"], "tetik": dec.get("tetik_yonde"),
                                           "taken": taken, "engel": dec.get("engel"), "yol": dec.get("yol")}))
        self.active.append(mk("rastgele", self.rng.choice((1, -1)), {}))
        if trend_dir:
            self.active.append(mk("trend_naif", trend_dir, {}))

    def on_price(self, inst: str, px: float, ts: int, swing=None) -> list[dict]:
        """Gölge sinyaller GERÇEK İŞLEM GİBİ yönetilir: aynı risk motoru (1R başabaş, 2R yarısı, %50 geri verme sınırı,
        yapısal iz stop, plan süresi). Sonuç R'si maliyetler düşülerek yazılır."""
        done = []
        ufuk = self.cfg["cikis"]["max_sure_saat"] * 3_600_000
        keep = []
        for x in self.active:
            if x["inst"] != inst:
                keep.append(x)
                continue
            d = x["d"]
            r = x["r"]
            pp = x.setdefault("p", {"side": "long" if d > 0 else "short", "entry": x["entry"], "sl": x["sl"],
                                     "r_dist": r, "opened": x["ts"]})
            x["mfe"] = max(x["mfe"], (px - x["entry"]) * d / r)
            if self.risk is not None:
                sw = swing(pp["side"], pp["opened"]) if swing else None
                for act in self.risk.manage(pp, px, ts, sw):
                    if act[0] == "kismi" and x["kalan"] > 0.5:
                        part = act[1] * x["kalan"]
                        x["R_kismi"] += part * (px - x["entry"]) * d / r
                        x["kalan"] -= part
                    elif act[0] == "stop":
                        pp["sl"] = act[1]
            sl = pp["sl"]
            hours = (ts - x["ts"]) / 3.6e6
            why = None
            exit_px = None
            if (px - sl) * d <= 0:
                exit_px, why = sl, ("iz stop" if (sl - x["entry"]) * d > 0 else "stop")
            elif (px - x["tp"]) * d >= 0:
                exit_px, why = x["tp"], "hedef"
            elif ts - x["ts"] >= ufuk:
                exit_px, why = px, "sure"
            if why is None:
                keep.append(x)
                continue
            gross = x["R_kismi"] + x["kalan"] * (exit_px - x["entry"]) * d / r
            x["R"] = round(gross - self.cost_r(x["entry"], r, x["spread_bp"], x["funding"], hours, d), 4)
            x["R_brut"] = round(gross, 4)
            x["sonuc"] = why
            x["kapanis_ts"] = ts
            x.pop("p", None)
            done.append(x)
        self.active = keep
        if done:
            self.resolved_file.parent.mkdir(parents=True, exist_ok=True)
            with self.resolved_file.open("a", encoding="utf-8") as f:
                for x in done:
                    f.write(json.dumps(x, ensure_ascii=False) + "\n")
            for x in done:
                if x["tip"] == "polat":
                    self.learn_from(x, real=False)
        return done

    # --- her sonuçtan ders --------------------------------------------------------------------
    def learn_from(self, x: dict, real: bool):
        """Tek bir sonuçtan (gerçek işlem ya da gölge sinyal) hemen ders çıkar:
          1. çevrimiçi katsayı: kazanan işlemde yönle aynı oyu veren okumanın katsayısı artar, kaybedende azalır
          2. yol defteri: bu yol (tetik ailesi + yön + baskın kategoriler + BTC rejimi) sonuçları biriktirir;
             zarar ettiren yoldan dönülür (kaçın), kâr ettiren yol benimsenir (eşik indirimi)
          3. A/B koruması: öğrenilmiş puanla seçilenler, öğrenilmemiş puanla seçilenlerden kötüyse ders geri alınır"""
        L = self.L
        R = max(-3.0, min(3.0, x["R"]))
        eta = L["ders_hizi"] * (2 if real else 1)
        lo, hi = L["carpan_alt"], L["carpan_ust"]
        for f, v in (x.get("oylar") or {}).items():
            a = v * x["d"]
            if abs(a) < 0.3:
                continue
            cur = self.online.get(f, 1.0)
            self.online[f] = round(max(lo, min(hi, cur * math.exp(eta * R * (1 if a > 0 else -1)))), 4)
        path = x.get("yol")
        if path:
            e = self.paths.setdefault(path, {"n": 0, "sumR": 0.0, "gercek": 0})
            e["n"] += 1
            e["sumR"] = round(e["sumR"] + x["R"], 4)
            e["gercek"] += int(real)
            score = e["sumR"] / (e["n"] + L["yol_kuculme"])
            prev = e.get("karar")
            e["karar"] = ("kacin" if score <= -L["yol_esik"] else "benimse" if score >= L["yol_esik"] else None) \
                if e["n"] >= L["yol_min"] else None
            if e["karar"] != prev:
                self._lesson({"olay": "yol", "yol": path, "karar": e["karar"], "n": e["n"], "ort_R": round(e["sumR"] / e["n"], 3)})
        # A/B: bu sinyal, öğrenilmiş ve temel puanla ayrı ayrı "girilir" miydi?
        esik = self.cfg["karar"]["esik"]
        if x.get("S") is not None and abs(x["S"]) >= esik:
            self.ab["ogrenilmis"].append(x["R"])
        if x.get("S_temel") is not None and abs(x["S_temel"]) >= esik:
            self.ab["temel"].append(x["R"])
        for k in self.ab:
            self.ab[k] = self.ab[k][-L["ab_pencere"]:]
        a, b = self.ab["ogrenilmis"], self.ab["temel"]
        if len(a) >= L["ab_min"] and len(b) >= L["ab_min"]:
            ma, mb = sum(a) / len(a), sum(b) / len(b)
            if ma < mb - L["ab_tolerans"]:
                self._lesson({"olay": "geri_alindi", "ogrenilmis_ort_R": round(ma, 3), "temel_ort_R": round(mb, 3),
                              "neden": "öğrenilmiş katsayılar temel halden kötü sonuç verdi"})
                self.online = {}
                self.ab = {"ogrenilmis": [], "temel": []}
        self.save_weights()

    def _migrate_paths(self, paths: dict) -> dict:
        """Eski 4 parçalı yolları (tetik|yön|kategoriler|btc) yeni 3 parçalıya (tetik|yön|btc) toplar.
        n ve toplam R toplanabilir olduğu için geçmiş birikim kaybolmaz; hüküm yeni eşiklerle yeniden verilir."""
        if not any(k.count("|") == 3 for k in paths):
            return paths
        L, out = self.L, {}
        for k, v in paths.items():
            p = k.split("|")
            nk = f"{p[0]}|{p[1]}|{p[3]}" if len(p) == 4 else k
            e = out.setdefault(nk, {"n": 0, "sumR": 0.0, "gercek": 0})
            e["n"] += v.get("n", 0)
            e["sumR"] = round(e["sumR"] + v.get("sumR", 0.0), 4)
            e["gercek"] += v.get("gercek", 0)
        for nk, e in out.items():
            score = e["sumR"] / (e["n"] + L["yol_kuculme"])
            e["karar"] = ("kacin" if score <= -L["yol_esik"] else "benimse" if score >= L["yol_esik"] else None) \
                if e["n"] >= L["yol_min"] else None
        return out

    def path_rule(self, path: str | None) -> str | None:
        e = self.paths.get(path or "")
        return e.get("karar") if e else None

    def _lesson(self, rec: dict):
        rec["ts"] = int(time.time() * 1000)
        self.lessons_file.parent.mkdir(parents=True, exist_ok=True)
        with self.lessons_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def combined_mult(self) -> dict:
        """Karar için katsayı: walk-forward katsayısı × çevrimiçi katsayı."""
        keys = set(self.mult) | set(self.online)
        lo, hi = self.L["carpan_alt"], self.L["carpan_ust"]
        return {k: max(lo, min(hi, self.mult.get(k, 1.0) * self.online.get(k, 1.0))) for k in keys}

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
