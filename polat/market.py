"""Piyasa durumu: her WebSocket mesajı geldiği anda ilgili coinin durumuna işlenir.

Coin başına: ticker, emir defteri (seqId sıra doğrulamalı), tape + delta/CVD, büyük işlemler, açık pozisyon,
fonlama, mark/index (basis), likidasyonlar, long/short oranı, duvar takibi, 5 zaman diliminde mum serisi.
"""
from __future__ import annotations

import time
import zlib
from math import isfinite
from collections import deque

from .okx import CHANNEL_TF, now_ms, parse_candle
from .series import Series
from radar.unified.quality import ChannelRule, MarketDataQualityEngine, QualityState
from radar.unified.event_journal import QueuedEventJournal
from radar.unified.market_data import normalize_okx_ticker


class OrderBook:
    def __init__(self):
        self.bids: dict[float, tuple[str, str]] = {}
        self.asks: dict[float, tuple[str, str]] = {}
        self.ts = 0
        self.ok = False
        self.bad = 0
        self.seq: int | None = None      # OKX seqId: son işlenen mesajın sıra numarası

    def apply(self, d: dict, snapshot: bool) -> bool:
        """Apply a snapshot/update fail-closed; malformed levels invalidate the book."""
        try:
            seq_raw, prev_raw = d.get("seqId"), d.get("prevSeqId")
            seq = int(seq_raw) if seq_raw is not None else None
            prev = int(prev_raw) if prev_raw is not None else None
            if snapshot:
                self.bids.clear()
                self.asks.clear()
                self.seq = None
            else:
                if not self.ok:
                    return False
                if seq is None or prev is None or self.seq is None or prev != self.seq:
                    self.ok = False
                    self.bad += 1
                    return False
            for side, book in (("bids", self.bids), ("asks", self.asks)):
                for level in d.get(side, ()):
                    if len(level) < 2:
                        raise ValueError("orderbook level incomplete")
                    px_s, sz_s = level[0], level[1]
                    px, size = float(px_s), float(sz_s)
                    if not isfinite(px) or not isfinite(size) or px <= 0 or size < 0:
                        raise ValueError("orderbook level invalid")
                    if size == 0:
                        book.pop(px, None)
                    else:
                        book[px] = (str(px_s), str(sz_s))
            self.ts = int(d.get("ts") or 0)
            if seq is not None:
                self.seq = seq
            checksum = d.get("checksum")
            if checksum not in (None, 0, "0") and not self.checksum_ok(int(checksum)):
                raise ValueError("orderbook checksum mismatch")
            bid, ask = self.best()
            if bid is not None and ask is not None and bid >= ask:
                raise ValueError("orderbook locked or crossed")
            self.ok = bool(bid is not None and ask is not None)
            return self.ok
        except (TypeError, ValueError, OverflowError, KeyError):
            self.ok = False
            self.bad += 1
            return False

    def top(self, n: int = 25):
        b = sorted(self.bids.items(), key=lambda x: -x[0])[:n]
        a = sorted(self.asks.items(), key=lambda x: x[0])[:n]
        return b, a

    def checksum_ok(self, cs: int) -> bool:
        b, a = self.top(25)
        parts = []
        for i in range(max(len(b), len(a))):
            if i < len(b):
                parts += [b[i][1][0], b[i][1][1]]
            if i < len(a):
                parts += [a[i][1][0], a[i][1][1]]
        crc = zlib.crc32(":".join(parts).encode()) & 0xFFFFFFFF
        if crc >= 2 ** 31:
            crc -= 2 ** 32
        return crc == cs

    def best(self):
        bb = max(self.bids) if self.bids else None
        ba = min(self.asks) if self.asks else None
        return bb, ba

    def levels(self, side: str, n: int = 400, ct_val: float = 1.0):
        """[(fiyat, baz miktar)] en iyiden başlayarak."""
        if side == "bids":
            it = sorted(self.bids.items(), key=lambda x: -x[0])[:n]
        else:
            it = sorted(self.asks.items(), key=lambda x: x[0])[:n]
        return [(p, float(v[1]) * ct_val) for p, v in it]


