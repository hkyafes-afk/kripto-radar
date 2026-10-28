"""Risk motoru: kullanıcının ilkeleri sayıya dökülmüş hali.

  * Sabit risk: her işlemde özsermayenin aynı küçük yüzdesi (tutarlılık, kartopu). Kazanınca/kaybedince keyfi büyütme yok.
  * Durmayı bilmek: günlük ve haftalık zarar sınırı, üst üste zarar arası, özsermaye zirvesinden düşüşte risk yarıya,
    daha derin düşüşte tamamen dur (kaybı geri kazanmaya çalışırken dibi görmemek için).
  * Overtrade: günlük işlem tavanı. İntikam işlemi yok: stop olan coinde aynı yöne bekleme süresi.
  * Kaldıraç dengesi: kaldıraç büyüklüğü belirlemez; risk stop mesafesinden gelir. Kaldıraç yalnızca marjı küçültür,
    ve likidasyon fiyatı stop'un çok ötesinde kalacak kadar düşük seçilir.
  * Çıkış: %12,5 hedef, 2R'de yarısı, 1R'de başabaş, kâr tepeden en fazla %50 geri verilir (merdiveni birkaç basamak
    birden inmeye tahammül sınırı), yapısal iz stop.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone


def utc_day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def utc_week(ts_ms: int) -> str:
    y, w, _ = datetime.fromtimestamp(ts_ms / 1000, timezone.utc).isocalendar()
    return f"{y}-W{w:02d}"


class Risk:
    def __init__(self, cfg: dict, start_equity: float):
        self.cfg = cfg
        self.r = cfg["risk"]
        self.x = cfg["cikis"]
        self.st = {"peak": start_equity, "day": None, "day_start": start_equity, "week": None,
                   "week_start": start_equity, "trades_today": 0, "loss_streak": 0, "pause_until": 0,
                   "halted": False, "halt_reason": "", "last_stop": {}}

    def to_state(self):
        return self.st

    def load_state(self, st: dict):
        self.st.update(st)

    # --- dönem takibi ------------------------------------------------------------------------
    def roll(self, now_ms: int, equity: float):
        d, w = utc_day(now_ms), utc_week(now_ms)
        if self.st["day"] != d:
            self.st.update(day=d, day_start=equity, trades_today=0)
        if self.st["week"] != w:
            self.st.update(week=w, week_start=equity)
        self.st["peak"] = max(self.st["peak"], equity)
        dd = 1 - equity / self.st["peak"]
        if dd >= self.r["zirveden_dusus_dur"] and not self.st["halted"]:
            self.st["halted"] = True
            self.st["halt_reason"] = f"özsermaye zirveden −%{dd * 100:.1f}: kullanıcı onayı olmadan yeni işlem yok"
        return dd

    def risk_frac(self, equity: float) -> float:
        dd = 1 - equity / self.st["peak"] if self.st["peak"] else 0
        f = self.r["islem_riski"]
        return f / 2 if dd >= self.r["zirveden_dusus_yarim"] else f

    # --- giriş kapıları ---------------------------------------------------------------------
    def can_open(self, now_ms: int, equity: float, positions: list[dict], inst: str, side: str,
                 new_risk_usd: float) -> str | None:
        r, st = self.r, self.st
        if st["halted"]:
            return st["halt_reason"]
        if now_ms < st["pause_until"]:
            return f"üst üste {r['ust_uste_zarar']} zarar: ara ({(st['pause_until'] - now_ms) / 3.6e6:.1f} saat kaldı)"
        if equity <= st["day_start"] * (1 - r["gunluk_zarar_durdur"]):
            return f"günlük zarar sınırı (−%{r['gunluk_zarar_durdur'] * 100:g}): bugün yeni işlem yok"
        if equity <= st["week_start"] * (1 - r["haftalik_zarar_durdur"]):
            return f"haftalık zarar sınırı (−%{r['haftalik_zarar_durdur'] * 100:g})"
        if st["trades_today"] >= r["gunluk_max_islem"]:
            return f"günlük işlem tavanı ({r['gunluk_max_islem']}): overtrade yok"
        if len(positions) >= r["max_acik_pozisyon"]:
            return "açık pozisyon tavanı"
        if any(p["inst"] == inst for p in positions):
            return "bu coinde zaten pozisyon var"
        if sum(1 for p in positions if p["side"] == side) >= r["max_ayni_yon"]:
            return "aynı yönde pozisyon tavanı"
        open_risk = sum(p.get("risk_usd_now", p.get("risk_usd", 0)) for p in positions)
        if open_risk + new_risk_usd > equity * r["max_acik_risk"]:
            return "toplam açık risk tavanı"
        ls = st["last_stop"].get(f"{inst}|{side}")
        if ls and now_ms - ls < r["intikam_yasagi_saat"] * 3.6e6:
            return "intikam işlemi yasağı (aynı coinde aynı yön yakın zamanda stop oldu)"
        return None

    def size(self, equity: float, entry: float, sl: float, max_lever_inst: float) -> dict | None:
        """Pozisyon büyüklüğü riskten; kaldıraç likidasyon tamponundan."""
        dist = abs(entry - sl) / entry
        if dist <= 0:
            return None
        risk_usd = equity * self.risk_frac(equity)
        qty = risk_usd / (entry * dist)
        notional = qty * entry
        mmr = self.cfg["yurutme"]["bakim_marji"]
        # likidasyon mesafesi ≈ 1/lev − mmr ≥ (1 + tampon) × stop mesafesi
        need = (1 + self.r["likidasyon_tamponu"]) * dist + mmr
        lev_max = 1 / need if need > 0 else 1
        lev = max(1, min(math.floor(lev_max), self.r["max_kaldirac"], int(max_lever_inst or 20)))
        return {"qty": qty, "notional": notional, "lev": lev, "risk_usd": risk_usd, "stop_yuzde": dist * 100,
                "margin": notional / lev}

    # --- sonuç kaydı ------------------------------------------------------------------------
    def on_open(self):
        self.st["trades_today"] += 1

    def on_close(self, now_ms: int, inst: str, side: str, pnl: float, reason: str):
        if pnl < 0:
            self.st["loss_streak"] += 1
            if reason in ("stop", "likidasyon"):
                self.st["last_stop"][f"{inst}|{side}"] = now_ms
            if self.st["loss_streak"] >= self.r["ust_uste_zarar"]:
                self.st["pause_until"] = now_ms + self.r["ara_saat"] * 3.6e6
                self.st["loss_streak"] = 0
        else:
            self.st["loss_streak"] = 0

    # --- çıkış yönetimi ---------------------------------------------------------------------
    def manage(self, p: dict, px: float, now_ms: int, swing: float | None) -> list[tuple]:
        """Açık pozisyon için yapılacaklar: [("stop", yeni_sl, neden) | ("kismi", oran, neden) | ("kapat", neden)].
        Yalnızca kurala bağlı değişiklik: her tikte keyfi müdahale (overwatch) yok."""
        acts = []
        L = p["side"] == "long"
        sgn = 1 if L else -1
        R = p["r_dist"]                                 # fiyat cinsinden 1R
        prof = (px - p["entry"]) * sgn
        p["mfe"] = max(p.get("mfe", 0.0), prof)
        p["mae"] = min(p.get("mae", 0.0), prof)
        peak = p["mfe"]
        x = self.x
        new_sl = p["sl"]
        why = None
        if not p.get("partial_done") and prof >= x["kismi_r"] * R:
            acts.append(("kismi", x["kismi_oran"], f"{x['kismi_r']:g}R: yarısı satıldı"))
            p["partial_done"] = True
        if peak >= x["basabas_r"] * R:
            fee_buf = p["entry"] * 2 * self.cfg["yurutme"]["ucret_taker"]
            be = p["entry"] + sgn * fee_buf
            if (be - new_sl) * sgn > 0:
                new_sl, why = be, f"{x['basabas_r']:g}R: stop başabaşa"
        if peak >= x["geri_verme_baslangic_r"] * R:
            keep = p["entry"] + sgn * peak * (1 - x["geri_verme"])
            if (keep - new_sl) * sgn > 0:
                new_sl, why = keep, f"geri verme sınırı: tepe kârın %{(1 - x['geri_verme']) * 100:.0f}'si korunuyor"
        if swing is not None and peak >= x["basabas_r"] * R and (swing - new_sl) * sgn > 0 and (px - swing) * sgn > 0:
            new_sl, why = swing, f"yapısal iz stop ({x['iz_yapi']} son {'dip' if L else 'tepe'})"
        if (new_sl - p["sl"]) * sgn > 1e-12:
            acts.append(("stop", new_sl, why))
        if now_ms - p["opened"] > x["max_sure_saat"] * 3.6e6 and prof < x["basabas_r"] * R:
            acts.append(("kapat", f"plan süresi doldu ({x['max_sure_saat']} saat), gerekçe gerçekleşmedi"))
        return acts
