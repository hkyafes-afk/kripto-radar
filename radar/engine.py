"""Canlı motor: sürekli açık kalan, gecikmesiz döngü.

  WebSocket (OKX)   → her fiyat değişiminde (~100 ms) stop/hedef kontrolü, gerçek bid/ask, canlı funding
  WebSocket (Bitget)→ ikinci borsa fiyatı ve funding (borsalar arası fark)
  Telegram          → long polling ile komutlara anında cevap, gönderim kuyruğu + yeniden deneme
  Ana döngü (0,5 sn)→ tetiklenen stop/hedefleri kapat, süre dolanları kapat, funding kesimlerini uygula,
                      elle kapatma isteklerini işle, gerekirse yayınla
  Arka plan işçisi  → 15dk/1S/4S mum kapanır kapanmaz tarama (+2 sn, mum kesinleşene kadar tekrar),
                      15 dk'da bir funding, saatte bir coin listesi/BTC rejimi/OI/harvest/diğer borsalar
  Yedek             → WebSocket koparsa REST ile 5 sn'de bir fiyat; her dakika 1 dk'lık mumlarla kontrol

Aynı sınıf, test ve elle çalıştırma için tek seferlik taramayı da yapar (run_once).
"""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import commands as cm
from . import harvest as hv
from . import ledger as lg
from . import notify as nt
from . import other_exchanges as ox
from . import signals as sg
from . import store
from .config import ROOT, load_config
from .scan import (VERSION, _f, _interval_h, btc_info, build_universe, fetch_funding, karne_md,
                   msg_daily, oi_changes, summary_md)

