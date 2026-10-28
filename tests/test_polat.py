"""Polat testleri: WebSocket çerçeveleri, göstergelerin doğruluğu, emir defteri checksum'u, okumalar/kurulumlar,
risk ve çıkış kuralları, sanal OKX borsası, coin seçici, uçtan uca ajan (sahte veri akışıyla), devir arası kontrol."""
from __future__ import annotations

import asyncio
import copy
import json
import math
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polat import brain, selector  # noqa: E402
from polat.__main__ import load_cfg  # noqa: E402
from polat.agent import Polat  # noqa: E402
from polat.broker import PaperBroker, round_contracts, walk  # noqa: E402
from polat.journal import report, report_md  # noqa: E402
from polat.market import Coin, Market, OrderBook  # noqa: E402
from polat.risk import Risk  # noqa: E402
from polat.series import Series  # noqa: E402
from polat.wsclient import encode_frame, read_frame  # noqa: E402

CFG = load_cfg()
META = {"ctVal": 0.01, "lotSz": 1.0, "minSz": 1.0, "tickSz": 0.1, "maxLever": 100}
H = 3_600_000
T0 = 1_790_000_000_000 - (1_790_000_000_000 % 86_400_000)


# ------------------------------------------------------------------------------------------- yardımcılar
def bars(n, tf_ms, start, p0=100.0, drift=0.0, vol=0.01, seed=1, volume=1e6):
    rng = np.random.default_rng(seed)
    out, p = [], p0
    for i in range(n):
        o = p
        c = o * (1 + drift + rng.normal(0, vol))
        h = max(o, c) * (1 + abs(rng.normal(0, vol / 2)))
        l = min(o, c) * (1 - abs(rng.normal(0, vol / 2)))
        v = volume * (1 + rng.random())
        out.append([start + i * tf_ms, o, h, l, c, v / c, v, 1])
        p = c
    return out


def feed_series(s: Series, rows):
    for r in rows:
        s.on_candle(r)


def book_msg(px, spread=0.01, levels=30, size="500"):
    bids = [[f"{px - spread / 2 - i * 0.01:.4f}", size, "0", "1"] for i in range(levels)]
    asks = [[f"{px + spread / 2 + i * 0.01:.4f}", size, "0", "1"] for i in range(levels)]
    d = {"bids": bids, "asks": asks, "ts": "1"}
    b = OrderBook()
    b.apply(d, True)
    bb, aa = b.top(25)
    parts = []
    for i in range(max(len(bb), len(aa))):
        if i < len(bb):
            parts += [bb[i][1][0], bb[i][1][1]]
        if i < len(aa):
            parts += [aa[i][1][0], aa[i][1][1]]
    crc = zlib.crc32(":".join(parts).encode()) & 0xFFFFFFFF
    d["checksum"] = crc - 2 ** 32 if crc >= 2 ** 31 else crc
    return d


# ------------------------------------------------------------------------------------------- WebSocket
def test_ws_frame_roundtrip():
    async def go():
        for payload in (b"x", b"a" * 200, b"b" * 70000):
            fr = encode_frame(payload, 1)
            # istemci çerçevesi maskelidir; okuyucu maskeyi çözmeli
            r = asyncio.StreamReader()
            r.feed_data(fr)
            r.feed_eof()
            op, fin, data = await read_frame(r)
            assert op == 1 and fin and data == payload
    asyncio.run(go())


