"""Polat: veriyi geldiği anda işleyen, planı olmadan işlem açmayan, her kararını kaydeden tek ajan.

Olay akışı:
  işlem (trade) mesajı → pozisyon varsa: likidasyon / stop / hedef kontrolü + kurala bağlı stop taşıma (sn'de en çok 1)
  5 dk mum kapanışı    → o coin için tam okuma (1G → 4S → 1S → 15dk → 5dk + türev + akış) → kurulum → teyit/veto →
                         risk kapıları → plan → emir
  15 dk mum kapanışı   → yapısal iz stop güncellemesi
  her saniye           → funding kesimi, süre çıkışı
  5 dk'da bir          → coin seçici (hacim + hacim ivmesi), long/short oranı, BTC dominansı
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import brain, extras, selector
from .broker import PaperBroker
from .journal import DISCLAIMER, Journal, report, report_md
from .market import Market
from .okx import Feed, Rest, TF_MS, now_ms
from .risk import Risk, utc_day as _utc_day

VERSION = "1.0"


class Notifier:
    def __init__(self, cfg: dict):
        b = cfg.get("bildirim", {})
        self.on = b.get("telegram", True)
        self.tag = b.get("etiket", "Polat")
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.sent: list[str] = []

    async def send(self, text: str):
        msg = f"{self.tag}\n{text}\n<i>{DISCLAIMER}</i>"
        self.sent.append(msg)
        if not (self.on and self.token and self.chat):
            return
        data = urllib.parse.urlencode({"chat_id": self.chat, "text": msg, "parse_mode": "HTML",
                                       "disable_web_page_preview": "true"}).encode()

        def post():
            req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendMessage", data=data)
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
        try:
            await asyncio.to_thread(post)
        except Exception:  # noqa: BLE001
            pass


class Polat:
    def __init__(self, cfg: dict, root: Path, rest: Rest | None = None, feed_cls=Feed, notifier=None):
        self.cfg = cfg
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.rest = rest or Rest(cfg)
        self.feed_cls = feed_cls
        self.journal = Journal(self.root / "gunluk")
        self.notify = notifier or Notifier(cfg)
        self.market: Market | None = None
        self.feed = None
        self.broker = PaperBroker(cfg, cfg["sermaye_usd"])
        self.risk = Risk(cfg, cfg["sermaye_usd"])
        self.deep: set[str] = set()
        self.btc_rd: dict | None = None
        self.last_manage: dict[str, float] = {}
        self.last_logged_sl: dict[str, float] = {}
        self.tasks: list[asyncio.Task] = []
        self.started = now_ms()
        self.stopping = False
        self.counters = {"degerlendirme": 0, "kurulum": 0, "giris": 0, "karar_ms_max": 0.0}
        self.pending_eval: set[str] = set()

    # ===================================================================================== durum
    @property
    def state_file(self) -> Path:
        return self.root / "durum.json"

    def save(self):
        st = {"version": VERSION, "saved": now_ms(), "broker": self.broker.to_state(), "risk": self.risk.to_state(),
              "deep": sorted(self.deep)}
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self.state_file)
        if self.market:
            (self.root / "ozet.json").write_text(json.dumps(self.status(), ensure_ascii=False, default=str), encoding="utf-8")

    def load(self) -> dict | None:
        if not self.state_file.exists():
            return None
        st = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.broker.load_state(st["broker"])
        self.risk.load_state(st["risk"])
        return st

    def status(self) -> dict:
        m = self.market
        coins = m.coins if m else {}
        eq = self.broker.equity(coins)
        return {"surum": VERSION, "zaman": now_ms(), "baslangic": self.started, "mod": self.cfg["yurutme"]["mod"],
                "ozsermaye": round(eq, 2), "nakit": round(self.broker.cash, 2),
                "pozisyonlar": [{k: p.get(k) for k in ("inst", "side", "entry", "sl", "tp", "qty", "lev", "kurulum", "opened")}
                                for p in self.broker.positions.values()],
                "izlenen": sorted(self.deep), "risk": self.risk.to_state(), "veri": m.stats() if m else {},
                "baglanti": self.feed.stats if self.feed else {}, "sayac": self.counters,
                "global": m.global_ if m else {}, "uyari": DISCLAIMER}

    # ===================================================================================== başlangıç
    async def start(self, run_seconds: float | None = None):
        meta = await self.rest.instruments()
        self.market = m = Market(self.cfg, meta)
        m.on_close = self._on_close
        m.on_trade = self._on_trade
        m.on_book_bad = self._on_book_bad
        st = self.load()
        for t in await self.rest.tickers():
            if t["instId"] in meta:
                m._ticker(t, now_ms())
        self.feed = self.feed_cls(self.cfg, m.handle)
        self.feed.start()
        await self.feed.market_wide(list(meta))
        keep = {p["inst"] for p in self.broker.positions.values()}
        prev = set(st["deep"]) if st else set()
        await self._select(initial=prev | keep)
        if st:
            await self._reconcile_gap(st["saved"])
        self.journal.write("olaylar", {"olay": "basladi", "surum": VERSION, "mod": self.cfg["yurutme"]["mod"],
                                       "ozsermaye": self.broker.equity(m.coins), "izlenen": sorted(self.deep)})
        await self.notify.send(f"Başladı · özsermaye {self.broker.equity(m.coins):,.0f}$ · izlenen {len(self.deep)} coin"
                               f" · açık pozisyon {len(self.broker.positions)}")
        loops = [self._loop_select(), self._loop_second(), self._loop_save(), self._loop_ls(), self._loop_extras(),
                 self._bootstrap_h1_all(list(meta))]
        self.tasks = [asyncio.create_task(x) for x in loops]
        if run_seconds:
            await asyncio.sleep(run_seconds)
            await self.stop()

    async def stop(self, reason: str = "süre doldu"):
        if self.stopping:
            return
        self.stopping = True
        for t in self.tasks:
            t.cancel()
        if self.feed:
            await self.feed.stop()
        self.save()
        self.journal.write("olaylar", {"olay": "durdu", "neden": reason, "veri": self.market.stats() if self.market else {}})

    # ===================================================================================== seçici
    async def _select(self, initial: set[str] | None = None):
        m = self.market
        ranked = selector.rank(m.coins, m.h1_all, self.cfg)
        keep = {p["inst"] for p in self.broker.positions.values()}
        cur = self.deep if initial is None else set(initial)
        chosen, changes = selector.choose(ranked, cur, keep, self.cfg)
        chosen = {x for x in chosen if x in m.coins}
        add = chosen - self.deep
        rem = self.deep - chosen
        for inst in rem:
            await self.feed.unwatch(inst)
            m.coins[inst].deep = False
        await asyncio.gather(*(self._watch(inst) for inst in add))
        self.deep = chosen
        if add or rem:
            self.journal.write("olaylar", {"olay": "secici", "girdi": sorted(add), "cikti": sorted(rem),
                                           "ilk10": [(i, round(s, 3), inf) for i, s, inf in ranked[:10]]})

    async def _watch(self, inst: str):
        m = self.market
        c = m.coin(inst)
        for tf in self.cfg["veri"]["zaman_dilimleri"]:
            try:
                rows = await self.rest.candles(inst, tf, self.cfg["veri"]["gecmis_mum"])
            except Exception as e:  # noqa: BLE001
                self.journal.write("olaylar", {"olay": "gecmis_hata", "inst": inst, "tf": tf, "hata": str(e)[:200]})
                continue
            s = c.series[tf]
            for r in rows:
                if r[7]:
                    s.on_candle(r)
        c.deep = True
        await self.feed.watch(inst)
        asyncio.create_task(self._backfill(inst))

    async def _backfill(self, inst: str):
        """Abonelikten hemen sonra: geçmiş ile canlı akış arasındaki boşluğu (kaçan kapanmış mum) kapat."""
        await asyncio.sleep(3)
        c = self.market.coins.get(inst)
        if not c:
            return
        for tf in self.cfg["veri"]["zaman_dilimleri"]:
            s = c.series[tf]
            if not s.bars:
                continue
            try:
                rows = await self.rest.candles(inst, tf, 5)
            except Exception:  # noqa: BLE001
                continue
            for r in rows:
                if r[7] and r[0] > s.bars[-1][0]:
                    s.on_candle(r)

    async def _bootstrap_h1_all(self, insts: list[str]):
        """Bütün piyasanın son 25 saatlik 1S hacmi (hacim ivmesi için). Hız sınırına takılmadan arka planda."""
        for i, inst in enumerate(insts):
            if self.stopping:
                return
            if inst in self.market.h1_all and len(self.market.h1_all[inst]) >= 20:
                continue
            try:
                for r in await self.rest.candles(inst, "1H", 26):
                    self.market._h1_all(inst, r)
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.06)

    # ===================================================================================== olaylar
    def _on_book_bad(self, c):
        _spawn(self.feed.resync_book(c.inst))

    def _on_close(self, c, tf: str):
        if not c.deep:
            return
        if tf == "5m":
            if c.inst == "BTC-USDT-SWAP":
                self.btc_rd = brain.read_all(self.market, c, c.series["5m"].bars[-1][0] + TF_MS["5m"])
            try:
                self.evaluate(c)
            except Exception as e:  # noqa: BLE001
                self.journal.write("olaylar", {"olay": "degerlendirme_hata", "inst": c.inst, "hata": repr(e)[:300]})

    def _on_trade(self, c, px: float, ts: int):
        if not self.broker.positions:
            return
        evs = self.broker.on_price(c, px, ts)
        for ev in evs:
            self._closed(c, ev)
        t = time.monotonic()
        for p in list(self.broker.positions.values()):
            if p["inst"] != c.inst:
                continue
            if t - self.last_manage.get(p["id"], 0) < 1.0:
                continue
            self.last_manage[p["id"]] = t
            self._manage(c, p, px, ts)

    def _manage(self, c, p: dict, px: float, ts: int):
        s15 = c.series.get("15m")
        swing = None
        if s15:
            if p["side"] == "long" and s15.pivots_lo:
                swing = s15.pivots_lo[-1][1]
            elif p["side"] == "short" and s15.pivots_hi:
                swing = s15.pivots_hi[-1][1]
            if swing is not None and s15.pivots_lo and s15.pivots_lo[-1][0] < p["opened"] and p["side"] == "long":
                swing = None                   # girişten önceki dip iz stop sayılmaz
            if swing is not None and s15.pivots_hi and s15.pivots_hi[-1][0] < p["opened"] and p["side"] == "short":
                swing = None
        for act in self.risk.manage(p, px, ts, swing):
            if act[0] == "kismi":
                ev = self.broker.close(c, p["id"], act[1], ts, "kismi")
                if ev:
                    self._partial(c, p, ev, act[2])
            elif act[0] == "stop":
                self.broker.amend_stop(p["id"], act[1])
                sgn = 1 if p["side"] == "long" else -1
                p["risk_usd_now"] = max(0.0, (p["entry"] - act[1]) * sgn * p["qty"])
                last = self.last_logged_sl.get(p["id"], p["sl0"])
                if abs(act[1] - last) >= 0.25 * p["r_dist"]:
                    self.last_logged_sl[p["id"]] = act[1]
                    self.journal.write("islemler", {"olay": "stop_tasima", "pid": p["id"], "inst": p["inst"],
                                                    "sl": act[1], "neden": act[2], "fiyat": px})
            elif act[0] == "kapat":
                ev = self.broker.close(c, p["id"], 1.0, ts, "sure")
                if ev:
                    ev["reason"] = act[1]
                    self._closed(c, ev)

    async def _loop_second(self):
        while True:
            await asyncio.sleep(1)
            n = now_ms()
            for p in list(self.broker.positions.values()):
                c = self.market.coins.get(p["inst"])
                if not c:
                    continue
                for ev in self.broker.on_funding(c, n):
                    self.journal.write("islemler", {"olay": "funding", **ev})
                if c.price() and p["id"] in self.broker.positions:
                    self._manage(c, p, c.price(), n)
            eq = self.broker.equity(self.market.coins)
            day_before, halted_before = self.risk.st["day"], self.risk.st["halted"]
            if day_before and day_before != _utc_day(n):
                start = self.risk.st["day_start"]
                w = report(self.journal, n)["pencereler"]["1_gun"]["genel"]
                await self.notify.send(f"📒 Gün sonu {day_before} · özsermaye {eq:,.0f}$ ({(eq / start - 1) * 100:+.2f}%) · "
                                       f"işlem {w.get('islem', 0)} · kazanma %{w.get('kazanma', 0)} · toplam {w.get('toplam_r', 0)}R")
                self.write_report()
            self.risk.roll(n, eq)
            if self.risk.st["halted"] and not halted_before:
                self.journal.write("olaylar", {"olay": "durdu_risk", "neden": self.risk.st["halt_reason"], "ozsermaye": eq})
                await self.notify.send(f"⛔ Polat yeni işlem açmayı durdurdu: {self.risk.st['halt_reason']}.\n"
                                       f"Devam için: python -m polat devam")

    # ===================================================================================== karar
    def evaluate(self, c):
        t0 = time.perf_counter()
        m = self.market
        self.counters["degerlendirme"] += 1
        now = c.series["5m"].bars[-1][0] + TF_MS["5m"]
        rd = brain.read_all(m, c, now)
        side = brain.bias(rd, self.btc_rd, c.inst == "BTC-USDT-SWAP")
        if not side:
            return None
        sts = brain.setups(m, c, rd, side)
        if not sts:
            return None
        st = sts[0]
        self.counters["kurulum"] += 1
        teyit, veto = brain.confirmations(m, c, rd, st, self.btc_rd)
        L = side == "long"
        entry = (c.ask if L else c.bid) or c.price()
        sl = st["sl"]
        k, r = self.cfg["kurulum"], self.cfg["risk"]
        atr15 = c.series["15m"].ind.get("atr") or 0
        eq = self.broker.equity(m.coins)
        engel = None
        dist_pct = abs(entry - sl) / entry * 100 if entry else 0
        wall_t = now_ms()
        if (sl - entry) * (1 if L else -1) >= 0:
            engel = "stop yanlış tarafta (fiyat stop seviyesini geçmiş)"
        elif not c.tick_ts and not c.tape:
            engel = "veri yok"
        elif c.tape and wall_t - c.tape[-1][0] > 60_000 and c.tick_ts and wall_t - c.tick_ts > 60_000:
            engel = "veri bayat (60 sn'den eski)"
        elif not c.book.ok:
            engel = "emir defteri doğrulanmadı"
        elif (c.spread_bp() or 99) > r["max_spread_bp"]:
            engel = f"spread {c.spread_bp():.1f} bp > {r['max_spread_bp']}"
        elif dist_pct > r["max_stop_yuzde"]:
            engel = f"stop çok geniş (%{dist_pct:.2f} > %{r['max_stop_yuzde']})"
        elif atr15 and abs(entry - sl) < r["min_stop_atr"] * atr15:
            engel = "stop gürültü bandında (< 0,5 × 15dk ATR)"
        elif any(abs(e["ts"] - wall_t) <= k["takvim_sessizlik_dk"] * 60_000 for e in m.global_.get("events") or []):
            engel = "yüksek etkili ABD verisi yakın (takvim sessizliği)"
        elif veto:
            engel = "veto: " + "; ".join(veto)
        elif len(teyit) < k["min_teyit"]:
            engel = f"teyit yetersiz ({len(teyit)} < {k['min_teyit']})"
        sz = None
        if not engel:
            sz = self.risk.size(eq, entry, sl, c.meta.get("maxLever", 20))
            if not sz:
                engel = "büyüklük hesaplanamadı"
        if not engel:
            engel = self.risk.can_open(now, eq, list(self.broker.positions.values()), c.inst, side, sz["risk_usd"])
        if not engel:
            lv = c.book.levels("asks" if L else "bids", 5, c.ct)
            depth = sum(p * s for p, s in lv)
            if depth < r["min_derinlik_kat"] * sz["notional"]:
                engel = f"derinlik yetersiz (ilk 5 kademe {depth:,.0f}$ < {r['min_derinlik_kat']} × {sz['notional']:,.0f}$)"
        tp = entry * (1 + (1 if L else -1) * self.cfg["cikis"]["hedef_yuzde"] / 100)
        plan = {"giris": entry, "stop": sl, "hedef": tp, "stop_yuzde": round(dist_pct, 3),
                "hedef_r": round(abs(tp - entry) / abs(entry - sl), 2) if entry != sl else None,
                "kismi": f"{self.cfg['cikis']['kismi_r']:g}R'de %{self.cfg['cikis']['kismi_oran'] * 100:.0f}",
                "risk_usd": round(sz["risk_usd"], 2) if sz else None, "kaldirac": sz["lev"] if sz else None}
        rec = {"inst": c.inst, "kurulum": st["ad"], "yon": side, "girildi": False, "engel": engel,
               "gerekce": st["gerekce"], "teyit": teyit, "veto": veto, "plan": plan, "okuma": _compact(rd)}
        if not engel:
            tag = {"kurulum": st["ad"], "sl0": sl, "r_dist": abs(entry - sl), "risk_usd": sz["risk_usd"],
                   "risk_usd_now": sz["risk_usd"], "gerekce": st["gerekce"], "teyit": teyit,
                   "teyit_sayisi": len(teyit), "seans": rd["seans"],
                   "btc_rejim": (self.btc_rd or {}).get("yapi_4H", {}).get("yon"), "okuma_giris": _compact(rd)}
            res = self.broker.open(c, side, sz["qty"], sz["lev"], sl, tp, now_ms(), tag)
            if not res["ok"]:
                rec["engel"] = "emir: " + res["neden"]
            else:
                p = res["pos"]
                # gerçek dolum fiyatına göre R mesafesi ve hedef yeniden
                p["r_dist"] = abs(p["entry"] - sl)
                p["tp"] = p["entry"] * (1 + (1 if L else -1) * self.cfg["cikis"]["hedef_yuzde"] / 100)
                p["risk_usd"] = p["risk_usd_now"] = p["r_dist"] * p["qty"]
                self.risk.on_open()
                rec["girildi"] = True
                rec["pid"] = p["id"]
                self.counters["giris"] += 1
                self.journal.write("islemler", {"olay": "acilis", "pid": p["id"], "inst": c.inst, "yon": side,
                                                "kurulum": st["ad"], "giris": p["entry"], "sl": sl, "tp": p["tp"],
                                                "miktar": p["qty"], "kaldirac": p["lev"], "marj": p["margin"],
                                                "risk_usd": p["risk_usd"], "kayma_bp": p["slip_bp"], "ucret": p["fees"],
                                                "gerekce": st["gerekce"], "teyit": teyit, "plan": plan})
                _spawn(self.notify.send(
                    f"<b>{'🟢 LONG' if L else '🔴 SHORT'} {c.sym}</b> · {st['ad']}\n"
                    f"Giriş {p['entry']:.6g} · Stop {sl:.6g} (%{dist_pct:.2f}) · Hedef {p['tp']:.6g} (%12,5)\n"
                    f"Risk {p['risk_usd']:,.0f}$ · {p['lev']}x\n"
                    f"Gerekçe: {'; '.join(st['gerekce'])}\nTeyit ({len(teyit)}): {'; '.join(teyit)}"))
        self.journal.write("kararlar", rec)
        dt = (time.perf_counter() - t0) * 1000
        self.counters["karar_ms_max"] = max(self.counters["karar_ms_max"], round(dt, 2))
        return rec

    def _partial(self, c, p, ev, why):
        self.journal.write("islemler", {"olay": "kismi", "pid": p["id"], "inst": p["inst"], "fiyat": ev["px"],
                                        "miktar": ev["qty"], "pnl": ev["pnl"], "ucret": ev["fee"], "neden": why})

    def _closed(self, c, ev: dict):
        p = ev.get("pos") or {}
        pid = ev["pid"]
        net = p.get("realized", 0) - p.get("fees", 0) - p.get("funding", 0)
        risk0 = p.get("risk_usd") or 1
        R = net / risk0
        dur_h = (ev["ts"] - p.get("opened", ev["ts"])) / 3.6e6
        errs = []
        reason = ev["reason"]
        if reason == "stop" and dur_h < 0.25:
            errs.append("ilk 15 dk'da stop (giriş zamanlaması)")
        if ev.get("slip_bp", 0) > 10 or p.get("slip_bp", 0) > 10:
            errs.append("kayma > 10 bp")
        if reason == "likidasyon":
            errs.append("likidasyon (kaldıraç/stop hatası)")
        mfe_r = p.get("mfe", 0) / p["r_dist"] if p.get("r_dist") else 0
        if mfe_r >= 1 and R <= 0:
            errs.append("1R+ kârdayken zararla kapandı")
        rec = {"olay": "kapanis", "pid": pid, "inst": p.get("inst"), "yon": p.get("side"), "kurulum": p.get("kurulum"),
               "neden": reason, "giris": p.get("entry"), "cikis": ev["px"], "r": round(R, 3), "pnl_net": round(net, 2),
               "brut": round(p.get("realized", 0), 2), "ucret": round(p.get("fees", 0), 2),
               "funding": round(p.get("funding", 0), 2), "slip_bp": ev.get("slip_bp", 0), "sure_saat": round(dur_h, 2),
               "mfe_r": round(mfe_r, 2), "mae_r": round(p.get("mae", 0) / p["r_dist"], 2) if p.get("r_dist") else None,
               "teyit_sayisi": p.get("teyit_sayisi"), "seans": p.get("seans"), "btc_rejim": p.get("btc_rejim"),
               "gerekce": p.get("gerekce"), "teyit": p.get("teyit"), "hatalar": errs,
               "okuma_giris": p.get("okuma_giris"), "ts": ev["ts"]}
        try:
            rec["okuma_cikis"] = _compact(brain.read_all(self.market, c, ev["ts"])) if c.deep else None
        except Exception:  # noqa: BLE001
            rec["okuma_cikis"] = None
        self.journal.write("islemler", rec)
        self.risk.on_close(ev["ts"], p.get("inst", ""), p.get("side", ""), net, reason)
        self.last_manage.pop(pid, None)
        self.last_logged_sl.pop(pid, None)
        icon = "✅" if net > 0 else "❌"
        _spawn(self.notify.send(
            f"{icon} <b>{c.sym}</b> kapandı · {reason} · {R:+.2f}R · {net:+,.0f}$ · {dur_h:.1f} saat"
            + (f"\nHata: {'; '.join(errs)}" if errs else "")))

    # ===================================================================================== döngüler
    async def _loop_select(self):
        while True:
            await asyncio.sleep(self.cfg["secici"]["yenileme_dk"] * 60)
            try:
                await self._select()
            except Exception as e:  # noqa: BLE001
                self.journal.write("olaylar", {"olay": "secici_hata", "hata": repr(e)[:300]})

    async def _loop_save(self):
        while True:
            await asyncio.sleep(self.cfg["devir"]["kayit_sn"])
            self.save()

    async def _loop_ls(self):
        while True:
            for inst in list(self.deep):
                v = await self.rest.long_short(inst)
                c = self.market.coins.get(inst)
                if v is not None and c:
                    c.ls_ratio, c.ls_ts = v, now_ms()
                await asyncio.sleep(0.5)
            await asyncio.sleep(self.cfg["veri"]["long_short_dk"] * 60)

    async def _loop_extras(self):
        v = self.cfg["veri"]
        g = self.market.global_
        last = {"dom": 0, "st": 0, "cal": 0}
        while True:
            n = now_ms()
            if n - last["dom"] >= v["dominans_dk"] * 60_000:
                d = await extras.btc_dominance()
                if d is not None:
                    g["btc_dom"], g["btc_dom_ts"] = d, n
                last["dom"] = n
            if n - last["st"] >= v["stablecoin_dk"] * 60_000:
                s = await extras.stablecoins()
                if s:
                    g["stable_usd"], g["stable_chg24"], g["stable_ts"] = s[0], s[1], n
                last["st"] = n
            if n - last["cal"] >= v["takvim_saat"] * 3_600_000:
                ev = await extras.calendar()
                if ev is not None:
                    g["events"] = ev
                last["cal"] = n
            await asyncio.sleep(30)

    async def _reconcile_gap(self, since_ms: int):
        """Devir arasında (kayıt → yeniden başlama) stop/hedef tetiklendi mi? 1 dk mumlarla, muhafazakâr:
        aynı mumda ikisi de varsa stop sayılır."""
        for p in list(self.broker.positions.values()):
            c = self.market.coins.get(p["inst"])
            if not c:
                continue
            try:
                rows = await self.rest.candles_since(p["inst"], "1m", since_ms)
            except Exception:  # noqa: BLE001
                continue
            L = p["side"] == "long"
            for r in rows:
                lo, hi = r[3], r[2]
                hit_sl = (L and lo <= p["sl"]) or (not L and hi >= p["sl"])
                hit_tp = p.get("tp") and ((L and hi > p["tp"]) or (not L and lo < p["tp"]))
                if hit_sl or hit_tp:
                    ev = self.broker.close(c, p["id"], 1.0, r[0] + 60_000, "stop" if hit_sl else "hedef",
                                           px_hint=p["sl"] if hit_sl else p["tp"], maker=not hit_sl)
                    if ev:
                        ev["reason"] += " (devir arası)"
                        self._closed(c, ev)
                    break

    def write_report(self):
        rep = report(self.journal)
        (self.root / "RAPOR.md").write_text(report_md(rep), encoding="utf-8")
        return rep


def _spawn(coro):
    """Çalışan olay döngüsü varsa arka plan görevi başlat; yoksa (senkron test) sessizce bırak."""
    try:
        return asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return None


def _compact(rd: dict) -> dict:
    """Günlüğe yazılacak okuma: her şey, ama seviyeler yalnızca fiyata en yakın 8 tanesi."""
    out = dict(rd)
    px = rd.get("fiyat") or 0
    lv = rd.get("seviyeler") or []
    out["seviyeler"] = sorted(lv, key=lambda x: abs(x["px"] - px))[:8]
    return out