TF_SEC = {"15m": 900, "30m": 1800, "1H": 3600, "2H": 7200, "4H": 14400}
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
        self.bg_book: dict[str, tuple] = {}     # baz coin → Bitget (last, bid, ask, an)
        self.fund_cur: dict[str, tuple] = {}    # inst → (oran, kesim_ms, kaynak)
        self.fund_events: list[tuple] = []      # (inst, oran, kesim_ms) geçmiş kesimler
        self.bg_fund: dict[str, float] = {}
        self.watch: dict[str, tuple] = {}       # inst → ((id, side, sl, tp), ...)
        self.hits: dict[str, tuple] = {}        # id → (sonuç, dolum, ms, maker)
        self.manual: list[str] = []
        self.feeds: dict = {}
        self.tg: nt.TelegramWorker | None = None
        self.lock = threading.RLock()
        self.jobs: queue.Queue = queue.Queue()
        self.job_names: set[str] = set()
        self.pending_open: list[dict] = []
        self.worker: threading.Thread | None = None
        self.t = {"universe": 0.0, "funding": 0.0, "hourly": 0.0, "prices": 0.0, "reconcile": 0.0,
                  "cmd": 0.0, "pub": 0.0, "cfg": time.time(), "mark": 0.0}
        self.dirty = True
        self.payload: dict | None = None
        self.started = time.time()
        self.cycles = 0
        self.fail_streak = 0
        self.stats_counter = {"opened": 0, "closed": 0, "scans": 0, "ws_closes": 0, "rest_closes": 0,
                              "funding_events": 0, "signal_latency_s": None}

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
        if venue == "bitget":
            self.bg_book[key] = (last, bid, ask, now)
            return
        if last <= 0:
            return
        self.book[key] = (last, bid, ask, now)
        self.prices[key] = last
        self._check_watch(key, last, bid, ask, ts)

    def _check_watch(self, inst, last, bid, ask, ts):
        for tid, side, sl, tp, liq_first in self.watch.get(inst, ()):
            if tid in self.hits:
                continue
            if side == "long":
                if last <= sl:
                    fill = min(bid, last) if bid > 0 else last          # stop-market: alıcıya (bid) satılır
                    self.hits[tid] = ("likidasyon" if liq_first else "stop", fill, ts, False)
                elif last > tp:                                          # limit emir: fiyat hedefin içinden geçmeli
                    self.hits[tid] = ("hedef", tp, ts, True)
            else:
                if last >= sl:
                    fill = max(ask, last) if ask > 0 else last
                    self.hits[tid] = ("likidasyon" if liq_first else "stop", fill, ts, False)
                elif last < tp:
                    self.hits[tid] = ("hedef", tp, ts, True)

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
        if not prev or ftime >= prev[1]:
            self.fund_cur[inst] = (rate, ftime, src)

    def rebuild_watch(self):
        w: dict[str, list] = {}
        for t in self.ledger["open"]:
            w.setdefault(t["inst"], []).append((t["id"], t["side"], t["sl"], t["tp"], bool(t.get("liq_first"))))
        self.watch = {k: tuple(v) for k, v in w.items()}

    def _quote(self, inst):
        b = self.book.get(inst)
        if b and time.time() - b[3] < 15 and b[1] > 0 and b[2] > 0:
            return b[1], b[2], b[0]
        return None

    def _feed_symbols(self):
        insts = {r["inst"] for r in self.universe} | {t["inst"] for t in self.ledger["open"]}
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
                    self.meta[r["inst"]] = {"ct_val": r["ct_val"], "lot_sz": r["lot_sz"], "min_sz": r["min_sz"]}
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
        for r in self.universe:
            x = by.get(r["inst"])
            if x:
                last, o24 = self.prices.get(r["inst"]) or _f(x.get("last")), _f(x.get("open24h"))
                r["price"] = last
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

    def refresh_hourly(self):
        now_ms = int(time.time() * 1000)
        self.btc = btc_info(self.client, self.cfg)
        self.oi_chg, self.oi_src = oi_changes(self.client, self.universe, self.state, now_ms)
        if not self.demo and self.cfg["borsa"].get("funding_karsilastir"):
            self.other, self.ex_status = ox.fetch_all(self.cfg["borsa"]["funding_karsilastir"])
        self._refresh_harvest()
        self.d1_cache.clear()
        self.taker_cache.clear()
        self.t["hourly"] = time.time()

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
        f = sg.frame_features(df, self.cfg, bh).iloc[-1]
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
                      "regime": regime, "vol24_usd": row["vol24_usd"], "bar_ms": bar_ms})
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
        return {"coin": coin, "signals": sigs}

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
        print(f"[{tf}] {len(results)}/{len(uni)} coin, {len(sigs)} sinyal, {len(strong)} güçlü, "
              f"{time.time() - t0:.1f} sn" + (f" (mum kapanışından {time.time() - boundary:.1f} sn sonra)" if boundary else ""))
        return strong

    # ── arka plan işçisi ─────────────────────────────────────────────
    def enqueue(self, name: str):
        with self.lock:
            if name in self.job_names:
                return
            self.job_names.add(name)
        self.jobs.put(name)

    def _worker_loop(self):
        while True:
            name = self.jobs.get()
            if name == "__stop__":
                return
            try:
                if name.startswith("scan:"):
                    _, tfs, b = name.split(":")
                    strong = []
                    for tf in tfs.split(","):
                        strong += self.scan_tf(tf, float(b) if b else None)
                    with self.lock:
                        self.pending_open.extend(strong)
                elif name == "funding":
                    self.refresh_funding()
                elif name == "hourly":
                    self.refresh_universe()
                    self.refresh_funding()
                    self.refresh_hourly()
                    self.maybe_reload_config()
            except Exception as e:  # noqa: BLE001
                print(f"[işçi] {name} hata: {e}")
                traceback.print_exc()
                self.errors = ([f"{name}: {e}"] + self.errors)[:20]
            finally:
                with self.lock:
                    self.job_names.discard(name)

    # ── pozisyonlar ──────────────────────────────────────────────────
    def _finish_close(self, closed: list[dict], via: str):
        if not closed:
            return
        self.stats_counter["closed"] += len(closed)
        self.stats_counter[f"{via}_closes"] = self.stats_counter.get(f"{via}_closes", 0) + len(closed)
        self.dirty = True
        self.rebuild_watch()
        self._notify(self.msg_closed(closed))

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
        items = []
        for tid in list(self.hits):
            kind, px, ts, maker = self.hits.pop(tid)
            items.append((tid, px, ts, kind, maker))
        self._close_ids(items, "ws")

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

    def open_positions(self, strong: list[dict]) -> list[dict]:
        if not strong:
            return []
        skipped: list[dict] = []
        opened = lg.open_new(self.ledger, strong, self.cfg, int(time.time() * 1000),
                             quote=self._quote, meta=lambda i: self.meta.get(i), skipped=skipped)
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
        for r in self.universe:
            a, b = self.book.get(r["inst"]), self.bg_book.get(r["sym"])
            if a and b and now - a[3] < 20 and now - b[3] < 20 and a[0] > 0 and b[0] > 0:
                spreads.append({"sym": r["sym"], "okx": a[0], "bitget": b[0], "bps": round((b[0] / a[0] - 1) * 1e4, 1)})
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
        lg.mark(self.ledger, self.prices)
        lstats = lg.stats(self.ledger, self.cfg, self.tfs)
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
            "ledger": {
                "stats": lstats,
                "open": sorted(self.ledger["open"], key=lambda t: t["entry_ts"], reverse=True),
                "closed": sorted(self.ledger["closed"], key=lambda t: t.get("exit_ts") or 0, reverse=True)[:150],
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
            src = "kitaptan" if s.get("entry_src") == "kitap" else "son fiyattan"
            ct = f" · {s['contracts']:g} kontrat" if s.get("contracts") else ""
            L.append(f"💵 {_usd(s['notional_usd'], False)} pozisyon, giriş {fp(s['entry'])} ({src}){ct}")
            L.append(f"Stop {fp(s['sl'])} ({sl_s}{s['sl_pct']:.1f}%) · Hedef {fp(s['tp'])} ({tp_s}{s['tp_pct']:.1f}%)"
                     f" · en geç {_fmt_dur(s['max_saat'])}")
            if s.get("liq_first"):
                L.append(f"⚠️ Kaldıraç yüksek: likidasyon {fp(s['liq'])} stop'tan önce geliyor")
            L += [f"<i>{nt.esc(r)}</i>" for r in s.get("reasons", [])[:3]]
        if len(opened) > mx:
            L.append(f"\n…ve {len(opened) - mx} işlem daha (dashboard'da)")
        b = lg.balance(self.ledger, self.cfg)
        lat = self.stats_counter.get("signal_latency_s")
        L.append(f"\nDemo bakiye {_usd(b['equity'], False)} · açık {len(self.ledger['open'])} işlem"
                 + (f" · mum kapanışından {lat:g} sn sonra" if lat is not None else ""))
        L.append("<i>Sanal işlemdir, gerçek emir verilmez. Yatırım tavsiyesi değildir.</i>")
        return "\n".join(L)

    def msg_closed(self, closed: list[dict]) -> str:
        icon = {"hedef": "✅", "stop": "❌", "süre": "⏱", "manuel": "✋", "likidasyon": "💥"}
        what = {"hedef": "hedefte", "stop": "stopta", "süre": "süre dolunca", "manuel": "elle",
                "likidasyon": "LİKİDASYONLA"}
        L = []
        for t in closed:
            extra = ""
            if t.get("real"):
                extra = f" · ücret {_usd(t.get('fee_usd', 0), False)}"
                if abs(t.get("funding_usd", 0)) >= 0.005:
                    extra += f" · funding {_usd(-t['funding_usd'])}"
            L.append(f"{icon.get(t['outcome'], '•')} <b>{nt.esc(t['sym'])}</b> {t['side']} "
                     f"({sg.TF_LABEL.get(t.get('tf'), t.get('tf'))} {sg.SETUP_LABELS[t['setup']]}) "
                     f"{what.get(t['outcome'], t['outcome'])} kapandı → <b>{_usd(t['pnl_usd'])}</b> "
                     f"({t['pnl_pct']:+.2f}%) · {_fmt_dur(t['hours'])}"
                     f"\n   {nt.fmt_price(t['entry'])} → {nt.fmt_price(t['exit'])}{extra}")
        b = lg.balance(self.ledger, self.cfg)
        ret = (b["equity"] / b["start"] - 1) * 100 if b["start"] else 0
        L.append(f"Demo bakiye {_usd(b['equity'], False)} (%{ret:+.2f})")
        return "\n".join(L)

    def maybe_daily(self):
        local = datetime.now(self.tz)
        today = local.strftime("%Y-%m-%d")
        if local.hour >= self.cfg["bildirim"]["gunluk_ozet_saat"] and self.state.get("last_daily") != today:
            p = self.payload or self.build_payload()
            dm = p["ledger"]["stats"]["demo"]
            txt = msg_daily(p, self.cfg) + (
                f"\n💼 <b>Demo hesap</b>: {_usd(dm['equity'], False)} (%{dm['return_pct']:+.2f}) · "
                f"açık {len(self.ledger['open'])} işlem")
            self._notify(txt, buttons=self._dash_button())
            self.state["last_daily"] = today
            self.dirty = True

    def maybe_karne(self):
        if self.demo:
            return
        if time.time() * 1000 - (self.state.get("last_karne") or 0) > 7 * 86400 * 1000 and self.payload:
            (ROOT / "data").mkdir(exist_ok=True)
            (ROOT / "data" / "KARNE.md").write_text(karne_md(self.payload), encoding="utf-8")
            self.state["last_karne"] = int(time.time() * 1000)

    def maybe_reload_config(self):
        """Saatte bir GitHub'daki config.yaml'ı çek (telefondan yapılan ayar değişikliği döngüyü beklemesin)."""
        if not os.environ.get("GITHUB_ACTIONS") or time.time() - self.t["cfg"] < 3600:
            return
        self.t["cfg"] = time.time()
        try:
            subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=ROOT, timeout=60, check=True)
            txt = subprocess.run(["git", "show", "origin/main:config.yaml"], cwd=ROOT, timeout=30,
                                 check=True, capture_output=True, text=True).stdout
            if txt and txt != (ROOT / "config.yaml").read_text(encoding="utf-8"):
                (ROOT / "config.yaml").write_text(txt, encoding="utf-8")
                self.cfg = load_config(self.cfg_path)
                self._setup_cfg()
                print("✓ config.yaml güncellendi")
        except Exception as e:  # noqa: BLE001
            print(f"[uyarı] ayar yenilenemedi: {e}")

    def _on_tg_updates(self, updates):
        def on_scan():
            self.scan_at.clear()
            return "⟳ Tarama başladı; güçlü sinyal çıkarsa demo işlem açılır ve buraya yazılır."
        if cm.handle(updates, self.state, self.payload or self.build_payload(), on_scan, self.on_close_request):
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
    def full_refresh(self):
        self.refresh_universe()
        if not self.universe:
            raise RuntimeError("Taranacak coin bulunamadı (borsa verisi boş).")
        self.refresh_funding()
        self.refresh_hourly()

    def run_once(self) -> dict:
        """Tek tur: her şeyi tara, pozisyonları güncelle, dosyaları yaz."""
        t0 = time.time()
        self.full_refresh()
        self.refresh_prices()
        self.rebuild_watch()
        self.reconcile(catchup=True)
        strong = []
        for tf in self.tfs:
            strong += self.scan_tf(tf)
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

    def start_live(self):
        """WebSocket akışları ve Telegram işçisini başlatır."""
        from . import ws
        if self.live.get("websocket", True) and not self.demo:
            self.feeds["okx"] = ws.OKXFeed(self._on_tick, self._on_funding)
            if self.live.get("bitget_ws", True):
                self.feeds["bitget"] = ws.BitgetFeed(self._on_tick, self._on_funding)
            self._feed_symbols()
            for f in self.feeds.values():
                f.start()
        if nt.tg_token() and self.notify_on:
            self.tg = nt.TelegramWorker(self._on_tg_updates).start()
        self.worker = threading.Thread(target=self._worker_loop, daemon=True, name="isci")
        self.worker.start()

    def stop_live(self):
        for f in self.feeds.values():
            f.stop()
        self.jobs.put("__stop__")
        if self.tg:
            self.tg.stop()

    def run_loop(self, minutes: float | None = None):
        minutes = minutes or self.live["dongu_dakika"]
        end = self.started + minutes * 60
        print(f"▶ Canlı döngü başladı: {minutes:.0f} dk, periyotlar {self.tfs}, yayın={self.publish_on}")
        gap_min = (time.time() * 1000 - (self.state.get("last_alive") or 0)) / 60000 if self.state.get("last_alive") else 0
        self.full_refresh()
        self.start_live()
        self.refresh_prices()
        self.rebuild_watch()
        t_ws = time.time()
        while time.time() - t_ws < 8 and not self.okx_live() and self.feeds:
            time.sleep(0.25)
        print(f"  canlı akış: {'bağlandı' if self.okx_live() else 'yok, REST yedeği kullanılıyor'}")
        self.reconcile(catchup=True)
        if gap_min > 15:
            self._notify(f"⚠️ <b>Kripto Radar</b> {_fmt_dur(gap_min / 60)} kapalı kaldı, tekrar çalışıyor. "
                         f"Bu sürede stop/hedefe değen demo işlemler dakikalık mumlarla kontrol edildi.")
        strong = []
        for tf in self.tfs:
            strong += self.scan_tf(tf)
        self.open_positions(strong)
        self.publish(force=True)
        try:
            while time.time() < end:
                now = time.time()
                try:
                    self.process_hits()
                    self.process_manual()
                    self.process_time_exits()
                    self.process_funding()
                    with self.lock:
                        pend, self.pending_open = self.pending_open, []
                    if pend:
                        for s in pend:        # giriş: tarama bittiği andaki gerçek fiyat
                            px = self.prices.get(s["inst"])
                            if px and abs(px / s["entry"] - 1) < 0.01:
                                shift = px - s["entry"]
                                s["entry"], s["sl"], s["tp"] = px, s["sl"] + shift, s["tp"] + shift
                        self.open_positions(pend)
                    due, bnd = self._due_tfs(now)
                    if due:
                        for tf in due:
                            self.scan_at[tf] = now      # tekrar kuyruğa girmesin
                        self.enqueue(f"scan:{','.join(due)}:{bnd or ''}")
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
                    if not self.tg and now - self.t["cmd"] >= self.live["komut_kontrol_sn"]:
                        self.poll_commands()
                    self.maybe_daily()
                    self.maybe_karne()
                    if self.dirty:
                        self.publish()
                    elif now - self.t["pub"] >= 15 * 60:
                        self.publish()       # açık K/Z ve "canlı" göstergesi tazelensin
                    self.cycles += 1
                    self.fail_streak = 0
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
                time.sleep(0.5)
        finally:
            self.publish(force=True)
            self.stop_live()
        o = self.stats_counter
        dm = (self.payload or {}).get("ledger", {}).get("stats", {}).get("demo", {})
        fs = {k: f.status() for k, f in self.feeds.items()}
        print(f"::notice title=Canlı döngü bitti::{(time.time() - self.started) / 60:.0f} dk, "
              f"{o['scans']} tarama, {o['opened']} açıldı, {o['closed']} kapandı "
              f"(akıştan {o.get('ws_closes', 0)}, mumdan {o.get('rest_closes', 0)}), "
              f"funding {o['funding_events']}, demo bakiye {dm.get('equity')}, "
              f"akış: " + ", ".join(f"{k} {v['ticks']} fiyat/{v['reconnects']} kopma" for k, v in fs.items()))