# ------------------------------------------------------------------------------------------- göstergeler
def test_indicators_match_reference():
    rows = bars(400, H, T0, seed=3)
    s = Series("1H")
    feed_series(s, rows)
    df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v", "vq", "k"])
    c = df["c"]
    # EMA: ilk n değer SMA ile tohumlanan standart EMA
    def ema(x, n):
        out = [None] * len(x)
        a = 2 / (n + 1)
        v = x[:n].mean()
        out[n - 1] = v
        for i in range(n, len(x)):
            v = v + a * (x.iloc[i] - v)
            out[i] = v
        return out[-1]
    assert math.isclose(s.ind["ema20"], ema(c, 20), rel_tol=1e-9)
    assert math.isclose(s.ind["ema200"], ema(c, 200), rel_tol=1e-9)
    # RSI (Wilder)
    d = c.diff().dropna()
    up, dn = d.clip(lower=0), (-d).clip(lower=0)
    au, ad = up[:14].mean(), dn[:14].mean()
    for i in range(14, len(d)):
        au = (au * 13 + up.iloc[i]) / 14
        ad = (ad * 13 + dn.iloc[i]) / 14
    assert math.isclose(s.ind["rsi"], 100 - 100 / (1 + au / ad), rel_tol=1e-9)
    # ATR (Wilder, ilk mumda TR = H−L)
    pc = c.shift(1)
    tr = pd.concat([df["h"] - df["l"], (df["h"] - pc).abs(), (df["l"] - pc).abs()], axis=1).max(axis=1)
    tr.iloc[0] = df["h"].iloc[0] - df["l"].iloc[0]
    a = tr[:14].mean()
    for i in range(14, len(tr)):
        a = (a * 13 + tr.iloc[i]) / 14
    assert math.isclose(s.ind["atr"], a, rel_tol=1e-9)
    # Bollinger (popülasyon std, 20)
    m, sd = c[-20:].mean(), c[-20:].std(ddof=0)
    assert math.isclose(s.ind["bb_up"], m + 2 * sd, rel_tol=1e-9)
    assert math.isclose(s.ind["vol_avg"], df["vq"][-20:].mean(), rel_tol=1e-9)


def test_series_live_and_missed_confirm():
    s = Series("5m")
    rows = bars(70, 300_000, T0)
    feed_series(s, rows)
    n = s.n
    live = rows[-1][:]
    live[0] += 300_000
    live[7] = 0
    assert s.on_candle(live) is False and s.live is not None
    nxt = live[:]
    nxt[0] += 300_000
    # kesin bildirimi kaçtı, yeni mum başladı → önceki kapanmış sayılır
    assert s.on_candle(nxt) is True and s.n == n + 1
    # eski mum tekrar gelirse göstergeler bozulmaz
    assert s.on_candle(rows[10]) is False and s.n == n + 1


def test_pivots_do_not_look_ahead():
    s = Series("15m")
    rows = bars(100, 900_000, T0, seed=5)
    feed_series(s, rows)
    for ts, px, idx in s.pivots_hi:
        assert idx <= s.n - 1 - Series.PIVOT_K


# ------------------------------------------------------------------------------------------- emir defteri
def test_orderbook_checksum_and_updates():
    b = OrderBook()
    d = book_msg(100.0)
    assert b.apply(d, True) and b.ok
    bb, ba = b.best()
    assert bb < ba
    bad = {"bids": [["99.99", "0", "0", "0"]], "asks": [], "ts": "2", "checksum": 12345}
    assert not b.apply(bad, False) and not b.ok and b.bad == 1


def test_walk_book():
    px, got = walk([(100, 1.0), (101, 1.0), (102, 5.0)], 2.5)
    assert got == 2.5 and math.isclose(px, (100 + 101 + 102 * 0.5) / 2.5)
    assert round_contracts(0.0349, META) == 3.0 and round_contracts(0.005, META) == 0.0


# ------------------------------------------------------------------------------------------- okumalar
def test_candle_reading():
    assert brain.candle([0, 100, 101, 95, 100.6])["tip"] == "pin_alt"
    assert brain.candle([0, 100, 105, 99.5, 100.3])["tip"] == "pin_ust"
    assert brain.candle([0, 100, 106, 99.8, 105.8])["tip"] == "guclu_yukari"
    assert brain.candle([0, 100.4, 100.6, 98.5, 98.7], prev=[0, 99, 100.5, 98.8, 100.2])["tip"] == "yutan_asagi"
    assert brain.candle([0, 100, 100.05, 99.95, 100.0])["tip"] == "doji"


def test_volume_profile_poc():
    rows = [[i, 100, 101, 99, 100, 1, 1000.0, 1] for i in range(30)]
    rows += [[100 + i, 110, 111, 109, 110, 1, 50.0, 1] for i in range(10)]
    vp = brain.volume_profile(rows)
    assert 99 <= vp["poc"] <= 101 and vp["val"] <= vp["poc"] <= vp["vah"]


def test_structure_up_down():
    s = Series("4H")
    # zigzag yükselen: yüksek-yüksek, yüksek-düşük
    rows, p, ts = [], 100.0, T0
    for wave in range(8):
        for i in range(6):
            p *= 1.01
            rows.append([ts, p / 1.01, p * 1.002, p / 1.012, p, 1, 1e6, 1]); ts += 4 * H
        for i in range(3):
            p *= 0.995
            rows.append([ts, p / 0.995, p / 0.993, p * 0.998, p, 1, 1e6, 1]); ts += 4 * H
    feed_series(s, rows)
    st = brain.structure(s)
    assert st["yon"] == "yukari" and st["hh"] and st["hl"], st


