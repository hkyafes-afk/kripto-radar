"""Risk müdürü (CIO) ve işlem masası.

Görevler:
  - Sermaye: başlangıç bakiyesi yöneticilere eşit dağıtılır; haftalık performansa göre yeniden dağıtılır.
  - Onay: her fikir firma limitlerinden geçer (günlük kayıp, zirveden düşüş, toplam risk, coin başı risk,
    net yön riski, teminat). İşlem sayısı sınırı YOK; sınırlar risk cinsinden.
  - Büyüklük: risk = yönetici sermayesi × (taban + (tavan − taban) × güven). Kayıp serisinde yarıya iner.
  - Giriş: piyasa emri (kitaptan, VWAP derinlik) ya da geri çekilmede limit emir (dolarsa maker ücret).
  - Yedek kulübesi: zirvesinden limitinden fazla düşen yönetici gölgede (sermayesiz) işlem yapar;
    gölgede kendini kanıtlarsa geri döner.
  - Elif: spot + vadeli short funding pozisyonlarını açar/kapatır.
  - Günlük (journal): her karar gerekçesiyle kaydedilir.
"""
from __future__ import annotations

import math
import time
import uuid

import numpy as np

from . import ledger as lg
from . import director, experts, learn, macro, quant
from . import team


def sym_side(side: str) -> str:
    return "long (yükseliş)" if side == "long" else "short (düşüş)"


def firm_cfg(cfg: dict) -> dict:
    f = {"gunluk_kayip_yuzde": 3.0, "max_dusus_yuzde": 15.0, "max_isi_yuzde": 10.0, "coin_risk_yuzde": 2.5,
         "yon_risk_yuzde": 6.0, "max_nominal_kat": 2.0, "max_kaldirac": 5, "tekrar_bekleme_mum": 3,
         "yeniden_dagitim_gun": 7, "dagitim_min_islem": 15, "yedekten_donus_islem": 10, "yedekten_donus_r": 0.1,
         "kayip_serisi": 4, "seri_carpan": 0.5,
         "carry_max_cift": 3, "carry_min_yillik": 15, "carry_min_pozitif": 80, "carry_min_istikrar": 45,
         "carry_cikis_yillik": 5, "carry_max_gun": 45, "kume_risk_yuzde": 4.0, "kume_korelasyon": 0.7,
         "sinirlar_aktif": True, "durdurma_aktif": True, "yedek_kulubesi": True,
         "gunluk_ek_hak_yuzde": 1.5, "gunluk_ek_hak_sayisi": 1,
         # v9 Faz 0 — Genel Müdür (her zaman açık; demo limitlerinden bağımsız güvenlik kuralları)
         "carpisma_yasak": True, "coin_tavan_yuzde": 1.25, "coin_tavan_min_oran": 0.25,
         "sinif_a_gun": 1095, "sinif_c_gun": 365, "ioc_bant_bp": 10, "ioc_min_dolum": 0.5, "dongu_uyari_sn": 10}
    f.update(((cfg.get("ekip") or {}).get("firma")) or {})
    return f


