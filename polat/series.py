"""Artımlı mum serisi ve göstergeler. Her mum kapanışında O(1) güncellenir; baştan hesap yok.

Göstergeler: EMA 20/50/200, RSI 14 (Wilder), ATR 14 (Wilder), MACD 12/26/9, Bollinger 20/2 (+ bant genişliği),
hacim ortalaması 20, salınım noktaları (pivot, k mum sonra kesinleşir — geleceği görmez).
"""
from __future__ import annotations

import math
from collections import deque

MAXLEN = 600


class EMA:
    __slots__ = ("n", "a", "v", "count", "_seed")

    def __init__(self, n: int):
        self.n, self.a, self.v, self.count, self._seed = n, 2 / (n + 1), None, 0, 0.0

    def update(self, x: float):
        self.count += 1
        if self.count <= self.n:                     # ilk n değer: basit ortalama ile tohumla
            self._seed += x
            self.v = self._seed / self.count
        else:
            self.v += self.a * (x - self.v)
        return self.v

    @property
    def ready(self):
        return self.count >= self.n


class Wilder:
    """RSI ve ATR'nin kullandığı Wilder yumuşatması."""
    __slots__ = ("n", "v", "count", "_seed")

    def __init__(self, n: int):
        self.n, self.v, self.count, self._seed = n, None, 0, 0.0

    def update(self, x: float):
        self.count += 1
        if self.count <= self.n:
            self._seed += x
            self.v = self._seed / self.count
        else:
            self.v = (self.v * (self.n - 1) + x) / self.n
        return self.v


class Rolling:
    """Kayan pencere toplamı ve kare toplamı (Bollinger, hacim ortalaması)."""
    __slots__ = ("n", "q", "s", "ss")

    def __init__(self, n: int):
        self.n, self.q, self.s, self.ss = n, deque(), 0.0, 0.0

    def update(self, x: float):
        self.q.append(x)
        self.s += x
        self.ss += x * x
        if len(self.q) > self.n:
            y = self.q.popleft()
            self.s -= y
            self.ss -= y * y

    @property
    def ready(self):
        return len(self.q) >= self.n

    @property
    def mean(self):
        return self.s / len(self.q) if self.q else float("nan")

    @property
    def std(self):
        if len(self.q) < 2:
            return float("nan")
        m = self.mean
        return math.sqrt(max(self.ss / len(self.q) - m * m, 0.0))


class Series:
    """Tek bir coin + zaman dilimi. bars: kapanmış mumlar; live: oluşmakta olan mum."""

    PIVOT_K = 3

    def __init__(self, tf: str):
        self.tf = tf
        self.bars: deque = deque(maxlen=MAXLEN)       # [ts, o, h, l, c, v_base, v_usd]
        self.live: list | None = None
        self.ema20, self.ema50, self.ema200 = EMA(20), EMA(50), EMA(200)
        self.ema12, self.ema26, self.macd_sig = EMA(12), EMA(26), EMA(9)
        self.rsi_up, self.rsi_dn, self.atr_w = Wilder(14), Wilder(14), Wilder(14)
        self.bb, self.vol = Rolling(20), Rolling(20)
        self.bbw_hist: deque = deque(maxlen=120)      # bant genişliği geçmişi (sıkışma yüzdeliği)
        self.ind: dict = {}
        self.ind_hist: deque = deque(maxlen=MAXLEN)   # her kapanıştaki gösterge görüntüsü (MACD kesişimi vb.)
        self.pivots_hi: deque = deque(maxlen=40)      # (ts, fiyat, index)
        self.pivots_lo: deque = deque(maxlen=40)
        self.n = 0
        self.closed_count = 0                         # bu oturumda kapanan mum (yeni kapanış tespiti)

    # ---------------------------------------------------------------------------------------
    def on_candle(self, c: list) -> bool:
        """c = [ts, o, h, l, c, v_base, v_usd, kesin]. Yeni bir mum KAPANDIYSA True döner."""
        ts = c[0]
        bar = c[:7]
        if self.bars and ts <= self.bars[-1][0]:
            if ts == self.bars[-1][0] and c[7]:
                self.bars[-1] = bar                   # aynı kapanmış mumun düzeltmesi (göstergeler korunur)
            return False
        if c[7]:
            self.live = None
            self._close(bar)
            return True
        if self.live is not None and ts > self.live[0]:
            # önceki canlı mum kesin bildirimi gelmeden yenisi başladıysa: eskisini kapanmış say
            self._close(self.live)
            self.live = bar
            return True
        self.live = bar
        return False

    def _close(self, b: list):
        ts, o, h, l, c, v, vq = b
        prev = self.bars[-1][4] if self.bars else None
        self.bars.append(b)
        self.n += 1
        self.closed_count += 1
        self.ema20.update(c)
        self.ema50.update(c)
        self.ema200.update(c)
        m = self.ema12.update(c) - self.ema26.update(c)
        sig = self.macd_sig.update(m) if self.ema26.ready else None
        if prev is not None:
            ch = c - prev
            up, dn = self.rsi_up.update(max(ch, 0.0)), self.rsi_dn.update(max(-ch, 0.0))
            tr = max(h - l, abs(h - prev), abs(l - prev))
        else:
            up = dn = None
            tr = h - l
        atr = self.atr_w.update(tr)
        self.bb.update(c)
        self.vol.update(vq)
        rsi = None
        if up is not None and self.rsi_up.count >= 14:
            rsi = 100.0 if dn == 0 else 100 - 100 / (1 + up / dn)
        bbm, bbs = self.bb.mean, self.bb.std
        bbw = (4 * bbs / bbm) if self.bb.ready and bbm else None
        if bbw is not None:
            self.bbw_hist.append(bbw)
        self.ind = {
            "ts": ts, "close": c,
            "ema20": self.ema20.v if self.ema20.ready else None,
            "ema50": self.ema50.v if self.ema50.ready else None,
            "ema200": self.ema200.v if self.ema200.ready else None,
            "rsi": rsi, "atr": atr if self.atr_w.count >= 14 else None,
            "macd": m if self.ema26.ready else None, "macd_sig": sig if self.macd_sig.ready else None,
            "bb_mid": bbm if self.bb.ready else None,
            "bb_up": bbm + 2 * bbs if self.bb.ready else None,
            "bb_lo": bbm - 2 * bbs if self.bb.ready else None,
            "bbw": bbw,
            "vol_avg": self.vol.mean if self.vol.ready else None,
        }
        self.ind_hist.append(self.ind)
        self._pivots()

    def _pivots(self):
        k = self.PIVOT_K
        if len(self.bars) < 2 * k + 1:
            return
        i = len(self.bars) - 1 - k
        mid = self.bars[i]
        win = [self.bars[j] for j in range(i - k, i + k + 1)]
        idx = self.n - 1 - k
        if mid[2] == max(b[2] for b in win) and all(mid[2] > b[2] for b in win[:k]):
            self.pivots_hi.append((mid[0], mid[2], idx))
        if mid[3] == min(b[3] for b in win) and all(mid[3] < b[3] for b in win[:k]):
            self.pivots_lo.append((mid[0], mid[3], idx))

    # --- yardımcılar ------------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self.n >= 60

    def last(self, n: int = 1) -> list:
        return list(self.bars)[-n:]

    def bbw_pct(self) -> float | None:
        """Bant genişliğinin son 120 mumdaki yüzdeliği (düşük = sıkışma)."""
        if len(self.bbw_hist) < 30 or self.ind.get("bbw") is None:
            return None
        cur = self.ind["bbw"]
        return sum(1 for x in self.bbw_hist if x <= cur) / len(self.bbw_hist)