def test_derivatives_quadrants_and_liq():
    m = Market(CFG, {"X-USDT-SWAP": META})
    c = m.coin("X-USDT-SWAP")
    s = c.series["1H"]
    feed_series(s, bars(70, H, T0, p0=100, drift=0.002, vol=0.001))
    now = s.bars[-1][0] + H
    c.oi_hist.extend([(now - 3_000_000, 1000.0), (now - 10, 1100.0)])
    c.vol24_usd = 1e8
    c.liqs.extend([(now - 60_000, "long", 2_000_000.0)])
    c.funding = 0.0005
    c.mark, c.index = 100.2, 100.0
    d = brain.derivatives(c, now)
    assert d["oi_fiyat"] in ("yeni_long", "yeni_short") and d["oi_1s"] > 9
    assert d["liq_patlama"] == "long" and d["fonlama_yillik"] > 50 and d["basis_yuzde"] > 0.19


def test_flow_divergence_and_walls():
    cfg = copy.deepcopy(CFG)
    m = Market(cfg, {"X-USDT-SWAP": META})
    c = m.coin("X-USDT-SWAP")
    now = T0 + 10 * H
    # fiyat yeni tepe yapıyor, CVD yapmıyor → ayı ayrışması
    for i in range(60):
        px = 100 + (i * 0.01 if i < 30 else (0.3 + (i - 30) * 0.02))
        cvd = i * 100 if i < 30 else 2800 - (i - 30) * 50
        c.cvd_hist.append((now - 900_000 + i * 15_000, cvd, px))
    d = book_msg(100.0)
    d["asks"][3][1] = "20000"                      # büyük satış duvarı
    del d["checksum"]
    m._book("X-USDT-SWAP", [d], "snapshot", now - 60_000)
    m._book("X-USDT-SWAP", [{"bids": [], "asks": [], "ts": "3"}], "update", now)
    c.last = 100.0
    f = brain.flow(c, now, atr=0.5, persist_s=30)
    assert f["ayrisma"] == "ayi"
    assert f["duvar_ust"] and f["duvar_ust"]["sure_sn"] >= 59


# ------------------------------------------------------------------------------------------- risk
def test_risk_sizing_and_leverage():
    r = Risk(CFG, 100_000)
    sz = r.size(100_000, 100.0, 98.0, 100)
    assert math.isclose(sz["risk_usd"], 500) and math.isclose(sz["notional"], 25_000)
    assert sz["lev"] == 10                                    # tavan
    sz6 = r.size(100_000, 100.0, 94.0, 100)
    liq_dist = 1 / sz6["lev"] - CFG["yurutme"]["bakim_marji"]
    assert liq_dist >= 3 * 0.06 - 1e-9                      # likidasyon stop'un 2 stop ötesinde
    r.st["peak"] = 100_000
    assert math.isclose(r.size(89_000, 100, 98, 100)["risk_usd"], 89_000 * 0.0025)   # zirveden −%11 → yarım risk


def test_risk_gates():
    r = Risk(CFG, 100_000)
    now = T0 + 5 * H
    r.roll(now, 100_000)
    assert r.can_open(now, 100_000, [], "A", "long", 500) is None
    assert "günlük zarar" in r.can_open(now, 97_900, [], "A", "long", 500)
    r.st["trades_today"] = 8
    assert "overtrade" in r.can_open(now, 100_000, [], "A", "long", 500)
    r.st["trades_today"] = 0
    r.on_close(now, "A", "long", -500, "stop")
    assert "intikam" in r.can_open(now + H, 100_000, [], "A", "long", 500)
    assert r.can_open(now + H, 100_000, [], "A", "short", 500) is None
    r.on_close(now, "B", "long", -500, "stop")
    r.on_close(now, "C", "long", -500, "stop")
    assert "üst üste" in r.can_open(now + H, 100_000, [], "D", "long", 500)
    r2 = Risk(CFG, 100_000)
    r2.roll(now, 84_000)
    assert r2.st["halted"] and "onay" in r2.can_open(now, 84_000, [], "A", "long", 10)