class Desk:
    def __init__(self, cfg: dict, state: dict, ledger: dict):
        self.cfg, self.state, self.ledger = cfg, state, ledger
        self.mgrs = team.roster(cfg)
        self.by_id = {m.id: m for m in self.mgrs}
        self.fc = firm_cfg(cfg)
        st = state.setdefault("desk", {})
        start = float(lg.demo_cfg(cfg)["baslangic_bakiye"])
        st.setdefault("start", start)
        st.setdefault("started", int(time.time() * 1000))
        alloc = st.setdefault("alloc", {})
        self.budget = float(((cfg.get("ekip") or {}).get("butce") or {}).get("yonetici_usd") or start / len(team.ROSTER))
        for m in self.mgrs:
            alloc.setdefault(m.id, self.budget)
        st.setdefault("adj", {})
        st.setdefault("peak", {})
        st.setdefault("bench", {})
        st.setdefault("last_rebalance", int(time.time() * 1000))
        st.setdefault("journal", [])
        ledger.setdefault("orders", [])
        self.st = st
        self.env: dict = {}       # motorun verdiği ortam: hava, olay takvimi, korku-açgözlülük, korelasyon

    # ── muhasebe ─────────────────────────────────────────────────────
    def mgr_trades(self, mid: str, shadow: bool = False):
        cl = [t for t in self.ledger["closed"] if t.get("mgr") == mid and bool(t.get("shadow")) == shadow]
        op = [t for t in self.ledger["open"] if t.get("mgr") == mid and bool(t.get("shadow")) == shadow]
        return cl, op

    def capital(self, mid: str) -> float:
        return self.st["alloc"].get(mid, 0.0) + self.st["adj"].get(mid, 0.0)

    def equity(self, mid: str) -> float:
        cl, op = self.mgr_trades(mid)
        p = lg.pnl_parts(cl, op)
        return self.capital(mid) + p["realized"] + p["unrealized"]

    def firm_equity(self) -> float:
        return sum(self.equity(m.id) for m in team.ROSTER if m.id in self.st["alloc"])

    def mgr_free(self, mid: str) -> float:
        cl, op = self.mgr_trades(mid)
        p = lg.pnl_parts(cl, op)
        reserved = sum(o["margin_usd"] for o in self.ledger["orders"] if o["mgr"] == mid and not o.get("shadow"))
        return self.capital(mid) + p["realized"] - p["in_use"] - reserved

    def benched(self, mid: str) -> bool:
        return bool(self.fc.get("yedek_kulubesi", True)) and mid in self.st["bench"]

    def segment(self, mid: str, side: str) -> str | None:
        """Öğrenme: işlem türü (yönetici|yön) canlı kanıta göre 'gercek' ya da 'golge' olarak açılıp kapanır."""
        key = f"{mid}|{side}"
        st = (self.st.get("segments") or {}).get(key, {}).get("state")
        if st is None and key in learn.DEFAULT_SHADOW:
            return "golge"          # kanıtlanana kadar gölgede başlayan türler (5 yıllık testte eksi ya da ~0R)
        return st

    def loss_streak(self, mid: str) -> int:
        cl, _ = self.mgr_trades(mid)
        n = 0
        for t in sorted(cl, key=lambda x: x.get("exit_ts") or 0, reverse=True):
            if t.get("r", 0) <= 0:
                n += 1
            else:
                break
        return n

    def journal(self, mid, sym, tf, side, action, reason):
        j = self.st["journal"]
        j.append({"ts": int(time.time() * 1000), "mgr": mid, "sym": sym, "tf": tf, "side": side,
                  "action": action, "reason": reason[:220]})
        del j[:-300]

    # ── firma riski ──────────────────────────────────────────────────
    def _live_open(self):
        return [t for t in self.ledger["open"] if not t.get("shadow")]

    def _order_risk(self, o) -> float:
        return o["qty"] * abs(o["idea"]["entry"] - o["idea"]["sl"])

    def heat(self) -> float:
        return (sum(lg.open_risk(t) for t in self._live_open())
                + sum(self._order_risk(o) for o in self.ledger["orders"] if not o.get("shadow")))

    def coin_risk(self, inst: str) -> float:
        return (sum(lg.open_risk(t) for t in self._live_open() if t["inst"] == inst)
                + sum(self._order_risk(o) for o in self.ledger["orders"] if o["idea"]["inst"] == inst and not o.get("shadow")))

    def side_risk(self, side: str) -> float:
        return (sum(lg.open_risk(t) for t in self._live_open() if t["side"] == side and not t.get("carry"))
                + sum(self._order_risk(o) for o in self.ledger["orders"] if o["idea"]["side"] == side and not o.get("shadow")))

    # ── karar ────────────────────────────────────────────────────────
    def evaluate(self, idea: dict, now_ms: int, quote=None, meta=None, book=None, halted: str | None = None):
        """Fikri değerlendirir. Döner: ("market"|"limit"|"red", ayrıntı, sebep)."""
        m = self.by_id.get(idea["mgr"])
        if not m:
            return "red", None, "yönetici pasif"
        sym, tf, side = idea["sym"], idea["tf"], idea["side"]
        seg = self.segment(m.id, side)
        full = bool((m.extra or {}).get("tam_yetki"))     # 👑 Baş Trader: Genel Müdür kısıtları yok, hep gerçek işlem
        shadow = False if full else (self.benched(m.id) or (seg == "golge")
                                     or (bool(idea.get("shadow_only")) and seg != "gercek"))
        if any(t["mgr"] == m.id and t["inst"] == idea["inst"] for t in self.ledger["open"] if t.get("mgr")) \
                and not (full and idea.get("pyramid")):
            return "red", None, None                         # zaten pozisyonu var (sessiz); Baş Trader piramitte ekler
        if any(o["mgr"] == m.id and o["idea"]["inst"] == idea["inst"] for o in self.ledger["orders"]):
            return "red", None, None                         # zaten bekleyen emri var
        cool = self.fc["tekrar_bekleme_mum"] * {"15m": 900_000, "1H": 3600_000, "4H": 14_400_000,
                                                "1D": 86_400_000}.get(tf, 3600_000)
        last_exit = max([t.get("exit_ts") or 0 for t in self.ledger["closed"]
                         if t.get("mgr") == m.id and t["inst"] == idea["inst"]] or [0])
        if not full and now_ms - last_exit < cool:
            return "red", None, None
        if halted and not shadow and not full:
            return "red", None, f"devre kesici: {halted}"
        env = self.env or {}
        if env.get("cal_block"):
            return "red", None, env["cal_block"]
        card = idea.get("card") or {}
        if not full and card.get("mult", 1.0) <= 0:
            return "red", None, "baş analist: " + "; ".join(card.get("notes") or ["uygun değil"])
        # ── Genel Müdür (v9 Faz 0): kanıt sınıfı + çatışma hakemi ──
        gm: list[str] = []
        cls = director.coin_class(meta(idea["inst"]) if meta else None, now_ms, self.fc)
        cstate = director.class_state(self, m.id, cls)
        cm = 1.0 if full else director.STEP_MULT[cstate]
        if not shadow and cm <= 0:
            shadow = True
            gm.append(f"kanıt sınıfı {director.CLASS_TEXT[cls]}: bu coinde kanıtımız yok, kanıtlanana kadar gölgede")
        elif not shadow and cm < 1:
            gm.append(f"kanıt sınıfı {director.CLASS_TEXT[cls]}: yarım boyut")
        bl = float(self.fc.get("ayi_long_carpan", 0.5))
        wx = (self.env or {}).get("weather") or {}
        if (not shadow and not full and bl <= 0 and side == "long" and m.id != "ayse"
                and (wx.get("btc_bear") or wx.get("trend") == "ayi")):
            shadow = True
            gm.append("BTC kuralı (5 yıllık yarış): BTC 50 günlük ortalamanın altında → long açılmaz, gölgede izleniyor")
        if not shadow and not full and self.fc.get("carpisma_yasak", True):
            opp = director.opposite_holder(self, idea)
            if opp:
                shadow = True
                who = self.by_id[opp["mgr"]].name if opp["mgr"] in self.by_id else opp["mgr"]
                gm.append(f"çatışma hakemi: {who} bu coinde {opp['side']} tarafında; ters pozisyon açılmaz, gölgede izleniyor")
        eq_m = self.equity(m.id)
        if shadow:   # gölge: kanıt toplamak için bütçe kadar sanal sermayeyle ölçülür (firma bakiyesine dokunmaz)
            eq_m = max(eq_m, self.budget)
        if eq_m <= 0:
            return "red", None, "yönetici sermayesi tükendi"
        mult = self.fc["seri_carpan"] if self.loss_streak(m.id) >= self.fc["kayip_serisi"] else 1.0
        wm = (quant.weather_mult(env.get("weather"), m.id, side, bear_long=bl if bl > 0 else 0.5)
              if env.get("weather") else 1.0)
        fm = env["fng_mult"](side) if env.get("fng_mult") else 1.0
        km = float(card.get("mult", 1.0))
        hm = macro.weekend_mult(now_ms, tf)
        xm = float(idea.get("exp_mult") or 1.0)                  # uzmanların karne ağırlıklı görüşü
        kel, kel_why = experts.kelly_mult(self, m.id)            # Genel Müdür · Sermaye Dağıtıcı (30+ işlemden sonra)
        if kel != 1.0 and not shadow and not full:
            gm.append(kel_why)
        ext = wm * fm * km * hm * xm * (kel if not shadow else 1.0) * (cm if (cm > 0 and not shadow) else 1.0)
        if full:          # Baş Trader kendi puanıyla boyutlanır; dış çarpanlar (hava, uzman, Kelly, seri) uygulanmaz
            mult, ext = 1.0, 1.0
            ext *= float(idea.get("risk_scale") or 1.0)          # piramit katı: her kat bir öncekinin yarısı
            cf = env.get("corr")
            if cf:        # benzer hareket eden coinlerde aynı yöne birikme: engellemez, boyutu yumuşakça küçültür
                k = sum(1 for t in self._live_open() if t.get("mgr") == m.id and t["side"] == side
                        and t["inst"] != idea["inst"] and cf(t["inst"], idea["inst"]) >= 0.7)
                if k:
                    ext /= math.sqrt(1 + k)
                    gm.append(f"benzer {k} coinde zaten {side}: boyut ×{1 / math.sqrt(1 + k):.2f}")
        risk_pct = (m.base_risk + (m.max_risk - m.base_risk) * idea["conviction"]) * mult * ext
        risk_usd = eq_m * risk_pct / 100
        if idea.get("notional_target"):     # Ortak Masa: anlaşan yöneticilerin %5 katkısı kadar pozisyon
            ref_px = idea.get("limit_px") or idea["entry"]
            risk_usd = idea["notional_target"] * abs(idea["entry"] - idea["sl"]) / ref_px
        if not shadow and not full:      # coin risk tavanı: tüm yöneticilerin bu coindeki toplam riski
            cap, used, room = director.coin_room(self, idea["inst"])
            if risk_usd > room:
                if room >= risk_usd * self.fc["coin_tavan_min_oran"]:
                    gm.append(f"coin tavanı: {sym}'de firma riski {used:.0f}$ / {cap:.0f}$ → boyut {risk_usd:.0f}$'dan "
                              f"{room:.0f}$'a küçültüldü")
                    risk_usd = room
                else:
                    shadow = True
                    gm.append(f"coin tavanı dolu: {sym}'de firma riski {used:.0f}$ / {cap:.0f}$; gölgede izleniyor")
                    eq_m = max(eq_m, self.budget)
                    risk_usd = eq_m * risk_pct / 100
        # giriş fiyatı
        limit = idea.get("entry_type") == "limit" and idea.get("limit_px")
        entry, src, slip_bp = idea["entry"], "piyasa", 0.0
        if not limit:
            q = quote(idea["inst"]) if quote else None
            if q:
                bid, ask, last = q
                if bid > 0 and ask > 0:
                    spread_bp = (ask - bid) / ((ask + bid) / 2) * 1e4
                    if spread_bp > lg.risk_cfg(self.cfg)["max_spread_bp"]:
                        return "red", None, f"spread geniş ({spread_bp:.1f} bp)"
                px = ask if side == "long" else bid
                if px and px > 0 and abs(px / entry - 1) < 0.015:
                    entry, src = px, "kitap"
        else:
            entry, src = idea["limit_px"], "limit"
        stop_dist = abs(entry - (idea["sl"] + (entry - idea["entry"])))
        if stop_dist <= 0:
            return "red", None, "stop mesafesi sıfır"
        lev, lev_why = self.choose_leverage(m, idea, stop_dist / entry, meta(idea["inst"]) if meta else None)
        notional_t = risk_usd / stop_dist * entry
        if self.fc.get("sinirlar_aktif", True) and not full:
            notional_t = min(notional_t, eq_m * self.fc["max_nominal_kat"])
        contracts, qty, err = lg.round_contracts(notional_t, entry, meta(idea["inst"]) if meta else None)
        if err or qty <= 0:
            return "red", None, err or "miktar sıfır"
        if not limit and book:
            bk = book(idea["inst"])
            if bk:
                lv = bk.get("asks" if side == "long" else "bids") or []
                if not lv:
                    return "red", None, "kitap boş"
                # IOC limit (v9): çıplak piyasa emri yok; en iyi fiyatın ±bant ötesine geçilmez, dolmayan kısım iptal
                band = float(idea.get("ioc_bp") or self.fc.get("ioc_bant_bp", 10))
                lim_px = lv[0][0] * (1 + band / 1e4) if side == "long" else lv[0][0] * (1 - band / 1e4)
                lvb = [x for x in lv if (x[0] <= lim_px if side == "long" else x[0] >= lim_px)]
                vwap, got = lg.vwap_fill(lvb, qty)
                if vwap is None or got < qty * self.fc.get("ioc_min_dolum", 0.5):
                    return "red", None, f"IOC limit: ±%{band / 100:.2f} bant içinde yeterli karşı emir yok"
                if got < qty * 0.999:
                    want = qty
                    contracts, qty, err = lg.round_contracts(got * vwap, vwap, meta(idea["inst"]) if meta else None)
                    if err or qty <= 0:
                        return "red", None, err or "IOC kısmi dolum lot altında"
                    gm.append(f"IOC kısmi dolum: bant içinde istenenin %{got / want * 100:.0f}'i doldu, kalan iptal")
                slip_bp = abs(vwap / lv[0][0] - 1) * 1e4
                if slip_bp > lg.risk_cfg(self.cfg)["max_kayma_bp"]:
                    return "red", None, f"tahmini kayma yüksek ({slip_bp:.1f} bp)"
                entry, src = vwap, "derinlik (VWAP)"
        risk_now = qty * stop_dist
        margin = qty * entry / lev
        if not shadow and not full and self.fc.get("sinirlar_aktif", True):
            firm_eq = self.firm_equity()
            if self.heat() + risk_now > firm_eq * self.fc["max_isi_yuzde"] / 100:
                return "red", None, f"firma toplam risk limiti (%{self.fc['max_isi_yuzde']:g})"
            if self.coin_risk(idea["inst"]) + risk_now > firm_eq * self.fc["coin_risk_yuzde"] / 100:
                return "red", None, f"{sym} için firma riski dolu (başka yöneticiler de bu coinde)"
            if self.side_risk(side) + risk_now > firm_eq * self.fc["yon_risk_yuzde"] / 100:
                return "red", None, f"firma {side} yönü riski dolu (%{self.fc['yon_risk_yuzde']:g})"
            cf = env.get("corr")
            if cf:
                rho = self.fc["kume_korelasyon"]
                peers = [t for t in self._live_open() if t["side"] == side and not t.get("carry")
                         and (t["inst"] == idea["inst"] or cf(t["inst"], idea["inst"]) >= rho)]
                cl = sum(lg.open_risk(t) for t in peers)
                if cl + risk_now > firm_eq * self.fc["kume_risk_yuzde"] / 100:
                    return "red", None, (f"yığılma: {len(peers)} pozisyon {sym} ile birlikte hareket ediyor "
                                         f"(ρ≥{rho:g}); bu bahsin riski dolu (%{self.fc['kume_risk_yuzde']:g})")
        if not shadow and self.mgr_free(m.id) < margin:     # olmayan parayla işlem açılamaz (kısıt değil, gerçeklik)
            return "red", None, f"yöneticinin boşta teminatı yetersiz ({self.mgr_free(m.id):.0f}$ < {margin:.0f}$)"
        detail = {"entry": entry, "qty": qty, "contracts": contracts, "lev": lev, "src": src, "slip_bp": slip_bp,
                  "lot_sz": (meta(idea["inst"]) or {}).get("lot_sz") if meta else None,
                  "risk_usd": risk_now, "risk_pct": risk_now / eq_m * 100, "margin_usd": margin, "shadow": shadow,
                  "eq_m": eq_m, "mult": mult, "ext": round(ext, 3), "lev_why": lev_why,
                  "mults": {"hava": round(wm, 2), "korku": round(fm, 2), "analist": round(km, 2), "haftasonu": round(hm, 2),
                            "sinif": round(cm, 2), "uzman": round(xm, 2), "kelly": round(kel, 2)},
                  "coin_class": cls, "gm": gm}
        return ("limit" if limit else "market"), detail, None

    # Filtre kategorileri: elenen fikir gölgede izlenir ki "filtre haklı mıydı?" ölçülebilsin (v9: öğrenme yavaşlamaz).
    # Veri/yapı sebepleri (sıfır stop, lot, kitap yok) sınanacak bir karar değildir → izlenmez.
    NO_SHADOW = ("stop mesafesi", "miktar sıfır", "kitap boş", "en küçük lot", "lot altında", "yönetici pasif",
                 "sermayesi tükendi", "teminatı yetersiz")

    @staticmethod
    def filter_kind(why: str) -> str:
        w = why.lower()
        for key, name in (("olay takvimi", "olay takvimi"), ("fed", "olay takvimi"), ("baş analist", "baş analist"),
                          ("spread", "spread"), ("kayma", "kayma"), ("derinli", "derinlik"), ("ioc", "IOC bandı"),
                          ("devre kesici", "devre kesici"), ("yığılma", "yığılma"), ("riski dolu", "firma limiti"),
                          ("risk limiti", "firma limiti")):
            if key in w:
                return name
        return why.split(":")[0][:40]

    def shadow_rejected(self, idea: dict, why: str, now_ms: int, meta=None) -> dict | None:
        """Reddedilen fikri gölgede (sermayesiz) açar; sonucu filtrenin karnesine yazılır."""
        m = self.by_id.get(idea.get("mgr"))
        if not m or idea.get("kind") == "carry" or any(k in why for k in self.NO_SHADOW):
            return None
        if any(t.get("mgr") == m.id and t["inst"] == idea["inst"] for t in self.ledger["open"]):
            return None
        entry = idea.get("limit_px") if idea.get("entry_type") == "limit" and idea.get("limit_px") else idea["entry"]
        stop_dist = abs(idea["entry"] - idea["sl"])
        if not entry or stop_dist <= 0:
            return None
        risk_usd = self.budget * (m.base_risk + (m.max_risk - m.base_risk) * idea.get("conviction", 0.5)) / 100
        qty = risk_usd / stop_dist
        t = lg.build_trade(idea, entry, qty, 0.0, 1.0, self.cfg, now_ms, self.budget, src="gölge (elendi)", shadow=True)
        self._tag(t, idea, None)
        t["filtered"] = why[:160]
        t["filter_kind"] = self.filter_kind(why)
        t["coin_class"] = director.coin_class(meta(idea["inst"]) if meta else None, now_ms, self.fc)
        self.ledger["open"].append(t)
        return t

    def choose_leverage(self, m, idea: dict, stop_frac: float, meta: dict | None) -> tuple[float, str]:
        """Yöneticinin kaldıraç kararı. Kaldıraç kârı/riski değiştirmez (risk stop × miktar ile belirlenir); teminatı
        küçültüp aynı parayla daha çok fırsata girmeyi sağlar. Mutlak kural: likidasyon stop'un en az 1,5 katı ötede."""
        mmr = lg.fees_cfg(self.cfg)["mmr_yuzde"] / 100
        safe = max(1, math.floor(1 / (stop_frac * 1.5 + mmr)))
        cap = float((m.extra or {}).get("lev_cap", self.fc["max_kaldirac"]))
        want = max(1, round(1 + (cap - 1) * float(idea.get("conviction", 0.5))))
        okx = float((meta or {}).get("max_lev") or 0) or cap
        lev = float(max(1, min(safe, want, okx, cap)))
        why = (f"{lev:g}x: güven %{idea.get('conviction', 0) * 100:.0f} → istenen {want:g}x, "
               f"stop %{stop_frac * 100:.1f} uzakta → likidasyon güvenliği en fazla {safe}x"
               + (f", OKX sınırı {okx:g}x" if okx < cap else ""))
        return lev, why

    def execute(self, ideas: list[dict], now_ms: int, quote=None, meta=None, book=None, halted=None):
        """Fikirleri sırayla (güven yüksekten düşüğe) değerlendirir. Döner: (açılanlar, emirler, redler)."""
        opened, orders, rejected = [], [], []
        for idea in sorted(ideas, key=lambda x: -x["conviction"]):
            if idea.get("maker_first") and idea.get("entry_type") == "piyasa" and quote:
                q = quote(idea["inst"])
                if q and q[0] > 0 and q[1] > 0:     # önce kitaptaki en iyi fiyata limit (maker), 10 dk; dolmazsa piyasa
                    idea = {**idea, "entry_type": "limit", "limit_px": q[0] if idea["side"] == "long" else q[1],
                            "limit_valid_ms": 600_000, "fallback_market": True, "maker_first": False}
            kind, d, why = self.evaluate(idea, now_ms, quote, meta, book, halted)
            if kind == "red":
                if why:
                    rejected.append((idea, why))
                    sh = self.shadow_rejected(idea, why, now_ms, meta)
                    self.journal(idea["mgr"], idea["sym"], idea["tf"], idea["side"], "reddedildi",
                                 why + (" → gölgede izleniyor (filtre de sınanır)" if sh else ""))
                continue
            if kind == "market":
                t = lg.build_trade(idea, d["entry"], d["qty"], d["contracts"], d["lev"], self.cfg, now_ms,
                                   d["eq_m"], src=d["src"], slip_bp=d["slip_bp"], shadow=d["shadow"])
                t["lot_sz"] = d.get("lot_sz")
                t["lev_why"] = d.get("lev_why")
                t["coin_class"], t["gm"] = d.get("coin_class"), d.get("gm") or []
                self._tag(t, idea, d.get("mults"))
                if idea.get("contrib") and not d["shadow"]:
                    t["contrib"], t["votes"] = idea["contrib"], idea.get("votes")
                self.ledger["open"].append(t)
                opened.append({**idea, **t})
                self.journal(idea["mgr"], idea["sym"], idea["tf"], idea["side"],
                             "gölgede açtı" if d["shadow"] else "açtı",
                             f"güven %{idea['conviction'] * 100:.0f}, risk {d['risk_usd']:.2f}$ (%{d['risk_pct']:.2f}), {d['lev']:g}x"
                             + (f", çarpan ×{d['ext']:g}" if d.get("ext", 1) != 1 else "")
                             + ("; analist: " + "; ".join((idea.get("card") or {}).get("notes") or []) if (idea.get("card") or {}).get("notes") else "")
                             + ("; Genel Müdür: " + "; ".join(d["gm"]) if d.get("gm") else ""))
            else:
                o = {"oid": uuid.uuid4().hex[:10], "mgr": idea["mgr"], "idea": idea, "px": d["entry"], "qty": d["qty"],
                     "contracts": d["contracts"], "lev": d["lev"], "margin_usd": d["margin_usd"], "shadow": d["shadow"],
                     "eq_m": d["eq_m"], "created": now_ms, "expire_ts": now_ms + (idea.get("limit_valid_ms", 600_000) if idea.get("fallback_market")
                                                           else max(idea.get("limit_valid_ms", 0), 900_000)),
                     "risk_usd": d["risk_usd"], "lot_sz": d.get("lot_sz"), "lev_why": d.get("lev_why"),
                     "coin_class": d.get("coin_class"), "gm": d.get("gm") or [], "mults": d.get("mults")}
                self.ledger["orders"].append(o)
                orders.append(o)
                self.journal(idea["mgr"], idea["sym"], idea["tf"], idea["side"], "limit emir",
                             f"{team.nfp(d['entry'])} kitaptaki en iyi fiyattan maker giriş, 10 dk; dolmazsa piyasa"
                             if idea.get("fallback_market") else f"{team.nfp(d['entry'])} seviyesinde geri çekilme bekliyor")
        return opened, orders, rejected

    def fill_order(self, oid: str, px: float, ts: int) -> dict | None:
        o = next((x for x in self.ledger["orders"] if x["oid"] == oid), None)
        if not o:
            return None
        self.ledger["orders"].remove(o)
        t = lg.build_trade(o["idea"], o["px"], o["qty"], o["contracts"], o["lev"], self.cfg, ts, o["eq_m"],
                           src="limit (maker)", maker_entry=True, shadow=o["shadow"])
        t["lot_sz"] = o.get("lot_sz")
        t["lev_why"] = o.get("lev_why")
        t["coin_class"], t["gm"] = o.get("coin_class"), o.get("gm") or []
        self._tag(t, o["idea"], o.get("mults"))
        if o["idea"].get("contrib") and not o.get("shadow"):
            t["contrib"], t["votes"] = o["idea"]["contrib"], o["idea"].get("votes")
        self.ledger["open"].append(t)
        self.journal(o["mgr"], t["sym"], t["tf"], t["side"], "limit doldu", f"{team.nfp(o['px'])} seviyesinden girdi")
        return {**o["idea"], **t}

    def expire_orders(self, now_ms: int) -> list[dict]:
        gone = [o for o in self.ledger["orders"] if now_ms >= o["expire_ts"]]
        for o in gone:
            self.ledger["orders"].remove(o)
            self.journal(o["mgr"], o["idea"]["sym"], o["idea"]["tf"], o["idea"]["side"], "emir iptal",
                         "maker emir dolmadı → fiyat kaçmadıysa piyasadan" if o["idea"].get("fallback_market")
                         else "geri çekilme gelmedi, süre doldu")
        return gone

    # ── yedek kulübesi ve yeniden dağıtım ───────────────────────────
    def manager_tick(self, now_ms: int) -> list[str]:
        """Zirve takibi, yedek kulübesi ve dönüş. Döner: bildirim metinleri."""
        msgs = []
        for m in self.mgrs:
            eq = self.equity(m.id)
            peak = max(self.st["peak"].get(m.id) or eq, eq)
            self.st["peak"][m.id] = peak
            dd = (eq / peak - 1) * 100 if peak > 0 else 0
            if m.kind == "carry":
                continue
            if self.fc.get("yedek_kulubesi", True) and not self.benched(m.id) and dd <= -m.dd_bench:
                self.st["bench"][m.id] = now_ms
                msgs.append(f"🪑 <b>{m.emoji} {m.name}</b> yedek kulübesine alındı: zirvesinden %{abs(dd):.1f} düştü "
                            f"(limit %{m.dd_bench:g}). Gölgede sermayesiz işlem yapmaya devam edecek; kendini kanıtlarsa geri dönecek.")
                self.journal(m.id, "-", "-", "-", "yedeğe alındı", f"zirveden %{abs(dd):.1f} düşüş")
            elif self.benched(m.id):
                since = self.st["bench"][m.id]
                sh = [t for t in self.ledger["closed"] if t.get("mgr") == m.id and t.get("shadow")
                      and (t.get("exit_ts") or 0) >= since]
                if len(sh) >= self.fc["yedekten_donus_islem"]:
                    avg = float(np.mean([t["r"] for t in sh]))
                    if avg >= self.fc["yedekten_donus_r"]:
                        del self.st["bench"][m.id]
                        self.st["peak"][m.id] = eq
                        self.st["last_rebalance"] = 0          # sermayesini hemen bir sonraki dağıtımda alsın
                        msgs.append(f"🔙 <b>{m.emoji} {m.name}</b> yedek kulübesinden döndü: gölgede {len(sh)} işlemde "
                                    f"ortalama {avg:+.2f}R yaptı. Tekrar gerçek sermayeyle işlem yapacak.")
                        self.journal(m.id, "-", "-", "-", "yedekten döndü", f"gölgede {len(sh)} işlem, {avg:+.2f}R")
        return msgs

    def apply_policy(self, active: list[str], fixed: dict, now_ms: int, why: str) -> str:
        """Kanıta dayalı sermaye (PLAN.md): testi geçenler sermaye alır, geçemeyenler gölgede (sermayesiz) işlem yapar
        ve kendini kanıtlarsa geri döner. fixed: sabit paylı yöneticiler (ör. Elif %15)."""
        feq = self.firm_equity()
        fixed_sum = sum(w for mid, w in fixed.items() if mid in active)
        rest = [m.id for m in self.mgrs if m.id in active and m.id not in fixed]
        lines = []
        for m in self.mgrs:
            eq = self.equity(m.id)
            if m.id in active:
                target = feq * (fixed[m.id] if m.id in fixed else (1 - fixed_sum) / max(len(rest), 1))
                self.st["bench"].pop(m.id, None)
            else:
                target = 0.0
                self.st["bench"].setdefault(m.id, now_ms)
                self.st.setdefault("bench_why", {})[m.id] = why
            self.st["adj"][m.id] = self.st["adj"].get(m.id, 0.0) + (target - eq)
            self.st["peak"][m.id] = max(target, 0.0)
            lines.append(f"{m.emoji} {m.name}: " + (f"<b>{target:.2f}$</b>" if m.id in active else "gölgede (sermayesiz)"))
            self.journal(m.id, "-", "-", "-", "politika", f"{eq:.2f}$ → {target:.2f}$ ({why})" if m.id in active
                         else f"gölgeye alındı ({why})")
        self.st["policy"] = {"active": list(active), "fixed": dict(fixed), "ts": now_ms, "why": why}
        self.st["last_rebalance"] = now_ms
        return (f"🏦 <b>Sermaye politikası</b> ({why})\n" + "\n".join(lines) +
                "\n<i>Gölgedekiler sermayesiz işlem yapmaya devam eder; 10 işlemde ortalama +0,1R yaparsa geri döner.</i>")

    def maybe_rebalance(self, now_ms: int) -> str | None:
        days = self.fc["yeniden_dagitim_gun"]
        if not days or days <= 0 or now_ms - self.st["last_rebalance"] < days * 86400_000:
            return None
        self.st["last_rebalance"] = now_ms
        cut = now_ms - 30 * 86400_000
        fixed = (self.st.get("policy") or {}).get("fixed") or {}
        scores, lines = {}, []
        for m in self.mgrs:
            cl = [t for t in self.ledger["closed"] if t.get("mgr") == m.id and not t.get("shadow")
                  and (t.get("exit_ts") or 0) >= cut]
            if self.benched(m.id):
                sc = 0.0                        # gölgedeki yöneticiye sermaye yok
            elif len(cl) >= self.fc["dagitim_min_islem"]:
                avg = float(np.mean([t["r"] for t in cl]))
                sc = float(np.clip(1 + 2 * avg, 0.5, 1.5))
            else:
                sc = 1.0
            scores[m.id] = sc
        fixed_live = {mid: w for mid, w in fixed.items() if mid in scores and scores[mid] > 0}
        tot = sum(sc for mid, sc in scores.items() if mid not in fixed_live) or 1
        feq = self.firm_equity()
        rest_share = 1 - sum(fixed_live.values())
        for m in self.mgrs:
            eq = self.equity(m.id)
            target = feq * fixed_live[m.id] if m.id in fixed_live else feq * rest_share * scores[m.id] / tot
            self.st["adj"][m.id] = self.st["adj"].get(m.id, 0.0) + (target - eq)
            self.st["peak"][m.id] = max(target, 0)
            lines.append(f"{m.emoji} {m.name}: {eq:.2f}$ → <b>{target:.2f}$</b>"
                         + (" (gölgede)" if self.benched(m.id) else f" (puan {scores[m.id]:.2f})"))
            self.journal(m.id, "-", "-", "-", "sermaye", f"{eq:.2f}$ → {target:.2f}$")
        return "🏦 <b>Haftalık sermaye dağıtımı</b> (son 30 gün performansına göre)\n" + "\n".join(lines)

    # ── Elif: funding carry ─────────────────────────────────────────
    def carry_plan(self, harvest: list[dict], prices: dict, now_ms: int) -> tuple[list, list]:
        """Döner: (açılacaklar [(info, spot_px, perp_px, qty, shadow)], kapatılacaklar [(trade, sebep)])."""
        m = self.by_id.get("elif")
        if not m:
            return [], []
        f = self.fc
        held = [t for t in self.ledger["open"] if t.get("mgr") == "elif" and t.get("carry")]
        hv_by = {h["inst"]: h for h in harvest}
        to_close = []
        for t in held:
            h = hv_by.get(t["inst"])
            if t.get("neg_count", 0) >= 2:
                to_close.append((t, "funding art arda 2 kez negatif"))
            elif h and h.get("ann_7d") is not None and h["ann_7d"] < f["carry_cikis_yillik"]:
                to_close.append((t, f"7 günlük funding %{h['ann_7d']:.0f}'e düştü"))
            elif now_ms - t["entry_ts"] > f["carry_max_gun"] * 86400_000:
                to_close.append((t, f"{f['carry_max_gun']} günü doldurdu"))
        keep = [t for t in held if t not in [x[0] for x in to_close]]
        slots = f["carry_max_cift"] - len(keep)
        to_open = []
        if slots > 0:
            eq = self.equity("elif")
            per = eq / f["carry_max_cift"]
            shadow = self.benched("elif")
            held_insts = {t["inst"] for t in keep}
            cands = [h for h in harvest if h["inst"] not in held_insts and h["ann_30d"] >= f["carry_min_yillik"]
                     and h["pos_pct"] >= f["carry_min_pozitif"] and h["stability"] >= f["carry_min_istikrar"]
                     and not h.get("cooling") and (h.get("ann_now") or 0) > 5]
            for h in cands[:slots]:
                spot = prices.get(f"{h['sym']}-USDT")
                perp = prices.get(h["inst"])
                if not (spot and perp) or abs(perp / spot - 1) > 0.01:
                    continue          # spot fiyatı yok ya da baz anormal → girme
                qty = per / (spot + perp / 5)
                if not shadow and self.mgr_free("elif") < qty * (spot + perp / 5):
                    break
                to_open.append((h, spot, perp, qty, shadow))
        return to_open, to_close

    # ── Ortak Masa (konsensüs) ──────────────────────────────────────
    def consensus_plan(self, now_ms: int, prices: dict) -> tuple[list, list]:
        """En az 2 yönetici aynı coinde aynı yönde GERÇEK pozisyondaysa Ortak Masa kendi bütçesiyle ayrı bir işlem açar;
        uzlaşı 2'nin altına düşünce çıkar. v9: gölgedeki oy sayılmaz (v9 öncesi açılan masa işlemleri eski sayımla izlenir)."""
        m = self.by_id.get("ortak")
        if not m:
            return [], []
        voters = {x.id for x in self.mgrs if x.kind == "directional"}
        groups: dict = {}
        groups_all: dict = {}
        for t in self.ledger["open"]:
            if t.get("mgr") in voters and not t.get("carry"):
                groups_all.setdefault((t["inst"], t["side"]), {}).setdefault(t["mgr"], t)
                if not t.get("shadow"):        # gölgedeki oy sayılmaz
                    groups.setdefault((t["inst"], t["side"]), {}).setdefault(t["mgr"], t)
        mine = {t["inst"]: t for t in self.ledger["open"] if t.get("mgr") == "ortak"}
        to_open, to_close = [], []
        for inst, t in mine.items():
            g = (groups if "gm" in t else groups_all).get((inst, t["side"]), {})
            if len(g) < 2:
                to_close.append((t, f"uzlaşı dağıldı (aynı yönde {len(g)} yönetici kaldı)"))
        thr, thr_why = director.committee_threshold(self)
        pct = float(((self.cfg.get("ekip") or {}).get("ortak") or {}).get("katki_yuzde", 5.0))
        for (inst, side), g in groups.items():
            if len(g) < 2 or inst in mine:
                continue
            if any(o["mgr"] == "ortak" and o["idea"]["inst"] == inst for o in self.ledger["orders"]):
                continue
            px = prices.get(inst)
            trs = list(g.values())
            if not px or px <= 0:
                continue
            # güven kuralı: karne ağırlıklı oy + en az iki farklı tarz
            votes = {mid: director.vote_weight(self, mid, side) for mid in g}
            wsum = sum(w for w, _ in votes.values())
            fams = {director.FAMILY.get(mid, mid) for mid, (w, _) in votes.items() if w > 0}
            if wsum < thr or len(fams) < 2:
                seen = self.st.setdefault("cons_skip", {})
                if seen.get(f"{inst}|{side}") != round(wsum, 2):
                    seen[f"{inst}|{side}"] = round(wsum, 2)
                    self.journal("ortak", trs[0]["sym"], "1D", side, "komite reddetti",
                                 f"ağırlıklı oy {wsum:.2f} (eşik {thr:.2f}), {len(fams)} farklı tarz"
                                 + (" — aynı aileden iki oy yetmez" if len(fams) < 2 and len(g) >= 2 else "")
                                 + "; " + ", ".join(f"{self.by_id[m].name if m in self.by_id else m} {w:.2f} ({why})"
                                                    for m, (w, why) in votes.items()))
                    if len(seen) > 200:
                        seen.pop(next(iter(seen)))
                continue
            sgn = 1 if side == "long" else -1
            dist = float(np.median([abs(x["entry"] - (x.get("sl_init") or x["sl"])) / x["entry"] for x in trs])) * px
            atr = float(np.median([(x.get("atr") or 0) / x["entry"] for x in trs])) * px or dist / 2
            if dist <= 0:
                continue
            names = [self.by_id[mid].name if mid in self.by_id else mid for mid in sorted(g)]
            contrib = {mid: round(self.capital(mid) * pct / 100, 2) for mid, (w, _) in votes.items() if w > 0}
            notional = sum(contrib.values())
            if notional <= 0:
                continue
            conv = min(1.0, wsum / 3.0)
            to_open.append({
                "mgr": "ortak", "inst": inst, "sym": trs[0]["sym"], "tf": "1D", "side": side, "entry": px, "market_px": px,
                "sl": px - sgn * dist,
                "tp": px + 3 * dist if sgn > 0 else max(px - 3 * dist, px * 0.05),
                "tp1": px + 2 * dist if sgn > 0 else max(px - 2 * dist, px * 0.1), "atr": atr,
                "max_saat": 30 * 24, "pp": {"partial_frac": 0.3, "trail_atr": 2.5, "basabas": True},
                "conviction": round(conv, 3),
                "checks": [[f"{self.by_id[m].name if m in self.by_id else m} oy {w:.2f} ({why})", w > 0] for m, (w, why) in votes.items()],
                "core": f"{len(g)} yönetici hemfikir", "reasons": [f"{len(g)} yönetici hemfikir: {', '.join(names)}"],
                "plan": (f"{', '.join(names)} aynı anda {sym_side(side)} tarafında. Yatırım komitesi onayı: ağırlıklı oy "
                         f"{wsum:.2f} ≥ eşik {thr:.2f} ({thr_why}), {len(fams)} farklı tarz ({', '.join(sorted(fams))}). "
                         f"Her biri bütçesinin %{pct:g}'ini koydu: toplam {notional:,.0f}$ pozisyon; kâr/zarar katkı payına göre "
                         f"onlara döner. Stop, onların stop mesafelerinin ortancası; 2R'de %30 alıp kalanı 2,5 ATR iz süren stopla "
                         f"taşırım. Hemfikir sayısı 2'nin altına düşerse çıkarım."),
                "entry_type": "piyasa", "limit_px": None, "limit_valid_ms": 0, "setup": "ortak", "kind": "consensus",
                "funding_rate": trs[0].get("funding_rate", 0.0), "interval_h": trs[0].get("interval_h", 8), "regime": 0,
                "rr": 3.0, "tags": names, "exit": None, "maker_first": True, "consensus": sorted(g),
                "contrib": contrib, "notional_target": notional, "votes": {m: w for m, (w, _) in votes.items()}})
        return to_open, to_close

    @staticmethod
    def _tag(t: dict, idea: dict, mults: dict | None):
        """Fikirden işleme taşınan öğrenme bilgisi: giriş özellikleri (Hakem), uzman görüşleri (karne), varyantlar."""
        for k in ("feat", "experts", "exp_mult", "variants", "bas_score", "bas_parts", "pyramid", "layer"):
            if idea.get(k) is not None:
                t[k] = idea[k]
        if mults:
            t["mults"] = mults

    def on_closed(self, closed: list[dict]) -> list[str]:
        """Ortak Masa işlemi kapanınca kâr/zarar katkı payına göre katkı veren yöneticilere aktarılır (v9 Faz 4)."""
        msgs = experts.score_closed(self.st, closed)            # uzman karneleri (gerçek + gölge)
        for t in closed:
            c = t.get("contrib")
            if t.get("mgr") != "ortak" or not c or t.get("shadow") or t.get("distributed"):
                continue
            pnl = float(t.get("pnl_usd") or 0.0)
            tot = sum(c.values()) or 1.0
            dist = {mid: round(pnl * usd / tot, 2) for mid, usd in c.items()}
            for mid, amt in dist.items():
                self.st["adj"][mid] = self.st["adj"].get(mid, 0.0) + amt
            self.st["adj"]["ortak"] = self.st["adj"].get("ortak", 0.0) - pnl
            t["distributed"] = dist
            line = ", ".join(f"{self.by_id[m].name if m in self.by_id else m} {v:+.2f}$" for m, v in dist.items())
            self.journal("ortak", t["sym"], t.get("tf", "1D"), t["side"], "kâr/zarar dağıtıldı", f"{pnl:+.2f}$ → {line}")
            msgs.append(f"🤝 Ortak Masa {t['sym']}: {pnl:+.2f}$ katkı payına göre dağıtıldı ({line})")
        return msgs

    # ── rapor ────────────────────────────────────────────────────────
    def summary(self, now_ms: int) -> dict:
        out = {}
        for m in team.ROSTER:
            if m.id not in self.st["alloc"]:
                continue
            cl, op = self.mgr_trades(m.id)
            shc, sho = self.mgr_trades(m.id, shadow=True)
            p = lg.pnl_parts(cl, op)
            cap = self.capital(m.id)
            eq = cap + p["realized"] + p["unrealized"]
            peak = self.st["peak"].get(m.id) or eq
            s = lg._summ(cl)
            out[m.id] = {**self.by_id.get(m.id, m).public(), "active": m.id in self.by_id,   # config ayarlı risk
                         "capital": round(cap, 2), "equity": round(eq, 2),
                         "pnl": round(eq - cap, 2),
                         "return_pct": round((eq / cap - 1) * 100, 2) if cap > 0 else 0,
                         "realized": round(p["realized"], 2), "unrealized": round(p["unrealized"], 2),
                         "dd_pct": round((eq / peak - 1) * 100, 2) if peak else 0,
                         "open": len(op), "orders": sum(1 for o in self.ledger["orders"] if o["mgr"] == m.id),
                         "heat": round(sum(lg.open_risk(t) for t in op), 2),
                         "benched": self.benched(m.id), "loss_streak": self.loss_streak(m.id),
                         "bench_why": (self.st.get("bench_why") or {}).get(m.id) if self.benched(m.id) else None,
                         "stats": s, "shadow": {"open": len(sho), "closed": len(shc),
                                                "avg_r": round(float(np.mean([t["r"] for t in shc])), 3) if shc else None}}
        return out
