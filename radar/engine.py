"""Canlı motor process'i: yalnızca başlatıldığı GitHub Actions çalışması boyunca döngü yürütür.

  WebSocket (OKX)    → bağlantı varken fiyat/defter olaylarını işler; bağlantı ve olay ritmi garanti edilmez.
  WebSocket (Bitget) → yapılandırılmışsa ikinci borsa fiyatı/funding teyidi.
  Telegram           → yapılandırılmışsa long polling; ağ ve workflow süresince kullanılabilir.
  Ana döngü          → açık pozisyon koruma/çıkışlarını ve süre/funding kontrollerini yürütür.
  Arka plan işçisi   → yapılandırılmış mum/periyot taramaları ve piyasa araştırması.
  Yedek              → tanımlı REST yedekleri; kaynak erişilemezse veri UNKNOWN/STALE kalmalıdır.

GitHub Actions lease zinciri kalıcı sunucu değildir: runner değişimi, timeout, iptal, cron
gecikmesi ve kota nedeniyle kesinti olabilir. Bu modül 7/24 veya milisaniyelik SLA vaat etmez.
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import json

from . import commands as cm
from . import desk as dk
from . import director as gmd
from . import bas
BAS_IDS = ("bas", "turbo")      # Baş Trader beyniyle çalışan tam yetkili ajanlar
from . import experts as ex
from . import hakem as hk
from . import harvest as hv
from . import lease
from . import learn
from . import macro
from . import news as nw
from . import quant
from . import team
from . import ledger as lg
from . import notify as nt
from . import other_exchanges as ox
from . import signals as sg
from . import store
from .config import ROOT, load_config
from .unified.quality import ChannelRule, MarketDataQualityEngine, QualityState
from .unified.market_data import normalize_bbo
from .scan import (VERSION, _f, _interval_h, btc_info, build_universe, fetch_funding, karne_md,
                   msg_daily, oi_changes, summary_md)

TF_SEC = {"15m": 900, "30m": 1800, "1H": 3600, "2H": 7200, "4H": 14400, "1D": 86400}


def _recent_closes(df, n: int = 45) -> list:
    """Son n kapanmış günün (ts_ms, kapanış) listesi — v10 yeniden giriş kuralı için."""
    try:
        w = df.tail(n)
        return [(int(ts.value // 1_000_000), float(c)) for ts, c in zip(w.index, w["close"])]
    except Exception:  # noqa: BLE001
        return []
SCAN_DELAY = 2           # mum kapanışından sonra ilk deneme (kesinleşmemişse tekrar denenir)


def _fmt_dur(hours: float) -> str:
    m = int(round(hours * 60))
    if m < 60:
        return f"{m} dk"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} sa {m} dk" if m else f"{h} sa"
    d, h = divmod(h, 24)
    return f"{d} gün {h} sa" if h else f"{d} gün"


def _usd(v: float, sign: bool = True) -> str:
    s = f"{v:+,.2f}" if sign else f"{v:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") + "$"


def _p(v: float, d: int = 1, sign: bool = False) -> str:
    """Türkçe yüzde biçimi: 4,5"""
    return (f"{v:+.{d}f}" if sign else f"{v:.{d}f}").replace(".", ",")


def _ms(ts) -> int:
    return int((ts - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1))


class Engine:
    def __init__(self, site: Path, cfg_path=None, client=None, notify: bool = True,
                 demo: bool = False, publish: bool = False):
        self.site = Path(site)
        self.cfg_path = cfg_path
        self.cfg = load_config(cfg_path)
        self.notify_on = notify
        self.demo = demo
        self.publish_on = publish and not demo and bool(os.environ.get("GITHUB_TOKEN"))
        if client is None:
            from .okx import OKXClient
            client = OKXClient()
        self.client = client
        self.state = store.load(self.site / "state.json", {})
        self.ledger = lg.migrate(store.load(self.site / "ledger.json", lg.empty()), self.cfg)
        self._setup_cfg()
        self.universe: list[dict] = []
        self.spot_vol: dict[str, float] = {}
        self.prices: dict[str, float] = {}
        self.meta: dict[str, dict] = {}
        self.unified_closed_bars: dict[str, dict] = {}   # birleşik Baş Trader: son tamamlanmış günlük bağlamlar
        self.funding: dict[str, dict] = {}
        self.oi_chg: dict[str, float] = {}
        self.oi_src = "-"
        self.btc: dict = {"regime": 0, "regime_text": "Bilinmiyor"}
        self.harvest: list[dict] = []
        self.other: dict = {}
        self.ex_status: dict = {}
        self.d1_cache: dict = {}
        self.taker_cache: dict = {}
        self.signals_by_tf: dict[str, list] = {}
        self.coins_by_tf: dict[str, list] = {}
        self.scan_at: dict[str, float] = {}
        self.errors: list[str] = []
        self.skipped: list[dict] = []
        # canlı veri
        self.book: dict[str, tuple] = {}        # inst → (last, bid, ask, alındığı an)
        self.bbo_quality = MarketDataQualityEngine({
            "ws_ticker": ChannelRule(max_age_ms=int(self.BBO_MAX_AGE_S * 1000)),
            "bitget_ticker": ChannelRule(max_age_ms=int(self.BBO_MAX_AGE_S * 1000)),
            "rest_ticker": ChannelRule(max_age_ms=int(self.BBO_MAX_AGE_S * 1000)),
        })
        self.bbo_quality_latest: dict[tuple[str, str], object] = {}
        self.bg_book: dict[str, tuple] = {}     # baz coin → Bitget (last, bid, ask, an)
        self.fund_cur: dict[str, tuple] = {}    # inst → (oran, kesim_ms, kaynak)
        self.fund_events: list[tuple] = []      # (inst, oran, kesim_ms) geçmiş kesimler
        self.golge_fund_events: list[tuple] = []  # aynı kesimler, gölge sanal defter için
        self.bg_fund: dict[str, float] = {}
        self.watch: dict[str, tuple] = {}       # inst → ((id, side, sl, tp, liq_first, tp1), ...)
        self.hw: dict[str, list] = {}           # inst → [en yüksek, en düşük] (iz süren stop için)
        self.blocked: set = set()               # negatif beklentiyle kapatılmış (setup, tf)
        self.halt: str | None = None            # devre kesici nedeni
        self.risk_mult = 1.0
        self.hits: dict[str, tuple] = {}        # id → (sonuç, dolum, ms, maker)
        self.manual: list[str] = []
        self.feeds: dict = {}
        self.tg: nt.TelegramWorker | None = None
        self.lock = threading.RLock()
        self.jobs: queue.Queue = queue.Queue()          # hız kritik işler: günlük tarama, mum taraması
        self.jobs_slow: queue.Queue = queue.Queue()     # ağır arka plan işleri: saatlik yenileme, haber, takvim, funding
        self.job_names: set[str] = set()
        self.worker_slow: threading.Thread | None = None
        self.pending_open: list[dict] = []
        self.worker: threading.Thread | None = None
        self.t = {"universe": 0.0, "funding": 0.0, "hourly": 0.0, "prices": 0.0, "reconcile": 0.0,
                  "cmd": 0.0, "pub": 0.0, "cfg": time.time(), "mark": 0.0, "risk": 0.0, "eq": 0.0, "panel": 0.0}
        self.dirty = True
        self.payload: dict | None = None
        self.started = time.time()
        self.cycles = 0
        self.perf = gmd.PerfMeter()          # v9: döngü hızı sayacı
        self.fail_streak = 0
        self.stats_counter = {"opened": 0, "closed": 0, "scans": 0, "ws_closes": 0, "rest_closes": 0,
                              "funding_events": 0, "signal_latency_s": None, "partials": 0, "trail_moves": 0}
        self.state.setdefault("risk", {})
        self.state.setdefault("eq_hist", [])
        self.halt = self.state["risk"].get("halt")
        # ── ekip ──
        self.team_on = (self.cfg.get("ekip") or {}).get("aktif", True)
        self.desk: dk.Desk | None = None
        if self.team_on and self.state.get("team_started"):
            self.desk = dk.Desk(self.cfg, self.state, self.ledger)
        self.order_watch: dict = {}
        self.order_hits: dict = {}
        self.pending_ideas: list = []
        self.pending_carry: list = []
        self.ideas_seen = 0
        # ── sağlık ──
        self.health: dict = {"status": "başlıyor", "issues": [], "checked": 0}
        self.suspect: dict = {}
        self.alerted: dict = {}
        self.successor_sent = False
        self.lost_lease = False
        self.loop_beat = time.time()       # ana döngünün son turu (iç bekçi takılmayı bununla anlar)
        # ── v8: piyasa havası, olay takvimi, Haber Masası, yığılma için günlük getiriler ──
        self.weather: dict = {}
        self.daily_cache: dict = {}
        self.signal_exits: list = []
        self.live_seen: dict = {}            # kesintisiz değerlendirmede aynı fikri dakikada bir tekrar önermemek için
        self.calendar = macro.Calendar()
        self.news = nw.NewsDesk()
        self.news.from_state(self.state.get("news") or {})
        self.cfg["_learned"] = learn.load_learned()
        self.hakem = hk.load()
        self.crowd_cache: dict = {}
        self.smart: dict = {}
        self.loop_done = False
        self.my_hb = 0
        self.lease_thread: threading.Thread | None = None
        self.lease_ok = None
        self.t.update({"health": 0.0, "mgr": 0.0, "orders": 0.0, "lease": 0.0, "rebal": 0.0, "news": 0.0, "cal": 0.0,
                       "live1d": 0.0, "cons": 0.0, "learn": 0.0})
        self.stats_counter.update({"ideas": 0, "rejected": 0, "orders": 0, "fills": 0, "carry": 0})

    def _setup_cfg(self):
        c = self.cfg
        self.tfs = [tf for tf in (c["tarama"].get("periyotlar") or [c["tarama"]["periyot"]]) if tf in TF_SEC]
        self.setups = sg.build_setups(c)
        self.live = {"dongu_dakika": 330, "fiyat_kontrol_sn": 60, "komut_kontrol_sn": 15,
                     "yayin_min_dakika": 6, "websocket": True, "bitget_ws": True, **(c.get("canli") or {})}
        self.tz = ZoneInfo(c["bildirim"]["saat_dilimi"])

    # ── canlı akış geri çağrıları (WS thread'lerinden) ────────────────
    def _on_tick(self, venue, key, last, bid, ask, ts):
        now = time.time()
        receive_ms = int(now * 1000)
        normalized = normalize_bbo(
            venue=venue, symbol=key, last=last, bid=bid, ask=ask, event_ts_ms=ts,
        )
        channel = "bitget_ticker" if venue == "bitget" else "ws_ticker"
        observation = self.bbo_quality.observe(
            symbol=key, channel=channel, event_ts_ms=normalized.event_ts_ms,
            receive_ts_ms=receive_ms, valid=normalized.valid,
        )
        self.bbo_quality_latest[(venue, key)] = observation
        # Kaynağın timestamp'i veya BBO'su güvenilmezse fiyat; izleme, limit dolumu
        # ya da strateji kararına hiçbir şekilde aktarılmaz.
        if observation.state is not QualityState.LIVE:
            return
        decision_ts = normalized.event_ts_ms
        if venue == "bitget":
            self.bg_book[key] = (normalized.last, normalized.bid, normalized.ask, now)
            return
        self.book[key] = (normalized.last, normalized.bid, normalized.ask, now)
        self.prices[key] = normalized.last
        self._check_watch(key, normalized.last, normalized.bid, normalized.ask, decision_ts)
        ow = self.order_watch.get(key)
        if ow:
            for oid, side, px in ow:
                if oid not in self.order_hits and ((side == "long" and normalized.last < px) or (side == "short" and normalized.last > px)):
                    self.order_hits[oid] = (px, decision_ts)  # limit emir: doğrulanmış fiyat seviyesi geçti → doldu

    def _check_watch(self, inst, last, bid, ask, ts):
        w = self.watch.get(inst)
        if not w:
            return
        h = self.hw.get(inst)
        if h is None:
            self.hw[inst] = [last, last]
        else:
            if last > h[0]:
                h[0] = last
            if last < h[1]:
                h[1] = last
        for tid, side, sl, tp, liq_first, tp1 in w:
            if tid in self.hits:
                continue
            if side == "long":
                if last <= sl:
                    fill = min(bid, last) if bid > 0 else last          # stop-market: alıcıya (bid) satılır
                    self.hits[tid] = ("likidasyon" if liq_first else "stop", fill, ts, False)
                elif last > tp:                                          # limit emir: fiyat hedefin içinden geçmeli
                    self.hits[tid] = ("hedef", tp, ts, True)
                elif tp1 and last > tp1 and tid + "|tp1" not in self.hits:
                    self.hits[tid + "|tp1"] = ("tp1", tp1, ts, True)
            else:
                if last >= sl:
                    fill = max(ask, last) if ask > 0 else last
                    self.hits[tid] = ("likidasyon" if liq_first else "stop", fill, ts, False)
                elif last < tp:
                    self.hits[tid] = ("hedef", tp, ts, True)
                elif tp1 and last < tp1 and tid + "|tp1" not in self.hits:
                    self.hits[tid + "|tp1"] = ("tp1", tp1, ts, True)

    def _on_funding(self, venue, key, rate, ftime, nxt):
        if venue == "bitget":
            self.bg_fund[key] = rate
            return
        self._set_fund(key, rate, ftime, "ws")

    def _set_fund(self, inst, rate, ftime, src):
        if not ftime:
            return
        prev = self.fund_cur.get(inst)
        if prev and ftime > prev[1] and prev[1] <= time.time() * 1000 + 60_000:
            self.fund_events.append((inst, prev[0], prev[1]))   # önceki dönem kesildi
            if hasattr(self, "golge_fund_events"):
                self.golge_fund_events.append((inst, prev[0], prev[1]))   # gölge sanal defter için ayrı kopya
        if not prev or ftime >= prev[1]:
            self.fund_cur[inst] = (rate, ftime, src)

    def rebuild_watch(self):
        w: dict[str, list] = {}
        for t in self.ledger["open"]:
            if t.get("carry") or t.get("sl") is None:
                continue
            tp1 = t.get("tp1") if (t.get("real") and not t.get("tp1_done")) else None
            w.setdefault(t["inst"], []).append((t["id"], t["side"], t["sl"], t["tp"], bool(t.get("liq_first")), tp1))
        self.watch = {k: tuple(v) for k, v in w.items()}
        ow: dict[str, list] = {}
        for o in self.ledger.get("orders", []):
            ow.setdefault(o["idea"]["inst"], []).append((o["oid"], o["idea"]["side"], o["px"]))
        self.order_watch = {k: tuple(v) for k, v in ow.items()}

    def _quote(self, inst):
        b = self.book.get(inst)
        if b and time.time() - b[3] < 15 and b[1] > 0 and b[2] > 0:
            return b[1], b[2], b[0]
        return None

    def _feed_symbols(self):
        insts = ({r["inst"] for r in self.universe} | {t["inst"] for t in self.ledger["open"]}
                 | {t["spot_inst"] for t in self.ledger["open"] if t.get("carry")})
        if "okx" in self.feeds:
            self.feeds["okx"].set_symbols(sorted(insts))
        if "bitget" in self.feeds:
            self.feeds["bitget"].set_symbols(sorted({r["sym"] for r in self.universe}))

    def okx_live(self) -> bool:
        f = self.feeds.get("okx")
        return bool(f and f.healthy())

    # ── REST veri ────────────────────────────────────────────────────
    def refresh_universe(self):
        uni, spot = build_universe(self.client, self.cfg)
        if uni:
            self.universe, self.spot_vol = uni, spot
            for r in uni:
                self.prices.setdefault(r["inst"], r["price"])
                if r.get("ct_val"):
                    age = (self.state.get("coin_age") or {}).get(r["sym"])
                    lt = min(x for x in (r.get("list_ts") or 0.0, age or 0.0, 9e15) if x > 0)
                    self.meta[r["inst"]] = {"ct_val": r["ct_val"], "lot_sz": r["lot_sz"], "min_sz": r["min_sz"],
                                            "max_lev": r.get("max_lev") or 0.0, "list_ts": 0.0 if lt >= 9e15 else lt}
            self._feed_symbols()
        self.t["universe"] = time.time()

    def refresh_prices(self) -> dict[str, float]:
        rows = self.client.tickers("SWAP")
        now = time.time()
        live = self.okx_live()
        by = {}
        for x in rows:
            v = _f(x.get("last"))
            if not (v == v and v > 0):
                continue
            inst = x["instId"]
            by[inst] = x
            if not live:      # canlı akış yoksa REST fiyatı esas al ve tetikleri kontrol et
                bid, ask = _f(x.get("bidPx"), 0.0), _f(x.get("askPx"), 0.0)
                self.book[inst] = (v, bid, ask, now)
                self.prices[inst] = v
                if inst in self.watch:
                    self._check_watch(inst, v, bid, ask, int(now * 1000))
        if self.team_on:
            try:
                for x in self.client.tickers("SPOT"):
                    iid = x.get("instId", "")
                    v = _f(x.get("last"))
                    if iid.endswith("-USDT") and v == v and v > 0:
                        b = self.book.get(iid)
                        if not (b and live and now - b[3] < 15):
                            self.prices[iid] = v
                            self.book[iid] = (v, _f(x.get("bidPx"), 0.0), _f(x.get("askPx"), 0.0), now)
            except Exception as e:  # noqa: BLE001
                print(f"[uyarı] spot fiyat alınamadı: {e}")
        for r in self.universe:
            x = by.get(r["inst"])
            if x:
                last, o24 = self.prices.get(r["inst"]) or _f(x.get("last")), _f(x.get("open24h"))
                r["price"] = last
                r["vol24_base"] = _f(x.get("volCcy24h"))
                if o24 and o24 == o24:
                    r["chg24"] = (last / o24 - 1) * 100
        self.t["prices"] = now
        return self.prices

    def refresh_funding(self):
        if self.universe:
            self.funding = fetch_funding(self.client, self.universe)
            for inst, fr in self.funding.items():
                cur = self.fund_cur.get(inst)
                if not cur or cur[2] != "ws" or not self.okx_live():
                    self._set_fund(inst, _f(fr.get("fundingRate"), 0.0), int(_f(fr.get("fundingTime"), 0)), "rest")
        self.t["funding"] = time.time()

    def refresh_coin_ages(self, per_call: int = 25):
        """Coin yaşı (Genel Müdür kanıt sınıfı): OKX vadeli listeleme tarihi sözleşme yenilenince sıfırlanabilir; bu yüzden
        spot (yoksa vadeli) aylık mumların ilk ayı da bakılır, en eskisi alınır. Bir kez öğrenilir, durumda saklanır."""
        ages = self.state.setdefault("coin_age", {})
        todo = [r for r in self.universe if r["sym"] not in ages][:per_call]
        for r in todo:
            first = 0.0
            for inst in (f"{r['sym']}-USDT", r["inst"]):
                try:
                    df = self.client.candles(inst, "1M", limit=100, closed_only=False)
                    if len(df):
                        first = float(df.index[0].value // 1_000_000)
                        break
                except Exception:  # noqa: BLE001
                    continue
            ages[r["sym"]] = first or (r.get("list_ts") or 0.0)
        if todo:
            for r in self.universe:
                if r["inst"] in self.meta:
                    lt = min(x for x in (r.get("list_ts") or 0.0, ages.get(r["sym"]) or 0.0, 9e15) if x > 0)
                    self.meta[r["inst"]]["list_ts"] = 0.0 if lt >= 9e15 else lt

    def refresh_hourly(self):
        now_ms = int(time.time() * 1000)
        try:
            self.refresh_coin_ages()
        except Exception as e:  # noqa: BLE001
            print(f"[coin yaşı] {e}")
        self.btc = btc_info(self.client, self.cfg)
        self.oi_chg, self.oi_src = oi_changes(self.client, self.universe, self.state, now_ms)
        if not self.demo and self.cfg["borsa"].get("funding_karsilastir"):
            self.other, self.ex_status = ox.fetch_all(self.cfg["borsa"]["funding_karsilastir"])
        self._refresh_harvest()
        self.refresh_weather()
        self.d1_cache.clear()
        self.taker_cache.clear()
        self.t["hourly"] = time.time()

    def refresh_weather(self):
        """Piyasa havası: BTC günlük trend + oynaklık rejimi (risk müdürü ve yöneticiler kullanır)."""
        try:
            B = quant.features(self.client.candles("BTC-USDT-SWAP", "1D", 300))
            self.weather = quant.btc_weather(B)
        except Exception as e:  # noqa: BLE001
            print(f"[uyarı] piyasa havası hesaplanamadı: {e}")

    def _refresh_harvest(self):
        h = self.cfg["harvest"]
        pre = []
        for r in self.universe:
            fr = self.funding.get(r["inst"]) or {}
            rate = _f(fr.get("fundingRate"))
            iv = _interval_h(fr)
            if (rate == rate and rate > 0 and self.spot_vol.get(r["sym"], 0) >= h["min_spot_hacim_usd"]
                    and r["vol24_usd"] >= h["min_perp_hacim_usd"]):
                pre.append({**r, "rate": rate, "iv": iv, "ann": rate * 8760 / iv * 100})
        pre.sort(key=lambda c: c["ann"], reverse=True)
        pre = pre[:45]

        def fh(c):
            try:
                return c, self.client.funding_history(c["inst"], h["gun"])
            except Exception as e:  # noqa: BLE001
                print(f"[uyarı] funding geçmişi {c['sym']}: {e}")
                return c, None

        rows = []
        with ThreadPoolExecutor(6) as ex:
            for c, hist in ex.map(fh, pre):
                rows.append({"sym": c["sym"], "inst": c["inst"], "vol24_usd": c["vol24_usd"],
                             "spot_vol_usd": self.spot_vol.get(c["sym"], 0), "funding_ann": c["ann"],
                             "interval_h": c["iv"], "stats": hv.funding_stats(hist, c["iv"]),
                             "other": self.other.get(c["sym"])})
        self.harvest = hv.rank_candidates(rows, self.cfg)

    def _d1(self, inst):
        hit = self.d1_cache.get(inst)
        if hit is not None:
            return hit
        d1 = self.client.candles(inst, "1D", 120)
        self.d1_cache[inst] = d1
        return d1

    def _taker(self, inst):
        if inst in self.taker_cache:
            return self.taker_cache[inst]
        val = None
        try:
            tv = self.client.taker_volume(inst, "1H", 48)
            if len(tv) >= 20:
                last = tv.iloc[-24:]
                buy, sell = last["buy"].sum(), last["sell"].sum()
                if buy + sell > 0:
                    val = ((buy - sell) / (buy + sell), buy - sell)
        except Exception:  # noqa: BLE001
            pass
        self.taker_cache[inst] = val
        return val

    # ── tarama ───────────────────────────────────────────────────────
    def scan_coin(self, row: dict, tf: str, expect_ms: int | None = None) -> dict:
        inst = row["inst"]
        bh = sg.BAR_HOURS[tf]
        df = self.client.candles(inst, tf, 300)
        if expect_ms:   # mum kapanışından hemen sonra: son kapanmış mum gelene kadar kısa aralıkla tekrar
            for _ in range(5):
                if len(df) and _ms(df.index[-1]) >= expect_ms:
                    break
                time.sleep(1.2)
                df = self.client.candles(inst, tf, 300)
        d1 = self._d1(inst)
        if len(df) < 120 or len(d1) < 40:
            raise ValueError(f"yetersiz veri ({len(df)} {tf}, {len(d1)} 1G)")
        ff = sg.frame_features(df, self.cfg, bh)
        f = ff.iloc[-1]
        fd = sg.daily_features(d1, self.cfg).iloc[-1]
        cur = self.fund_cur.get(inst)
        fr = self.funding.get(inst) or {}
        rate = cur[0] if cur else _f(fr.get("fundingRate"))
        iv = _interval_h(fr)
        feat = {k: _f(v) for k, v in f.items()}
        price = self.prices.get(inst) or row["price"]
        feat.update({
            "price": price if price == price else feat["close"],
            "d_trend": _f(fd["d_trend"], 0), "tsmom": _f(fd["tsmom"], 0),
            "funding_rate": rate, "interval_h": iv,
            "funding_ann": rate * (8760 / iv) * 100 if rate == rate else float("nan"),
            "oi_chg24": _f(self.oi_chg.get(inst)),
        })
        if row.get("chg24") == row.get("chg24") and row.get("chg24") is not None:
            feat["ret24"] = row["chg24"] / 100
        taker_src = "vekil"
        tk = self._taker(inst)
        if tk:
            imb, delta = tk
            feat["taker_imb"] = imb
            feat["cvd_div"] = (1 if (feat["ret24"] < -0.01 and delta > 0) else
                               -1 if (feat["ret24"] > 0.01 and delta < 0) else 0)
            taker_src = "gerçek"
        regime = self.btc.get("regime", 0)
        sigs = sg.evaluate_coin(feat, regime, self.setups, self.cfg, tf)
        bar_ms = _ms(df.index[-1]) if len(df) else None
        for s in sigs:
            s.update({"inst": inst, "sym": row["sym"], "price": feat["price"], "chg24": row.get("chg24"),
                      "funding_ann": feat["funding_ann"], "funding_rate": rate, "interval_h": iv,
                      "oi_chg24": feat["oi_chg24"], "rsi": feat["rsi"], "adx": feat["adx"],
                      "regime": regime, "vol24_usd": row["vol24_usd"], "bar_ms": bar_ms, "atr": feat["atr"]})
        best = max(sigs, key=lambda s: s["score"]) if sigs else None
        coin = {
            "sym": row["sym"], "inst": inst, "price": feat["price"], "chg24": row.get("chg24"),
            "vol24_usd": row["vol24_usd"], "rsi": feat["rsi"], "adx": feat["adx"],
            "st": int(feat["st_dir"]), "d_trend": int(feat["d_trend"]), "tsmom": feat["tsmom"],
            "funding_ann": feat["funding_ann"], "interval_h": iv, "oi_chg24": feat["oi_chg24"],
            "taker_imb": feat["taker_imb"], "bb_pct": feat["bb_pct"], "div": int(feat["div"]),
            "atr_pct": feat["atr"] / feat["price"] * 100 if feat["price"] else None,
            "taker_src": taker_src, "tf": tf, "other_funding": self.other.get(row["sym"]),
            "best": {"label": best["label"], "score": best["score"], "side": best["side"]} if best else None,
        }
        ctx = None
        if self.team_on:
            Q = quant.features(df)
            qc = quant.ctx_at(Q)
            if tf == "1D":
                qc["hvn"] = quant.hvn_levels(Q)
                with self.lock:
                    self.daily_cache[inst] = {"ret": Q["ret1"].tail(90).to_numpy(), "ts": time.time()}
            Dl = quant.features(d1).iloc[-1]
            close_ts = df.index[-1] + pd.Timedelta(hours=bh)
            w = self.weather or {}
            ctx = {**qc, "inst": inst, "sym": row["sym"], "tf": tf, "bar_ms": TF_SEC[tf] * 1000,
                   "recent_closes": _recent_closes(df) if tf == "1D" else None,
                   "price": feat["price"], "regime": regime, "funding_ann": feat["funding_ann"], "funding_rate": rate,
                   "interval_h": iv, "oi_chg24": feat["oi_chg24"], "taker_imb": tk[0] if tk else None,
                   "ret24": feat["ret24"], "chg24": row.get("chg24"), "signals": sigs, "vol24_usd": row["vol24_usd"],
                   "btc_bear": w.get("btc_bear", regime < 0), "btc_bull": w.get("btc_bull", regime > 0),
                   "w_trend": w.get("trend"), "high_vol": w.get("high_vol"),
                   "d_up": bool(Dl["close"] > Dl["sma50"]), "d_dn": bool(Dl["close"] < Dl["sma50"]),
                   "hour": int(close_ts.hour), "dow": int(close_ts.day_of_week)}
            issue = self._data_issue(df, tf, feat["price"], row["sym"], feat.get("atr"))
            if issue:
                ctx["suspect"] = issue
                self.suspect[inst] = {"sym": row["sym"], "tf": tf, "reason": issue, "ts": int(time.time() * 1000)}
            else:
                self.suspect.pop(inst, None)
            coin["suspect"] = issue
        return {"coin": coin, "signals": sigs, "ctx": ctx}

    def _data_issue(self, df, tf: str, live_px: float, sym: str, atr: float | None = None) -> str | None:
        """Veri sağlığı: eksik/gecikmiş mum, tutarsız fiyat, borsalar arası büyük fark → bu coinde işlem açılmaz.

        Mumlar yalnızca kapanmış mumlardır; son kapanıştan bu yana en fazla 1 mum süresi geçer. Bu yüzden canlı fiyat
        sapma eşiği coinin kendi oynaklığına göre ölçeklenir (en az %8, 5 ATR): sert ama gerçek hareketler (hacimli
        kırılım) "bozuk veri" sayılmaz; yanlış enstrüman / ondalık hatası gibi kaba hatalar yine yakalanır."""
        tail = df["close"].iloc[-30:]
        if tail.isna().any() or (tail <= 0).any():
            return "mum verisinde boşluk"
        if not df.index.is_monotonic_increasing:
            return "mum sırası bozuk"
        tf_ms = TF_SEC[tf] * 1000
        age = time.time() * 1000 - (_ms(df.index[-1]) + tf_ms)
        if age > 2 * tf_ms + 180_000:
            return f"son mum {age / 60000:.0f} dk gecikmiş"
        last_close = float(df["close"].iloc[-1])
        atr_frac = (atr / last_close) if (atr and atr == atr and last_close > 0) else 0.0
        thr = max(0.08, 5 * atr_frac)
        if live_px and abs(live_px / last_close - 1) > thr:
            return f"canlı fiyat ile mum farkı %{abs(live_px / last_close - 1) * 100:.1f} (eşik %{thr * 100:.0f})"
        bg = self.bg_book.get(sym)
        if bg and live_px and time.time() - bg[3] < 30 and abs(bg[0] / live_px - 1) > 0.03:
            return f"Bitget ile fiyat farkı %{abs(bg[0] / live_px - 1) * 100:.1f} (farklı coin olabilir)"
        return None

    def scan_tf(self, tf: str, boundary: float | None = None) -> list[dict]:
        t0 = time.time()
        expect = int((boundary - TF_SEC[tf]) * 1000) if boundary else None
        results, errors = [], []
        uni = list(self.universe)
        with ThreadPoolExecutor(8) as ex:
            futs = {ex.submit(self.scan_coin, r, tf, expect): r for r in uni}
            for fu in as_completed(futs):
                try:
                    results.append(fu.result())
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{futs[fu]['sym']} ({tf}): {e}")
        if len(results) < max(5, len(uni) // 3):
            raise RuntimeError(f"{tf}: çok az coin taranabildi ({len(results)}/{len(uni)}). "
                               f"İlk hatalar: {errors[:3]}")
        sigs = sorted([s for x in results for s in x["signals"]], key=lambda s: -s["score"])
        self.signals_by_tf[tf] = sigs
        self.coins_by_tf[tf] = sorted([x["coin"] for x in results], key=lambda c: -c["vol24_usd"])
        self.scan_at[tf] = time.time()
        self.errors = errors[:20]
        self.stats_counter["scans"] += 1
        if boundary:
            self.stats_counter["signal_latency_s"] = round(time.time() - boundary, 1)
        self.dirty = True
        strong = [s for s in sigs if s["score"] >= self.cfg["sinyal"]["guclu_esik"]]
        if tf == "1D":
            self._orphan_close_stops({r["inst"] for r in uni})
        if self.team_on and self.desk:
            ideas = []
            for x in results:
                c = x.get("ctx")
                if not c:
                    continue
                c["peers"] = self._peers(c["inst"])
                self._queue_rule_exits(c)
                if tf == "1D":                            # birleşik mod kural çıkışları tamamlanmış günlük mumla
                    self.unified_closed_bars[c["inst"]] = c
                if c.get("suspect"):
                    continue
                for idea in team.ideas_for(c, self.desk.mgrs, self.cfg):
                    idea["card"] = team.analyst_card(c, idea["side"], self.news.flag(c["sym"]))
                    if idea["tf"] == "1D" and idea["entry_type"] == "piyasa":
                        idea["maker_first"] = True       # önce limit (maker), dolmazsa piyasa
                    self.enrich_idea(idea, c, fetch=False)
                    ideas.append(idea)
            self.stats_counter["ideas"] += len(ideas)
            self.ideas_seen += len(ideas)
            with self.lock:
                self.pending_ideas.extend(ideas)
            if ideas:
                print(f"[{tf}] ekip fikirleri: " + ", ".join(f"{i['mgr']}:{i['sym']} {i['side']} %{i['conviction'] * 100:.0f}" for i in ideas[:12]))
        print(f"[{tf}] {len(results)}/{len(uni)} coin, {len(sigs)} sinyal, {len(strong)} güçlü, "
              f"{time.time() - t0:.1f} sn" + (f" (mum kapanışından {time.time() - boundary:.1f} sn sonra)" if boundary else ""))
        return strong

    def _live_fields(self, row: dict, inst: str) -> dict:
        cur = self.fund_cur.get(inst)
        fr = self.funding.get(inst) or {}
        rate = cur[0] if cur else _f(fr.get("fundingRate"))
        iv = _interval_h(fr)
        tk = self._taker(inst)
        w = self.weather or {}
        regime = self.btc.get("regime", 0)
        return {"inst": inst, "sym": row["sym"], "regime": regime, "funding_rate": rate, "interval_h": iv,
                "funding_ann": rate * (8760 / iv) * 100 if rate == rate else float("nan"),
                "oi_chg24": _f(self.oi_chg.get(inst)), "taker_imb": tk[0] if tk else None,
                "chg24": row.get("chg24"), "vol24_usd": row.get("vol24_usd"),
                "btc_bear": w.get("btc_bear", regime < 0), "btc_bull": w.get("btc_bull", regime > 0),
                "w_trend": w.get("trend"), "high_vol": w.get("high_vol"), "signals": []}

    def live_daily_ctx(self, row: dict) -> dict | None:
        """Kapanmış günler + bugünün CANLI mumu (fiyat canlı akıştan, hacim kayan 24 saat). Günün saatinden bağımsız."""
        inst = row["inst"]
        px = self.prices.get(inst)
        if not px:
            return None
        df = self.client.candles(inst, "1D", 300, closed_only=False)
        if len(df) < 120:
            return None
        df = df.copy()
        day0 = pd.Timestamp(int(time.time() * 1000), unit="ms", tz="UTC").normalize()
        if df.index[-1] < day0:
            df.loc[day0] = {"open": px, "high": px, "low": px, "close": px, "volume": 0.0, "quote_volume": 0.0}
        i = df.index[-1]
        df.at[i, "close"] = px
        df.at[i, "high"] = max(df.at[i, "high"], px)
        df.at[i, "low"] = min(df.at[i, "low"], px)
        v24 = row.get("vol24_base")
        if v24 and v24 == v24 and v24 > 0:
            df.at[i, "volume"] = v24
        Q = quant.features(df)
        qc = quant.ctx_at(Q)
        qc["hvn"] = quant.hvn_levels(Q)
        ctx = {**qc, **self._live_fields(row, inst), "tf": "1D", "bar_ms": TF_SEC["1D"] * 1000, "price": px,
               "live_bar": True, "recent_closes": _recent_closes(df.iloc[:-1])}
        bg = self.bg_book.get(row["sym"])
        if bg and time.time() - bg[3] < 30 and abs(bg[0] / px - 1) > 0.03:
            ctx["suspect"] = f"Bitget ile fiyat farkı %{abs(bg[0] / px - 1) * 100:.1f}"
        return ctx

    def scan_live_daily(self):
        """(işçi) Kesintisiz değerlendirme: günlük yöneticiler her dakika canlı mumla bakar; fırsat oluştuğu an fikir verir,
        kural çıkışları da anlık çalışır. Aynı fikir 30 dakikada bir defadan sık önerilmez."""
        if not (self.team_on and self.desk):
            return
        self.t["live1d"] = time.time()
        mgrs = [m for m in self.desk.mgrs if m.analyze and "1D" in m.tfs]
        rows = list(self.universe)
        ctxs = []
        with ThreadPoolExecutor(6) as ex:
            for c in ex.map(lambda r: self._safe(self.live_daily_ctx, r), rows):
                if c:
                    ctxs.append(c)
        self._rel_strength(ctxs)
        for c in ctxs:
            c["peers"] = self._peers(c["inst"])
            c["crowd"] = self.crowd(c["inst"], fetch=False)
        # Balina verisi (long/short oranları, likidasyon) her coin için her dakika çekilmez (hız): puanı eşiğe yakın
        # en fazla 8 coin için taze çekilir (10 dk önbellek), diğerleri önbellekten.
        try:
            near = sorted(((abs(bas.score(c)[0]), c) for c in ctxs), key=lambda x: -x[0])
            for sc, c in [x for x in near if x[0] >= 0.3][:8]:
                c["crowd"] = self.crowd(c["inst"], fetch=True)
        except Exception as e:  # noqa: BLE001
            print("[baş trader] balina verisi:", e)
        # Ayşe: kapitülasyon adayı coinler için likidasyon / long-short / akıllı para verisi taze çekilir (en fazla 6)
        try:
            for c in [c for c in ctxs if team.ayse_core(c)][:6]:
                c["crowd"] = self.crowd(c["inst"], fetch=True)
        except Exception as e:  # noqa: BLE001
            print("[ayşe] kalabalık verisi:", e)
        try:
            self.bas_watch = {"ts": int(time.time() * 1000), "rows": bas.watch(ctxs)}
        except Exception as e:  # noqa: BLE001
            print("[baş trader] izleme:", e)
        now = time.time()
        ideas = []
        held = {(t.get("mgr"), t["inst"]) for t in list(self.ledger["open"])}
        held |= {(o["mgr"], o["idea"]["inst"]) for o in list(self.ledger.get("orders") or [])}
        for c in ctxs:
            self._queue_rule_exits(c)
            if c.get("suspect"):
                continue
            for idea in team.ideas_for(c, mgrs, self.cfg):
                if (idea["mgr"], idea["inst"]) in held:       # zaten pozisyonu/emri var → değerlendirmeye gerek yok
                    if not (idea["mgr"] in BAS_IDS and self._bas_pyramid(idea)):
                        continue
                key = (idea["mgr"], idea["inst"], idea["side"])
                if now - self.live_seen.get(key, 0) < 1800:
                    continue
                self.live_seen[key] = now
                idea["card"] = team.analyst_card(c, idea["side"], self.news.flag(c["sym"]))
                if idea["entry_type"] == "piyasa":
                    idea["maker_first"] = True
                self.enrich_idea(idea, c, fetch=True)
                ideas.append(idea)
        if ideas:
            self.stats_counter["ideas"] += len(ideas)
            with self.lock:
                self.pending_ideas.extend(ideas)
            print("[canlı 1G] " + ", ".join(f"{i['mgr']}:{i['sym']} {i['side']}" for i in ideas[:12]))
        self.stats_counter["live_evals"] = self.stats_counter.get("live_evals", 0) + 1
        self.stats_counter["live_coins"] = len(ctxs)
        self._unified_shadow(ctxs)

    BBO_MAX_AGE_S = 15.0

    def _ws_bbo(self, inst: str) -> tuple:
        """WS defterindeki (bid, ask, yaş) — yoksa (None, None, None)."""
        b = self.book.get(inst)
        if not b:
            return None, None, None
        _last, bid, ask, ts = b
        bid = bid if bid and bid > 0 else None
        ask = ask if ask and ask > 0 else None
        return bid, ask, (round(time.time() - ts, 3) if ts else None)

    def _trusted_bbo(self, inst: str, now: float | None = None):
        """Yalnız borsa timestamp'i doğrulanmış ve 15 sn içinde olan BBO'yu döndür."""
        now = time.time() if now is None else now
        latest = getattr(self, "bbo_quality_latest", {})
        ws_obs = latest.get(("okx", inst))
        ws = getattr(self, "book", {}).get(inst)
        if ws and ws_obs and ws_obs.state is QualityState.LIVE:
            age = now - ws[3]
            if age >= 0 and age <= self.BBO_MAX_AGE_S and ws[1] > 0 and ws[2] > 0 and ws[1] < ws[2]:
                return ws, "ws"
        rest_obs = latest.get(("rest", inst))
        rb = (getattr(self, "bbo_rest", {}) or {}).get(inst)
        if rb and rest_obs and rest_obs.state is QualityState.LIVE:
            age = now - rb[2]
            if age >= 0 and age <= self.BBO_MAX_AGE_S and rb[0] > 0 and rb[1] > 0 and rb[0] < rb[1]:
                return ((rb[0] + rb[1]) / 2, rb[0], rb[1], rb[2]), "rest"
        return None, "kalite_yok"

    def _book_ctx(self, inst: str) -> dict:
        """Unified kararına yalnız event timestamp'i doğrulanmış, taze ve çapraz olmayan BBO ver."""
        now = time.time()
        book, src = self._trusted_bbo(inst, now)
        if not book:
            return {"bid": None, "ask": None, "book_age_s": None, "bbo_kaynak": src}
        return {"bid": book[1], "ask": book[2], "book_age_s": round(now - book[3], 3), "bbo_kaynak": src}

    def _bbo_snapshot(self, insts: set[str]) -> None:
        """WS defteri eksik/bayat olan karar coinleri için TEK bir REST tickers çağrısı (public, anlık BBO)."""
        need = set()
        for i in insts:
            trusted, _ = self._trusted_bbo(i) if i else (None, "kalite_yok")
            if not trusted:
                need.add(i)
        if not need:
            return
        try:
            now = time.time()
            rest = getattr(self, "bbo_rest", None)
            if rest is None:
                rest = self.bbo_rest = {}
            for x in self.client.tickers("SWAP"):
                iid = x.get("instId")
                if iid in need:
                    bid, ask = _f(x.get("bidPx"), 0.0), _f(x.get("askPx"), 0.0)
                    event_ts = int(_f(x.get("ts"), 0.0)) or None
                    obs = self.bbo_quality.observe(
                        symbol=iid, channel="rest_ticker", event_ts_ms=event_ts,
                        receive_ts_ms=int(now * 1000), valid=bid > 0 and ask > 0 and bid < ask,
                    )
                    self.bbo_quality_latest[("rest", iid)] = obs
                    if obs.state is QualityState.LIVE:
                        rest[iid] = (bid, ask, now)
        except Exception as e:  # noqa: BLE001
            self._bbo_err = f"{type(e).__name__}: {e}"

    def _bbo_tanilama(self, ctxs: list[dict]) -> None:
        """Her karar turunda WS/BBO tanılaması (salt okunur) → self._bbo_diag; shadow durum.json'a gömülür."""
        try:
            from .unified.liquidity import spread_status
            now = time.time()
            f = (getattr(self, "feeds", {}) or {}).get("okx")
            feed_syms = set(getattr(f, "symbols", []) or [])
            rows, durum = [], {}
            for c in ctxs or []:
                inst = c.get("inst")
                if not inst:
                    continue
                bid, ask, age = self._ws_bbo(inst)
                st, _ = spread_status(c.get("bid"), c.get("ask"), c.get("book_age_s"))
                durum[st] = durum.get(st, 0) + 1
                quality = getattr(self, "bbo_quality_latest", {}).get(("okx", inst))
                if len(rows) < 12:
                    rows.append({"decision_inst": inst, "feed_inst": inst in feed_syms, "book_exists": inst in self.book,
                                 "ws_bid": bid, "ws_ask": ask, "ws_book_age": age, "bid": c.get("bid"),
                                 "ask": c.get("ask"), "book_age": c.get("book_age_s"),
                                 "bbo_kaynak": c.get("bbo_kaynak"), "spread_status": st,
                                 "quality_state": quality.state.value if quality else "UNKNOWN",
                                 "quality_reason": quality.reason_code if quality else "observation_missing"})
            qstates = {}
            for observation in getattr(self, "bbo_quality_latest", {}).values():
                qstates[observation.state.value] = qstates.get(observation.state.value, 0) + 1
            self._bbo_diag = {"ts": int(now * 1000), "ws_healthy": self.okx_live(),
                              "tick_count": getattr(f, "ticks", None),
                              "last_tick_age": round(now - f.last_msg, 1) if f is not None and getattr(f, "last_msg", 0) else None,
                              "feed_symbols": len(feed_syms), "decision_coins": len(ctxs or []),
                              "book_entries": len(self.book), "spread_durum_sayim": durum, "bbo_quality_states": qstates,
                              "bbo_rest_hata": getattr(self, "_bbo_err", None), "ornek": rows}
        except Exception as e:  # noqa: BLE001 — tanılama karar akışını durdurmaz; hata durum.json'a yazılır
            self._bbo_diag = {"hata": f"{type(e).__name__}: {e}"}

    def _unified_shadow(self, ctxs: list[dict]):
        """Birleşik Baş Trader kancası (karar/işlem) + etkinlik denetçisi."""
        try:                                                  # BBO zenginleştirme: hata yutulur, kararı durdurmaz
            self._bbo_snapshot({c.get("inst") for c in ctxs or [] if c.get("inst")})
            for c in ctxs or []:
                if c.get("inst"):
                    c.update(self._book_ctx(c["inst"]))
        except Exception:  # noqa: BLE001
            pass
        self._bbo_tanilama(ctxs)
        self._unified_step(ctxs)
        self._unified_audit()

    def _poll_polat_research_bridge(self) -> dict:
        """Read Polat telemetry into a separate status file; never feed it into execution quotes."""
        target = self.site / "birlesik" / "polat_kopru.json"
        shared_path = (os.environ.get("UNIFIED_POLAT_BRIDGE_PATH")
                       or os.environ.get("POLAT_UNIFIED_BRIDGE_PATH") or "").strip()
        now_ms = int(time.time() * 1000)
        status = {
            "source": "polat", "scope": "research_only", "status": "disabled",
            "reason": "shared_path_not_configured", "last_sequence": None,
            "sequence": None, "generated_at_ms": None, "received_at_ms": now_ms,
            "age_ms": None, "observation_count": 0, "source_run_id": None, "telemetry": {},
        }
        last_sequence = getattr(self, "_polat_bridge_last_sequence", None)
        if last_sequence is None:
            try:
                previous = json.loads(target.read_text(encoding="utf-8"))
                value = previous.get("last_sequence")
                if type(value) is int and value >= 0:
                    last_sequence = value
            except (OSError, ValueError, AttributeError):
                pass
        if shared_path:
            from .unified.polat_bridge import read_snapshot
            result = read_snapshot(
                shared_path, now_ms=now_ms, max_age_ms=45_000, last_sequence=last_sequence,
                hmac_key=os.environ.get("UNIFIED_POLAT_BRIDGE_HMAC_KEY") or None,
                require_hmac=bool(os.environ.get("UNIFIED_POLAT_BRIDGE_HMAC_KEY")),
            )
            if result.status == "fresh" and result.sequence is not None:
                last_sequence = result.sequence
                self._polat_bridge_last_sequence = result.sequence
            status.update({
                "status": result.status, "reason": result.reason,
                "last_sequence": last_sequence, "sequence": result.sequence,
                "generated_at_ms": result.generated_at_ms,
                "age_ms": (now_ms - result.generated_at_ms) if result.generated_at_ms else None,
                "observation_count": len(result.observations),
                "source_run_id": result.source_run_id,
                "telemetry": result.telemetry or {},
            })
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(status, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(target)
        self.polat_bridge_status = status
        return status

    def _safe_poll_polat_research_bridge(self) -> dict:
        """Bridge diagnostics are non-critical and must never stop the UnifiedTrader cycle."""
        try:
            return self._poll_polat_research_bridge()
        except Exception as exc:  # noqa: BLE001
            status = {
                "source": "polat", "scope": "research_only", "status": "error",
                "reason": f"status_publish_failed:{type(exc).__name__}",
                "last_sequence": getattr(self, "_polat_bridge_last_sequence", None),
                "sequence": None, "generated_at_ms": None,
                "received_at_ms": int(time.time() * 1000), "age_ms": None,
                "observation_count": 0, "source_run_id": None,
            }
            self.polat_bridge_status = status
            print("[polat bridge telemetry] error:", type(exc).__name__, str(exc)[:180])
            return status

    def _unified_step(self, ctxs: list[dict]):
        """Birleşik Baş Trader kancası. `birlesik.karar_modu`:
          legacy  → hiçbir şey yapmaz (varsayılan)
          shadow  → karar günlüğü + AYRI gölge sanal defter (site/birlesik/golge/): Baş Trader'ın açma kararları canlı
                    fiyatla sanal pozisyon olur; gerçek emir YOK, legacy/demo/unified paper defterleri değişmez
          unified → ayrı bir PAPER defterinde (site/birlesik/yurutme.json) birleşik motorla işlem açar/yönetir.
        Legacy defter, emirler ve legacy yöneticiler hiçbir modda etkilenmez. Her hata yutulur."""
        try:
            from .unified.flags import decision_mode
            from .unified.models import DecisionMode
            mode = decision_mode(self.cfg)
            if mode is DecisionMode.LEGACY:
                return
            # Polat telemetry is published separately for research/diagnostics only.
            self._safe_poll_polat_research_bridge()
            from .unified.trader import UnifiedTrader, quote_from_book
            if mode is DecisionMode.SHADOW:
                tr = getattr(self, "_golge_trader", None)
                if tr is None:
                    tr = self._golge_trader = UnifiedTrader(self.cfg, self.site / "birlesik" / "golge",
                                                            journal_root=self.site / "birlesik", mode="shadow")
                tr.diag = getattr(self, "_bbo_diag", None)
                now = time.time()
                insts = {c.get("inst") for c in ctxs} | set(tr.adapter.positions) | \
                    {o["intent"]["inst"] for o in tr.adapter.open_orders()}
                book = {i: trusted for i in insts if i and (trusted := self._trusted_bbo(i)[0])}
                quotes = {i: q for i in insts if i and (q := quote_from_book(i, book.get(i), now))}
                ev, self.golge_fund_events = list(getattr(self, "golge_fund_events", [])), []
                okx = (getattr(self, "feeds", {}) or {}).get("okx")
                out = tr.step(ctxs, quotes, meta=self.meta, closed_bars=getattr(self, "unified_closed_bars", {}),
                              ws_healthy=okx.healthy() if okx else None, funding_events=ev)
                self.unified_shadow = out
                print(f"[birleşik gölge] {out['coin']} coin · açık {out['acik']} · açılan {len(out['acilan'])} · "
                      f"çıkış {len(out['cikislar'])} · {out['karar']}")
                return
            tr = getattr(self, "_unified_trader", None)
            if tr is None:
                tr = self._unified_trader = UnifiedTrader(self.cfg, self.site / "birlesik")
            tr.diag = getattr(self, "_bbo_diag", None)
            now = time.time()
            insts = {c.get("inst") for c in ctxs} | set(tr.adapter.positions) | \
                {o["intent"]["inst"] for o in tr.adapter.open_orders()}
            book = {i: trusted for i in insts if i and (trusted := self._trusted_bbo(i)[0])}
            quotes = {i: q for i in insts if i and (q := quote_from_book(i, book.get(i), now))}
            okx = (getattr(self, "feeds", {}) or {}).get("okx")
            out = tr.step(ctxs, quotes, meta=self.meta, closed_bars=getattr(self, "unified_closed_bars", {}),
                          ws_healthy=okx.healthy() if okx else None,
                          global_issue=getattr(self, "halt", None))
            self.unified_shadow = out
            print(f"[birleşik paper] bakiye {out['bakiye']} · açık {out['acik']} · açılan {len(out['acilan'])} · "
                  f"çıkış {len(out['cikislar'])}")
        except Exception as e:  # noqa: BLE001
            print("[birleşik] hata:", type(e).__name__, e)

    def _unified_audit(self, interval_s: float = 3600):
        """Etkinlik denetçisi (shadow/unified modda saatte bir): legacy defter + birleşik paper defteri → etkinlik.json.
        Yalnız okur ve rapor yazar; hiçbir işlemi açmaz/kapatmaz. Hata yutulur."""
        try:
            from .unified.flags import decision_mode
            from .unified.models import DecisionMode
            if decision_mode(self.cfg) is DecisionMode.LEGACY:
                return
            now = time.time()
            if now - getattr(self, "_audit_at", 0.0) < interval_s:
                return
            self._audit_at = now
            from .unified.effectiveness import EffectivenessAuditor
            tr = getattr(self, "_unified_trader", None)
            aud = EffectivenessAuditor(self.cfg, self.site / "birlesik")
            rep = aud.audit((getattr(self, "ledger", None) or {}).get("closed") or [],
                            (tr.adapter.closed if tr else []) +
                            (self._golge_trader.adapter.closed if getattr(self, "_golge_trader", None) else []))
            aud.write(rep)
            self.unified_audit = rep.get("ozet")
        except Exception as e:  # noqa: BLE001
            print("[birleşik etkinlik] hata:", type(e).__name__, e)

    def _peers(self, inst: str) -> dict:
        """Baş Trader bütün kulvarları izler: diğer yöneticilerin bu coindeki gerçek pozisyonları."""
        out = {"long": 0, "short": 0, "who": []}
        for t in list(self.ledger["open"]):
            if t.get("inst") != inst or t.get("shadow") or t.get("carry") or t.get("mgr") in (None, "ortak", *BAS_IDS):
                continue
            out[t["side"]] = out.get(t["side"], 0) + 1
            m = self.desk.by_id.get(t["mgr"]) if self.desk else None
            out["who"].append(f"{m.name if m else t['mgr']} {t['side']}")
        return out

    def _bas_pyramid(self, idea: dict) -> bool:
        """Kazanana ekleme (piramit): Baş Trader bu coinde aynı yönde kârdaysa (son kat ≥ +1R ve stop'u başabaş ya da
        ötesinde, yani o katların riski sıfır) ve puan hâlâ eşiğin üstündeyse yeni bir kat ekler. En fazla 3 kat; her
        yeni kat bir öncekinin yarısı kadar risk alır. Önceki katlar başabaşta olduğu için açık risk sadece yeni kat kadardır."""
        me = idea["mgr"]
        mine = [t for t in self.ledger["open"] if t.get("mgr") == me and t["inst"] == idea["inst"]]
        if not mine or any(o["mgr"] == me and o["idea"]["inst"] == idea["inst"] for o in self.ledger.get("orders") or []):
            return False
        if any(t["side"] != idea["side"] for t in mine) or len(mine) >= 3:
            return False
        last = max(mine, key=lambda t: t.get("entry_ts") or 0)
        if (last.get("r_now") or 0) < 1.0 or any(lg.open_risk(t) > 0 for t in mine):
            return False
        idea["pyramid"] = True
        idea["layer"] = len(mine) + 1
        full_layers = bool(((self.desk.by_id.get(me).extra if self.desk and self.desk.by_id.get(me) else {}) or {}).get("piramit_tam"))
        idea["risk_scale"] = 1.0 if full_layers else 0.5 ** len(mine)
        idea["plan"] = (f"Kazanana ekleme: {len(mine)}. kat +{last.get('r_now', 0):.1f}R kârda ve stop'u başabaşta (riski "
                        f"sıfır); puan hâlâ güçlü → {idea['layer']}. kat, {'tam' if full_layers else 'yarım'} risk. ") + idea["plan"]
        return True

    def daily_tick(self, hour: int = 9):
        """Her sabah (yerel 09:00 sonrası ilk tur) Telegram'a kısa özet: dünden bu yana kim ne kazandı/kaybetti."""
        loc = datetime.now(self.tz)
        if loc.hour < hour:
            return
        day = loc.strftime("%Y-%m-%d")
        st = self.state.setdefault("daily", {})
        if st.get("date") == day:
            return
        summ = self.desk.summary(int(time.time() * 1000))
        eq = {k: v["equity"] for k, v in summ.items() if v.get("active")}
        prev, since = st.get("eq") or {}, int(st.get("ts") or 0)
        label = "dünden beri"
        if not prev:      # ilk özet: başlangıç bütçesine göre
            prev, since, label = {k: v["capital"] for k, v in summ.items()}, int(self.desk.st.get("started") or 0), "başlangıçtan beri"
        st.update({"date": day, "eq": eq, "ts": int(time.time() * 1000)})
        self.dirty = True
        closed = [t for t in self.ledger["closed"] if (t.get("exit_ts") or 0) > since and not t.get("shadow")]
        L = [f"☀️ <b>Sabah özeti</b> · {loc.strftime('%d.%m')}"]
        tot0, tot1 = sum(prev.get(k, 0) for k in eq), sum(eq.values())
        L.append(f"Firma: {_usd(tot1, False)} ({_usd(tot1 - tot0)} {label}) · {len(closed)} işlem kapandı")
        rows = sorted(((summ[k], eq[k] - prev.get(k, eq[k])) for k in eq), key=lambda x: -x[1])
        for m, d in rows:
            nc = [t for t in closed if t.get("mgr") == m["id"]]
            wr = sum(1 for t in nc if (t.get("r") or 0) > 0)
            L.append(f"{m['emoji']} {nt.esc(m['name'])}: {_usd(d)} · {m['open']} açık" +
                     (f" · {len(nc)} kapandı ({wr} kâr)" if nc else ""))
        w = (getattr(self, "bas_watch", None) or {}).get("rows") or []
        if w:
            L.append("👑 Radarda: " + ", ".join(f"{r['sym']} {r['score']:+.2f}" for r in w[:4]))
        self._notify("\n".join(L))

    def _bas_public(self) -> dict | None:
        if not self.desk or "bas" not in self.desk.by_id:
            return None
        w = getattr(self, "bas_watch", None) or {}
        op = [t for t in self.ledger["open"] if t.get("mgr") == "bas"]
        cl = [t for t in self.ledger["closed"] if t.get("mgr") == "bas"]
        lanes = []
        for m in self.desk.mgrs:
            if m.id in BAS_IDS:
                continue
            mo = [t for t in self.ledger["open"] if t.get("mgr") == m.id and not t.get("shadow")]
            lanes.append({"id": m.id, "name": m.name, "emoji": m.emoji, "open": len(mo),
                          "upnl": round(sum(t.get("upnl_usd", 0.0) for t in mo), 2),
                          "coins": [f"{t['sym']} {'▲' if t['side'] == 'long' else '▼'}" for t in mo][:8]})
        return {"watch": w.get("rows") or [], "watch_ts": w.get("ts"),
                "giris_esik": bas.GIRIS_ESIK, "cikis_esik": bas.CIKIS_ESIK, "weights": bas.W, "labels": bas.LABEL,
                "open": [{"sym": t["sym"], "side": t["side"], "entry": t.get("entry"), "sl": t.get("sl"),
                          "upnl": round(t.get("upnl_usd", 0.0), 2), "r": round(t.get("r_now", t.get("r", 0.0)) or 0.0, 2),
                          "lev": t.get("lev"), "score": t.get("bas_score"), "ts": t.get("entry_ts"),
                          "layer": t.get("layer") or 1} for t in op],
                "closed_n": len(cl), "closed_pnl": round(sum(t.get("pnl_usd", 0.0) for t in cl), 2),
                "learn": self.desk.st.get("bas_learn"), "w_learn": bas.W_LEARN,
                "lanes": lanes,
                "decisions": [j for j in (self.desk.st.get("journal") or []) if j.get("mgr") == "bas"][-12:]}

    def _rel_strength(self, ctxs: list[dict]):
        """Gözcü: BTC'ye göre 30 günlük göreli güç ve kesitsel sıra (1 = en güçlü)."""
        btc = next((c for c in ctxs if c.get("sym") == "BTC"), None)
        b30 = (btc or {}).get("ret30")
        vals = []
        for c in ctxs:
            r = c.get("ret30")
            if isinstance(r, (int, float)) and r == r and isinstance(b30, (int, float)) and b30 == b30:
                c["rs_rel"] = r - b30
                vals.append(c["rs_rel"])
        if len(vals) >= 10:
            arr = sorted(vals)
            for c in ctxs:
                if "rs_rel" in c:
                    c["rs_rank"] = sum(1 for v in arr if v <= c["rs_rel"]) / len(arr)

    def crowd(self, inst: str, fetch: bool = True) -> dict:
        """(işçi) Balina Gözü verisi: hesap ve büyük hesap long/short oranı + son 1 saatin likidasyonları. 10 dk önbellek."""
        hit = self.crowd_cache.get(inst)
        sm = ((self.smart or {}).get("coins") or {}).get(inst.split("-")[0])
        if hit and time.time() - hit[0] < 600:
            return {**hit[1], "hl": sm} if sm else hit[1]
        if not fetch or self.demo:
            return (hit or (0, {}))[1]
        out = {}
        try:
            r = self.client.get("/api/v5/rubik/stat/contracts/long-short-account-ratio-contract",
                                {"instId": inst, "period": "1H", "limit": "1"})
            out["ls_acct"] = _f(r[0][1]) if r else None
            r = self.client.get("/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader",
                                {"instId": inst, "period": "1H", "limit": "1"})
            out["ls_top"] = _f(r[0][1]) if r else None
        except Exception:  # noqa: BLE001
            pass
        try:
            uly = inst.replace("-SWAP", "")
            rows = self.client.get("/api/v5/public/liquidation-orders",
                                   {"instType": "SWAP", "uly": uly, "state": "filled", "limit": "100"})
            ct = (self.meta.get(inst) or {}).get("ct_val") or 1.0
            cut = int(time.time() * 1000) - 3600_000
            lq = {"long_usd": 0.0, "short_usd": 0.0}
            for row in rows:
                for d in row.get("details") or []:
                    if _f(d.get("ts"), 0) < cut:
                        continue
                    side = "long" if d.get("posSide") == "long" or d.get("side") == "sell" else "short"
                    lq[f"{side}_usd"] += _f(d.get("sz"), 0.0) * _f(d.get("bkPx"), 0.0) * ct
            out["liq_1h"] = lq
        except Exception:  # noqa: BLE001
            pass
        self.crowd_cache[inst] = (time.time(), out)
        if len(self.crowd_cache) > 400:
            self.crowd_cache.pop(next(iter(self.crowd_cache)))
        return {**out, "hl": sm} if sm else out

    def enrich_idea(self, idea: dict, c: dict, fetch: bool = True):
        """Yöneticinin uzmanları görüş verir (Gözcü, Balina Gözü, Analist-Hakem); karne ağırlıklı çarpan fikre eklenir."""
        if not self.desk:
            return
        fa = c.get("funding_ann")
        if isinstance(fa, (int, float)) and fa == fa:
            idea["funding_ann"] = fa                           # v10 kalabalık kapısı
        if c.get("recent_closes"):
            idea["recent_closes"] = c["recent_closes"]         # v10 yeniden giriş kuralı (kapanmış günler)
        try:
            c.setdefault("coin_class", gmd.coin_class(self.meta.get(idea["inst"]), int(time.time() * 1000), self.desk.fc))
            idea["feat"] = team.features_for(c, idea["side"], idea.get("conviction", 0.5))
            ops = [ex.gozcu(c, idea, self.hakem), ex.balina(c, idea, self.crowd(idea["inst"], fetch=fetch)), ex.hakem(idea, self.hakem)]
            mult, ops = ex.combine(ops, self.desk.st, idea["mgr"])
            idea["experts"], idea["exp_mult"] = ops, round(mult, 3)
        except Exception as e:  # noqa: BLE001
            print(f"[uzmanlar] {idea.get('mgr')} {idea.get('sym')}: {e}")

    def _safe(self, fn, *a):
        try:
            return fn(*a)
        except Exception as e:  # noqa: BLE001
            return None

    def _orphan_close_stops(self, scanned: set):
        """Evrenden düşmüş coinlerdeki açık işlemlerin kapanış stop'u da günlük kapanışta kontrol edilir."""
        for inst in {t["inst"] for t in list(self.ledger["open"]) if t.get("sl_close") is not None and t.get("tf") == "1D"} - scanned:
            try:
                df = self.client.candles(inst, "1D", 3, closed_only=True)
                if len(df):
                    self._queue_rule_exits({"inst": inst, "tf": "1D", "close": float(df["close"].iloc[-1]), "_only_cs": True})
            except Exception as e:  # noqa: BLE001
                print(f"[kapanış stop] {inst}: {e}")

    def _queue_rule_exits(self, c: dict):
        """(işçi) Kural tabanlı çıkışlar ve v10 kapanış stop'u MUM KAPANIŞINDA kontrol edilir; ana döngü kapatır.
        Günlük işlemlerde canlı (kapanmamış) mumla çıkış yapılmaz: 5 yıllık laboratuvar kapanışla doğrulandı."""
        live = bool(c.get("live_bar"))
        for t in list(self.ledger["open"]):
            if (not live and t.get("sl_close") is not None and t["inst"] == c["inst"] and t.get("tf") == c["tf"]
                    and isinstance(c.get("close"), (int, float))):
                sgn = 1 if t["side"] == "long" else -1
                if sgn * (c["close"] - t["sl_close"]) <= 0:
                    with self.lock:
                        self.signal_exits.append((t["id"], f"kapanış stop'u: günlük kapanış {c['close']:.6g}, stop "
                                                           f"{t['sl_close']:.6g}'in ötesinde"))
                    continue
            r = t.get("exit_rule")
            if (live and t.get("tf") == "1D") or c.get("_only_cs"):
                continue
            if r and t["inst"] == c["inst"] and t.get("tf") == c["tf"] and not t.get("carry"):
                why = quant.exit_signal(r, c, t["side"])
                if why:
                    with self.lock:
                        self.signal_exits.append((t["id"], why))

    def process_rule_exits(self):
        with self.lock:
            req, self.signal_exits = self.signal_exits, []
        if not req:
            return
        now_ms = int(time.time() * 1000)
        items, seen = [], set()
        for tid, why in req:
            t = next((x for x in self.ledger["open"] if x["id"] == tid), None)
            if not t or tid in seen:
                continue
            seen.add(tid)
            q = self._quote(t["inst"])
            px = (q[0] if t["side"] == "long" else q[1]) if q else self.prices.get(t["inst"], t["entry"])
            t["exit_note"] = why
            items.append((tid, px, now_ms, "kural çıkışı", False))
        if items:
            self._close_ids(items, "ws")

    def corr(self, inst_a: str, inst_b: str) -> float:
        if inst_a == inst_b:
            return 1.0
        a, b = self.daily_cache.get(inst_a), self.daily_cache.get(inst_b)
        if not (a and b):
            return 0.0
        return quant.corr(a["ret"][-60:], b["ret"][-60:])

    def refresh_news(self):
        """(işçi) Haber Masası: kaynakları tarar, yeni kırmızı/sarı uyarıları bildirir."""
        import requests
        self.t["news"] = time.time()
        sess = requests.Session()
        sess.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) kripto-radar/8", "Accept": "*/*"})

        def gj(u):
            r = sess.get(u, timeout=12)
            r.raise_for_status()
            return r.json()

        def gt(u):
            r = sess.get(u, timeout=12)
            r.raise_for_status()
            return r.text

        uni = {r["sym"] for r in self.universe}
        self.news.refresh(uni, gj, gt)
        alerts, self.news.new_alerts = self.news.new_alerts, []
        held = {t["sym"] for t in self.ledger["open"] if not t.get("shadow")}
        now_ms = int(time.time() * 1000)
        lines = []
        for a in alerts:
            if now_ms - a["ts"] > 24 * 3600_000:
                continue
            if a["level"] == "kirmizi" or a["sym"] in held:
                tag = "🔴 KIRMIZI" if a["level"] == "kirmizi" else "🟡 SARI"
                lines.append(f"{tag} <b>{nt.esc(a['sym'])}</b>{' (pozisyonumuz var)' if a['sym'] in held else ''}: "
                             f"{nt.esc(a['title'][:140])} <i>({nt.esc(a['src'])})</i>")
        if lines:
            self._notify("📰 <b>Haber Masası</b>\n" + "\n".join(lines[:6]) +
                         "\n<i>Kırmızı: o coinde yeni işlem yok. Sarı: yükselişin peşine düşülmez.</i>")
        for s_ in list(self.news.delist_okx):
            for t in list(self.ledger["open"]):
                if t["sym"] == s_ and not t.get("carry"):
                    with self.lock:
                        self.signal_exits.append((t["id"], "OKX borsadan çıkarma duyurusu"))
        self.news.delist_okx.clear()
        with self.lock:
            self.state["news"] = self.news.to_state()
        self.dirty = True

    def _bt8(self) -> dict | None:
        """5 yıllık test özeti (data/backtest_v8.json, test iş akışı yazar). Saatte bir okunur."""
        if time.time() - getattr(self, "_bt8_t", 0) < 3600 and hasattr(self, "_bt8_v"):
            return self._bt8_v
        self._bt8_t = time.time()
        try:
            r = json.loads((ROOT / "data" / "backtest_v8.json").read_text(encoding="utf-8"))
            keep = ("n", "avg_r", "win_pct", "pf", "t_stat")
            ek = ("cagr_pct", "max_dd_pct", "sharpe", "dsr")
            self._bt8_v = {
                "generated": r.get("generated"), "test_start": r.get("test_start"), "n_coins": r.get("n_coins"),
                "years": r.get("years"), "trials": r.get("trials"), "policy_active": r.get("policy_active"),
                "cost": r.get("cost"),
                "managers": {k: {"name": b["name"], "all": {x: b["all"].get(x) for x in keep},
                                 "oos": {x: b["oos"].get(x) for x in keep}, "long": {x: b["long"].get(x) for x in keep},
                                 "short": {x: b["short"].get(x) for x in keep},
                                 "equity": {x: (b.get("equity") or {}).get(x) for x in ek}, "by_year": b.get("by_year")}
                             for k, b in (r.get("managers") or {}).items()},
                "firm": {k: {"taken": f.get("taken"), "equity": {x: (f.get("equity") or {}).get(x) for x in ek}}
                         for k, f in (r.get("firm") or {}).items()}}
        except Exception:  # noqa: BLE001
            self._bt8_v = None
        return self._bt8_v

    def desk_env(self):
        """Risk müdürünün kullandığı ortam bilgisi (her turda güncellenir)."""
        if self.desk:
            self.desk.env = {"weather": self.weather or None, "cal_block": self.calendar.block(),
                             "fng_mult": self.news.fng_mult, "corr": self.corr}

    # ── arka plan işçisi ─────────────────────────────────────────────
    SLOW_JOBS = ("hourly", "funding", "news", "cal")

    def enqueue(self, name: str):
        with self.lock:
            if name in self.job_names:
                return
            self.job_names.add(name)
        (self.jobs_slow if name.split(":")[0] in self.SLOW_JOBS else self.jobs).put(name)

    def _start_workers(self):
        self.worker = threading.Thread(target=self._worker_loop, args=(self.jobs,), daemon=True, name="isci")
        self.worker.start()
        self.worker_slow = threading.Thread(target=self._worker_loop, args=(self.jobs_slow,), daemon=True,
                                            name="isci-agir")
        self.worker_slow.start()

    def _worker_loop(self, q: queue.Queue | None = None):
        q = q or self.jobs
        while True:
            name = q.get()
            if name == "__stop__":
                return
            t_job = time.perf_counter()
            try:
                if name.startswith("scan:"):
                    _, tfs, b = name.split(":")
                    strong = []
                    for tf in tfs.split(","):
                        strong += self.scan_tf(tf, float(b) if b else None)
                    if not (self.team_on and self.desk):
                        with self.lock:
                            self.pending_open.extend(strong)
                elif name == "funding":
                    self.refresh_funding()
                elif name == "news":
                    self.refresh_news()
                elif name == "live1d":
                    self.scan_live_daily()
                elif name == "cal":
                    self.t["cal"] = time.time()
                    self.calendar.refresh()
                elif name == "hourly":
                    self.refresh_universe()
                    self.refresh_funding()
                    self.refresh_hourly()
                    self.maybe_reload_config()
                    self.plan_carry()
            except Exception as e:  # noqa: BLE001
                print(f"[işçi] {name} hata: {e}")
                traceback.print_exc()
                self.errors = ([f"{name}: {e}"] + self.errors)[:20]
            finally:
                self.perf.add("işçi:" + name.split(":")[0], time.perf_counter() - t_job)
                with self.lock:
                    self.job_names.discard(name)

    # ── pozisyonlar ──────────────────────────────────────────────────
    def _finish_close(self, closed: list[dict], via: str):
        if not closed:
            return
        for t in closed:
            try:
                t["lesson"] = learn.lesson(t)
            except Exception:  # noqa: BLE001
                pass
        if self.desk:
            try:
                for msg in self.desk.on_closed(closed):
                    if "Uzman karnesi" in msg:
                        self._notify(msg)
            except Exception as e:  # noqa: BLE001
                print(f"[kapanış sonrası] {e}")
        self.stats_counter["closed"] += len(closed)
        self.stats_counter[f"{via}_closes"] = self.stats_counter.get(f"{via}_closes", 0) + len(closed)
        self.dirty = True
        self.rebuild_watch()
        real = [t for t in closed if not t.get("shadow")]      # gölge işlemler Telegram'ı meşgul etmez
        if real:
            self._notify(self.msg_closed(real))

    def _close_ids(self, items: list[tuple], via: str):
        """items: (id, fiyat, ms, sonuç, maker)"""
        closed = []
        by_id = {t["id"]: t for t in self.ledger["open"]}
        for tid, px, ts, outcome, maker in items:
            t = by_id.get(tid)
            if not t:
                continue
            self.ledger["open"].remove(t)
            closed.append(lg.close_trade(t, px, int(ts), outcome, self.cfg, maker=maker))
        self.ledger["closed"].extend(closed)
        self._finish_close(closed, via)
        return closed

    def process_hits(self):
        if not self.hits:
            return
        items, partial_msgs = [], []
        by_id = {t["id"]: t for t in self.ledger["open"]}
        for key in list(self.hits):
            kind, px, ts, maker = self.hits.pop(key)
            if kind == "tp1":
                t = by_id.get(key.split("|")[0])
                if t and not t.get("tp1_done"):
                    part = lg.take_partial(t, px, int(ts), self.cfg)
                    self.stats_counter["partials"] += 1
                    partial_msgs.append((t, part))
                continue
            t = by_id.get(key)
            if t and kind == "stop" and t.get("trail") and t.get("tp1_done"):
                kind = "iz stop"
            items.append((key, px, ts, kind, maker))
        if partial_msgs:
            self.dirty = True
            self.rebuild_watch()
            pm = [x for x in partial_msgs if not x[0].get("shadow")]
            if pm:
                self._notify(self.msg_partial(pm))
        self._close_ids(items, "ws")

    def process_trailing(self):
        """İz süren stop: canlı akıştaki en iyi fiyata göre stop'u kâr yönüne taşır."""
        moved = False
        for t in self.ledger["open"]:
            if t.get("carry"):
                continue
            px = self.prices.get(t["inst"])        # v10.2: işlemin kendi tepesi (t["hw"]) anlık fiyattan izlenir;
            if not px:                              # coin bazlı oturum tepesi girişten önceki fiyatları da içerebilir
                continue
            if lg.trail(t, px, self.cfg):
                moved = True
                self.stats_counter["trail_moves"] += 1
        if moved:
            self.rebuild_watch()
            self.dirty = True

    def process_time_exits(self):
        now_ms = int(time.time() * 1000)
        items = []
        for t in self.ledger["open"]:
            if not t.get("real"):
                continue
            if now_ms >= t["entry_ts"] + t["max_saat"] * 3600 * 1000:
                q = self._quote(t["inst"])
                px = (q[0] if t["side"] == "long" else q[1]) if q else self.prices.get(t["inst"], t["entry"])
                items.append((t["id"], px, now_ms, "süre", False))
        if items:
            self._close_ids(items, "ws")

    def process_funding(self):
        now_ms = time.time() * 1000
        events, self.fund_events = self.fund_events, []
        open_insts = {t["inst"] for t in self.ledger["open"]}
        for inst in open_insts:
            cur = self.fund_cur.get(inst)
            if cur and cur[1] and now_ms >= cur[1]:
                events.append((inst, cur[0], cur[1]))
        for inst, rate, settle in events:
            if inst not in open_insts:
                continue
            px = self.prices.get(inst)
            if not px:
                continue
            pays = lg.apply_funding(self.ledger, inst, rate, int(settle), px)
            for t, _pay in pays:
                if t.get("carry"):
                    t["neg_count"] = t.get("neg_count", 0) + 1 if rate < 0 else 0
            if pays:
                self.stats_counter["funding_events"] += len(pays)
                self.dirty = True

    def process_manual(self):
        while self.manual:
            target = self.manual.pop(0)
            now_ms = int(time.time() * 1000)
            items = []
            for t in self.ledger["open"]:
                if t["id"] == target or t["sym"] == target:
                    q = self._quote(t["inst"])
                    px = (q[0] if t["side"] == "long" else q[1]) if q else self.prices.get(t["inst"], t["entry"])
                    items.append((t["id"], px, now_ms, "manuel", False))
            if items:
                self._close_ids(items, "ws")
            else:
                self._reply(f"{target} için açık demo işlem bulunamadı.")

    def reconcile(self, catchup: bool = False) -> list[dict]:
        """1 dk'lık mumlarla yedek kontrol (akışta kaçan anlık iğneler ve kesinti anları için)."""
        self.t["reconcile"] = time.time()
        if not self.ledger["open"]:
            return []
        lim = 300 if catchup else 6

        def fetch(inst):
            return self.client.candles(inst, "1m", lim, closed_only=False)

        closed = lg.update(self.ledger, fetch, self.prices, self.cfg, int(time.time() * 1000), bar_ms=60_000)
        self._finish_close(closed, "rest")
        return closed

    manage_positions = reconcile   # geriye dönük ad

    # ── risk yöneticisi ──────────────────────────────────────────────
    def risk_limits(self) -> dict:
        """Devre kesici limitleri. Ekip modunda risk müdürünün firma limitleri geçerlidir (tekli sistemin değil)."""
        rc = dict(lg.risk_cfg(self.cfg))
        if self.desk:
            fc = self.desk.fc
            rc.update(gunluk_kayip_yuzde=fc["gunluk_kayip_yuzde"], max_dusus_yuzde=fc["max_dusus_yuzde"],
                      max_isi_yuzde=fc["max_isi_yuzde"], kayip_serisi=fc["kayip_serisi"])
            yon = [m for m in self.desk.mgrs if m.max_risk > 0]       # Elif (carry) stop riskiyle çalışmaz
            lo = min((m.base_risk for m in yon), default=0)
            hi = max((m.max_risk for m in yon), default=0)
            rc["risk_yuzde"] = f"{lo:g}–{hi:g} (yöneticiye ve kanaate göre)".replace(".", ",")
            rc["kaynak"] = "firma"
        return rc

    def risk_tick(self):
        """Her saniye: piyasa değerine göre özsermaye, günlük kayıp ve zirveden düşüş kontrolü."""
        self.t["risk"] = time.time()
        lg.mark(self.ledger, self.prices, self.cfg)
        b = lg.balance(self.ledger, self.cfg)
        eq = b["equity"]
        rs = self.state["risk"]
        rc = self.risk_limits()
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if rs.get("day") != day:
            rs["day"], rs["day_start"] = day, eq
            if rs.get("halt") == "gunluk":
                rs["halt"] = None
                self._notify("🟢 Yeni gün başladı: günlük kayıp limiti sıfırlandı, işlemler yeniden açık.")
        rs["peak"] = max(rs.get("peak") or eq, eq)
        rs["equity"] = round(eq, 2)
        day_pnl = eq - (rs.get("day_start") or eq)
        rs["day_pnl"] = round(day_pnl, 2)
        day_lim = self.day_limit_usd(rc)
        dd = (eq / rs["peak"] - 1) * 100 if rs["peak"] else 0
        rs["dd_pct"] = round(dd, 2)
        # Limit düzeltmesi: günlük durdurma daha düşük (yanlış) bir limitle konmuşsa ve kayıp güncel limitin
        # altında kalıyorsa bir kez kaldırılır. Gerçek limit aşıldıktan sonra toparlanma durdurmayı KALDIRMAZ.
        if (rs.get("halt") == "gunluk" and (rs.get("halt_lim") or 0) < rc["gunluk_kayip_yuzde"]
                and rs.get("day_start") and day_pnl > -day_lim):
            rs["halt"] = None
            rs["halt_lim"] = None
            self._notify(f"🟢 <b>Limit düzeltmesi</b>: günlük durdurma eski (daha sıkı) tekli sistem limitiyle konmuştu; "
                         f"ekibin firma limiti %{rc['gunluk_kayip_yuzde']:g}. Bugün {_usd(day_pnl)}, limitin içinde → işlemler yeniden açık.")
        stops_on = bool((self.desk.fc if self.desk else {}).get("durdurma_aktif", True))
        if not stops_on and rs.get("halt"):
            rs["halt"], rs["halt_lim"] = None, None
            self._notify("🟢 Demo modu: işlemi durduran limitler (günlük kayıp, zirveden düşüş) gerçek API'ye kadar kapalı.")
        if stops_on and not rs.get("halt"):
            if dd <= -rc["max_dusus_yuzde"]:
                rs["halt"] = "dusus"
                rs["halt_lim"] = rc["max_dusus_yuzde"]
                self._notify(f"🛑 <b>Bot durduruldu</b>: zirveden %{abs(dd):.1f} düşüş (limit %{rc['max_dusus_yuzde']:g}). "
                             f"Açık işlemler yönetilmeye devam ediyor, yeni işlem açılmayacak. Devam için /devam yaz.")
            elif rs.get("day_start") and day_pnl <= -day_lim:
                rs["halt"] = "gunluk"
                rs["halt_lim"] = rc["gunluk_kayip_yuzde"]
                self._notify(f"⏸ <b>Günlük kayıp limiti</b>: bugün {_usd(day_pnl)} (limit %{rc['gunluk_kayip_yuzde']:g}). "
                             f"Bugün yeni işlem açılmayacak; UTC gece yarısı otomatik açılır.")
        self.halt = rs.get("halt")
        # kayıp serisi → risk küçült
        rs_list = [t["r"] for t in sorted(self.ledger["closed"], key=lambda x: x.get("exit_ts") or 0)[-20:]]
        streak = 0
        for r in reversed(rs_list):
            if r <= 0:
                streak += 1
            else:
                break
        self.risk_mult = rc["seri_risk_carpan"] if streak >= rc["kayip_serisi"] else 1.0
        rs["loss_streak"] = streak
        rs["risk_mult"] = self.risk_mult
        # setup kill switch
        if rc["otomatik_kapat"]:
            groups: dict = {}
            for t in self.ledger["closed"]:
                # Yalnız eski sinyal setup'ları (Trend/Dönüş): ekip işlemlerinin setup'ı yönetici kimliğidir (kemal…) ve
                # onların kendi denetimi var. Eskiden bu döngü 'kemal' setup'ında SETUP_LABELS KeyError ile döngüyü çökertiyordu.
                if t.get("real") and t.get("setup") in sg.SETUP_LABELS:
                    groups.setdefault((t["setup"], t.get("tf", "4H")), []).append(t["r"])
            newly = []
            for k, rs_ in groups.items():
                if len(rs_) >= rc["setup_kapat_min_islem"] and sum(rs_) / len(rs_) < rc["setup_kapat_beklenti_r"]:
                    if k not in self.blocked:
                        newly.append((k, sum(rs_) / len(rs_), len(rs_)))
                    self.blocked.add(k)
            for (setup, tf), avg, n in newly:
                self._notify(f"🚫 <b>{sg.SETUP_LABELS.get(setup, setup)} ({sg.TF_LABEL.get(tf, tf)})</b> kapatıldı: "
                             f"{n} işlemde ortalama {avg:+.2f}R. Para kaybettiren setup'a artık işlem açılmayacak.")
            rs["blocked"] = [f"{a}|{b}" for a, b in sorted(self.blocked)]
        # özsermaye geçmişi (5 dk'da bir)
        if time.time() - self.t["eq"] >= 300:
            self.t["eq"] = time.time()
            h = self.state["eq_hist"]
            h.append([int(time.time() * 1000), round(eq, 2)])
            if len(h) > 4000:
                old, new = h[:2000], h[2000:]
                self.state["eq_hist"] = old[::6] + new        # eski kısmı seyrekleştir
        return eq

    def day_limit_usd(self, rc: dict | None = None) -> float:
        """Bugünkü kayıp limiti ($): firma limiti + kullanıcının o gün verdiği ek hak."""
        rc = rc or self.risk_limits()
        rs = self.state["risk"]
        base = (rs.get("day_start") or 0) * rc["gunluk_kayip_yuzde"] / 100
        ex = rs.get("extra") or {}
        return base + (ex.get("usd", 0.0) if ex.get("day") == rs.get("day") else 0.0)

    def resume(self, arg: str = "") -> str:
        """/devam. Zirveden düşüşte: zirveyi sıfırlar. Günlük limitte: kullanıcı onayıyla günde 1 kez ek kayıp hakkı."""
        rs = self.state["risk"]
        was = rs.get("halt")
        if was == "gunluk":
            fc = self.desk.fc if self.desk else {}
            ek = float(fc.get("gunluk_ek_hak_yuzde", 1.5))
            hak = int(fc.get("gunluk_ek_hak_sayisi", 1))
            ex = rs.get("extra") or {}
            used = ex.get("n", 0) if ex.get("day") == rs.get("day") else 0
            if used >= hak:
                return (f"⛔ Bugünkü ek kayıp hakkı kullanıldı ({used}/{hak}). Limit TR 03:00'te (UTC gece yarısı) sıfırlanır. "
                        "Kötü günde durmak sistemin en önemli frenidir.")
            add = (rs.get("day_start") or 0) * ek / 100
            if arg.strip().lower() not in ("evet", "onay", "tamam"):
                return (f"⏸ Günlük kayıp limiti dolu: bugün {_usd(rs.get('day_pnl', 0))}.\n"
                        f"Onaylarsan bugün için <b>%{ek:g} ({_usd(add, False)})</b> ek kayıp hakkı açılır: ekip işlem açmaya devam "
                        f"eder, bu pay da biterse gün sonuna kadar durur. Günde {hak} kez kullanılabilir.\n"
                        "Onay için: <b>/devam evet</b>")
            lim_now = self.day_limit_usd()
            loss = max(0.0, -(rs.get("day_pnl") or 0.0))
            extra_total = (ex.get("usd", 0.0) if ex.get("day") == rs.get("day") else 0.0) + max(loss - lim_now, 0.0) + add
            rs["extra"] = {"day": rs.get("day"), "usd": round(extra_total, 4), "n": used + 1, "ts": int(time.time() * 1000)}
            rs["halt"], rs["halt_lim"] = None, None
            self.halt = None
            self.dirty = True
            if self.desk:
                self.desk.journal("-", "-", "-", "-", "ek limit", f"kullanıcı onayıyla bugün %{ek:g} ({add:.2f}$) ek kayıp hakkı")
            return (f"🟢 <b>Ek kayıp hakkı açıldı</b> (kullanıcı onayı): bugün {_usd(add, False)} daha. Yeni limit: "
                    f"günün zararı {_usd(-(loss + add))} olursa ekip gün sonuna kadar durur.")
        rs["halt"] = None
        rs["peak"] = rs.get("equity")
        self.halt = None
        self.dirty = True
        return ("🟢 Devre kesici kaldırıldı, zirve bugünkü bakiyeye sıfırlandı. Yeni işlemler açılabilir."
                if was else "Zaten aktif bir durdurma yok.")

    def _book(self, inst):
        try:
            b = self.client.books(inst, 50)
        except Exception:  # noqa: BLE001
            return None
        ct = (self.meta.get(inst) or {}).get("ct_val") or 1.0
        return {"asks": [(p, q * ct) for p, q in b["asks"]], "bids": [(p, q * ct) for p, q in b["bids"]]}

    def _unified_authority_active(self) -> bool:
        """True only when the unified paper trader is configured as the sole new-entry authority."""
        try:
            from .unified.flags import decision_mode
            return decision_mode(self.cfg).value == "unified"
        except Exception:  # noqa: BLE001 — an explicit unified config must fail closed
            return str((self.cfg.get("birlesik") or {}).get("karar_modu") or "").strip().lower() == "unified"

    def _enforce_unified_authority(self) -> bool:
        """Stop legacy entry paths while retaining feeds and management of already-open legacy positions."""
        if not self._unified_authority_active():
            return False
        cancelled = []
        with self.lock:
            self.pending_open.clear()
            self.pending_ideas.clear()
            self.order_hits.clear()
            for order in list(self.ledger.get("orders") or []):
                self.ledger["orders"].remove(order)
                cancelled.append(order)
        if cancelled:
            if self.desk:
                for order in cancelled:
                    idea = order.get("idea") or {}
                    self.desk.journal(order.get("mgr") or idea.get("mgr") or "legacy",
                                      idea.get("sym") or "-", idea.get("tf") or "-",
                                      idea.get("side") or "-", "iptal",
                                      "Yeni pozisyon yetkisi Birleşik Baş Trader'a devredildi")
            self.rebuild_watch()
            self.dirty = True
        return True

    def open_positions(self, strong: list[dict]) -> list[dict]:
        if self._enforce_unified_authority():
            return []
        if not strong:
            return []
        skipped: list[dict] = []
        if self.ledger["open"] or strong:
            self.risk_tick()
        opened = lg.open_new(self.ledger, strong, self.cfg, int(time.time() * 1000),
                             quote=self._quote, meta=lambda i: self.meta.get(i), skipped=skipped,
                             book=self._book, risk_mult=self.risk_mult, blocked=self.blocked,
                             halted={"dusus": "zirveden düşüş limiti", "gunluk": "günlük kayıp limiti"}.get(self.halt))
        if skipped:
            self.skipped = (skipped + self.skipped)[:20]
        if opened:
            self.stats_counter["opened"] += len(opened)
            self.dirty = True
            self.rebuild_watch()
            if any(t["inst"] not in {r["inst"] for r in self.universe} for t in opened):
                self._feed_symbols()
            self._notify(self.msg_opened(opened), buttons=self._open_buttons(opened))
        return opened

    def on_close_request(self, target: str) -> str:
        target = target.strip().upper().replace("USDT", "").replace("-", "") if len(target) < 10 else target.strip()
        match = [t for t in self.ledger["open"] if t["id"] == target or t["sym"] == target]
        if not match:
            return f"{target} için açık demo işlem yok."
        self.manual.append(target)
        return f"⏳ {match[0]['sym']} demo işlemi piyasa fiyatından kapatılıyor…"

    # ── çıktı ────────────────────────────────────────────────────────
    def _arbitrage(self):
        by = {k: dict(v) for k, v in (self.other or {}).items()}
        for r in self.universe:
            cur = self.fund_cur.get(r["inst"])
            fr = self.funding.get(r["inst"]) or {}
            if cur:
                by.setdefault(r["sym"], {})["okx"] = round(cur[0] * 8760 / _interval_h(fr) * 100, 1)
        arb = ox.arbitrage(by, {r["sym"] for r in self.universe})
        spreads = []
        now = time.time()
        bps_by = {}
        for r in self.universe:
            a, b = self.book.get(r["inst"]), self.bg_book.get(r["sym"])
            if a and b and now - a[3] < 20 and now - b[3] < 20 and a[0] > 0 and b[0] > 0:
                bps = round((b[0] / a[0] - 1) * 1e4, 1)
                bps_by[r["sym"]] = bps
                spreads.append({"sym": r["sym"], "okx": a[0], "bitget": b[0], "bps": bps})
        for x in arb:
            # Aynı isimli farklı coin riskine karşı: iki bacağın fiyatı karşılaştırılabiliyorsa doğrula
            legs = {x["long_at"], x["short_at"]}
            if legs <= {"okx", "bitget"} and x["sym"] in bps_by:
                x["check"] = "ok" if abs(bps_by[x["sym"]]) < 200 else "fark"
            elif "bitget" in (x.get("venues") or {}) and x["sym"] in bps_by and abs(bps_by[x["sym"]]) >= 200:
                x["check"] = "fark"
            else:
                x["check"] = "yok"
        arb = [x for x in arb if x["check"] != "fark"]
        spreads.sort(key=lambda x: -abs(x["bps"]))
        return arb, spreads[:15], len(spreads)

    def build_payload(self) -> dict:
        now_ms = int(time.time() * 1000)
        thr, watch = self.cfg["sinyal"]["guclu_esik"], self.cfg["sinyal"]["izle_esik"]
        all_sigs = sorted([s for tf in self.tfs for s in self.signals_by_tf.get(tf, [])],
                          key=lambda s: -s["score"])
        pref = next((tf for tf in ("1H", "4H", "15m") if self.coins_by_tf.get(tf)), None)
        coins = [dict(c) for c in (self.coins_by_tf.get(pref, []) if pref else [])]
        for c in coins:
            c["price"] = self.prices.get(c["inst"], c["price"])
            bg = self.bg_book.get(c["sym"])
            if bg and c["price"]:
                c["bitget_bps"] = round((bg[0] / c["price"] - 1) * 1e4, 1)
        btc = dict(self.btc)
        btc_row = next((r for r in self.universe if r["sym"] == "BTC"), None)
        if btc_row:
            btc.update({"price": self.prices.get(btc_row["inst"], btc_row["price"]), "chg24": btc_row.get("chg24")})
        lg.mark(self.ledger, self.prices, self.cfg)
        lstats = lg.stats(self.ledger, self.cfg, self.tfs, self.state.get("eq_hist"))
        arb, spreads, n_spread = self._arbitrage()
        feeds = {k: f.status() for k, f in self.feeds.items()}
        if self.tg:
            feeds["telegram"] = self.tg.status()
        p = {
            "version": VERSION, "demo": self.demo, "live": True,
            "generated_at": now_ms,
            "generated_local": datetime.fromtimestamp(now_ms / 1000, self.tz).strftime("%d.%m.%Y %H:%M"),
            "exchange": "OKX",
            "timeframe": " · ".join(sg.TF_LABEL.get(tf, tf) for tf in self.tfs),
            "timeframes": self.tfs,
            "scan_times": {tf: datetime.fromtimestamp(ts, self.tz).strftime("%H:%M")
                           for tf, ts in self.scan_at.items()},
            "loop": {"started": int(self.started * 1000), "cycles": self.cycles, **self.stats_counter},
            "feeds": feeds,
            "duration_s": None,
            "api_calls": getattr(self.client, "calls", None),
            "btc": btc,
            "thresholds": {"strong": thr, "watch": watch},
            "counts": {"universe": len(self.universe), "scanned": len(coins), "errors": len(self.errors),
                       "signals": len(all_sigs), "strong": sum(1 for s in all_sigs if s["score"] >= thr)},
            "signals": [s for s in all_sigs if s["raw"] >= 40][:300],
            "coins": coins,
            "harvest": self.harvest,
            "arbitrage": arb,
            "spreads": spreads,
            "spreads_n": n_spread,
            "exchanges_status": self.ex_status,
            "oi_source": self.oi_src,
            "taker_real": sum(1 for c in coins if c.get("taker_src") == "gerçek"),
            "fees": lg.fees_cfg(self.cfg),
            "risk": {**self.state.get("risk", {}), "cfg": self.risk_limits(), "halt": self.halt,
                     "risk_mult": self.risk_mult},
            "eq_hist": self.state.get("eq_hist", [])[-600:],
            "team": self.desk.summary(now_ms) if self.desk else None,
            "team_started": self.state.get("team_started"),
            "orders": [{"oid": o["oid"], "mgr": o["mgr"], "sym": o["idea"]["sym"], "tf": o["idea"]["tf"],
                        "side": o["idea"]["side"], "px": o["px"], "market": self.prices.get(o["idea"]["inst"]),
                        "expire_ts": o["expire_ts"], "conviction": o["idea"]["conviction"], "shadow": o.get("shadow"),
                        "plan": o["idea"].get("plan", "")} for o in self.ledger.get("orders", [])],
            "journal": (self.desk.st["journal"][-80:] if self.desk else []),
            "archive": {"periods": (self.ledger.get("archive") or {}).get("periods", []),
                        "n": len((self.ledger.get("archive") or {}).get("closed", []))},
            "health": self.health,
            "weather": self.weather or None,
            "learning": {"segments": (self.desk.st.get("segments") if self.desk else None) or {},
                         "default_shadow": sorted(learn.DEFAULT_SHADOW),
                         "variants": ((self.cfg.get("ekip") or {}).get("varyant") or {}),
                         "learned": {k: v for k, v in (self.cfg.get("_learned") or {}).items() if k in ("managers", "generated", "split")},
                         "stops_on": bool((self.desk.fc if self.desk else {}).get("durdurma_aktif", True)),
                         "limits_on": bool((self.desk.fc if self.desk else {}).get("sinirlar_aktif", True))},
            "backtest_v8": self._bt8(),
            "director": self._director(now_ms),
            "bas": self._bas_public(),
            "experts": ex.report(self.desk.st) if self.desk else None,
            "hakem": self._hakem_public(),
            "news": self.news.public(),
            "calendar": {"status": self.calendar.status, "upcoming": self.calendar.upcoming(),
                         "block": self.calendar.block()},
            "lease": {"holder": lease.run_id(), "handoff_s": self.state.get("handoff_s"),
                      "handoff_kind": self.state.get("handoff_kind"), "ok": self.lease_ok},
            "karne": self._karne(now_ms),
            "ledger": {
                "stats": lstats,
                "open": sorted(self.ledger["open"], key=lambda t: t["entry_ts"], reverse=True),
                "closed": sorted([t for t in self.ledger["closed"] if not t.get("shadow")],
                                 key=lambda t: t.get("exit_ts") or 0, reverse=True)[:150],
                "shadow_closed": sorted([t for t in self.ledger["closed"] if t.get("shadow")],
                                        key=lambda t: t.get("exit_ts") or 0, reverse=True)[:100],
                "skipped": self.skipped[:10],
            },
            "errors": self.errors,
            "dashboard_url": self.cfg["dashboard"]["url"],
            "repo": os.environ.get("GITHUB_REPOSITORY", ""),
        }
        self.payload = p
        return p

    def save(self):
        from .dashboard import write_site
        p = self.build_payload()
        write_site(self.site, p)
        store.dump(self.site / "ledger.json", self.ledger)
        self.state["last_scan"] = p["generated_at"]
        self.state["last_alive"] = p["generated_at"]
        store.dump(self.site / "state.json", self.state, compact=True)
        (self.site / "ozet.md").write_text(summary_md(p), encoding="utf-8")
        self.dirty = False
        return p

    def publish(self, force: bool = False):
        now = time.time()
        if not force and now - self.t["pub"] < self.live["yayin_min_dakika"] * 60:
            return False
        self.save()
        self.t["pub"] = now
        if self.publish_on:
            r = subprocess.run(["bash", "scripts/site.sh", "push", "canlı"], cwd=ROOT,
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                print(f"[uyarı] yayın başarısız: {r.stderr[-300:]}")
        return True

    # ── bildirim ─────────────────────────────────────────────────────
    def _muted(self) -> bool:
        return (self.state.get("mute_until") or 0) > time.time() * 1000

    def _notify(self, text: str, buttons=None):
        if self.notify_on and not self._muted() and text:
            nt.broadcast(text, self.state, self.cfg, buttons)

    def _reply(self, text: str):
        if self.notify_on:
            nt.tg_send(nt.tg_chat(self.state), text)

    def _dash_button(self):
        url = self.cfg["dashboard"]["url"]
        return [[{"text": "📊 Dashboard'u aç", "url": url}]] if url else None

    def _open_buttons(self, opened):
        rows = []
        btns = [{"text": f"❌ {t['sym']} kapat", "callback_data": f"kapat:{t['id']}"} for t in opened[:8]]
        for i in range(0, len(btns), 2):
            rows.append(btns[i:i + 2])
        dash = self._dash_button()
        return (dash or []) + rows if (dash or rows) else None

    def msg_opened(self, opened: list[dict]) -> str:
        fp = nt.fmt_price
        mx = self.cfg["bildirim"]["max_sinyal_mesaj"]
        L = [f"🚨 <b>Yeni sinyal{'ler' if len(opened) > 1 else ''} · demo işlem açıldı</b>"]
        for s in sorted(opened, key=lambda x: -x["score"])[:mx]:
            side = "LONG" if s["side"] == "long" else "SHORT"
            sl_s, tp_s = ("−", "+") if s["side"] == "long" else ("+", "−")
            L.append("")
            L.append(f"{'🟢' if s['side'] == 'long' else '🔴'} <b>{nt.esc(s['sym'])} {side}</b> · "
                     f"{s.get('tf_label', s.get('tf'))} {s['label']} · <b>{s['score']}</b>/100")
            src = {"kitap": "kitaptan", "derinlik (VWAP)": "derinlik VWAP"}.get(s.get("entry_src"), "son fiyattan")
            ct = f" · {s['contracts']:g} kontrat" if s.get("contracts") else ""
            L.append(f"💵 Pozisyon {_usd(s['notional_usd'], False)} · teminat {_usd(s['margin_usd'], False)} · "
                     f"<b>risk {_usd(s.get('risk_usd', 0), False)} (%{_p(s.get('risk_pct', 0), 2)})</b>")
            L.append(f"Giriş {fp(s['entry'])} ({src}{', kayma ' + format(s['slip_bp'], '.1f') + ' bp' if s.get('slip_bp') else ''}){ct}")
            L.append(f"Stop {fp(s['sl'])} ({sl_s}%{_p(s['sl_pct'])}) · Hedef {fp(s['tp'])} ({tp_s}%{_p(s['tp_pct'])})"
                     f" · en geç {_fmt_dur(s['max_saat'])}")
            if s.get("tp1"):
                L.append(f"Plan: {fp(s['tp1'])}'de (+1R) yarısını kapat → stop başabaşa → kalanı iz süren stop")
            if s.get("liq_first"):
                L.append(f"⚠️ Kaldıraç yüksek: likidasyon {fp(s['liq'])} stop'tan önce geliyor")
            L += [f"<i>{nt.esc(r)}</i>" for r in s.get("reasons", [])[:3]]
        if len(opened) > mx:
            L.append(f"\n…ve {len(opened) - mx} işlem daha (dashboard'da)")
        b = lg.balance(self.ledger, self.cfg)
        heat = sum(lg.open_risk(t) for t in self.ledger["open"])
        L.append(f"\nPortföy riski {_usd(heat, False)} (%{_p(heat / b['equity'] * 100 if b['equity'] else 0)})")
        lat = self.stats_counter.get("signal_latency_s")
        L.append(f"Demo bakiye {_usd(b['equity'], False)} · açık {len(self.ledger['open'])} işlem"
                 + (f" · mum kapanışından {lat:g} sn sonra" if lat is not None else ""))
        L.append("<i>Sanal işlemdir, gerçek emir verilmez. Yatırım tavsiyesi değildir.</i>")
        return "\n".join(L)

    def msg_team_opened(self, opened: list[dict], fill: bool = False) -> str:
        fp = nt.fmt_price
        mx = self.cfg["bildirim"]["max_sinyal_mesaj"]
        L = ["✅ <b>Limit emir doldu</b>" if fill else f"🚨 <b>Yeni işlem{'ler' if len(opened) > 1 else ''}</b>"]
        for t in sorted(opened, key=lambda x: -x.get("conviction", 0))[:mx]:
            m = team.BY_ID.get(t.get("mgr") or "")
            side = "LONG" if t["side"] == "long" else "SHORT"
            sl_s, tp_s = ("−", "+") if t["side"] == "long" else ("+", "−")
            chk = t.get("checks") or []
            passed = sum(1 for c in chk if c[1])
            L.append("")
            L.append(f"{m.emoji if m else ''} <b>{m.name if m else '?'}</b>{' (gölgede, sermayesiz)' if t.get('shadow') else ''} · "
                     f"{'🟢' if t['side'] == 'long' else '🔴'} <b>{nt.esc(t['sym'])} {side}</b> · {sg.TF_LABEL.get(t['tf'], t['tf'])} · "
                     f"güven %{t.get('conviction', 0) * 100:.0f} ({passed}/{len(chk)} doğrulama)")
            src = t.get("entry_src", "")
            if t.get("pyramid"):
                L.append(f"➕ <b>Kazanana ekleme · {t.get('layer', 2)}. kat</b>")
            L.append(f"💵 Pozisyon {_usd(t['notional_usd'], False)} · teminat {_usd(t['margin_usd'], False)} · {t.get('lev', 1):g}x · "
                     f"<b>risk {_usd(t.get('risk_usd', 0), False)}</b> (sermayesinin %{_p(t.get('risk_pct', 0), 2)}'i)")
            stop_s = (f"Kapanış stop'u {fp(t['sl_close'])} ({sl_s}%{_p(t['sl_pct'])}, günlük kapanışla) · felaket stop'u "
                      f"{fp(t['sl'])}" if t.get("sl_close") is not None else f"Stop {fp(t['sl'])} ({sl_s}%{_p(t['sl_pct'])})")
            L.append(f"Giriş {fp(t['entry'])} ({src}) · {stop_s} · Hedef {fp(t['tp'])} ({tp_s}%{_p(t['tp_pct'])})")
            if t.get("plan"):
                L.append(f"<i>{nt.esc(t['plan'])}</i>")
            ok = [c[0] for c in chk if c[1]][:4]
            if ok:
                L.append("✓ " + " · ".join(nt.esc(x) for x in ok))
        if len(opened) > mx:
            L.append(f"\n…ve {len(opened) - mx} işlem daha")
        b = lg.balance(self.ledger, self.cfg)
        heat = sum(lg.open_risk(t) for t in self.ledger["open"] if not t.get("shadow"))
        lat = self.stats_counter.get("signal_latency_s")
        L.append(f"\nFirma bakiyesi {_usd(b['equity'], False)} · açık risk {_usd(heat, False)} "
                 f"(%{_p(heat / b['equity'] * 100 if b['equity'] else 0)})"
                 + (f" · mum kapanışından {lat:g} sn sonra" if lat is not None and not fill else ""))
        L.append("<i>Sanal işlemdir, komisyon ve funding dahil hesaplanır. Yatırım tavsiyesi değildir.</i>")
        return "\n".join(L)

    def msg_orders(self, orders: list[dict]) -> str:
        L = []
        for o in orders:
            m = team.BY_ID.get(o["mgr"])
            i = o["idea"]
            mins = (o["expire_ts"] - o["created"]) / 60000
            L.append(f"📌 {m.emoji} <b>{m.name}</b>{' (gölgede)' if o.get('shadow') else ''} limit emir verdi: "
                     f"<b>{nt.esc(i['sym'])} {'LONG' if i['side'] == 'long' else 'SHORT'}</b> {nt.fmt_price(o['px'])}'den "
                     f"(şu an {nt.fmt_price(i.get('market_px'))}) · {_fmt_dur(mins / 60)} geçerli · güven %{i['conviction'] * 100:.0f}"
                     f"\n<i>{nt.esc(i.get('plan', ''))}</i>")
        return "\n".join(L)

    def msg_closed(self, closed: list[dict]) -> str:
        icon = {"hedef": "✅", "stop": "❌", "süre": "⏱", "manuel": "✋", "likidasyon": "💥", "iz stop": "🔒",
                "devir": "📦", "carry çıkış": "⚖️", "kural çıkışı": "🧭"}
        what = {"hedef": "hedefte", "stop": "stopta", "süre": "süre dolunca", "manuel": "elle",
                "likidasyon": "LİKİDASYONLA", "iz stop": "iz süren stopta", "kural çıkışı": "kural gereği"}
        L = []
        for t in closed:
            extra = ""
            if t.get("real"):
                extra = f" · ücret {_usd(t.get('fee_usd', 0), False)}"
                if t.get("partials"):
                    extra += f" · kısmi kâr dahil ({_usd(t.get('partial_pnl_usd', 0))})"
                if abs(t.get("funding_usd", 0)) >= 0.005:
                    extra += f" · funding {_usd(-t['funding_usd'])}"
            m = team.BY_ID.get(t.get("mgr") or "")
            who = (f"{m.emoji} {m.name}{' (gölgede)' if t.get('shadow') else ''} · ") if m else ""
            setup_lbl = sg.SETUP_LABELS.get(t["setup"], m.title if m else t["setup"])
            L.append(f"{icon.get(t['outcome'], '•')} {who}<b>{nt.esc(t['sym'])}</b> {t['side']} "
                     f"({sg.TF_LABEL.get(t.get('tf'), t.get('tf'))} {setup_lbl}) "
                     f"{what.get(t['outcome'], t['outcome'])} kapandı → <b>{_usd(t['pnl_usd'])}</b> "
                     f"({_p(t['r'], 2, True)}R) · {_fmt_dur(t['hours'])}"
                     f"\n   {nt.fmt_price(t['entry'])} → {nt.fmt_price(t['exit'])}{extra}"
                     + (f"\n   <i>{nt.esc(t['exit_note'])}</i>" if t.get("exit_note") else "")
                     + (f"\n   📘 {nt.esc(t['lesson'])}" if t.get("lesson") else ""))
        b = lg.balance(self.ledger, self.cfg)
        ret = (b["equity"] / b["start"] - 1) * 100 if b["start"] else 0
        L.append(f"Demo bakiye {_usd(b['equity'], False)} (%{_p(ret, 2, True)})")
        return "\n".join(L)

    def msg_partial(self, items) -> str:
        L = []
        for t, part in items:
            m = team.BY_ID.get(t.get("mgr") or "")
            who = f"{m.emoji} {m.name} · " if m else ""
            if part:
                L.append(f"🎯 {who}<b>{nt.esc(t['sym'])}</b> {t['side']} ilk hedefe ulaştı: pozisyonun yarısı {nt.fmt_price(part['px'])}'den "
                         f"kısmen kapandı → <b>{_usd(part['pnl'])}</b>. Stop başabaşa ({nt.fmt_price(t['sl'])}) çekildi"
                         + (", kalan kısım iz süren stop ile takipte." if t.get("trail") else ".") + " Bu işlem artık zararla kapanamaz.")
            else:
                L.append(f"🎯 {who}<b>{nt.esc(t['sym'])}</b> ilk hedef: pozisyon bölünemeyecek kadar küçük; stop başabaşa çekildi.")
        return "\n".join(L)

    def panel_text(self) -> str:
        """Telegram'da sabitlenen canlı panel (sessizce düzenlenir)."""
        b = lg.balance(self.ledger, self.cfg)
        rs = self.state.get("risk", {})
        ret = (b["equity"] / b["start"] - 1) * 100 if b["start"] else 0
        heat = sum(lg.open_risk(t) for t in self.ledger["open"])
        now = datetime.now(self.tz).strftime("%H:%M:%S")
        live = "🟢 canlı" if self.okx_live() else "🟡 REST"
        L = [f"📊 <b>Kripto Radar · canlı panel</b> · {now} · {live}",
             f"Bakiye <b>{_usd(b['equity'], False)}</b> (%{_p(ret, 2, True)}) · bugün {_usd(rs.get('day_pnl', 0))}",
             f"Açık K/Z {_usd(b['unrealized'])} · risk {_usd(heat, False)} (%{_p(heat / b['equity'] * 100 if b['equity'] else 0)})"
             + (f" · ⛔ {self.halt}" if self.halt else "")]
        if self.desk:
            L.append("")
            for m in self.desk.mgrs:
                eq = self.desk.equity(m.id)
                n_open = sum(1 for t in self.ledger["open"] if t.get("mgr") == m.id and not t.get("shadow"))
                n_sh = sum(1 for t in self.ledger["open"] if t.get("mgr") == m.id and t.get("shadow"))
                if self.desk.benched(m.id):
                    L.append(f"{m.emoji} {m.name} · 👤 gölgede" + (f" ({n_sh} gölge işlem)" if n_sh else "")
                             + (f" · {n_open} eski açık" if n_open else ""))
                else:
                    L.append(f"{m.emoji} {m.name} {_usd(eq, False)}" + (f" · {n_open} açık" if n_open else ""))
            w = self.weather or {}
            wt = {"boga": "🟢 boğa", "ayi": "🔴 ayı", "yatay": "🟡 yatay"}.get(w.get("trend"), "?")
            red = sum(1 for f in self.news.flags.values() if f["level"] == "kirmizi" and f["until"] > time.time() * 1000)
            fng = (self.news.fng or {}).get("value")
            blk = self.calendar.block()
            L.append(f"Hava {wt}" + (" · oynak" if w.get("high_vol") else "") + (f" · K/A {fng}" if fng is not None else "")
                     + (f" · 🔴 {red} haber uyarısı" if red else "") + (f" · ⛔ {blk}" if blk else ""))
            L.append("")
        op = sorted([t for t in self.ledger["open"] if not t.get("shadow")], key=lambda t: -abs(t.get("upnl_usd", 0)))
        for t in op[:10]:
            mk = team.BY_ID.get(t.get("mgr") or "")
            mark = (mk.emoji if mk else "") + ("🔒" if t.get("partials") and not t.get("carry") else ("🟢" if t["side"] == "long" else "🔴"))
            L.append(f"{mark} {nt.esc(t['sym'])} {sg.TF_LABEL.get(t.get('tf'), t.get('tf'))} "
                     f"{nt.fmt_price(t.get('last', t['entry']))} · <b>{_usd(t.get('upnl_usd', 0))}</b> ({_p(t.get('r_now', 0), 2, True)}R)")
        if len(op) > 10:
            L.append(f"…ve {len(op) - 10} işlem daha")
        if not op:
            L.append("Açık işlem yok, sinyal bekleniyor.")
        lat = self.stats_counter.get("signal_latency_s")
        L.append(f"<i>Son tarama {', '.join(sg.TF_LABEL.get(k, k) + ' ' + datetime.fromtimestamp(v, self.tz).strftime('%H:%M') for k, v in self.scan_at.items())}"
                 + (f" · gecikme {lat:g} sn" if lat is not None else "") + "</i>")
        return "\n".join(L)

    def maybe_panel(self):
        pc = self.cfg.get("panel") or {}
        if not (self.tg and pc.get("telegram_panel", True)) or self._muted():
            return
        if time.time() - self.t["panel"] < float(pc.get("panel_sn", 15)):
            return
        self.t["panel"] = time.time()
        chat = nt.tg_chat(self.state)
        if chat:
            self.tg.set_panel(chat, self.panel_text())
            if self.tg.panel_id and self.state.get("panel_msg_id") != self.tg.panel_id:
                self.state["panel_msg_id"] = self.tg.panel_id

    def maybe_daily(self):
        local = datetime.now(self.tz)
        today = local.strftime("%Y-%m-%d")
        if local.hour >= self.cfg["bildirim"]["gunluk_ozet_saat"] and self.state.get("last_daily") != today:
            p = self.payload or self.build_payload()
            dm = p["ledger"]["stats"]["demo"]
            txt = msg_daily(p, self.cfg) + (
                f"\n💼 <b>Firma bakiyesi</b>: {_usd(dm['equity'], False)} (%{_p(dm['return_pct'], 2, True)}) · "
                f"açık {len(self.ledger['open'])} işlem")
            if p.get("team"):
                txt += "\n\n" + cm.cmd_ekip(p)
            self._notify(txt, buttons=self._dash_button())
            self.state["last_daily"] = today
            self.dirty = True

    def _karne(self, now_ms: int) -> dict | None:
        """Tek ajan (Baş Trader) karnesi: toplam + strateji/sinyal kaynağı (gerçek/gölge) + filtre karnesi."""
        try:
            from . import karne
            tr = getattr(self, "_unified_trader", None)
            gt = getattr(self, "_golge_trader", None)
            return karne.build(self.ledger["closed"], tr.adapter.closed if tr else [], now_ms,
                               golge_closed=gt.adapter.closed if gt else [])
        except Exception as e:  # noqa: BLE001
            print("[karne] hata:", type(e).__name__, e)
            return None

    def maybe_karne(self):
        if self.demo:
            return
        if time.time() * 1000 - (self.state.get("last_karne") or 0) > 7 * 86400 * 1000 and self.payload:
            from . import karne
            (ROOT / "data").mkdir(exist_ok=True)
            k = self.payload.get("karne")
            md = karne.to_md(k, f"Haftalık karne — {self.payload.get('generated_local')}") if k else karne_md(self.payload)
            (ROOT / "data" / "KARNE.md").write_text(md, encoding="utf-8")
            self.state["last_karne"] = int(time.time() * 1000)

    def load_smart_money(self):
        """Veri toplayıcının son kaydından Hyperliquid kârlı cüzdan yönleri (Balina Gözü). Sadece okuma; yoksa boş."""
        import gzip
        try:
            subprocess.run(["git", "fetch", "-q", "--depth", "1", "origin", "+veri:refs/remotes/origin/veri"], cwd=ROOT,
                           timeout=60, check=True)
            month = datetime.now(timezone.utc).strftime("%Y-%m")
            raw = subprocess.run(["git", "show", f"origin/veri:okx/okx-{month}.jsonl.gz"], cwd=ROOT, timeout=30,
                                 check=True, capture_output=True).stdout
            last = gzip.decompress(raw).decode("utf-8").strip().splitlines()[-1]
            rec = json.loads(last)
            hl = rec.get("hl") or {}
            if hl.get("coins"):
                self.smart = {"ts": rec["ts"], "wallets": hl.get("wallets"), "coins": hl["coins"]}
        except Exception as e:  # noqa: BLE001
            print(f"[akıllı para] okunamadı: {e}")

    def maybe_reload_config(self):
        """Saatte bir GitHub'daki config.yaml'ı çek (telefondan yapılan ayar değişikliği döngüyü beklemesin)."""
        if not os.environ.get("GITHUB_ACTIONS") or time.time() - self.t["cfg"] < 3600:
            return
        self.t["cfg"] = time.time()
        try:
            subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=ROOT, timeout=60, check=True)
            txt = subprocess.run(["git", "show", "origin/main:config.yaml"], cwd=ROOT, timeout=30,
                                 check=True, capture_output=True, text=True).stdout
            for fn in ("learned.json", "hakem.json"):
                lj = subprocess.run(["git", "show", f"origin/main:data/{fn}"], cwd=ROOT, timeout=30,
                                    capture_output=True, text=True).stdout
                if lj.strip():
                    (ROOT / "data").mkdir(exist_ok=True)
                    (ROOT / "data" / fn).write_text(lj, encoding="utf-8")
            self.hakem = hk.load()
            self.load_smart_money()
            if txt and txt != (ROOT / "config.yaml").read_text(encoding="utf-8"):
                (ROOT / "config.yaml").write_text(txt, encoding="utf-8")
                self.cfg = load_config(self.cfg_path)
                self._setup_cfg()
                print("✓ config.yaml güncellendi")
            self.cfg["_learned"] = learn.load_learned()
        except Exception as e:  # noqa: BLE001
            print(f"[uyarı] ayar yenilenemedi: {e}")

    def _on_tg_updates(self, updates):
        def on_scan():
            self.scan_at.clear()
            return "⟳ Tarama başladı; güçlü sinyal çıkarsa demo işlem açılır ve buraya yazılır."
        if cm.handle(updates, self.state, self.payload or self.build_payload(), on_scan, self.on_close_request,
                     on_resume=self.resume):
            self.dirty = True

    def poll_commands(self, first=False):
        """Telegram işçisi yoksa (test/tek tur) eski yöntemle kısa sorgu."""
        self.t["cmd"] = time.time()
        if not nt.tg_token() or self.tg:
            return
        data = self.payload or self.build_payload()

        def on_scan():
            self.scan_at.clear()
            return "⟳ Tarama başlıyor; sonuç bir iki dakikada gelir."

        if cm.poll(self.state, data, on_scan, first=first):
            self.dirty = True

    # ── akışlar ──────────────────────────────────────────────────────
    # ── ekip: kuruluş, fikirler, emirler, carry ───────────────────────
    def apply_policy_if_needed(self):
        """Kanıta dayalı sermaye politikası (config ekip.politika). Sürüm değişince bir kez uygulanır."""
        pol = (self.cfg.get("ekip") or {}).get("politika") or {}
        if not (self.desk and pol.get("aktif")):
            return
        cur = self.desk.st.get("policy") or {}
        if cur.get("surum") == pol.get("surum") and cur.get("active") == list(pol["aktif"]):
            return
        now_ms = int(time.time() * 1000)
        msg = self.desk.apply_policy(list(pol["aktif"]), dict(pol.get("sabit_pay") or {}), now_ms,
                                     pol.get("kaynak", "politika"))
        self.desk.st["policy"]["surum"] = pol.get("surum")
        self.dirty = True
        if pol.get("surum") == 8 and cur.get("surum") != 8:
            self._notify(
                "🧠 <b>Kripto Radar v8: kanıta dayalı ekip göreve başladı</b>\n"
                "• Yöneticilerin yöntemleri araştırmaya göre yeniden yazıldı ve 5 yıllık OKX verisinde sert maliyetle "
                "test edildi (komisyon 2 kat, stop kayması, funding).\n"
                "• Testi geçenler sermaye aldı; geçemeyenler gölgede (sermayesiz) çalışıp kendini kanıtlayınca dönecek.\n"
                "• Murat artık baş analist: her fikre coin karnesi veriyor.\n"
                "• Yeni: Haber Masası (borsa duyuruları, haberler, Korku-Açgözlülük), olay takvimi (Fed, TÜFE), "
                "piyasa havası, yığılma kontrolü, önce limit (maker) giriş.\n\n" + msg)
        else:
            self._notify(msg)

    def apply_budget_if_needed(self):
        """Ajan başı bütçe (config ekip.butce). Sürüm değişince bir kez: kapanmış işlemler arşive, sermaye yeniden kurulur;
        açık pozisyonlar yönetilmeye devam eder."""
        b = (self.cfg.get("ekip") or {}).get("butce") or {}
        if not (self.desk and b.get("surum")) or self.desk.st.get("budget_v") == b["surum"]:
            return
        now_ms = int(time.time() * 1000)
        per = float(b["yonetici_usd"])
        old = self.ledger["closed"]
        real = [t for t in old if not t.get("shadow")]
        arch = self.ledger.setdefault("archive", {"closed": [], "periods": []})
        arch["closed"] = ((arch.get("closed") or []) + real)[-500:]
        arch["periods"].append({"until": now_ms, "n": len(real), "label": b.get("onceki_etiket", "önceki dönem"),
                                "pnl_usd": round(sum(t.get("pnl_usd", 0) for t in real), 2)})
        self.ledger["closed"] = []
        st = self.desk.st
        st.update({"start": per * len(self.desk.mgrs), "alloc": {m.id: per for m in self.desk.mgrs}, "adj": {},
                   "peak": {}, "bench": {}, "budget_v": b["surum"], "started": now_ms})
        st.pop("policy", None)
        self.desk.budget = per
        self.state["risk"], self.state["eq_hist"] = {}, []
        self.state["team_started"] = now_ms
        self.halt = None
        self.dirty = True
        roster = "\n".join(f"{m.emoji} <b>{m.name}</b> · {m.title} · kaldıraç tavanı {(m.extra or {}).get('lev_cap', 10)}x"
                           for m in self.desk.mgrs)
        self._notify(
            f"🏦 <b>Yeni dönem: her yöneticiye {_usd(per, False)} bütçe</b> (toplam {_usd(per * len(self.desk.mgrs), False)})\n"
            f"Önceki dönemin {len(real)} işlemi arşive alındı ({_usd(arch['periods'][-1]['pnl_usd'])}); açık pozisyonlar devam ediyor.\n"
            "• İşlemi durduran limitler gerçek API'ye kadar kapalı; her işlemin stop'u ve riske göre boyutu duruyor.\n"
            "• Her yönetici kaldıracını kendi seçiyor (güven + stop mesafesi + OKX sınırı; likidasyon hep stop'un ötesinde).\n"
            "• 🤝 Ortak Masa: en az 2 yönetici hemfikir olunca kendi bütçesiyle ortak işlem açar.\n"
            "• Öğrenme: her işlemden ders, işlem türlerini canlı kanıta göre açma/kapama, haftalık ayar öğrenmesi.\n\n" + roster)

    def consensus_tick(self):
        """Ortak Masa: hemfikir yöneticileri bulur, ayrı işlem önerir; uzlaşı dağılınca çıkış ister."""
        if not self.desk:
            return
        self.t["cons"] = time.time()
        to_open, to_close = self.desk.consensus_plan(int(time.time() * 1000), self.prices)
        with self.lock:
            for t, why in to_close:
                self.signal_exits.append((t["id"], why))
        now = time.time()
        rows = {r["inst"]: r for r in self.universe}
        ideas = []
        for i in to_open:
            key = ("ortak", i["inst"], i["side"])
            if now - self.live_seen.get(key, 0) < 1800:
                continue
            self.live_seen[key] = now
            r = rows.get(i["inst"]) or {}
            c = {"tf": "1D", "vol24_usd": r.get("vol24_usd"), **self._live_fields(r, i["inst"])} if r else {"tf": "1D"}
            i["card"] = team.analyst_card(c, i["side"], self.news.flag(i["sym"]))
            ideas.append(i)
        if ideas:
            with self.lock:
                self.pending_ideas.extend(ideas)

    def check_speed(self, dt: float):
        """Döngü hızı (v9 hız kuralı): bir tur eşiği aşarsa ya da günlük değerlendirme 45 sn'yi geçerse uyarı (saatte en çok 1)."""
        self.perf.loop_done(dt)
        lim = float((self.desk.fc if self.desk else {}).get("dongu_uyari_sn", 10))
        job = (self.perf.parts.get("işçi:live1d") or [0.0])[0]
        if dt <= lim and job <= 45:
            return
        self.perf.slow += 1
        if time.time() - self.perf.last_alert < 3600:
            return
        self.perf.last_alert = time.time()
        top = ", ".join(f"{n} {v:.1f} sn" for n, v in self.perf.top(3))
        self._notify(f"🐢 <b>Döngü yavaşladı</b>: son tur {dt:.1f} sn (eşik {lim:g} sn), günlük değerlendirme {job:.0f} sn.\n"
                     f"En çok süren: {top}")

    def _hakem_public(self) -> dict | None:
        H = self.hakem or {}
        if not H.get("groups"):
            return None
        return {"generated": H.get("generated"), "n_backtest": H.get("n_backtest"), "n_live": H.get("n_live"),
                "groups": {k: {kk: g.get(kk) for kk in ("n", "val_corr", "val_gain_r", "validated", "top")}
                           for k, g in H["groups"].items()}}

    def _director(self, now_ms: int) -> dict | None:
        if not self.desk:
            return None
        try:
            out = gmd.audit(self.desk, lambda i: self.meta.get(i), now_ms)
        except Exception as e:  # noqa: BLE001
            out = {"error": str(e)[:200]}
        out["perf"] = self.perf.report()
        return out

    def learn_tick(self):
        """İşlem türü öğrenmesi (saatlik)."""
        if not self.desk:
            return
        self.t["learn"] = time.time()
        since = int(self.desk.st.get("started") or 0)
        now_ms = int(time.time() * 1000)
        try:      # 👑 Baş Trader kendi karnesinden öğrenir (100+ kapanmış işlemden sonra)
            old = dict(bas.W_LEARN)
            mult, rep = bas.learn([t for t in self.ledger["closed"] if t.get("mgr") == "bas"])
            bas.W_LEARN.clear()
            bas.W_LEARN.update(mult)
            self.desk.st["bas_learn"] = rep
            if mult and mult != old:
                txt = ", ".join(f"{bas.LABEL[k]} ×{v:g}" for k, v in sorted(mult.items(), key=lambda kv: -kv[1]))
                self._notify(f"👑 <b>Baş Trader öğrendi</b> ({rep['n']} işlem): {txt}")
        except Exception as e:  # noqa: BLE001
            print("[baş trader] öğrenme:", e)
        for msg in learn.segment_tick(self.desk, self.ledger, since, now_ms) + gmd.class_tick(self.desk, self.ledger, since, now_ms):
            self._notify(msg)
            self.desk.journal("-", "-", "-", "-", "öğrenme", msg.replace("<b>", "").replace("</b>", "")[:200])
            self.dirty = True

    def ensure_team(self):
        """İlk kez ekip moduna geçerken eski sistemin işlemlerini arşivler ve ekibi 1.000$ ile başlatır."""
        if not self.team_on or self.state.get("team_started"):
            if self.team_on and self.desk is None:
                self.desk = dk.Desk(self.cfg, self.state, self.ledger)
            self.apply_budget_if_needed()
            self.apply_policy_if_needed()
            return
        now_ms = int(time.time() * 1000)
        closed_now = []
        for t in list(self.ledger["open"]):
            q = self._quote(t["inst"])
            px = (q[0] if t["side"] == "long" else q[1]) if q else self.prices.get(t["inst"], t.get("last", t["entry"]))
            closed_now.append(lg.close_trade(t, px, now_ms, "devir", self.cfg))
        old_closed = self.ledger["closed"] + closed_now
        arch = self.ledger.setdefault("archive", {"closed": [], "periods": []})
        arch["closed"] = (arch.get("closed") or []) + old_closed
        arch["periods"].append({"until": now_ms, "n": len(old_closed),
                                "pnl_usd": round(sum(t.get("pnl_usd", 0) for t in old_closed), 2)})
        self.ledger["open"], self.ledger["closed"], self.ledger["orders"] = [], [], []
        self.state["risk"] = {}
        self.state["eq_hist"] = []
        self.state.pop("desk", None)
        self.state["team_started"] = now_ms
        self.halt = None
        self.desk = dk.Desk(self.cfg, self.state, self.ledger)
        b = (self.cfg.get("ekip") or {}).get("butce") or {}
        if b.get("surum"):
            self.desk.st["budget_v"] = b["surum"]      # sıfırdan kurulan ekip zaten yeni bütçeyle başlar
        self.apply_policy_if_needed()
        self.rebuild_watch()
        self.dirty = True
        roster = "\n".join(f"{m.emoji} <b>{m.name}</b> · {m.title} · {m.style}" for m in self.desk.mgrs)
        pnl_old = arch["periods"][-1]["pnl_usd"]
        self._notify("🏢 <b>Kripto Radar yatırım ekibi göreve başladı</b>\n"
                     f"Başlangıç sermayesi {_usd(self.desk.st['start'], False)}, yöneticilere eşit dağıtıldı "
                     f"({_usd(self.desk.st['start'] / len(team.ROSTER), False)}).\n\n{roster}\n\n"
                     f"Eski sistemin {len(old_closed)} işlemi arşive alındı (toplam {_usd(pnl_old)}).\n"
                     "Normal yöneticiler işlem başı %1,5–2,5, agresif ikili %3–5 risk alır; risk kanıt arttıkça büyür.")

    def process_ideas(self):
        with self.lock:
            ideas, self.pending_ideas = self.pending_ideas, []
        if self._enforce_unified_authority():
            if ideas and self.desk:
                for idea in ideas:
                    self.desk.journal(idea.get("mgr") or "legacy", idea.get("sym") or "-",
                                      idea.get("tf") or "-", idea.get("side") or "-",
                                      "challenger", "Yeni pozisyon yetkisi yalnız Birleşik Baş Trader'da")
            if ideas:
                self.stats_counter["legacy_blocked"] = self.stats_counter.get("legacy_blocked", 0) + len(ideas)
            return
        if not ideas or not self.desk:
            return
        now_ms = int(time.time() * 1000)
        fresh = []
        for i in ideas:
            px = self.prices.get(i["inst"])
            if i["entry_type"] == "piyasa" and px and abs(px / i["entry"] - 1) > 0.01:
                self.desk.journal(i["mgr"], i["sym"], i["tf"], i["side"], "vazgeçti", "fiyat tarama sonrası %1'den fazla kaçtı")
                continue
            fresh.append(i)
        self.risk_tick()
        halted = {"dusus": "zirveden düşüş limiti", "gunluk": "günlük kayıp limiti"}.get(self.halt)
        opened, orders, rejected = self.desk.execute(fresh, now_ms, quote=self._quote,
                                                     meta=lambda x: self.meta.get(x), book=self._book, halted=halted)
        self.stats_counter["rejected"] += len(rejected)
        if rejected:              # elenen fikirler gölgede izleniyor olabilir → fiyat takibine al
            self.dirty = True
            self.rebuild_watch()
            self._feed_symbols()
        if opened or orders:
            self.stats_counter["opened"] += len(opened)
            self.stats_counter["orders"] += len(orders)
            self.dirty = True
            self.rebuild_watch()
            self._feed_symbols()
        if opened:
            ro = [o for o in opened if not o.get("shadow")]
            if ro:
                self._notify(self.msg_team_opened(ro), buttons=self._open_buttons(ro))
        if orders:
            rord = [o for o in orders if not o.get("shadow")]
            if rord:
                self._notify(self.msg_orders(rord))

    def process_orders(self):
        if self._enforce_unified_authority():
            return
        if not self.desk:
            return
        if self.order_hits:
            filled = []
            for oid in list(self.order_hits):
                px, ts = self.order_hits.pop(oid)
                t = self.desk.fill_order(oid, px, int(ts))
                if t:
                    filled.append(t)
            if filled:
                self.stats_counter["fills"] += len(filled)
                self.stats_counter["opened"] += len(filled)
                self.dirty = True
                self.rebuild_watch()
                rf = [t for t in filled if not t.get("shadow")]
                if rf:
                    self._notify(self.msg_team_opened(rf, fill=True), buttons=self._open_buttons(rf))
        now = time.time()
        if now - self.t["orders"] >= 20:
            self.t["orders"] = now
            gone = self.desk.expire_orders(int(now * 1000))
            if gone:
                retry = []
                for o in gone:
                    i = o["idea"]
                    px = self.prices.get(i["inst"])
                    if i.get("fallback_market") and px and abs(px - i["entry"]) <= 0.5 * (i.get("atr") or 0):
                        retry.append({**i, "entry_type": "piyasa", "limit_px": None, "fallback_market": False,
                                      "maker_first": False})
                if retry:
                    with self.lock:
                        self.pending_ideas.extend(retry)
                self.rebuild_watch()
                self.dirty = True

    def plan_carry(self):
        """(işçi thread'i) Elif'in funding pozisyonları için plan; ana döngü uygular."""
        if not self.desk or not self.harvest:
            return
        plan = self.desk.carry_plan(self.harvest, dict(self.prices), int(time.time() * 1000))
        if plan[0] or plan[1]:
            with self.lock:
                self.pending_carry.append(plan)

    def process_carry(self):
        unified_only = self._enforce_unified_authority()
        with self.lock:
            plans, self.pending_carry = self.pending_carry, []
        if not plans:
            return
        now_ms = int(time.time() * 1000)
        msgs = []
        for to_open, to_close in plans:
            for t, why in to_close:
                if t not in self.ledger["open"]:
                    continue
                qp, qs = self._quote(t["inst"]), self._quote(t["spot_inst"])
                perp = qp[1] if qp else self.prices.get(t["inst"], t["entry"])          # short kapatma: ask'tan alınır
                spot = qs[0] if qs else self.prices.get(t["spot_inst"], t["spot_last"])  # spot satış: bid'e
                t["spot_last"] = spot
                self.ledger["open"].remove(t)
                c = lg.close_carry(t, perp, spot, now_ms, "carry çıkış", self.cfg)
                self.ledger["closed"].append(c)
                self.stats_counter["closed"] += 1
                msgs.append(f"⚖️ <b>Elif</b> {nt.esc(t['sym'])} funding pozisyonunu kapattı ({why}) → <b>{_usd(c['pnl_usd'])}</b> "
                            f"· {_fmt_dur(c['hours'])} · toplanan funding {_usd(-c.get('funding_usd', 0))}")
                self.desk.journal("elif", t["sym"], "8H", "nötr", "kapattı", why)
            for h, spot, perp, qty, shadow in to_open:
                if unified_only:  # carry eski ajan stratejisidir; unified carry stratejisi gelene kadar yeni giriş yok
                    continue
                if any(t.get("carry") and t["inst"] == h["inst"] and t.get("mgr") == "elif" for t in self.ledger["open"]):
                    continue
                qp, qs = self._quote(h["inst"]), self._quote(f"{h['sym']}-USDT")
                perp_px = qp[0] if qp else perp       # vadeli short: bid'e satılır
                spot_px = qs[1] if qs else spot       # spot alış: ask'tan
                t = lg.build_carry("elif", h["inst"], h["sym"], f"{h['sym']}-USDT", perp_px, spot_px, qty,
                                   self.cfg, now_ms, h, shadow=shadow)
                self.ledger["open"].append(t)
                self.stats_counter["carry"] += 1
                msgs.append(f"⚖️ <b>Elif</b> {'(gölgede) ' if shadow else ''}{nt.esc(h['sym'])} için spot alıp vadelide short açtı: "
                            f"{_usd(t['notional_usd'], False)} · 30g funding %{h['ann_30d']:.0f}/yıl · istikrar {h['stability']} · "
                            f"başabaş {h.get('breakeven_days') or '?'} gün")
                self.desk.journal("elif", h["sym"], "8H", "nötr", "açtı", f"30g funding %{h['ann_30d']:.0f}")
        if msgs:
            self.dirty = True
            self._feed_symbols()
            self._notify("\n".join(msgs))

    def manager_tick(self):
        if not self.desk:
            return
        now_ms = int(time.time() * 1000)
        for m in self.desk.manager_tick(now_ms):
            self._notify(m)
            self.dirty = True
        if time.time() - self.t["rebal"] >= 3600:
            self.t["rebal"] = time.time()
            txt = self.desk.maybe_rebalance(now_ms)
            if txt:
                self._notify(txt)
                self.dirty = True

    # ── sağlık denetçisi ─────────────────────────────────────────────
    def health_tick(self):
        """Her 30 sn: akışlar, thread'ler, tarama tazeliği, muhasebe tutarlılığı. Bozulanı onarır, kalıcı sorunu bildirir."""
        self.t["health"] = time.time()
        now = time.time()
        issues = []
        for name, f in list(self.feeds.items()):
            if not f.is_alive():
                from . import ws
                cls = ws.OKXFeed if name == "okx" else ws.BitgetFeed
                nf = cls(self._on_tick, self._on_funding)
                nf.set_symbols(f.symbols)
                nf.start()
                self.feeds[name] = nf
                issues.append(("warn", f"{name}-akis", f"{name.upper()} akış thread'i durmuştu, yeniden başlatıldı"))
            elif f.connected and f.last_msg and now - f.last_msg > 45 and f.conn:
                f.conn.close()                    # donmuş bağlantı → zorla yeniden bağlan
                issues.append(("warn", f"{name}-donma", f"{name.upper()} akışı 45 sn sessizdi, yeniden bağlanıldı"))
            elif not f.healthy() and now - self.started > 120:
                issues.append(("warn", f"{name}-kopuk", f"{name.upper()} akışı kopuk (REST yedeği devrede)"))
        if self.worker and not self.worker.is_alive():
            self.worker = threading.Thread(target=self._worker_loop, args=(self.jobs,), daemon=True, name="isci")
            self.worker.start()
            issues.append(("warn", "isci", "arka plan işçisi durmuştu, yeniden başlatıldı"))
        if self.worker_slow and not self.worker_slow.is_alive():
            self.worker_slow = threading.Thread(target=self._worker_loop, args=(self.jobs_slow,), daemon=True,
                                                name="isci-agir")
            self.worker_slow.start()
            issues.append(("warn", "isci-agir", "ağır işçi durmuştu, yeniden başlatıldı"))
        if self.tg and not (self.tg.t_poll.is_alive() and self.tg.t_send.is_alive()):
            pid = self.tg.panel_id
            self.tg.stop(flush_s=0)
            self.tg = nt.TelegramWorker(self._on_tg_updates).start()
            self.tg.panel_id = pid
            issues.append(("warn", "telegram", "Telegram bağlantısı yeniden kuruldu"))
        for tf in self.tfs:
            last = self.scan_at.get(tf, 0)
            if last and now - last > TF_SEC[tf] + 900:
                issues.append(("crit", f"tarama-{tf}", f"{sg.TF_LABEL.get(tf, tf)} taraması {int((now - last) / 60)} dk gecikti"))
        if self.desk:
            diff = abs(self.desk.firm_equity() - lg.balance(self.ledger, self.cfg)["equity"])
            if diff > 0.05:
                issues.append(("crit", "muhasebe", f"yönetici bakiyeleri ile firma bakiyesi arasında {diff:.2f}$ fark"))
        bad_open = [t["sym"] for t in self.ledger["open"] if not t.get("carry") and t.get("real")
                    and (t.get("qty_open", 0) <= 0 or t.get("sl") is None)]
        if bad_open:
            issues.append(("crit", "defter", f"geçersiz açık pozisyon: {', '.join(bad_open[:5])}"))
        if len(self.suspect) > max(5, len(self.universe) // 4):
            issues.append(("warn", "veri", f"{len(self.suspect)} coinde şüpheli veri (bu coinlerde işlem açılmıyor)"))
        if self.lease_ok is False:
            issues.append(("warn", "nobet", "nöbet nabzı yazılamadı (devir yedeği eski olabilir)"))
        status = "kritik" if any(k == "crit" for k, *_ in issues) else ("uyarı" if issues else "sağlıklı")
        self.health = {"status": status, "issues": [m for _, _, m in issues], "checked": int(now * 1000),
                       "suspect": list(self.suspect.values())[:15]}
        for kind, key, msg in issues:
            if kind == "crit" and now - self.alerted.get(key, 0) > 3600:
                self.alerted[key] = now
                self._notify(f"🩺 <b>Sistem uyarısı</b>: {msg}")

    # ── nöbet (7/24 devir) ───────────────────────────────────────────
    def _snapshot(self) -> dict:
        return {"ledger.json": json.dumps(store.clean(self.ledger), ensure_ascii=False, separators=(",", ":")),
                "state.json": json.dumps(store.clean(self.state), ensure_ascii=False, separators=(",", ":"))}

    def acquire_lease(self) -> bool:
        me = lease.run_id()
        devir = os.environ.get("DEVIR") == "1"
        t0 = time.time()
        waited = False
        prev_hb = None
        while True:
            L, files = lease.read()
            if not lease.is_live(L, me):
                if L and L.get("holder") != me and L.get("hb"):
                    prev_hb = L["hb"] / 1000     # önceki döngünün son nabzı / bırakış anı
                break
            if not devir:
                print(f"[nöbet] {L.get('holder')} nöbette ve sağlıklı; bu çalışma kapanıyor.")
                return False
            if not waited:
                print(f"[nöbet] devir bekleniyor ({L.get('holder')} nöbette)…")
                waited = True
            if time.time() - t0 > 900:
                print("[nöbet] 15 dk beklendi, nöbet bırakılmadı; çıkılıyor.")
                return False
            time.sleep(4)
        if files.get("state.json") and files.get("ledger.json"):
            try:
                st = json.loads(files["state.json"])
                if (st.get("last_alive") or 0) >= (self.state.get("last_alive") or 0):
                    self.state = st
                    self.ledger = lg.migrate(json.loads(files["ledger.json"]), self.cfg)
                    self.state.setdefault("risk", {})
                    self.state.setdefault("eq_hist", [])
                    self.halt = self.state["risk"].get("halt")
                    if self.team_on and self.state.get("team_started"):
                        self.desk = dk.Desk(self.cfg, self.state, self.ledger)
                    print(f"[nöbet] durum devralındı (son kayıt {(time.time() * 1000 - (st.get('last_alive') or 0)) / 1000:.0f} sn önce)")
            except Exception as e:  # noqa: BLE001
                print(f"[nöbet] devir yedeği okunamadı: {e}")
        now_ms = int(time.time() * 1000)
        self.my_hb = now_ms
        lease.write({"holder": me, "hb": now_ms, "released": False, "started": now_ms}, self._snapshot())
        time.sleep(3)
        L2, _ = lease.read()
        if L2 and L2.get("holder") != me:
            print(f"[nöbet] nöbeti {L2.get('holder')} aldı; çekiliyorum.")
            return False
        # kesinti süresi: önceki döngünün son nabzından (planlı devirde bırakış anı) bu döngünün devralmasına kadar
        if prev_hb:
            self.state["handoff_s"] = round(max(0.0, time.time() - prev_hb), 1)
            self.state["handoff_kind"] = "devir" if (L or {}).get("released") else "kurtarma"
        self.state["handoff_wait_s"] = round(time.time() - t0, 1) if waited else 0
        self.state["consecutive_failures"] = 0
        self.state.pop("last_error", None)     # sağlıklı başlangıç: eski hata kaydı kafa karıştırmasın
        return True

    def lease_heartbeat(self, force: bool = False, released: bool = False):
        if not self.publish_on:
            return
        now = time.time()
        if not force and (now - self.t["lease"] < 60 or (self.lease_thread and self.lease_thread.is_alive())):
            return
        self.t["lease"] = now
        self.state["last_alive"] = int(now * 1000)
        snap = self._snapshot()
        me = lease.run_id()
        info = {"holder": me, "hb": int(now * 1000), "released": released}

        def push():
            if not released:
                L, _ = lease.read()
                if L and L.get("holder") != me and not L.get("released") and (L.get("hb") or 0) > self.my_hb:
                    self.lost_lease = True          # başka bir döngü nöbeti aldı → çekil, hiçbir şey yazma
                    print(f"[nöbet] nöbet {L.get('holder')} tarafından alındı; bu döngü çekiliyor.")
                    return
            self.my_hb = info["hb"]
            self.lease_ok = lease.write(info, snap)

        if force:
            push()
        else:
            self.lease_thread = threading.Thread(target=push, daemon=True, name="nabiz")
            self.lease_thread.start()

    # ── akışlar ──────────────────────────────────────────────────────
    def full_refresh(self):
        self.refresh_universe()
        if not self.universe:
            raise RuntimeError("Taranacak coin bulunamadı (borsa verisi boş).")
        self.refresh_funding()
        if not self.demo:
            try:
                self.refresh_coin_ages(per_call=150)      # ilk açılışta hepsi (sonra durumda saklı)
            except Exception as e:  # noqa: BLE001
                print(f"[coin yaşı] {e}")
            if os.environ.get("GITHUB_ACTIONS"):
                self.load_smart_money()
        self.refresh_hourly()

    def run_once(self) -> dict:
        """Tek tur: her şeyi tara, pozisyonları güncelle, dosyaları yaz."""
        t0 = time.time()
        self.full_refresh()
        self.refresh_prices()
        self.ensure_team()
        self.rebuild_watch()
        self.reconcile(catchup=True)
        strong = []
        for tf in self.tfs:
            strong += self.scan_tf(tf)
        if self.team_on and self.desk:
            self.process_ideas()
            self.plan_carry()
            self.process_carry()
        else:
            self.open_positions(strong)
        self.maybe_daily()
        p = self.save()
        p["duration_s"] = round(time.time() - t0, 1)
        print(f"✓ Tarama bitti: {p['counts']}, açık demo {len(self.ledger['open'])}, "
              f"{p['duration_s']} sn, {p['api_calls']} istek")
        return p

    def _due_tfs(self, now: float) -> tuple[list[str], float | None]:
        due, bnd = [], None
        for tf in self.tfs:
            sec = TF_SEC[tf]
            boundary = (now - SCAN_DELAY) // sec * sec
            if self.scan_at.get(tf, 0) < boundary + SCAN_DELAY <= now:
                due.append(tf)
                bnd = max(bnd or 0, boundary)
        return due, bnd

    def start_feeds(self):
        from . import ws
        if self.live.get("websocket", True) and not self.demo:
            self.feeds["okx"] = ws.OKXFeed(self._on_tick, self._on_funding)
            if self.live.get("bitget_ws", True):
                self.feeds["bitget"] = ws.BitgetFeed(self._on_tick, self._on_funding)
            self._feed_symbols()
            for f in self.feeds.values():
                f.start()

    def start_live(self):
        """WebSocket akışları (başlamadıysa), Telegram işçisi ve arka plan işçisi."""
        if not self.feeds:
            self.start_feeds()
        if nt.tg_token() and self.notify_on and not self.tg:
            self.tg = nt.TelegramWorker(self._on_tg_updates).start()
            self.tg.panel_id = self.state.get("panel_msg_id")
        if not self.worker:
            self._start_workers()

    def stop_live(self):
        for f in self.feeds.values():
            f.stop()
        self.jobs.put("__stop__")
        self.jobs_slow.put("__stop__")
        if self.tg:
            self.tg.stop()

    # ── kendi kendini onarma ─────────────────────────────────────────
    # GitHub'ın zamanlanmış işleri (bekçi) "en iyi çaba"dır, saatlerce gecikebilir. Zincir ona bağlı kalmasın:
    #   - planlı halef çağrısı başarısızsa 30 sn arayla yeniden denenir,
    #   - döngü beklenmedik hatayla erken biterse halefini kendisi çağırır,
    #   - ana döngü takılırsa iç bekçi bunu görür ve yedek döngüyü çağırır (nabız durduğu için yedek 3 dk içinde devralır).
    HANG_S = 300
    WATCH_EVERY = 15
    MIN_RUN_FOR_RESTART_S = 600          # açılışta çöken döngü kendini sonsuz yeniden başlatmasın

    def call_successor(self, why: str) -> bool:
        if not self.publish_on or self.successor_sent or self.lost_lease:
            return False
        ok = lease.dispatch_successor()
        if ok:
            self.successor_sent = True
            try:
                # site/ değil depo kökü: site/ gh-pages'e yayınlanır ve sonraki döngüye geri gelirdi
                (Path(ROOT) / ".halef_gonderildi").write_text(why, encoding="utf-8")
            except OSError:
                pass
        print(f"[nöbet] halef çağrısı ({why}): {'gönderildi' if ok else 'BAŞARISIZ'}")
        return ok

    def _inner_watchdog(self):
        while not self.loop_done:
            time.sleep(self.WATCH_EVERY)
            if self.loop_done or self.lost_lease or self.successor_sent:
                continue
            stuck = time.time() - self.loop_beat
            if stuck > self.HANG_S and self.call_successor(f"ana döngü {stuck:.0f} sn ilerlemedi"):
                self._notify(f"🩺 <b>Sistem</b>: ana döngü {stuck / 60:.0f} dk takıldı; yedek döngü çağrıldı, "
                             f"en geç birkaç dakika içinde devralacak.")

    def run_loop(self, minutes: float | None = None):
        minutes = minutes or self.live["dongu_dakika"]
        end = self.started + minutes * 60
        print(f"▶ Canlı döngü başladı: {minutes:.0f} dk, periyotlar {self.tfs}, yayın={self.publish_on}")
        # Isınma: veri ve canlı akış nöbet beklenirken hazırlanır (devir saniyeler sürer)
        self.full_refresh()
        self.t["hourly"] = self.t["funding"] = time.time()
        self.start_feeds()
        if self.publish_on and not self.acquire_lease():
            self.stop_live()
            (Path(self.site) / ".nobet_yok").write_text("1", encoding="utf-8")
            return
        gap_min = (time.time() * 1000 - (self.state.get("last_alive") or 0)) / 60000 if self.state.get("last_alive") else 0
        self.start_live()
        self.refresh_prices()
        self.ensure_team()
        self.rebuild_watch()
        t_ws = time.time()
        while time.time() - t_ws < 8 and not self.okx_live() and self.feeds:
            time.sleep(0.25)
        print(f"  canlı akış: {'bağlandı' if self.okx_live() else 'yok, REST yedeği kullanılıyor'}")
        self.reconcile(catchup=True)
        if gap_min > 15:
            self._notify(f"⚠️ <b>Kripto Radar</b> {_fmt_dur(gap_min / 60)} kapalı kaldı, tekrar çalışıyor. "
                         f"Bu sürede stop/hedefe değen işlemler dakikalık mumlarla kontrol edildi.")
        strong = []
        for tf in self.tfs:
            strong += self.scan_tf(tf)
        if self.team_on and self.desk:
            self.process_ideas()
            self.plan_carry()
            self.process_carry()
        else:
            self.open_positions(strong)
        self.publish(force=True)
        self.lease_heartbeat(force=True)
        self.loop_beat = time.time()
        if self.publish_on:
            threading.Thread(target=self._inner_watchdog, daemon=True, name="ic-bekci").start()
        crashed = False
        try:
            while time.time() < end and not self.lost_lease:
                now = time.time()
                self.loop_beat = now
                pm = self.perf.timed
                try:
                    pm("stop/hedef", self.process_hits)
                    pm("emirler", self.process_orders)
                    pm("iz stop", self.process_trailing)
                    self.process_manual()
                    pm("kural çıkışı", self.process_rule_exits)
                    self.process_time_exits()
                    pm("fonlama", self.process_funding)
                    if now - self.t["risk"] >= 1:
                        pm("risk", self.risk_tick)
                    if now - self.t["mgr"] >= 10:
                        self.t["mgr"] = now
                        pm("yönetici", self.manager_tick)
                    pm("panel", self.maybe_panel)
                    if self.team_on and self.desk:
                        self.desk_env()
                        pm("fikir/karar", self.process_ideas)
                        pm("Elif", self.process_carry)
                    else:
                        with self.lock:
                            pend, self.pending_open = self.pending_open, []
                        if pend:
                            for s in pend:
                                px = self.prices.get(s["inst"])
                                if px and abs(px / s["entry"] - 1) < 0.01:
                                    shift = px - s["entry"]
                                    s["entry"], s["sl"], s["tp"] = px, s["sl"] + shift, s["tp"] + shift
                            self.open_positions(pend)
                    due, bnd = self._due_tfs(now)
                    if due:
                        for tf in due:
                            self.scan_at[tf] = now
                        self.enqueue(f"scan:{','.join(due)}:{bnd or ''}")
                    if self.team_on and self.desk and now - self.t["cons"] >= 60:
                        pm("Ortak Masa", self.consensus_tick)
                    if self.team_on and self.desk and now - self.t["learn"] >= 3600:
                        self.learn_tick()
                    if self.team_on and self.desk and now - self.t["live1d"] >= float(self.live.get("gunici_sn", 60)):
                        self.t["live1d"] = now
                        self.enqueue("live1d")
                    if self.team_on and self.desk:
                        pm("sabah özeti", self.daily_tick)
                    if now - self.t["news"] >= 600:
                        self.t["news"] = now
                        self.enqueue("news")
                    if now - self.t["cal"] >= 6 * 3600:
                        self.t["cal"] = now
                        self.enqueue("cal")
                    if now - self.t["hourly"] >= 3600:
                        self.t["hourly"] = now
                        self.enqueue("hourly")
                    elif now - self.t["funding"] >= 900:
                        self.t["funding"] = now
                        self.enqueue("funding")
                    live = self.okx_live()
                    if now - self.t["prices"] >= (60 if live else 5):
                        self.refresh_prices()
                    if now - self.t["reconcile"] >= self.live["fiyat_kontrol_sn"]:
                        self.reconcile()
                    if now - self.t["health"] >= 30:
                        self.health_tick()
                    if not self.tg and now - self.t["cmd"] >= self.live["komut_kontrol_sn"]:
                        self.poll_commands()
                    self.maybe_daily()
                    self.maybe_karne()
                    if self.dirty:
                        pm("yayın", self.publish)
                    elif now - self.t["pub"] >= 15 * 60:
                        pm("yayın", self.publish)
                    self.lease_heartbeat()
                    if (self.publish_on and not self.successor_sent and end - now <= 360
                            and now - self.t.get("succ_try", 0) >= 30):
                        self.t["succ_try"] = now      # başarısızsa 30 sn sonra yeniden dener
                        self.call_successor("planlı devir")
                    self.cycles += 1
                    self.fail_streak = 0
                    self.check_speed(time.time() - now)
                    if self.state.get("loop_alarm"):
                        self.state["loop_alarm"] = False
                except Exception:  # noqa: BLE001
                    self.fail_streak += 1
                    err = traceback.format_exc()
                    print(err)
                    if self.fail_streak == 5 and not self.state.get("loop_alarm"):
                        self.state["loop_alarm"] = True
                        self._notify("⚠️ <b>Kripto Radar</b>: canlı döngüde art arda hata var, deniyorum.\n<code>"
                                     + nt.esc(err.strip().splitlines()[-1][:300]) + "</code>")
                    if self.fail_streak >= 30:
                        raise
                    time.sleep(min(30, 2 * self.fail_streak))
                    continue
                time.sleep(0.25)
        except Exception:
            crashed = True                   # (iptal/KeyboardInterrupt sayılmaz: elle durdurma kendini yeniden başlatmaz)
            raise
        finally:
            self.loop_done = True
            if self.lost_lease:
                (Path(self.site) / ".nobet_yok").write_text("1", encoding="utf-8")
            else:
                self.publish(force=True)
                self.lease_heartbeat(force=True, released=True)
                ran = time.time() - self.started
                if crashed and ran >= self.MIN_RUN_FOR_RESTART_S:
                    self.call_successor("döngü hatayla bitti")
                elif not crashed and time.time() >= end - 360 and not self.successor_sent:
                    self.call_successor("planlı devir (son deneme)")
            self.stop_live()
        o = self.stats_counter
        dm = (self.payload or {}).get("ledger", {}).get("stats", {}).get("demo", {})
        fs = {k: f.status() for k, f in self.feeds.items()}
        print(f"::notice title=Canlı döngü bitti::{(time.time() - self.started) / 60:.0f} dk, "
              f"{o['scans']} tarama, {o.get('ideas', 0)} fikir, {o['opened']} açıldı, {o['closed']} kapandı "
              f"(akıştan {o.get('ws_closes', 0)}, mumdan {o.get('rest_closes', 0)}), "
              f"funding {o['funding_events']}, bakiye {dm.get('equity')}, "
              f"akış: " + ", ".join(f"{k} {v['ticks']} fiyat/{v['reconnects']} kopma" for k, v in fs.items()))