class Coin:
    def __init__(self, inst: str, meta: dict, tfs: list[str]):
        self.inst = inst
        self.sym = inst.split("-")[0]
        self.meta = meta
        self.ct = meta.get("ctVal", 1.0)
        self.series = {tf: Series(tf) for tf in tfs}
        self.last = self.bid = self.ask = 0.0
        self.vol24_usd = 0.0
        self.tick_ts = 0
        self.book = OrderBook()
        self.tape: deque = deque(maxlen=5000)         # (ts, px, baz, +1 alım / −1 satım)
        self.cvd = 0.0                                # kümülatif agresif alım − satım (USDT)
        self.cvd_hist: deque = deque(maxlen=2000)     # (ts, cvd, px) her işlemde değil, 1 sn'de bir
        self.big_trades: deque = deque(maxlen=200)
        self.oi_hist: deque = deque(maxlen=2000)      # (ts, oi_baz)
        self.oi_h1: deque = deque(maxlen=200)         # saatlik OI (likidasyon kümesi tahmini için)
        self.funding = None
        self.next_funding_ts = 0
        self.mark = self.index = None
        self.liqs: deque = deque(maxlen=1000)         # (ts, tasfiye edilen taraf 'long'/'short', usd)
        self.ls_ratio = None
        self.ls_ts = 0
        self.walls: dict[tuple, list] = {}            # (taraf, fiyat) → [ilk_görülme, son_görülme, baz]
        self.deep = False
        self.liq_map = None

    # --- yardımcılar ------------------------------------------------------------------------
    def spread_bp(self) -> float | None:
        bb, ba = self.book.best() if self.book.ok else (self.bid, self.ask)
        if not bb or not ba:
            bb, ba = self.bid, self.ask
        if not bb or not ba:
            return None
        return (ba - bb) / ((ba + bb) / 2) * 1e4

    def price(self) -> float:
        return self.last or self.mark or 0.0


