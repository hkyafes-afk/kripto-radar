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
from . import macro, quant
from . import team


def firm_cfg(cfg: dict) -> dict:
    f = {"gunluk_kayip_yuzde": 3.0, "max_dusus_yuzde": 15.0, "max_isi_yuzde": 10.0, "coin_risk_yuzde": 2.5,
         "yon_risk_yuzde": 6.0, "max_nominal_kat": 2.0, "max_kaldirac": 5, "tekrar_bekleme_mum": 3,
         "yeniden_dagitim_gun": 7, "dagitim_min_islem": 15, "yedekten_donus_islem": 10, "yedekten_donus_r": 0.1,
         "kayip_serisi": 4, "seri_carpan": 0.5,
         "carry_max_cift": 3, "carry_min_yillik": 15, "carry_min_pozitif": 80, "carry_min_istikrar": 45,
         "carry_cikis_yillik": 5, "carry_max_gun": 45, "kume_risk_yuzde": 4.0, "kume_korelasyon": 0.7}
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
        for m in self.mgrs:
            alloc.setdefault(m.id, start / len(team.ROSTER))
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
        return mid in self.st["bench"]

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
        shadow = self.benched(m.id)
        sym, tf, side = idea["sym"], idea["tf"], idea["side"]
        if any(t["mgr"] == m.id and t["inst"] == idea["inst"] for t in self.ledger["open"] if t.get("mgr")):
            return "red", None, None                         # zaten pozisyonu var (sessiz)
        if any(o["mgr"] == m.id and o["idea"]["inst"] == idea["inst"] for o in self.ledger["orders"]):
            return "red", None, None                         # zaten bekleyen emri var
        cool = self.fc["tekrar_bekleme_mum"] * {"15m": 900_000, "1H": 3600_000, "4H": 14_400_000,
                                                "1D": 86_400_000}.get(tf, 3600_000)
        last_exit = max([t.get("exit_ts") or 0 for t in self.ledger["closed"]
                         if t.get("mgr") == m.id and t["inst"] == idea["inst"]] or [0])
        if now_ms - last_exit < cool:
            return "red", None, None
        if halted and not shadow:
            return "red", None, f"devre kesici: {halted}"
        env = self.env or {}
        if env.get("cal_block"):
            return "red", None, env["cal_block"]
        card = idea.get("card") or {}
        if card.get("mult", 1.0) <= 0:
            return "red", None, "baş analist: " + "; ".join(card.get("notes") or ["uygun değil"])
        eq_m = self.equity(m.id)
        if eq_m <= 0:
            return "red", None, "yönetici sermayesi tükendi"
        mult = self.fc["seri_carpan"] if self.loss_streak(m.id) >= self.fc["kayip_serisi"] else 1.0
        wm = quant.weather_mult(env.get("weather"), m.id, side) if env.get("weather") else 1.0
        fm = env["fng_mult"](side) if env.get("fng_mult") else 1.0
        km = float(card.get("mult", 1.0))
        hm = macro.weekend_mult(now_ms, tf)
        ext = wm * fm * km * hm
        risk_pct = (m.base_risk + (m.max_risk - m.base_risk) * idea["conviction"]) * mult * ext
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
        # kaldıraç: likidasyon stop'un en az 1,5 katı ötede kalacak kadar (geniş günlük stoplarda 5x likide eder)
        mmr = lg.fees_cfg(self.cfg)["mmr_yuzde"] / 100
        lev = float(min(self.fc["max_kaldirac"], max(1, math.floor(1 / (stop_dist / entry * 1.5 + mmr)))))
        notional_t = min(risk_usd / stop_dist * entry, eq_m * self.fc["max_nominal_kat"])
        contracts, qty, err = lg.round_contracts(notional_t, entry, meta(idea["inst"]) if meta else None)
        if err or qty <= 0:
            return "red", None, err or "miktar sıfır"
        if not limit and book:
            bk = book(idea["inst"])
            if bk:
                lv = bk.get("asks" if side == "long" else "bids") or []
                vwap, got = lg.vwap_fill(lv, qty)
                if vwap is None or got < qty * 0.999:
                    return "red", None, "kitap derinliği yetersiz"
                slip_bp = abs(vwap / lv[0][0] - 1) * 1e4
                if slip_bp > lg.risk_cfg(self.cfg)["max_kayma_bp"]:
                    return "red", None, f"tahmini kayma yüksek ({slip_bp:.1f} bp)"
                entry, src = vwap, "derinlik (VWAP)"
        risk_now = qty * stop_dist
        margin = qty * entry / lev
        if not shadow:
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
            if self.mgr_free(m.id) < margin:
                return "red", None, "yöneticinin boşta teminatı yetersiz"
        detail = {"entry": entry, "qty": qty, "contracts": contracts, "lev": lev, "src": src, "slip_bp": slip_bp,
                  "risk_usd": risk_now, "risk_pct": risk_now / eq_m * 100, "margin_usd": margin, "shadow": shadow,
                  "eq_m": eq_m, "mult": mult, "ext": round(ext, 3),
                  "mults": {"hava": round(wm, 2), "korku": round(fm, 2), "analist": round(km, 2), "haftasonu": round(hm, 2)}}
        return ("limit" if limit else "market"), detail, None

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
                    self.journal(idea["mgr"], idea["sym"], idea["tf"], idea["side"], "reddedildi", why)
                continue
            if kind == "market":
                t = lg.build_trade(idea, d["entry"], d["qty"], d["contracts"], d["lev"], self.cfg, now_ms,
                                   d["eq_m"], src=d["src"], slip_bp=d["slip_bp"], shadow=d["shadow"])
                self.ledger["open"].append(t)
                opened.append({**idea, **t})
                self.journal(idea["mgr"], idea["sym"], idea["tf"], idea["side"],
                             "gölgede açtı" if d["shadow"] else "açtı",
                             f"güven %{idea['conviction'] * 100:.0f}, risk {d['risk_usd']:.2f}$ (%{d['risk_pct']:.2f})"
                             + (f", çarpan ×{d['ext']:g}" if d.get("ext", 1) != 1 else "")
                             + ("; analist: " + "; ".join((idea.get("card") or {}).get("notes") or []) if (idea.get("card") or {}).get("notes") else ""))
            else:
                o = {"oid": uuid.uuid4().hex[:10], "mgr": idea["mgr"], "idea": idea, "px": d["entry"], "qty": d["qty"],
                     "contracts": d["contracts"], "lev": d["lev"], "margin_usd": d["margin_usd"], "shadow": d["shadow"],
                     "eq_m": d["eq_m"], "created": now_ms, "expire_ts": now_ms + (idea.get("limit_valid_ms", 600_000) if idea.get("fallback_market")
                                                           else max(idea.get("limit_valid_ms", 0), 900_000)),
                     "risk_usd": d["risk_usd"]}
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
            if not self.benched(m.id) and dd <= -m.dd_bench:
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
        if now_ms - self.st["last_rebalance"] < days * 86400_000:
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
            out[m.id] = {**m.public(), "active": m.id in self.by_id,
                         "capital": round(cap, 2), "equity": round(eq, 2),
                         "pnl": round(eq - self.st["alloc"].get(m.id, 0), 2),
                         "return_pct": round((eq / self.st["alloc"][m.id] - 1) * 100, 2) if self.st["alloc"].get(m.id) else 0,
                         "realized": round(p["realized"], 2), "unrealized": round(p["unrealized"], 2),
                         "dd_pct": round((eq / peak - 1) * 100, 2) if peak else 0,
                         "open": len(op), "orders": sum(1 for o in self.ledger["orders"] if o["mgr"] == m.id),
                         "heat": round(sum(lg.open_risk(t) for t in op), 2),
                         "benched": self.benched(m.id), "loss_streak": self.loss_streak(m.id),
                         "bench_why": (self.st.get("bench_why") or {}).get(m.id) if self.benched(m.id) else None,
                         "stats": s, "shadow": {"open": len(sho), "closed": len(shc),
                                                "avg_r": round(float(np.mean([t["r"] for t in shc])), 3) if shc else None}}
        return out