def test_exit_rules_breakeven_partial_giveback():
    r = Risk(CFG, 100_000)
    p = {"side": "long", "entry": 100.0, "sl": 98.0, "r_dist": 2.0, "opened": T0}
    assert r.manage(p, 101.0, T0 + H, None) == []
    acts = r.manage(p, 102.0, T0 + H, None)                     # 1R: başabaş + geri verme
    sl = [a for a in acts if a[0] == "stop"][0][1]
    assert sl >= 100.0
    p["sl"] = sl
    acts = r.manage(p, 104.2, T0 + H, None)                     # 2,1R: yarısı + tepe kârın yarısı korunur
    assert any(a[0] == "kismi" for a in acts)
    sl = [a for a in acts if a[0] == "stop"][0][1]
    assert math.isclose(sl, 100 + 4.2 * 0.5)
    p["sl"] = sl
    assert not any(a[0] == "kismi" for a in r.manage(p, 104.5, T0 + H, None))
    short = {"side": "short", "entry": 100.0, "sl": 102.0, "r_dist": 2.0, "opened": T0}
    acts = r.manage(short, 96.0, T0 + H, None)
    assert [a for a in acts if a[0] == "stop"][0][1] <= 98.0
    old = {"side": "long", "entry": 100.0, "sl": 98.0, "r_dist": 2.0, "opened": T0}
    assert any(a[0] == "kapat" for a in r.manage(old, 100.5, T0 + 73 * H, None))


# ------------------------------------------------------------------------------------------- sanal borsa
def _coin_with_book(px=100.0):
    m = Market(CFG, {"X-USDT-SWAP": META})
    c = m.coin("X-USDT-SWAP")
    m._book("X-USDT-SWAP", [book_msg(px)], "snapshot", T0)
    c.bid, c.ask, c.last = px - 0.005, px + 0.005, px
    c.funding, c.next_funding_ts = 0.0001, T0 + 8 * H
    return m, c


def test_paper_broker_open_stop_target_funding():
    m, c = _coin_with_book()
    b = PaperBroker(CFG, 100_000)
    res = b.open(c, "long", 10.0, 10, 98.0, 112.5, T0, {"kurulum": "test"})
    assert res["ok"]
    p = res["pos"]
    # 10 baz = 1000 kontrat; her kademe 500 kontrat × 0,01 = 5 baz → iki kademe
    assert math.isclose(p["entry"], (100.005 + 100.015) / 2, rel_tol=1e-9)
    assert math.isclose(p["fees"], p["entry"] * 10 * 0.0005)
    assert p["liq"] < 98.0
    eq0 = b.equity(m.coins)
    assert eq0 < 100_000                                    # ücret + spread
    # funding
    evs = b.on_funding(c, T0 + 8 * H + 1)
    assert evs and evs[0]["funding"] > 0
    # stop: fiyat stop'u geçer → o anki defterden dolar (stop-market)
    m._book("X-USDT-SWAP", [book_msg(97.9)], "snapshot", T0 + 9 * H)
    evs = b.on_price(c, 97.95, T0 + 9 * H)
    assert evs and evs[0]["reason"] == "stop" and evs[0]["px"] < 98.0 and evs[0]["full"]
    assert not b.positions
    # hedef: limit, maker ücreti, hedef fiyatından
    res = b.open(c, "short", 5.0, 5, 99.5, 87.5, T0 + 10 * H, {})
    ev = b.on_price(c, 87.4, T0 + 11 * H)[0]
    assert ev["reason"] == "hedef" and ev["px"] == 87.5 and math.isclose(ev["fee"], 87.5 * 5 * 0.0002)


def test_paper_broker_partial_and_liquidation():
    m, c = _coin_with_book()
    b = PaperBroker(CFG, 100_000)
    p = b.open(c, "long", 10.0, 10, 0.0, None, T0, {})["pos"]
    ev = b.close(c, p["id"], 0.5, T0 + H, "kismi")
    assert ev and not ev["full"] and math.isclose(b.positions[p["id"]]["qty"], 5.0)
    c.mark = p["liq"] - 0.01
    ev = b.on_price(c, c.mark, T0 + 2 * H)[0]
    assert ev["reason"] == "likidasyon" and ev["pnl"] < 0


def test_paper_broker_rejects_thin_book():
    m, c = _coin_with_book()
    b = PaperBroker(CFG, 100_000)
    res = b.open(c, "long", 10_000.0, 10, 98, 112.5, T0, {})
    assert not res["ok"] and "derinlik" in res["neden"]


