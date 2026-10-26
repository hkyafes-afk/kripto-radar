"""Canlı motor: sürekli açık kalan döngü.

  her ~15 sn  → Telegram komutları (anında cevap)
  her ~60 sn  → anlık fiyatlar + demo pozisyonlar dakikalık mumla kontrol (stop/hedef anında kapanır)
  15dk/1S/4S  → mum kapanır kapanmaz (+40 sn) o periyotta sinyal taraması, güçlü sinyalde demo işlem
  her saat    → coin listesi, BTC rejimi, OI, funding geçmişi (harvest), diğer borsalar, ayar dosyası
  ≥6 dk'da bir → dashboard yayını (GitHub Pages saatte ~10 yayın sınırı yüzünden)

Aynı sınıf, test ve elle çalıştırma için tek seferlik taramayı da yapar (run_once).
"""
from __future__ import annotations

import os
import subprocess
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

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
SCAN_DELAY = 40          # mum kapanışından sonra bekleme (borsanın mumu kesinleştirmesi için)


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
        self.t = {"universe": 0.0, "funding": 0.0, "hourly": 0.0, "prices": 0.0, "cmd": 0.0,
                  "pub": 0.0, "cfg": time.time()}
        self.dirty = True
        self.payload: dict | None = None
        self.started = time.time()
        self.cycles = 0
        self.fail_streak = 0
        self.stats_counter = {"opened": 0, "closed": 0, "scans": 0}

    def _setup_cfg(self):
        c = self.cfg
        self.tfs = [tf for tf in (c["tarama"].get("periyotlar") or [c["tarama"]["periyot"]]) if tf in TF_SEC]
        self.setups = sg.build_setups(c)
        self.live = {"dongu_dakika": 330, "fiyat_kontrol_sn": 60, "komut_kontrol_sn": 15,
                     "yayin_min_dakika": 6, **(c.get("canli") or {})}
        self.tz = ZoneInfo(c["bildirim"]["saat_dilimi"])

    # ── veri ─────────────────────────────────────────────────────────
    def refresh_universe(self):
        uni, spot = build_universe(self.client, self.cfg)
        if uni:
            self.universe, self.spot_vol = uni, spot
            for r in uni:
                self.prices[r["inst"]] = r["price"]
        self.t["universe"] = time.time()

    def refresh_prices(self) -> dict[str, float]:
        rows = self.client.tickers("SWAP")
        px = {}
        for x in rows:
            v = _f(x.get("last"))
            if v == v and v > 0:
                px[x["instId"]] = v
        by = {x["instId"]: x for x in rows}
        for r in self.universe:
            x = by.get(r["inst"])
            if x:
                last, o24 = _f(x.get("last")), _f(x.get("open24h"))
                r["price"] = last
                if o24 and o24 == o24:
                    r["chg24"] = (last / o24 - 1) * 100
        self.prices.update(px)
        self.t["prices"] = time.time()
        return px

    def refresh_funding(self):
        if self.universe:
            self.funding = fetch_funding(self.client, self.universe)
        self.t["funding"] = time.time()

    def refresh_hourly(self):
        """BTC rejimi, OI değişimi, harvest, diğer borsalar."""
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
    def scan_coin(self, row: dict, tf: str) -> dict:
        inst = row["inst"]
        bh = sg.BAR_HOURS[tf]
        df = self.client.candles(inst, tf, 300)
        d1 = self._d1(inst)
        if len(df) < 120 or len(d1) < 40:
            raise ValueError(f"yetersiz veri ({len(df)} {tf}, {len(d1)} 1G)")
        f = sg.frame_features(df, self.cfg, bh).iloc[-1]
        fd = sg.daily_features(d1, self.cfg).iloc[-1]
        fr = self.funding.get(inst) or {}
        rate = _f(fr.get("fundingRate"))
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
        for s in sigs:
            s.update({"inst": inst, "sym": row["sym"], "price": feat["price"], "chg24": row.get("chg24"),
                      "funding_ann": feat["funding_ann"], "funding_rate": rate, "interval_h": iv,
                      "oi_chg24": feat["oi_chg24"], "rsi": feat["rsi"], "adx": feat["adx"],
                      "regime": regime, "vol24_usd": row["vol24_usd"]})
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

    def scan_tf(self, tf: str) -> list[dict]:
        t0 = time.time()
        results, errors = [], []
        with ThreadPoolExecutor(8) as ex:
            futs = {ex.submit(self.scan_coin, r, tf): r for r in self.universe}
            for fu in as_completed(futs):
                try:
                    results.append(fu.result())
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{futs[fu]['sym']} ({tf}): {e}")
        if len(results) < max(5, len(self.universe) // 3):
            raise RuntimeError(f"{tf}: çok az coin taranabildi ({len(results)}/{len(self.universe)}). "
                               f"İlk hatalar: {errors[:3]}")
        sigs = sorted([s for x in results for s in x["signals"]], key=lambda s: -s["score"])
        self.signals_by_tf[tf] = sigs
        self.coins_by_tf[tf] = sorted([x["coin"] for x in results], key=lambda c: -c["vol24_usd"])
        self.scan_at[tf] = time.time()
        self.errors = errors[:20]
        self.stats_counter["scans"] += 1
        self.dirty = True
        strong = [s for s in sigs if s["score"] >= self.cfg["sinyal"]["guclu_esik"]]
        print(f"[{tf}] {len(results)}/{len(self.universe)} coin, {len(sigs)} sinyal, {len(strong)} güçlü, "
              f"{time.time() - t0:.1f} sn")
        return strong

    # ── demo pozisyonlar ─────────────────────────────────────────────
    def manage_positions(self, catchup: bool = False) -> list[dict]:
        self.refresh_prices()
        if not self.ledger["open"]:
            return []
        lim = 300 if catchup else 6

        def fetch(inst):
            return self.client.candles(inst, "1m", lim, closed_only=False)

        now_ms = int(time.time() * 1000)
        closed = lg.update(self.ledger, fetch, self.prices, self.cfg, now_ms, bar_ms=60_000)
        if closed:
            self.stats_counter["closed"] += len(closed)
            self.dirty = True
            self._notify(self.msg_closed(closed))
        return closed

    def open_positions(self, strong: list[dict]) -> list[dict]:
        if not strong:
            return []
        opened = lg.open_new(self.ledger, strong, self.cfg, int(time.time() * 1000))
        if opened:
            self.stats_counter["opened"] += len(opened)
            self.dirty = True
            self._notify(self.msg_opened(opened), buttons=self._dash_button())
        return opened

    # ── çıktı ────────────────────────────────────────────────────────
    def build_payload(self) -> dict:
        now_ms = int(time.time() * 1000)
        thr, watch = self.cfg["sinyal"]["guclu_esik"], self.cfg["sinyal"]["izle_esik"]
        all_sigs = sorted([s for tf in self.tfs for s in self.signals_by_tf.get(tf, [])],
                          key=lambda s: -s["score"])
        pref = next((tf for tf in ("1H", "4H", "15m") if self.coins_by_tf.get(tf)), None)
        coins = self.coins_by_tf.get(pref, []) if pref else []
        for c in coins:
            c["price"] = self.prices.get(c["inst"], c["price"])
        btc = dict(self.btc)
        btc_row = next((r for r in self.universe if r["sym"] == "BTC"), None)
        if btc_row:
            btc.update({"price": btc_row["price"], "chg24": btc_row.get("chg24")})
        lg.mark(self.ledger, self.prices)
        lstats = lg.stats(self.ledger, self.cfg, self.tfs)
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
            "duration_s": None,
            "api_calls": getattr(self.client, "calls", None),
            "btc": btc,
            "thresholds": {"strong": thr, "watch": watch},
            "counts": {"universe": len(self.universe), "scanned": len(coins), "errors": len(self.errors),
                       "signals": len(all_sigs), "strong": sum(1 for s in all_sigs if s["score"] >= thr)},
            "signals": [s for s in all_sigs if s["raw"] >= 40][:300],
            "coins": coins,
            "harvest": self.harvest,
            "exchanges_status": self.ex_status,
            "oi_source": self.oi_src,
            "taker_real": sum(1 for c in coins if c.get("taker_src") == "gerçek"),
            "ledger": {
                "stats": lstats,
                "open": sorted(self.ledger["open"], key=lambda t: t["entry_ts"], reverse=True),
                "closed": sorted(self.ledger["closed"], key=lambda t: t.get("exit_ts") or 0, reverse=True)[:150],
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

    def _dash_button(self):
        url = self.cfg["dashboard"]["url"]
        return [[{"text": "📊 Dashboard'u aç", "url": url}]] if url else None

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
            L.append(f"💵 {s['margin_usd']:g}$ ile giriş {fp(s['entry'])}")
            L.append(f"Stop {fp(s['sl'])} ({sl_s}{s['sl_pct']:.1f}%) · Hedef {fp(s['tp'])} ({tp_s}{s['tp_pct']:.1f}%)"
                     f" · en geç {_fmt_dur(s['max_saat'])}")
            L += [f"<i>{nt.esc(r)}</i>" for r in s.get("reasons", [])[:3]]
        if len(opened) > mx:
            L.append(f"\n…ve {len(opened) - mx} işlem daha (dashboard'da)")
        b = lg.balance(self.ledger, self.cfg)
        L.append(f"\nDemo bakiye {_usd(b['equity'], False)} · açık {len(self.ledger['open'])} işlem")
        L.append("<i>Sanal işlemdir, gerçek emir verilmez. Yatırım tavsiyesi değildir.</i>")
        return "\n".join(L)

    def msg_closed(self, closed: list[dict]) -> str:
        icon = {"hedef": "✅", "stop": "❌", "süre": "⏱"}
        what = {"hedef": "hedefte", "stop": "stopta", "süre": "süre dolunca"}
        L = []
        for t in closed:
            L.append(f"{icon.get(t['outcome'], '•')} <b>{nt.esc(t['sym'])}</b> {t['side']} "
                     f"({sg.TF_LABEL.get(t.get('tf'), t.get('tf'))} {sg.SETUP_LABELS[t['setup']]}) "
                     f"{what.get(t['outcome'], t['outcome'])} kapandı → <b>{_usd(t['pnl_usd'])}</b> "
                     f"({t['pnl_pct']:+.2f}%) · {_fmt_dur(t['hours'])}")
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

    def poll_commands(self, first=False):
        self.t["cmd"] = time.time()
        if not nt.tg_token():
            return
        data = self.payload or self.build_payload()

        def on_scan():
            self.scan_at.clear()  # bir sonraki turda tüm periyotlar taransın
            return "⟳ Tarama başlıyor; sonuç bir iki dakikada gelir. Güçlü sinyal çıkarsa demo işlem açılır."

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
        self.manage_positions(catchup=True)
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

    def _due_tfs(self, now: float) -> list[str]:
        due = []
        for tf in self.tfs:
            sec = TF_SEC[tf]
            boundary = (now - SCAN_DELAY) // sec * sec + SCAN_DELAY
            if self.scan_at.get(tf, 0) < boundary <= now:
                due.append(tf)
        return due

    def run_loop(self, minutes: float | None = None):
        minutes = minutes or self.live["dongu_dakika"]
        end = self.started + minutes * 60
        print(f"▶ Canlı döngü başladı: {minutes:.0f} dk, periyotlar {self.tfs}, yayın={self.publish_on}")
        self.full_refresh()
        self.manage_positions(catchup=True)
        self.poll_commands(first=True)
        strong = []
        for tf in self.tfs:
            strong += self.scan_tf(tf)
        self.open_positions(strong)
        self.publish(force=True)
        while time.time() < end:
            now = time.time()
            try:
                if now - self.t["cmd"] >= self.live["komut_kontrol_sn"]:
                    self.poll_commands()
                if now - self.t["hourly"] >= 3600:
                    self.refresh_universe()
                    self.refresh_funding()
                    self.refresh_hourly()
                    self.maybe_reload_config()
                elif now - self.t["funding"] >= 900:
                    self.refresh_funding()
                if now - self.t["prices"] >= self.live["fiyat_kontrol_sn"]:
                    self.manage_positions()
                due = self._due_tfs(now)
                if due:
                    strong = []
                    for tf in due:
                        strong += self.scan_tf(tf)
                    self.manage_positions()          # giriş fiyatları taze olsun
                    for s in strong:
                        px = self.prices.get(s["inst"])
                        if px and abs(px / s["entry"] - 1) < 0.003:   # fiyat kaçmadıysa güncelle
                            shift = px - s["entry"]
                            s["entry"], s["sl"], s["tp"] = px, s["sl"] + shift, s["tp"] + shift
                    self.open_positions(strong)
                self.maybe_daily()
                self.maybe_karne()
                if self.dirty:
                    self.publish()
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
                time.sleep(min(60, 5 * self.fail_streak))
                continue
            time.sleep(2)
        self.publish(force=True)
        o = self.stats_counter
        dm = (self.payload or {}).get("ledger", {}).get("stats", {}).get("demo", {})
        print(f"::notice title=Canlı döngü bitti::{(time.time() - self.started) / 60:.0f} dk, "
              f"{o['scans']} tarama, {o['opened']} işlem açıldı, {o['closed']} kapandı, "
              f"demo bakiye {dm.get('equity')}")
