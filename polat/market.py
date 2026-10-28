"""Piyasa durumu: her WebSocket mesajı geldiği anda ilgili coinin durumuna işlenir.

Coin başına: ticker, emir defteri (checksum doğrulamalı), tape + delta/CVD, büyük işlemler, açık pozisyon,
fonlama, mark/index (basis), likidasyonlar, long/short oranı, duvar takibi, 5 zaman diliminde mum serisi.
"""
from __future__ import annotations

import time
import zlib
from collections import deque

from .okx import CHANNEL_TF, now_ms, parse_candle
from .series import Series


class OrderBook:
    def __init__(self):
        self.bids: dict[float, tuple[str, str]] = {}
        self.asks: dict[float, tuple[str, str]] = {}
        self.ts = 0
        self.ok = False
        self.bad = 0

    def apply(self, d: dict, snapshot: bool) -> bool:
        if snapshot:
            self.bids.clear()
            self.asks.clear()
        for side, book in (("bids", self.bids), ("asks", self.asks)):
            for lv in d.get(side, ()):
                px_s, sz_s = lv[0], lv[1]
                px = float(px_s)
                if float(sz_s) == 0:
                    book.pop(px, None)
                else:
                    book[px] = (px_s, sz_s)
        self.ts = int(d.get("ts") or 0)
        cs = d.get("checksum")
        if cs is not None and not self.checksum_ok(int(cs)):
            self.ok = False
            self.bad += 1
            return False
        self.ok = True
        return True

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
    def __init__(self, cfg: dict, meta: dict[str, dict]):
        self.cfg = cfg
        self.meta = meta
        self.tfs = cfg["veri"]["zaman_dilimleri"]
        self.coins: dict[str, Coin] = {}
        self.h1_all: dict[str, deque] = {}           # bütün piyasa 1S mumları (seçici için)
        self.latency: deque = deque(maxlen=5000)       # borsa zaman damgası → bize ulaşma (ms)
        self.proc: deque = deque(maxlen=5000)          # mesaj işleme süresi (µs)
        self.msgs = 0
        self.on_close = None                           # geri çağrı: (coin, tf) — mum kapandı
        self.on_trade = None                           # geri çağrı: (coin, px, ts) — her işlem (stop/hedef)
        self.on_book_bad = None
        self.on_liq = None
        self.global_ = {"btc_dom": None, "btc_dom_ts": 0, "stable_usd": None, "stable_chg24": None,
                        "stable_ts": 0, "events": []}

    def coin(self, inst: str) -> Coin:
        c = self.coins.get(inst)
        if c is None:
            c = self.coins[inst] = Coin(inst, self.meta.get(inst, {}), self.tfs)
        return c

    # --- mesaj dağıtımı ---------------------------------------------------------------------
    def handle(self, msg: dict, recv_ms: int):
        t0 = time.perf_counter()
        self.msgs += 1
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
                self._candle(arg["instId"], ch, data, msg_conn=arg)
            elif ch == "open-interest":
                for d in data:
                    c = self.coin(d["instId"])
                    ts, oi = int(d["ts"]), float(d.get("oiCcy") or 0) or float(d["oi"]) * c.ct
                    c.oi_hist.append((ts, oi))
                    hr = ts - ts % 3_600_000
                    if not c.oi_h1 or c.oi_h1[-1][0] < hr:
                        c.oi_h1.append((hr, oi))
            elif ch == "funding-rate":
                for d in data:
                    c = self.coin(d["instId"])
                    c.funding = float(d["fundingRate"])
                    c.next_funding_ts = int(d.get("fundingTime") or 0)
            elif ch == "mark-price":
                for d in data:
                    self.coin(d["instId"]).mark = float(d["markPx"])
            elif ch == "index-tickers":
                for d in data:
                    c = self.coins.get(d["instId"] + "-SWAP")
                    if c:
                        c.index = float(d["idxPx"])
            elif ch == "liquidation-orders":
                for d in data:
                    c = self.coins.get(d.get("instId", ""))
                    if not c:
                        continue
                    for x in d.get("details", ()):
                        side = "long" if (x.get("posSide") == "long" or (x.get("posSide") == "net" and x.get("side") == "sell")) else "short"
                        px = float(x.get("bkPx") or c.last or 0)
                        usd = float(x.get("sz") or 0) * c.ct * px
                        c.liqs.append((int(x.get("ts") or recv_ms), side, usd))
                        if self.on_liq:
                            self.on_liq(c, side, usd)
        finally:
            self.proc.append((time.perf_counter() - t0) * 1e6)

    def _ticker(self, d: dict, recv_ms: int):
        inst = d["instId"]
        c = self.coin(inst)
        c.last = float(d.get("last") or 0) or c.last
        c.bid = float(d.get("bidPx") or 0) or c.bid
        c.ask = float(d.get("askPx") or 0) or c.ask
        vol = float(d.get("volCcy24h") or 0)
        c.vol24_usd = vol * c.last
        ts = int(d.get("ts") or 0)
        c.tick_ts = ts
        if ts:
            self.latency.append(recv_ms - ts)
        if not c.deep and self.on_trade:
            self.on_trade(c, c.last, ts or recv_ms)

    def _book(self, inst: str, data: list, action: str, recv_ms: int):
        c = self.coin(inst)
        for d in data:
            ok = c.book.apply(d, action == "snapshot")
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
        c = self.coin(d["instId"])
        px, sz = float(d["px"]), float(d["sz"]) * c.ct
        ts = int(d["ts"])
        sgn = 1 if d.get("side") == "buy" else -1
        c.tape.append((ts, px, sz, sgn))
        usd = px * sz
        c.cvd += sgn * usd
        if not c.cvd_hist or ts - c.cvd_hist[-1][0] >= 1000:
            c.cvd_hist.append((ts, c.cvd, px))
        if c.vol24_usd and usd >= max(c.vol24_usd / 20000, 25_000):      # günlük hacmin 1/20000'i ve ≥ 25 bin $
            c.big_trades.append((ts, px, usd, sgn))
        c.last = px
        self.latency.append(recv_ms - ts)
        if self.on_trade:
            self.on_trade(c, px, ts)

    def _candle(self, inst: str, ch: str, data: list, msg_conn=None):
        tf = CHANNEL_TF.get(ch)
        if tf is None:
            return
        for r in data:
            cd = parse_candle(r)
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

    def stats(self) -> dict:
        lat = sorted(self.latency)
        pr = sorted(self.proc)

        def pct(a, p):
            return round(a[int(len(a) * p)], 1) if a else None
        return {"mesaj": self.msgs, "gecikme_ms_p50": pct(lat, 0.5), "gecikme_ms_p95": pct(lat, 0.95),
                "isleme_us_p50": pct(pr, 0.5), "isleme_us_p95": pct(pr, 0.95)}


def now() -> int:
    return now_ms()