# ------------------------------------------------------------------------------------------- seçici
def test_selector_rank_hysteresis_keep():
    cfg = copy.deepcopy(CFG)
    cfg["secici"]["izlenen"], cfg["secici"]["cikis_sirasi"] = 4, 5
    cfg["secici"]["sabit"] = ["BTC-USDT-SWAP"]
    m = Market(cfg, {})
    h1 = {}
    for i, sym in enumerate(["BTC", "A", "B", "C", "D", "E"]):
        c = m.coin(f"{sym}-USDT-SWAP")
        c.vol24_usd = 1e9 - i * 1e8
        c.bid, c.ask = 100, 100.01
        h1[c.inst] = [[T0 + k * H, 1, 1, 1, 1, 1, 1e6, 1] for k in range(25)]
    h1["E-USDT-SWAP"][-1][6] = 2e7                            # E'nin hacmi aniden 20 kat
    ranked = selector.rank(m.coins, h1, cfg)
    order = [x[0] for x in ranked]
    # E en düşük hacimli ama hacmi hızla yükseliyor → kendisinden büyük C ve D'nin önüne geçer
    assert order.index("E-USDT-SWAP") < order.index("C-USDT-SWAP") < order.index("D-USDT-SWAP")
    chosen, _ = selector.choose(ranked, set(), set(), cfg)
    assert "BTC-USDT-SWAP" in chosen and "E-USDT-SWAP" in chosen and len(chosen) == 4
    chosen2, _ = selector.choose(ranked, chosen, {"D-USDT-SWAP"}, cfg)
    assert "D-USDT-SWAP" in chosen2                           # açık pozisyon listeden çıkmaz
    m.coins["C-USDT-SWAP"].vol24_usd = 1e6                     # min hacmin altı
    assert "C-USDT-SWAP" not in [x[0] for x in selector.rank(m.coins, h1, cfg)]


# ------------------------------------------------------------------------------------------- uçtan uca
class FakeRest:
    def __init__(self, data):
        self.data = data
        self.key = ""

    async def instruments(self):
        return {i: dict(META, ctVal=0.01) for i in self.data}

    async def tickers(self):
        return [{"instId": i, "last": str(d["1D"][-1][4]), "bidPx": str(d["1D"][-1][4] - 0.01),
                 "askPx": str(d["1D"][-1][4] + 0.01), "volCcy24h": "5000000", "ts": "0"} for i, d in self.data.items()]

    async def candles(self, inst, tf, limit=300):
        return [r[:] for r in self.data[inst][tf][-limit:]]

    async def candles_since(self, inst, tf, since):
        return self.data[inst].get("1m_gap", [])

    async def long_short(self, inst):
        return 0.8


class FakeFeed:
    def __init__(self, cfg, on_msg):
        self.stats = {"baglanti": {}, "kopma": {}, "son_hata": ""}
        self.watched = set()

    def start(self):
        pass

    async def stop(self):
        pass

    async def market_wide(self, insts):
        pass

    async def watch(self, inst):
        self.watched.add(inst)

    async def unwatch(self, inst):
        self.watched.discard(inst)

    async def resync_book(self, inst):
        pass


def _uptrend_data(inst_px=100.0):
    """1G/4S/1S yükselen yapı; 15dk'da önceki gün tepesinin hacimle kırılması için zemin."""
    d = {}
    end = T0 + 40 * 86_400_000
    for tf, ms, n in (("1D", 86_400_000, 120), ("4H", 4 * H, 300), ("1H", H, 300), ("15m", 900_000, 300), ("5m", 300_000, 300)):
        rows, p = [], inst_px
        start = end - n * ms
        rng = np.random.default_rng(7)
        for i in range(n):
            # dalgalı yükseliş: 6 yukarı, 3 aşağı
            step = 0.004 if (i % 9) < 6 else -0.003
            o = p
            c = o * (1 + step + rng.normal(0, 0.0005))
            rows.append([start + i * ms, o, max(o, c) * 1.001, min(o, c) * 0.999, c, 1e4 / c, 1e6, 1])
            p = c
        d[tf] = rows
    return d