class Market:
    def __init__(self, cfg: dict, meta: dict[str, dict], event_journal_path=None):
        self.cfg = cfg
        self.meta = meta
        self.event_journal = QueuedEventJournal(event_journal_path) if event_journal_path else None
        self.tfs = cfg["veri"]["zaman_dilimleri"]
        self.coins: dict[str, Coin] = {}
        self.h1_all: dict[str, deque] = {}           # bütün piyasa 1S mumları (seçici için)
        self.latency: deque = deque(maxlen=5000)       # borsa zaman damgası → bize ulaşma (ms)
        # Kaynakların beklenen yayın ritmine göre muhafazakâr kanal tazelik eşikleri.
        # Seyrek olay akışları (likidasyon) ayrı tutulur; bunlar fiyat kanalı yerine
        # olay akışı sağlığıdır. Mumlar kendi zaman dilimlerinin 2 barı içinde bayatlar.
        channel_age_ms = {
            "tickers": 15_000, "books": 5_000, "trades": 30_000,
            "open-interest": 180_000, "funding-rate": 1_800_000,
            "mark-price": 15_000, "index-tickers": 15_000,
            "liquidation-orders": 300_000,
        }
        tf_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
                 "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000}
        channel_age_ms.update({f"candle_{tf}": tf_ms[tf] * 2 for tf in self.tfs if tf in tf_ms})
        channel_age_ms.update({f"candle_{tf}": tf_ms[tf] * 2 for tf in tf_ms})
        self.quality = MarketDataQualityEngine({
            name: ChannelRule(max_age_ms=age, require_sequence=(name == "books"))
            for name, age in channel_age_ms.items()
        })
        self.quality_latest: dict[tuple[str, str], object] = {}
        self.quality_counts: dict[str, int] = {state.value: 0 for state in QualityState}
        self.proc: deque = deque(maxlen=5000)          # mesaj işleme süresi (µs)
        self.msgs = 0
        self.on_close = None                           # geri çağrı: (coin, tf) — mum kapandı
        self.on_trade = None                           # geri çağrı: (coin, px, ts) — her işlem (stop/hedef)
        self.on_book_bad = None
        self.on_liq = None
        # likidasyon tahmini kalibrasyonu: gerçek likidasyonların hangi kaldıraç kümesine düştüğü
        self.liq_calib = {"olay": 0, "isabet": 0, "lev": {"10": 1.0, "25": 1.0, "50": 1.0, "100": 1.0}}
        self.global_ = {"btc_dom": None, "btc_dom_ts": 0, "stable_usd": None, "stable_chg24": None,
                        "stable_ts": 0, "events": []}

    def coin(self, inst: str) -> Coin:
        c = self.coins.get(inst)
        if c is None:
            c = self.coins[inst] = Coin(inst, self.meta.get(inst, {}), self.tfs)
        return c

    @staticmethod
    def _event_ts(value) -> int | None:
        try:
            stamp = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return stamp if stamp > 0 else None

    def _observe_quality(self, symbol: str, channel: str, event_ts_ms: int | None,
                         recv_ms: int, *, seq_id: int | None = None,
                         prev_seq_id: int | None = None, snapshot: bool = False,
                         valid: bool = True) -> None:
        obs = self.quality.observe(symbol=symbol, channel=channel, event_ts_ms=event_ts_ms,
                                   receive_ts_ms=recv_ms, seq_id=seq_id,
                                   prev_seq_id=prev_seq_id, is_snapshot=snapshot, valid=valid)
        key = (symbol, channel)
        previous = self.quality_latest.get(key)
        if previous is not None:
            self.quality_counts[previous.state.value] = max(0, self.quality_counts[previous.state.value] - 1)
        self.quality_latest[key] = obs
        self.quality_counts[obs.state.value] += 1

    # --- mesaj dağıtımı ---------------------------------------------------------------------
    def handle(self, msg: dict, recv_ms: int):
        t0 = time.perf_counter()
        self.msgs += 1
        if self.event_journal is not None:
            self.event_journal.record(msg, receive_ts_ms=recv_ms)
        arg = msg.get("arg") or {}
        ch = arg.get("channel", "")
        data = msg.get("data") or []
        try:
            if ch == "tickers":
                for d in data:
                    self._ticker(d, recv_ms)
            elif ch.startswith("books"):
                self._book(arg["instId"], data, msg.get("action", "snapshot"), recv_ms)
            elif ch == "trades":
                for d in data:
                    self._trade(d, recv_ms)
            elif ch.startswith("candle"):
                self._candle(arg["instId"], ch, data, msg_conn=arg, recv_ms=recv_ms)
            elif ch == "open-interest":
                for d in data:
                    c = self.coin(d["instId"])
                    ts = self._event_ts(d.get("ts"))
                    try:
                        oi_ccy = float(d.get("oiCcy") or 0)
                        oi = oi_ccy if oi_ccy > 0 else float(d.get("oi") or 0) * c.ct
                    except (TypeError, ValueError, OverflowError):
                        oi = 0.0
                    self._observe_quality(c.inst, "open-interest", ts, recv_ms,
                                          valid=bool(ts and isfinite(oi) and oi > 0))
                    if self.quality_latest[(c.inst, "open-interest")].state is not QualityState.LIVE:
                        continue
                    c.oi_hist.append((ts, oi))
                    hr = ts - ts % 3_600_000
                    if not c.oi_h1 or c.oi_h1[-1][0] < hr:
                        c.oi_h1.append((hr, oi))
            elif ch == "funding-rate":
                for d in data:
                    c = self.coin(d["instId"])
                    event_ts = self._event_ts(d.get("ts"))
                    try:
                        funding = float(d.get("fundingRate"))
                    except (TypeError, ValueError, OverflowError):
                        funding = float("nan")
                    self._observe_quality(c.inst, "funding-rate", event_ts, recv_ms,
                                          valid=isfinite(funding) and -1.0 <= funding <= 1.0)
                    if self.quality_latest[(c.inst, "funding-rate")].state is not QualityState.LIVE:
                        continue
                    c.funding = funding
                    c.next_funding_ts = self._event_ts(d.get("fundingTime")) or 0
            elif ch == "mark-price":
                for d in data:
                    inst = d["instId"]
                    try:
                        mark_px = float(d.get("markPx"))
                    except (TypeError, ValueError, OverflowError):
                        mark_px = float("nan")
                    self._observe_quality(inst, "mark-price", self._event_ts(d.get("ts")), recv_ms,
                                          valid=isfinite(mark_px) and mark_px > 0)
                    if self.quality_latest[(inst, "mark-price")].state is QualityState.LIVE:
                        self.coin(inst).mark = mark_px
            elif ch == "index-tickers":
                for d in data:
                    c = self.coins.get(d["instId"] + "-SWAP")
                    if c:
                        try:
                            index_px = float(d.get("idxPx"))
                        except (TypeError, ValueError, OverflowError):
                            index_px = float("nan")
                        self._observe_quality(c.inst, "index-tickers", self._event_ts(d.get("ts")), recv_ms,
                                              valid=isfinite(index_px) and index_px > 0)
                        if self.quality_latest[(c.inst, "index-tickers")].state is QualityState.LIVE:
                            c.index = index_px
            elif ch == "liquidation-orders":
                for d in data:
                    c = self.coins.get(d.get("instId", ""))
                    if not c:
                        continue
                    for x in d.get("details", ()):
                        event_ts = self._event_ts(x.get("ts") or d.get("ts"))
                        try:
                            liq_size = float(x.get("sz") or 0)
                        except (TypeError, ValueError, OverflowError):
                            liq_size = 0.0
                        self._observe_quality(c.inst, "liquidation-orders", event_ts, recv_ms,
                                              valid=isfinite(liq_size) and liq_size > 0)
                        if self.quality_latest[(c.inst, "liquidation-orders")].state is not QualityState.LIVE:
                            continue
                        side = "long" if (x.get("posSide") == "long" or (x.get("posSide") == "net" and x.get("side") == "sell")) else "short"
                        try:
                            px = float(x.get("bkPx") or c.last or 0)
                            usd = liq_size * c.ct * px
                        except (TypeError, ValueError, OverflowError):
                            continue
                        if not isfinite(px) or not isfinite(usd) or px <= 0 or usd <= 0:
                            continue
                        c.liqs.append((event_ts, side, usd))
                        if self.on_liq:
                            self.on_liq(c, side, usd, px)
        finally:
            self.proc.append((time.perf_counter() - t0) * 1e6)

    def _ticker(self, d: dict, recv_ms: int):
        normalized = normalize_okx_ticker(d)
        inst = normalized.symbol
        if not inst:
            return
        self._observe_quality(inst, "tickers", normalized.event_ts_ms, recv_ms, valid=normalized.valid)
        observation = self.quality_latest[(inst, "tickers")]
        # Ticker ancak borsa zamanı ve geçerli BBO doğrulanırsa karar verisine girer.
        if observation.state is not QualityState.LIVE:
            return
        c = self.coin(inst)
        c.last, c.bid, c.ask = normalized.last, normalized.bid, normalized.ask
        vol = float(d.get("volCcy24h") or 0)
        c.vol24_usd = vol * c.last
        ts = normalized.event_ts_ms
        c.tick_ts = ts
        if recv_ms >= ts:
            self.latency.append(recv_ms - ts)
        if not c.deep and self.on_trade:
            self.on_trade(c, c.last, ts)

    def _book(self, inst: str, data: list, action: str, recv_ms: int):
        c = self.coin(inst)
        for d in data:
            is_snapshot = action == "snapshot"
            ok = c.book.apply(d, is_snapshot)
            self._observe_quality(inst, "books", self._event_ts(d.get("ts")), recv_ms,
                                  seq_id=self._event_ts(d.get("seqId")),
                                  prev_seq_id=self._event_ts(d.get("prevSeqId")),
                                  snapshot=is_snapshot, valid=ok)
            if self.quality_latest[(inst, "books")].state is not QualityState.LIVE:
                c.book.ok = False
                ok = False
            if not ok and self.on_book_bad:
                self.on_book_bad(c)
                return
        if c.book.ok:
            self._walls(c, recv_ms)

    def _walls(self, c: Coin, now: int):
        """Büyük emir duvarı: ilk 20 kademe ortalamasının duvar_kat katı. Yerinde kaldıkça süresi uzar."""
        k = self.cfg["kurulum"]["duvar_kat"]
        seen = set()
        for side in ("bids", "asks"):
            lv = c.book.levels(side, 20, c.ct)
            if len(lv) < 10:
                continue
            avg = sum(s for _, s in lv) / len(lv)
            for px, sz in lv:
                if sz >= k * avg:
                    key = (side, px)
                    seen.add(key)
                    w = c.walls.get(key)
                    if w:
                        w[1], w[2] = now, sz
                    else:
                        c.walls[key] = [now, now, sz]
        for key in [k_ for k_ in c.walls if k_ not in seen]:
            del c.walls[key]                           # kayboldu (iptal ya da yendi)

    def _trade(self, d: dict, recv_ms: int):
        inst = str(d.get("instId") or "")
        if not inst:
            return
        c = self.coin(inst)
        try:
            px, sz = float(d["px"]), float(d["sz"]) * c.ct
            ts = int(d.get("ts") or 0)
        except (KeyError, TypeError, ValueError, OverflowError):
            self._observe_quality(inst, "trades", None, recv_ms, valid=False)
            return
        valid = isfinite(px) and isfinite(sz) and px > 0 and sz > 0 and ts > 0 and d.get("side") in ("buy", "sell")
        self._observe_quality(inst, "trades", ts or None, recv_ms, valid=valid)
        if self.quality_latest[(inst, "trades")].state is not QualityState.LIVE:
            return
        sgn = 1 if d.get("side") == "buy" else -1
        c.tape.append((ts, px, sz, sgn))
        usd = px * sz
        c.cvd += sgn * usd
        if not c.cvd_hist or ts - c.cvd_hist[-1][0] >= 1000:
            c.cvd_hist.append((ts, c.cvd, px))
        if c.vol24_usd and usd >= max(c.vol24_usd / 20000, 25_000):      # günlük hacmin 1/20000'i ve ≥ 25 bin $
            c.big_trades.append((ts, px, usd, sgn))
        c.last = px
        if recv_ms >= ts:
            self.latency.append(recv_ms - ts)
        if self.on_trade:
            self.on_trade(c, px, ts)

    def _candle(self, inst: str, ch: str, data: list, msg_conn=None, recv_ms: int | None = None):
        tf = CHANNEL_TF.get(ch)
        if tf is None:
            return
        for r in data:
            try:
                cd = parse_candle(r)
                event_ts = int(cd[0]) if cd and cd[0] is not None else None
                valid = (len(cd) >= 6 and event_ts is not None and event_ts > 0
                         and all(float(v) > 0 for v in cd[1:5])
                         and float(cd[2]) >= max(float(cd[1]), float(cd[4]))
                         and float(cd[3]) <= min(float(cd[1]), float(cd[4])))
            except (TypeError, ValueError, IndexError, OverflowError):
                cd, event_ts, valid = None, None, False
            channel = f"candle_{tf}"
            self._observe_quality(inst, channel, event_ts, recv_ms if recv_ms is not None else now_ms(), valid=valid)
            if not valid or self.quality_latest[(inst, channel)].state is not QualityState.LIVE:
                continue
            if tf == "1H":
                self._h1_all(inst, cd)
            c = self.coins.get(inst)
            if c is None or tf not in c.series:
                continue
            if c.series[tf].on_candle(cd) and self.on_close:
                self.on_close(c, tf)

    def _h1_all(self, inst: str, cd: list):
        q = self.h1_all.setdefault(inst, deque(maxlen=48))
        if q and q[-1][0] == cd[0]:
            q[-1] = cd
        elif not q or cd[0] > q[-1][0]:
            q.append(cd)

    def disconnect_channels(self, connection_name: str, receive_ts_ms: int) -> None:
        """OKX bağlantı grubu koptuğunda son gözlemleri DISCONNECTED olarak yayımla."""
        channels_by_connection = {
            "piyasa": {"tickers", "open-interest", "funding-rate", "mark-price", "index-tickers", "liquidation-orders"},
            "defter": {"books"}, "tape": {"trades"},
            "mum": {f"candle_{tf}" for tf in CHANNEL_TF.values()},
            "mum_genel": {"candle_1H"},
        }
        channels = channels_by_connection.get(connection_name, set())
        for key, previous in list(self.quality_latest.items()):
            symbol, channel = key
            if channel not in channels:
                continue
            obs = self.quality.disconnected(symbol=symbol, channel=channel, receive_ts_ms=receive_ts_ms)
            self.quality_counts[previous.state.value] = max(0, self.quality_counts[previous.state.value] - 1)
            self.quality_latest[key] = obs
            self.quality_counts[obs.state.value] += 1

    def stats(self) -> dict:
        lat = sorted(self.latency)
        pr = sorted(self.proc)

        def pct(a, p):
            return round(a[int(len(a) * p)], 1) if a else None
        latest = {}
        now = now_ms()
        for (key, original) in self.quality_latest.items():
            symbol, channel = key
            obs = original
            rule = self.quality.rules.get(channel)
            if (obs.state is QualityState.LIVE and rule and rule.max_age_ms is not None
                    and obs.event_ts_ms is not None and obs.receive_ts_ms >= 1_000_000_000_000):
                age = now - obs.event_ts_ms
                if age > rule.max_age_ms:
                    obs = self.quality.disconnected(symbol=symbol, channel=channel, receive_ts_ms=now)
                    from dataclasses import replace
                    obs = replace(obs, state=QualityState.STALE, event_ts_ms=original.event_ts_ms,
                                  age_ms=age, reason_code="event_stale")
            latest.setdefault(channel, {"sembol_sayisi": 0, "durumlar": {state.value: 0 for state in QualityState},
                                        "son_olay_yasi_ms_max": None})
            row = latest[channel]
            row["sembol_sayisi"] += 1
            row["durumlar"][obs.state.value] += 1
            if obs.age_ms is not None:
                current = row["son_olay_yasi_ms_max"]
                row["son_olay_yasi_ms_max"] = obs.age_ms if current is None else max(current, obs.age_ms)
        current_counts = {state.value: sum(row["durumlar"][state.value] for row in latest.values())
                          for state in QualityState}
        return {"mesaj": self.msgs, "gecikme_ornek_sayisi": len(lat),
                "gecikme_ms_p50": pct(lat, 0.5), "gecikme_ms_p95": pct(lat, 0.95),
                "gecikme_ms_p99": pct(lat, 0.99), "gecikme_ms_max": round(lat[-1], 1) if lat else None,
                "isleme_ornek_sayisi": len(pr), "isleme_us_p50": pct(pr, 0.5),
                "isleme_us_p95": pct(pr, 0.95), "isleme_us_p99": pct(pr, 0.99),
                "isleme_us_max": round(pr[-1], 1) if pr else None,
                "ham_olay_gunlugu": self.event_journal.stats() if self.event_journal else {"enabled": False, "complete": False, "reason": "not_configured"},
                "kalite_telemetrisi": {"anlam": "Her kanal timestamp/sequence ve kanal tazelik eşiğiyle izlenir; strateji daha sıkı eşik uygulayabilir.",
                                       "kanallar": latest, "durum_sayilari": current_counts}}

    def close(self) -> dict:
        return self.event_journal.close() if self.event_journal else {"enabled": False, "complete": False}


def now() -> int:
    return now_ms()
