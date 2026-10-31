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

from . import brain, extras, karar, selector
from .learn import Learner
from .broker import PaperBroker
from .journal import DISCLAIMER, Journal, report, report_md
from .market import Market
from .okx import Feed, Rest, TF_MS, now_ms
from .risk import Risk, utc_day as _utc_day

VERSION = "2.0"


def new_position_authorized(cfg: dict) -> bool:
    """Eski bağımsız ajanlar varsayılan olarak challenger'dır; yeni pozisyon yetkisi açıkça verilir."""
    return bool((cfg.get("yetki") or {}).get("yeni_pozisyon", False))


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
        self.counters = {"degerlendirme": 0, "sinyal": 0, "giris": 0, "karar_ms_max": 0.0, "engel": {},
                         "defter_yeniden": 0}
        self._book_resync_at: dict[str, float] = {}
        self.learner = Learner(cfg, self.root)
        self.learner.risk = self.risk
        self._apply_adjust()
        self.eval_q: asyncio.Queue | None = None
        self.queued: set[str] = set()
        self.last_eval: dict[str, float] = {}
        self.key_levels: dict[str, list[float]] = {}
        self.dom_hist: list[tuple[int, float]] = []
        self.recent_closes: list[dict] = [x for x in self.journal.read("islemler") if x.get("olay") == "kapanis"][-50:]
        self.last_report: dict = {}
        # Nöbet kapısı (__main__ bağlar): nöbet kanıtlı değilse yeni işlem açılmaz → iki Polat aynı anda işlem yapamaz.
        self.lease_gate = None
        # Durum diskten yüklenmeden save() çağrılmaz: başlatma yarıda kalırsa boş defter gerçek durumun üstüne yazılmaz.
        self.state_ready = False

    # ===================================================================================== durum
    @property
    def state_file(self) -> Path:
        return self.root / "durum.json"

    def save(self):
        if getattr(self, "standby", False) or not self.state_ready:
            return                                   # halef ısınırken / durum yüklenmeden selefin dosyasına dokunmaz
        self.journal.flush()
        st = {"version": VERSION, "saved": now_ms(), "broker": self.broker.to_state(), "risk": self.risk.to_state(),
              "deep": sorted(self.deep), "learner": self.learner.to_state(),
              "liq_calib": self.market.liq_calib if self.market else None}
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
        self.learner.load_state(st.get("learner"))
        return st

    def status(self) -> dict:
        m = self.market
        coins = m.coins if m else {}
        eq = self.broker.equity(coins)
        pos = []
        for p in self.broker.positions.values():
            c = coins.get(p["inst"])
            px = c.price() if c else p["entry"]
            sgn = 1 if p["side"] == "long" else -1
            pos.append({**{k: p.get(k) for k in ("inst", "side", "entry", "sl", "tp", "qty", "lev", "kurulum", "opened",
                                                   "puan", "yol", "partial_done", "r_dist")},
                        "fiyat": px, "pnl_usd": round((px - p["entry"]) * p["qty"] * sgn + p.get("realized", 0) - p.get("fees", 0)
                                                     - p.get("funding", 0), 2),
                        "r_simdi": round((px - p["entry"]) * sgn / p["r_dist"], 2) if p.get("r_dist") else None})
        rep = self.last_report or {}
        return {"surum": VERSION, "zaman": now_ms(), "baslangic": self.started, "mod": self.cfg["yurutme"]["mod"],
                "sermaye": self.cfg["sermaye_usd"], "ozsermaye": round(eq, 2), "nakit": round(self.broker.cash, 2),
                "pozisyonlar": pos, "son_islemler": [{k: x.get(k) for k in ("inst", "yon", "kurulum", "neden", "giris", "cikis",
                                                                         "r", "pnl_net", "sure_saat", "hatalar", "ts")}
                                                    for x in self.recent_closes[-30:]][::-1],
                "izlenen": sorted(self.deep), "risk": self.risk.to_state(), "veri": m.stats() if m else {},
                "baglanti": self.feed.stats if self.feed else {}, "sayac": self.counters,
                "rest": {"istek": getattr(self.rest, "calls", 0), "tekrar": getattr(self.rest, "retries", 0),
                         "rtt_ms": {k: round(v, 1) for k, v in (getattr(self.rest, "rtt", {}) or {}).items()}},
                "likidasyon_kalibrasyon": m.liq_calib if m else None, "global": m.global_ if m else {},
                "kiyas": rep.get("kiyas"), "ogrenme": rep.get("ogrenme"),
                "pencereler": {k: v.get("genel") for k, v in (rep.get("pencereler") or {}).items()},
                "yollar": sorted(({"yol": k, **v} for k, v in self.learner.paths.items() if v.get("karar")),
                                 key=lambda x: -abs(x["sumR"]))[:20],
                "ayar": self.learner.adjust, "bekleme": getattr(self, "standby", False), "uyari": DISCLAIMER}

    # ===================================================================================== başlangıç
    async def start(self, run_seconds: float | None = None, standby: bool = False):
        """standby=True: halef olarak ısın (veri, abonelikler, göstergeler hazır) ama işlem yapma; activate() ile devral."""
        self.standby = standby
        try:
            rtt = await self.rest.pick_fastest()
            if rtt and "aws" in self.rest.bases[0]:                 # en hızlı AWS uç noktasıysa WS'de de onu öne al
                for k in ("ws_public", "ws_business"):
                    self.cfg["veri"][k] = sorted(self.cfg["veri"][k], key=lambda u: 0 if "wsaws" in u else 1)
            self.journal.write("olaylar", {"olay": "sunucu_secimi", "rtt_ms": {k: round(v, 1) for k, v in rtt.items()}})
        except Exception:  # noqa: BLE001
            pass
        meta = await self.rest.instruments()
        journal_path = Path(os.getenv("TMPDIR", "/tmp")) / f"polat-market-events-{os.getpid()}.jsonl"
        self.market = m = Market(self.cfg, meta, event_journal_path=journal_path)
        m.on_close = self._on_close
        m.on_trade = self._on_trade
        m.on_book_bad = self._on_book_bad
        m.on_liq = self._on_liq
        st = None if standby else self.load()
        if not standby:
            self.state_ready = True
        if st and st.get("liq_calib"):
            m.liq_calib = st["liq_calib"]
        self.eval_q = asyncio.Queue()
        for t in await self.rest.tickers():
            if t["instId"] in meta:
                m._ticker(t, now_ms())
        btc = m.coins.get("BTC-USDT-SWAP")
        if not self.learner.bench.get("btc_baslangic") and btc and btc.last:
            self.learner.bench.update(btc_baslangic=btc.last, baslangic_ts=now_ms())
            self.learner.save_weights()
        self.feed = self.feed_cls(self.cfg, m.handle, on_disconnect=m.disconnect_channels)
        self.feed.start()
        await self.feed.market_wide(list(meta))
        keep = {p["inst"] for p in self.broker.positions.values()}
        prev = set(st["deep"]) if st else self._peek_deep()
        await self._select(initial=prev | keep)
        if st:
            await self._reconcile_gap(st["saved"])
        if standby:
            loops = [self._loop_select(), self._bootstrap_h1_all(list(meta)), self._loop_eval(), self._loop_extras()]
            self.tasks = [asyncio.create_task(x) for x in loops]
            return
        self.journal.write("olaylar", {"olay": "basladi", "surum": VERSION, "mod": self.cfg["yurutme"]["mod"],
                                       "ozsermaye": self.broker.equity(m.coins), "izlenen": sorted(self.deep)})
        await self.notify.send(f"Başladı · özsermaye {self.broker.equity(m.coins):,.0f}$ · izlenen {len(self.deep)} coin"
                               f" · açık pozisyon {len(self.broker.positions)}")
        loops = [self._loop_select(), self._loop_second(), self._loop_save(), self._loop_ls(), self._loop_extras(),
                 self._bootstrap_h1_all(list(meta)), self._loop_eval(), self._loop_learn(), self._loop_fast()]
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
        if self.market:
            self.market.close()
        self.journal.write("olaylar", {"olay": "durdu", "neden": reason, "veri": self.market.stats() if self.market else {}})
        self.journal.flush()

    # ===================================================================================== seçici
    def _peek_deep(self) -> set[str]:
        try:
            return set(json.loads(self.state_file.read_text(encoding="utf-8")).get("deep", []))
        except Exception:  # noqa: BLE001
            return set()

    async def activate(self) -> dict:
        """Halef devralıyor: selefin son durumunu yükle, aradaki birkaç saniyeyi 1 dk mumlarla kapat, işleme başla.
        Klasör az önce uzak durumla değiştirildi: günlük dizini ve son işlemler de diskten yeniden okunur."""
        self.journal.flush()
        self.journal = Journal(self.root / "gunluk")
        self.recent_closes = [x for x in self.journal.read("islemler") if x.get("olay") == "kapanis"][-50:]
        st = self.load()
        self.state_ready = True
        if st and st.get("liq_calib"):
            self.market.liq_calib = st["liq_calib"]
        self.learner.__init__(self.cfg, self.root)                 # ağırlıklar/yollar diskten yeniden
        self.learner.risk = self.risk
        self._apply_adjust()
        for p in self.broker.positions.values():
            if p["inst"] not in self.deep:
                await self._watch(p["inst"])
                self.deep.add(p["inst"])
        if st:
            await self._reconcile_gap(st["saved"])
        self.standby = False
        for t in self.tasks:
            t.cancel()
        self.tasks = [asyncio.create_task(x) for x in (
            self._loop_select(), self._loop_second(), self._loop_save(), self._loop_ls(), self._loop_extras(),
            self._loop_eval(), self._loop_learn(), self._loop_fast())]
        gap = (now_ms() - st["saved"]) / 1000 if st else None
        self.journal.write("olaylar", {"olay": "devraldi", "bosluk_sn": gap, "pozisyon": len(self.broker.positions)})
        return {"bosluk_sn": gap}

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
        # öncelik: açık pozisyonlu coinler → BTC/ETH → diğerleri (her coin verisi gelir gelmez değerlendirmeye girer)
        first = [x for x in add if x in keep or x in set(self.cfg["secici"].get("sabit", []))]
        await asyncio.gather(*(self._watch(inst) for inst in first))
        await asyncio.gather(*(self._watch(inst) for inst in add if inst not in first))
        self.deep = chosen
        if add or rem:
            self.journal.write("olaylar", {"olay": "secici", "girdi": sorted(add), "cikti": sorted(rem),
                                           "ilk10": [(i, round(s, 3), inf) for i, s, inf in ranked[:10]]})

    async def _watch(self, inst: str):
        m = self.market
        c = m.coin(inst)
        for tf in self.cfg["veri"]["zaman_dilimleri"]:
            rows = None
            for attempt in range(3):                      # geçici hatada 3 deneme
                try:
                    rows = await self.rest.candles(inst, tf, self.cfg["veri"]["gecmis_mum"])
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 2:
                        self.journal.write("olaylar", {"olay": "gecmis_hata", "inst": inst, "tf": tf, "hata": str(e)[:200]})
                    await asyncio.sleep(1 + attempt)
            if rows is None:
                continue
            s = c.series[tf]
            for r in rows:
                if r[7]:
                    s.on_candle(r)
        try:
            for ts, oi in await self.rest.oi_history(inst):
                c.oi_h1.append((ts, oi))
        except Exception:  # noqa: BLE001
            pass
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
    def _on_liq(self, c, side: str, usd: float, px: float):
        """Gerçek likidasyon geldi: tahmini kümelerden birine (±%0,5) düştü mü? Düştüyse o kaldıracın ağırlığı artar.
        Ayrıca likidasyon patlaması anlık değerlendirme tetikler."""
        cal = self.market.liq_calib
        lm = c.liq_map
        if lm and lm.get("kumeler") and px:
            cal["olay"] += 1
            best = min(lm["kumeler"], key=lambda k: abs(k[0] - px))
            if abs(best[0] / px - 1) <= 0.005:
                cal["isabet"] += 1
                key = str(best[2])
                cal["lev"][key] = cal["lev"].get(key, 1.0) + 1.0
        if c.deep:
            self._enqueue(c.inst, anlik=True)

    def _on_book_bad(self, c):
        # Bozuk defter her güncellemede (100 ms) yeniden bildirilir; coin başına en çok 20 sn'de bir yeniden iste.
        # (Eskiden her mesajda istek gidiyordu → OKX 60014 hız sınırı fırtınası.)
        now = time.monotonic()
        last = self._book_resync_at.get(c.inst, 0.0)
        if now - last < 20.0:
            return
        self._book_resync_at[c.inst] = now
        self.counters["defter_yeniden"] = self.counters.get("defter_yeniden", 0) + 1
        _spawn(self.feed.resync_book(c.inst))

    def _on_close(self, c, tf: str):
        if not c.deep:
            return
        if tf == "5m" and c.inst == "BTC-USDT-SWAP":
            self.btc_rd = brain.read_all(self.market, c, c.series["5m"].bars[-1][0] + TF_MS["5m"])
        if tf == "1m":
            self._enqueue(c.inst)                       # her dakika kapanışında tam değerlendirme

    def _enqueue(self, inst: str, anlik: bool = False):
        if self.eval_q is None or inst in self.queued:
            return
        if anlik and time.monotonic() - self.last_eval.get(inst, 0) < self.cfg["karar"]["anlik_aralik_sn"]:
            return
        self.queued.add(inst)
        self.eval_q.put_nowait(inst)

    async def _loop_eval(self):
        """Değerlendirmeler sırayla; her biri arasında olay döngüsüne nefes (stop/hedef kontrolleri gecikmesin)."""
        while True:
            inst = await self.eval_q.get()
            self.queued.discard(inst)
            c = self.market.coins.get(inst)
            if c and c.deep and c.series["15m"].n >= 60 and not getattr(self, "standby", False):
                self.last_eval[inst] = time.monotonic()
                try:
                    # ağır hesapları parçala: her zaman dilimi ayrı adım, aralarda olay döngüsüne söz ver
                    for tf, srs in c.series.items():
                        if srs.n >= 60:
                            srs.ext()
                            await asyncio.sleep(0)
                            if tf in ("4H", "1H", "15m", "5m"):
                                for part in srs.PARTS:
                                    srs.pattern_part(part)
                                    await asyncio.sleep(0)
                    self.evaluate(c)
                except Exception as e:  # noqa: BLE001
                    self.journal.write("olaylar", {"olay": "degerlendirme_hata", "inst": inst, "hata": repr(e)[:300]})
            await asyncio.sleep(0)

    async def _loop_fast(self):
        """50 ms: dolum penceresi dolan stop-market emirlerini kapat (işlem akışı dursa bile)."""
        while True:
            await asyncio.sleep(0.05)
            if self.broker.pending:
                for ev in self.broker.flush(self.market.coins, now_ms()):
                    c = self.market.coins.get(ev["inst"])
                    if c:
                        self._closed(c, ev)

    async def _loop_learn(self):
        while True:
            await asyncio.sleep(self.cfg["ogrenme"]["guncelleme_dk"] * 60)
            res = self.learner.update(now_ms())
            self.journal.write("olaylar", {"olay": "ogrenme", **res})

    def _on_trade(self, c, px: float, ts: int):
        if getattr(self, "standby", False):
            return
        if self.learner.active:
            self.learner.on_price(c.inst, px, ts, swing=lambda side, opened: self._swing(c, side, opened))
        kl = self.key_levels.get(c.inst)
        if kl and c.deep:
            prev = getattr(c, "_prev_px", None)
            if prev is not None and any((prev - L) * (px - L) < 0 for L in kl):
                self._enqueue(c.inst, anlik=True)       # önemli seviye geçildi → mum kapanışını beklemeden bak
            c._prev_px = px
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

    def _swing(self, c, side: str, opened: int):
        s15 = c.series.get("15m")
        if not s15:
            return None
        piv = s15.pivots_lo if side == "long" else s15.pivots_hi
        return piv[-1][1] if piv and piv[-1][0] >= opened else None

    def _apply_adjust(self):
        """Hatalardan çıkan ayarlar (sınırlı aralıkta) çalışan yapılandırmaya uygulanır."""
        a = self.learner.adjust
        if "min_stop_atr" in a:
            self.cfg["risk"]["min_stop_atr"] = a["min_stop_atr"]
        if "basabas_r" in a:
            self.cfg["cikis"]["basabas_r"] = a["basabas_r"]

    def _post_mortem(self, rec: dict):
        """Gerçek işlemden ders: hata sınıfına göre sınırlı ayar (her biri dersler.jsonl'e yazılır).
        Kazançlı işlemde yol benimsenir; ayarlar zamanla başlangıç değerine geri yaklaşır (aşırı tepki yok)."""
        a = self.learner.adjust
        base = {"min_stop_atr": 0.5, "basabas_r": 1.0}
        errs = rec.get("hatalar") or []
        changed = {}
        if any("ilk 15 dk" in e for e in errs):
            a["min_stop_atr"] = round(min(1.2, a.get("min_stop_atr", base["min_stop_atr"]) + 0.05), 3)
            changed["min_stop_atr"] = a["min_stop_atr"]
        if any("1R+ kârdayken" in e for e in errs):
            a["basabas_r"] = round(max(0.7, a.get("basabas_r", base["basabas_r"]) - 0.05), 3)
            changed["basabas_r"] = a["basabas_r"]
        if any("kayma" in e for e in errs) and rec.get("inst"):
            a.setdefault("kayma_cezasi", {})[rec["inst"]] = a.get("kayma_cezasi", {}).get(rec["inst"], 0) + 1
            changed["kayma_cezasi"] = rec["inst"]
        if not errs:
            for k, v0 in base.items():                    # hatasız işlemler ayarı yavaşça başlangıca çeker
                if k in a:
                    a[k] = round(a[k] + (v0 - a[k]) * 0.2, 3)
        if changed:
            self.learner._lesson({"olay": "hata_ayari", "inst": rec.get("inst"), "hatalar": errs, "degisen": changed})
        self._apply_adjust()
        self.learner.save_weights()

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
            if getattr(self, "standby", False):
                continue
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
        """Tam okuma → bütün okumaların oyu → tetik → yapısal stop → sert kapılar → risk → emir. Her sinyal gölgede de takip edilir."""
        t0 = time.perf_counter()
        m = self.market
        self.counters["degerlendirme"] += 1
        now = now_ms()
        rd = karar.full_read(m, c, now)
        self.key_levels[c.inst] = [x["px"] for x in rd.get("seviyeler") or []] + list(rd.get("yuvarlak") or [])
        dec = karar.decide(m, c, rd, self.btc_rd, self.learner.combined_mult(), self.learner.cat_mult, self.cfg,
                           path_rule=self.learner.path_rule)
        d = dec["yon"]
        L = d > 0
        r, k = self.cfg["risk"], self.cfg["kurulum"]
        entry = (c.ask if L else c.bid) or c.price()
        engel = dec["engel"]
        if not engel and self.lease_gate is not None:
            engel = self.lease_gate()
        if not engel and not new_position_authorized(self.cfg):
            engel = "yetki: araştırma/challenger modu — yeni pozisyon açma izni yok"
        sl, sl_why = (None, "")
        if d and entry:
            sl, sl_why = karar.structural_stop(c, rd, d, entry, r["min_stop_atr"])
        if not engel and not sl:
            engel = "stop yeri bulunamadı"
        eq = self.broker.equity(m.coins)
        dist_pct = abs(entry - sl) / entry * 100 if (entry and sl) else 0
        if not engel:
            engel = self._hard_gates(c, rd, dec, entry, sl, dist_pct, now)
        sz = None
        if not engel:
            sz = self.risk.size(eq, entry, sl, c.meta.get("maxLever", 20))
            engel = "büyüklük hesaplanamadı" if not sz else self.risk.can_open(
                now, eq, list(self.broker.positions.values()), c.inst, "long" if L else "short", sz["risk_usd"])
        if not engel:
            lv = c.book.levels("asks" if L else "bids", 5, c.ct)
            depth = sum(p * q for p, q in lv)
            pen = 1 + 0.5 * self.learner.adjust.get("kayma_cezasi", {}).get(c.inst, 0)
            if depth < r["min_derinlik_kat"] * pen * sz["notional"]:
                engel = f"derinlik yetersiz (ilk 5 kademe {depth:,.0f}$ < {r['min_derinlik_kat']} × {sz['notional']:,.0f}$)"
        tp = entry * (1 + (1 if L else -1) * self.cfg["cikis"]["hedef_yuzde"] / 100) if entry else None
        plan = {"giris": entry, "stop": sl, "stop_neden": sl_why, "hedef": tp, "stop_yuzde": round(dist_pct, 3),
                "hedef_r": round(abs(tp - entry) / abs(entry - sl), 2) if (sl and entry != sl) else None,
                "kismi": f"{self.cfg['cikis']['kismi_r']:g}R'de %{self.cfg['cikis']['kismi_oran'] * 100:.0f}",
                "risk_usd": round(sz["risk_usd"], 2) if sz else None, "kaldirac": sz["lev"] if sz else None}
        dec["engel"] = engel
        side = "long" if L else "short"
        taken = False
        if not engel:
            tag = {"kurulum": "+".join(dec["tetik_yonde"][:3]), "sl0": sl, "r_dist": abs(entry - sl),
                   "risk_usd": sz["risk_usd"], "risk_usd_now": sz["risk_usd"], "gerekce": dec["tetik_yonde"],
                   "teyit": _top_votes(dec, d), "teyit_sayisi": sum(1 for v in dec["kategoriler"].values() if v * d > 0),
                   "puan": dec["S"], "puan_temel": dec.get("S_temel"), "yol": dec.get("yol"), "oylar": dec["oylar"],
                   "kategoriler": dec["kategoriler"], "seans": rd["seans"],
                   "btc_rejim": (self.btc_rd or {}).get("yapi_4H", {}).get("yon"), "okuma_giris": _compact(rd)}
            res = self.broker.open(c, side, sz["qty"], sz["lev"], sl, tp, now, tag)
            if not res["ok"]:
                dec["engel"] = engel = "emir: " + res["neden"]
            else:
                taken = True
                p = res["pos"]
                p["r_dist"] = abs(p["entry"] - sl)
                p["tp"] = p["entry"] * (1 + (1 if L else -1) * self.cfg["cikis"]["hedef_yuzde"] / 100)
                p["risk_usd"] = p["risk_usd_now"] = p["r_dist"] * p["qty"]
                self.risk.on_open()
                self.counters["giris"] += 1
                self.journal.write("islemler", {"olay": "acilis", "pid": p["id"], "inst": c.inst, "yon": side,
                                                "kurulum": tag["kurulum"], "giris": p["entry"], "sl": sl, "tp": p["tp"],
                                                "miktar": p["qty"], "kaldirac": p["lev"], "marj": p["margin"],
                                                "risk_usd": p["risk_usd"], "kayma_bp": p["slip_bp"], "ucret": p["fees"],
                                                "puan": dec["S"], "kategoriler": dec["kategoriler"], "tetik": dec["tetik_yonde"],
                                                "oylar": dec["oylar"], "plan": plan})
                cats = " · ".join(f"{kk} {vv:+.2f}" for kk, vv in sorted(dec["kategoriler"].items(), key=lambda x: -abs(x[1]))[:6])
                _spawn(self.notify.send(
                    f"<b>{'🟢 LONG' if L else '🔴 SHORT'} {c.sym}</b> · puan {dec['S']:+.2f}\n"
                    f"Giriş {p['entry']:.6g} · Stop {sl:.6g} (%{dist_pct:.2f}, {sl_why}) · Hedef {p['tp']:.6g} (%12,5)\n"
                    f"Risk {p['risk_usd']:,.0f}$ · {p['lev']}x\nTetik: {', '.join(dec['tetik_yonde'][:4])}\n{cats}"))
        if d:
            trend_dir = karar._sgn((dec["kategoriler"].get("trend") or 0))
            if plan["giris"] and plan["stop"]:
                self.learner.record(c, dec, plan, taken, now, trend_dir)
        if dec.get("tetik_yonde") and abs(dec["S"]) >= self.cfg["ogrenme"]["golge_min_puan"]:
            self.counters["sinyal"] += 1
            self.journal.write("kararlar", {"inst": c.inst, "yon": side if d else None, "girildi": taken, "engel": engel,
                                            "puan": dec["S"], "esik": dec["esik"], "kategoriler": dec["kategoriler"],
                                            "tetik": dec["tetikler"], "oylar": dec["oylar"], "plan": plan, "okuma": _compact(rd)})
        else:
            key = (engel or "").split(" (")[0].split(":")[0][:40]
            self.counters["engel"][key] = self.counters["engel"].get(key, 0) + 1
        dt = (time.perf_counter() - t0) * 1000
        self.counters["karar_ms_max"] = max(self.counters["karar_ms_max"], round(dt, 2))
        return dec

    def _hard_gates(self, c, rd, dec, entry, sl, dist_pct, now) -> str | None:
        r, k = self.cfg["risk"], self.cfg["kurulum"]
        d = dec["yon"]
        atr15 = (rd["ext"].get("15m") or {}).get("atr") or 0
        if (sl - entry) * d >= 0:
            return "stop yanlış tarafta"
        if not c.tick_ts and not c.tape:
            return "veri yok"
        last = max(c.tape[-1][0] if c.tape else 0, c.tick_ts or 0)
        if now - last > 60_000:
            return "veri bayat (60 sn'den eski)"
        if not c.book.ok:
            return "emir defteri doğrulanmadı"
        sp = c.spread_bp()
        if sp is None or sp > r["max_spread_bp"]:
            return f"spread {sp} bp > {r['max_spread_bp']}"
        if dist_pct > r["max_stop_yuzde"]:
            return f"stop çok geniş (%{dist_pct:.2f} > %{r['max_stop_yuzde']})"
        if atr15 and abs(entry - sl) < r["min_stop_atr"] * atr15:
            return "stop gürültü bandında"
        if any(abs(e["ts"] - now) <= k["takvim_sessizlik_dk"] * 60_000 for e in self.market.global_.get("events") or []):
            return "yüksek etkili ABD verisi yakın (takvim sessizliği)"
        fa = rd["turev"].get("fonlama_yillik")
        if fa is not None and ((d > 0 and fa > 2 * k["fonlama_kalabalik_long"]) or (d < 0 and fa < 2 * k["fonlama_kalabalik_short"])):
            return f"fonlama aşırı kalabalık (%{fa})"
        wall = rd["akis"].get("duvar_ust" if d > 0 else "duvar_alt")
        if wall and abs(wall["px"] - entry) < abs(entry - sl):
            return f"karşıda kalıcı duvar 1R'den yakın ({wall['px']:.6g})"
        return None

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
        self.recent_closes = (self.recent_closes + [rec])[-50:]
        self.risk.on_close(ev["ts"], p.get("inst", ""), p.get("side", ""), net, reason)
        self.learner.learn_from({"R": R, "d": 1 if p.get("side") == "long" else -1, "oylar": p.get("oylar") or {},
                                 "yol": p.get("yol"), "S": p.get("puan"), "S_temel": p.get("puan_temel")}, real=True)
        self._post_mortem(rec)
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
                    self.dom_hist.append((n, d))
                    self.dom_hist = [x for x in self.dom_hist if x[0] >= n - 7 * 3_600_000]
                    old = next((x for x in self.dom_hist if x[0] >= n - 6 * 3_600_000), None)
                    g["btc_dom_chg"] = round(d - old[1], 3) if old and old[0] < n - 3_600_000 else None
                last["dom"] = n
            g["haber_tepki"] = self._news_reaction(n)
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

    def _news_reaction(self, now: int) -> float | None:
        """Haber beklentiden saparsa piyasa tepki verir (txt). Yüksek etkili veriden 15 dk sonra BTC'nin o 15 dk'lık
        hareketi ATR cinsinden okunur ve 2 saat boyunca oy olarak kullanılır."""
        btc = self.market.coins.get("BTC-USDT-SWAP") if self.market else None
        if not btc or not btc.series.get("1m") or not btc.series["1m"].bars:
            return None
        for e in self.market.global_.get("events") or []:
            t = e["ts"]
            if t + 15 * 60_000 <= now <= t + 2 * 3_600_000:
                bars = [b for b in btc.series["1m"].bars if t <= b[0] < t + 15 * 60_000]
                atr = (btc.series["15m"].ind or {}).get("atr")
                if len(bars) >= 10 and atr:
                    return max(-1.0, min(1.0, (bars[-1][4] - bars[0][1]) / (2 * atr)))
        return None

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
        m = self.market
        btc = m.coins.get("BTC-USDT-SWAP") if m else None
        eq = self.broker.equity(m.coins) if m else self.broker.cash
        rep["kiyas"] = self.learner.benchmark_report(btc.last if btc else None, (eq / self.cfg["sermaye_usd"] - 1) * 100)
        rep["ogrenme"] = {"meta": self.learner.meta,
                          "en_guclu": sorted(self.learner.mult.items(), key=lambda x: -x[1])[:15],
                          "en_zayif": sorted(self.learner.mult.items(), key=lambda x: x[1])[:15],
                          "kategori": self.learner.cat_mult}
        (self.root / "RAPOR.md").write_text(report_md(rep), encoding="utf-8")
        self.last_report = rep
        return rep


def _spawn(coro):
    """Çalışan olay döngüsü varsa arka plan görevi başlat; yoksa (senkron test) sessizce bırak."""
    try:
        return asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return None


def _top_votes(dec: dict, d: int, n: int = 12) -> list[str]:
    vs = sorted(((k, v) for k, v in dec["oylar"].items() if v * d > 0), key=lambda x: -abs(x[1]))[:n]
    return [f"{k} {v:+.2f}" for k, v in vs]


def _compact(rd: dict) -> dict:
    """Günlüğe yazılacak okuma: her şey, ama seviyeler yalnızca fiyata en yakın 8 tanesi."""
    out = dict(rd)
    out.pop("piyasa", None)
    px = rd.get("fiyat") or 0
    lv = rd.get("seviyeler") or []
    out["seviyeler"] = sorted(lv, key=lambda x: abs(x["px"] - px))[:8]
    return out