def test_agent_end_to_end_decision_journal_and_restart():
    async def go():
        tmp = Path(tempfile.mkdtemp())
        inst = "X-USDT-SWAP"
        data = {inst: _uptrend_data(), "BTC-USDT-SWAP": _uptrend_data(50_000)}
        cfg = copy.deepcopy(CFG)
        cfg["secici"]["min_hacim_usd"] = 0
        cfg["secici"]["sabit"] = ["BTC-USDT-SWAP"]
        cfg["bildirim"]["telegram"] = False
        ag = Polat(cfg, tmp, rest=FakeRest(data), feed_cls=FakeFeed)
        await ag.start()
        assert inst in ag.deep and inst in ag.feed.watched
        m = ag.market
        c = m.coins[inst]
        s15, s5 = c.series["15m"], c.series["5m"]
        assert s15.n >= 299 and s5.ready
        # zorla bir kırılım kurulumu: dünkü tepenin üstünde, 4 kat hacimli, güçlü 15 dk kapanışı
        lv = {x["ad"]: x["px"] for x in brain.levels(c)}
        L = lv["dun_yuksek"]
        last = s15.bars[-1]
        o = L * 0.997
        cl = L * 1.004
        nb = [last[0] + 900_000, o, cl * 1.0005, o * 0.9995, cl, 1, s15.ind["vol_avg"] * 2.2, 1]
        # önceki kapanış seviyenin altında olsun
        s15.bars[-1] = [last[0], last[1], min(last[2], L * 0.999), min(last[3], L * 0.995), L * 0.998, last[5], last[6]]
        s15.on_candle(nb)
        # canlı fiyat, defter, akış
        px = cl
        m._book(inst, [book_msg(px, levels=40, size="500000")], "snapshot", nb[0] + 900_000)
        c.last, c.bid, c.ask = px, px - 0.005, px + 0.005
        c.tick_ts = nb[0] + 900_000
        now = nb[0] + 900_000
        for i in range(30):
            m._trade({"instId": inst, "px": str(px), "sz": "100", "side": "buy", "ts": str(now - 600_000 + i * 20_000)}, now)
        c.funding, c.next_funding_ts = 0.00001, now + 8 * H
        ag.btc_rd = brain.read_all(m, m.coins["BTC-USDT-SWAP"], now)
        s5.bars.append([now - 300_000, px, px, px, px, 1, 1, ])
        rd = brain.read_all(m, c, now)
        side = brain.bias(rd, ag.btc_rd, False)
        assert side == "long", (rd["yapi_1D"], rd["yapi_4H"])
        sts = brain.setups(m, c, rd, "long")
        assert sts and sts[0]["ad"] == "kirilim", rd["mum_15m"]
        rec = ag.evaluate(c)
        assert rec is not None and rec["kurulum"] == "kirilim"
        dec = ag.journal.read("kararlar")
        assert dec and dec[-1]["okuma"]["seviyeler"] and "plan" in dec[-1]
        if rec["girildi"]:
            p = list(ag.broker.positions.values())[0]
            assert p["kurulum"] == "kirilim" and p["sl"] < p["entry"] < p["tp"]
            assert math.isclose(p["tp"] / p["entry"], 1.125)
            # aynı coinde ikinci giriş engellenir
            assert ag.risk.can_open(now, 1e5, list(ag.broker.positions.values()), inst, "long", 1) is not None
            # devir: kaydet → yeni ajan → arada stop tetiklenmiş
            ag.save()
            await ag.stop("test")
            data[inst]["1m_gap"] = [[now + 60_000, px, px, p["sl"] * 0.99, p["sl"] * 0.995, 1, 1, 1]]
            ag2 = Polat(cfg, tmp, rest=FakeRest(data), feed_cls=FakeFeed)
            await ag2.start()
            assert not ag2.broker.positions
            closes = [x for x in ag2.journal.read("islemler") if x.get("olay") == "kapanis"]
            assert closes and "devir arası" in closes[-1]["neden"] and closes[-1]["r"] < 0
            await ag2.stop("test")
            rep = report(ag2.journal)
            assert rep["pencereler"]["1_gun"]["genel"]["islem"] >= 0
            assert "Yatırım tavsiyesi değildir" in report_md(rep)
        else:
            await ag.stop("test")
            assert rec["engel"], rec
        return rec
    rec = asyncio.run(go())
    print("   karar:", "girildi" if rec["girildi"] else rec["engel"], "| teyit:", rec["teyit"])


def test_live_mode_refused():
    from polat.__main__ import make_rest
    cfg = copy.deepcopy(CFG)
    cfg["yurutme"]["mod"] = "okx_canli"
    try:
        make_rest(cfg)
        raise AssertionError("canlı mod açılmamalı")
    except SystemExit as e:
        assert "Gerçek para" in str(e)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
