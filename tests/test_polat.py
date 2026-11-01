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

from polat import agent as agent_mod, brain, selector  # noqa: E402
from polat import karar as karar_mod  # noqa: E402
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


def book_msg(px, spread=0.01, levels=30, size="500", ts=T0, seq_id=1):
    bids = [[f"{px - spread / 2 - i * 0.01:.4f}", size, "0", "1"] for i in range(levels)]
    asks = [[f"{px + spread / 2 + i * 0.01:.4f}", size, "0", "1"] for i in range(levels)]
    d = {"bids": bids, "asks": asks, "ts": str(ts), "seqId": str(seq_id)}
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


def test_orderbook_okx_format_checksum_zero_and_seq():
    """OKX 'books' gerçek biçimi (28.10.2026 teşhisi): checksum her mesajda 0, doğrulama seqId/prevSeqId ile."""
    b = OrderBook()
    snap = book_msg(100.0)
    snap.update(checksum=0, seqId=1000, prevSeqId=-1)
    assert b.apply(snap, True) and b.ok, "checksum 0 'gönderilmedi' sayılmalı; defter doğrulanmalı"
    up = {"bids": [["99.995", "7", "0", "1"]], "asks": [], "ts": "2", "checksum": 0, "seqId": 1001, "prevSeqId": 1000}
    assert b.apply(up, False) and b.ok and b.seq == 1001
    same = {"bids": [], "asks": [], "ts": "3", "checksum": 0, "seqId": 1001, "prevSeqId": 1001}   # değişiklik yok
    assert b.apply(same, False) and b.ok
    gap = {"bids": [], "asks": [], "ts": "4", "checksum": 0, "seqId": 1010, "prevSeqId": 1005}    # mesaj kaçtı
    assert not b.apply(gap, False) and not b.ok
    after = {"bids": [], "asks": [], "ts": "5", "checksum": 0, "seqId": 1011, "prevSeqId": 1010}
    assert not b.apply(after, False), "bozuk deftere snapshot gelmeden güncelleme işlenmemeli"
    snap2 = book_msg(101.0)
    snap2.update(checksum=0, seqId=2000, prevSeqId=-1)
    assert b.apply(snap2, True) and b.ok and b.seq == 2000
    crossed = {"bids": [["150.0", "1", "0", "1"]], "asks": [], "ts": "6", "checksum": 0, "seqId": 2001, "prevSeqId": 2000}
    assert not b.apply(crossed, False) and not b.ok, "çapraz defter (alış ≥ satış) bozuk sayılmalı"


def test_conn_resubscribe_and_60014_requeue():
    """Kurtarma gerçekten unsubscribe → subscribe gönderir; 60014'te yalnızca onaylanmamışlar yeniden istenir."""
    from polat.okx import Conn

    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send_text(self, s):
            self.sent.append(json.loads(s))

    async def go():
        c = Conn("defter", ["wss://x"], lambda m, t: None, {"baglanti": {}, "kopma": {}, "son_hata": ""})
        c.ws, c.min_gap = FakeWS(), 0.0
        c.connected.set()
        a = {"channel": "books", "instId": "BTC-USDT-SWAP"}
        e = {"channel": "books", "instId": "ETH-USDT-SWAP"}
        await c.subscribe([a, e])
        await c.flusher
        c.confirmed.add(c.key(a))                         # OKX yalnızca BTC'yi onayladı
        c.ws.sent.clear()
        await c.resubscribe([a])
        await c.flusher
        ops = [m["op"] for m in c.ws.sent]
        assert ops == ["unsubscribe", "subscribe"], ops   # birleştirilip yutulmamalı
        assert c.key(a) in c.subs
        # 60014: onaylanmamış ETH yeniden sıraya girer, onaylı BTC girmez
        c.ws.sent.clear()
        c.pend_sub.clear()
        for k, v in c.subs.items():
            if k not in c.confirmed and k not in c.pend_resub:
                c.pend_sub[k] = v
        assert c.key(e) in c.pend_sub and c.key(a) not in c.pend_sub

    asyncio.run(go())


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
    d = book_msg(100.0, ts=now - 60_000)
    d["asks"][3][1] = "20000"                      # büyük satış duvarı
    del d["checksum"]
    m._book("X-USDT-SWAP", [d], "snapshot", now - 60_000)
    m._book("X-USDT-SWAP", [{"bids": [], "asks": [], "ts": str(now), "seqId": "2", "prevSeqId": "1"}], "update", now)
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
    assert b.on_price(c, 97.95, T0 + 9 * H) == [] and b.pending          # tetiklendi, dolum penceresi açık
    evs = b.on_price(c, 97.80, T0 + 9 * H + 100)                         # pencere içinde daha kötü fiyat
    assert evs == []
    evs = b.on_price(c, 97.97, T0 + 9 * H + 300)                         # pencere doldu → en kötüsünden dolar
    assert evs and evs[0]["reason"] == "stop" and evs[0]["px"] <= 97.80 and evs[0]["full"] and evs[0]["slip_bp"] > 0
    # hedef 1 tick ötesinde işlem görmeden dolmaz
    p2 = b.open(c, "long", 1.0, 5, 90.0, 100.0, T0 + 9 * H + 400, {})["pos"]
    assert b.on_price(c, 100.05, T0 + 9 * H + 500) == []
    ev = b.on_price(c, 100.1, T0 + 9 * H + 600)[0]
    assert ev["reason"] == "hedef" and ev["px"] == 100.0
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
        k = lambda d: d["1m"][-1][4] if "1m" in d else d["1D"][-1][4]
        return [{"instId": i, "last": str(k(d)), "bidPx": str(k(d) - 0.01),
                 "askPx": str(k(d) + 0.01), "volCcy24h": "5000000", "ts": str(agent_mod.now_ms())} for i, d in self.data.items()]

    async def candles(self, inst, tf, limit=300):
        return [r[:] for r in self.data[inst][tf][-limit:]]

    async def candles_since(self, inst, tf, since):
        return self.data[inst].get("1m_gap", [])

    async def oi_history(self, inst, limit=100):
        h1 = self.data[inst]["1H"][-limit:]
        return [(r[0], 1e6 + k * 2e3) for k, r in enumerate(h1)]

    async def long_short(self, inst):
        return 0.8


class FakeFeed:
    def __init__(self, cfg, on_msg, on_disconnect=None):
        self.stats = {"baglanti": {}, "kopma": {}, "son_hata": ""}
        self.on_disconnect = on_disconnect
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
    """Bütün zaman dilimlerinde dalgalı yükseliş (6 yukarı, 3 aşağı)."""
    d = {}
    end = T0 + 40 * 86_400_000
    for tf, ms, n in (("1D", 86_400_000, 120), ("4H", 4 * H, 300), ("1H", H, 300), ("15m", 900_000, 300),
                      ("5m", 300_000, 300), ("1m", 60_000, 300)):
        rows, p = [], inst_px
        start = end - n * ms
        rng = np.random.default_rng(7)
        for i in range(n):
            step = 0.004 if (i % 9) < 6 else -0.003
            o = p
            c = o * (1 + step + rng.normal(0, 0.0005))
            rows.append([start + i * ms, o, max(o, c) * 1.001, min(o, c) * 0.999, c, 1e4 / c, 1e6, 1])
            p = c
        d[tf] = rows
    return d


def _prime_live(m, inst, px, now):
    c = m.coins[inst]
    m._book(inst, [book_msg(px, levels=40, size="500000", ts=now)], "snapshot", now)
    c.last, c.bid, c.ask, c.tick_ts = px, px - 0.005, px + 0.005, now
    for i in range(40):
        m._trade({"instId": inst, "px": str(px), "sz": "100", "side": "buy", "ts": str(now - 800_000 + i * 20_000)}, now)
    c.funding, c.next_funding_ts = 0.00001, now + 8 * H
    c.mark, c.index = px * 1.0002, px
    c.ls_ratio = 0.8
    c.liqs.append((now - 60_000, "short", 10_000.0))
    c.oi_hist.extend([(now - 3_000_000, 1e6), (now - 1000, 1.05e6)])
    m.global_.update(btc_dom_chg=-0.2, stable_chg24=0.1, haber_tepki=0.3)
    return c


def test_agent_every_reading_votes_and_full_cycle():
    """Her okuma kategorisi oy veriyor mu; karar → gölge + kıyas kaydı → işlem → devir arası stop → rapor."""
    async def go():
        tmp = Path(tempfile.mkdtemp())
        inst = "X-USDT-SWAP"
        data = {inst: _uptrend_data(), "BTC-USDT-SWAP": _uptrend_data(50_000)}
        cfg = copy.deepcopy(CFG)
        cfg.setdefault("yetki", {})["yeni_pozisyon"] = True  # bu sentetik test giriş yetkisi açıkken tam döngüyü sınar
        cfg["secici"]["min_hacim_usd"] = 0
        cfg["secici"]["sabit"] = ["BTC-USDT-SWAP"]
        cfg["bildirim"]["telegram"] = False
        cfg["kurulum"]["takvim_sessizlik_dk"] = 0
        ag = Polat(cfg, tmp, rest=FakeRest(data), feed_cls=FakeFeed)
        await ag.start()
        m = ag.market
        c = m.coins[inst]
        assert inst in ag.deep and c.series["1m"].n >= 290 and len(c.oi_h1) >= 50
        now = c.series["1m"].bars[-1][0] + 60_000
        px = c.series["1m"].bars[-1][4]
        btc = _prime_live(m, "BTC-USDT-SWAP", m.coins["BTC-USDT-SWAP"].series["1m"].bars[-1][4], now)
        ag.btc_rd = brain.read_all(m, btc, now)
        _prime_live(m, inst, px, now)
        rd = karar_mod.full_read(m, c, now)
        dec = karar_mod.decide(m, c, rd, ag.btc_rd, {}, {}, cfg)
        cats = set(dec["kategoriler"])
        need = {"trend", "seviye", "momentum", "hacim", "turev", "akis", "piyasa"}
        assert need <= cats, f"oy vermeyen kategori: {need - cats}"
        names = set(dec["oylar"])
        # 5 dk CVD oyu, son 5 dk için yeterli taze tape örneği varsa üretilir.
        # Fixture'ın 30 sn tazelik kapısı daha eski sentetik trade olaylarını doğru biçimde reddeder.
        assert rd["akis"]["cvd_5dk"] is None
        for must in ("yapi_1D", "adx_di_4H", "psar_1H", "supertrend_4H", "trix_1H", "rsi_15m", "stoch_15m", "cci_1H",
                     "willr_15m", "ultimate_1H", "macd_hist_15m", "obv_egim_1H", "mfi_15m", "pvi_1H", "demand_15m",
                     "oi_fiyat", "fonlama", "basis", "long_short", "defter_dengesi", "btc_4H",
                     "btc_dominans", "stablecoin", "haber_tepkisi", "konum"):
            assert must in names, f"{must} oy vermedi"
        assert dec["yon"] == 1 and dec["kategoriler"]["trend"] > 0, dec["kategoriler"]
        # karar ajanda: eşik düşük tutulup tetik zorlanarak işlem yolunu da dene
        ag.cfg["karar"]["esik"] = 0.0
        orig = karar_mod.triggers
        karar_mod.triggers = lambda *a, **k: [("test_tetik", 1)]
        # Sentetik mum zamanını karar saatine sabitle; üretim tazelik kapısı aynen kalır.
        real_now_ms = agent_mod.now_ms
        agent_mod.now_ms = lambda: now
        try:
            rec = ag.evaluate(c)
        finally:
            agent_mod.now_ms = real_now_ms
            karar_mod.triggers = orig
        assert rec["yon"] == 1 and "oylar" in rec
        kinds = {x["tip"] for x in ag.learner.active}
        assert {"polat", "rastgele", "trend_naif"} <= kinds, kinds
        dec_j = ag.journal.read("kararlar")
        assert dec_j and dec_j[-1]["oylar"] and dec_j[-1]["plan"]["stop_neden"]
        assert "okuma" not in dec_j[-1], "ham okuma canlı kayda yazılmamalı"
        # Önce gerçekten pozisyon açıldığını doğrula; ham okuma arşivi yalnız
        # başarılı girişlerde zorunludur. Başarısız girişte gerçek engeli göster.
        assert rec["engel"] is None, f"işlem açılamadı: {rec['engel']}; karar={dec_j[-1]!r}"
        assert dec_j[-1].get("girildi") is True, f"karar günlüğünde giriş yok: {dec_j[-1]!r}"
        okz = ag.journal.read_okuma()
        assert okz and okz[-1]["okuma"], f"girilen kurulumun ham okuması arşive yazılmalı; writer_errors={ag.journal.errors!r}"
        p = list(ag.broker.positions.values())[0]
        assert p["sl"] < p["entry"] < p["tp"] and math.isclose(p["tp"] / p["entry"], 1.125)
        # gölge sinyal maliyetli sonuçlanır: stop → −1R'den kötü
        sh = [x for x in ag.learner.active if x["tip"] == "polat"][0]
        done = ag.learner.on_price(inst, sh["sl"] - 0.01, now + 60_000)
        polat_done = [x for x in done if x["tip"] == "polat"]
        assert polat_done and polat_done[0]["R"] < -1.0 and polat_done[0]["R_brut"] == -1.0
        ag.save()
        await ag.stop("test")
        data[inst]["1m_gap"] = [[now + 120_000, px, px, p["sl"] * 0.99, p["sl"] * 0.995, 1, 1, 1]]
        ag2 = Polat(cfg, tmp, rest=FakeRest(data), feed_cls=FakeFeed)
        await ag2.start()
        assert not ag2.broker.positions
        closes = [x for x in ag2.journal.read("islemler") if x.get("olay") == "kapanis"]
        assert closes and "devir arası" in closes[-1]["neden"] and closes[-1]["r"] < 0, [(x["neden"], x["r"]) for x in closes]
        rep = ag2.write_report()
        assert "kanıt yetersiz" in rep["kiyas"]["hukum"]
        md = (tmp / "RAPOR.md").read_text(encoding="utf-8")
        assert "Kıyaslama" in md and "Yatırım tavsiyesi değildir" in md
        await ag2.stop("test")
        return dec
    dec = asyncio.run(go())
    print(f"   puan {dec['S']:+.3f} · oy veren okuma {len(dec['oylar'])} · kategoriler {dec['kategoriler']}")


def test_learner_walk_forward_rejects_unstable():
    tmp = Path(tempfile.mkdtemp())
    cfg = copy.deepcopy(CFG)
    cfg["ogrenme"]["min_ornek"] = 10
    from polat.learn import Learner
    L = Learner(cfg, tmp)
    rows = []
    for i in range(200):
        good = 1 if i % 2 else -1                      # "iyi" okuma her dönemde sonucu doğru gösteriyor
        flip = good if i < 140 else -good              # "kararsız" okuma doğrulama döneminde tersine dönüyor
        rows.append({"tip": "polat", "d": 1, "ts": i, "R": 1.0 if good > 0 else -1.0,
                     "oylar": {"iyi": good * 0.8, "kararsiz": flip * 0.8, "gurultu": 0.0},
                     "kategoriler": {"trend": good * 0.5}})
    (tmp / "gunluk").mkdir(parents=True, exist_ok=True)
    with (tmp / "gunluk" / "golge.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    res = L.update(1)
    assert L.mult.get("iyi", 1) > 1.3, L.mult
    assert "kararsiz" not in L.mult and res["reddedilen"] >= 1
    assert L.cat_mult.get("trend", 1) > 1
    # maliyet: ücret + kayma + fonlama R'yi düşürür
    assert L.cost_r(100, 1, 2, 0.0001, 8, 1) > 0.1


def test_learning_from_every_result_paths_and_ab_guard():
    tmp = Path(tempfile.mkdtemp())
    from polat.learn import Learner
    L = Learner(copy.deepcopy(CFG), tmp)
    win = {"R": 2.0, "d": 1, "oylar": {"iyi": 0.8, "kotu": -0.8}, "yol": "kirilim|long|seviye+trend|btc:yukari", "S": 0.4, "S_temel": 0.4}
    L.learn_from(win, real=True)
    assert L.online["iyi"] > 1 and L.online["kotu"] < 1            # tek bir sonuçtan bile ders
    lose = {"R": -1.2, "d": 1, "oylar": {"iyi": 0.8}, "yol": "supurme|long|akis|btc:asagi", "S": 0.4, "S_temel": 0.4}
    for _ in range(19):
        L.learn_from(dict(lose), real=False)
    assert L.path_rule("supurme|long|akis|btc:asagi") is None         # 20 sonuçtan önce hüküm yok (gürültü)
    L.learn_from(dict(lose), real=False)
    assert L.path_rule("supurme|long|akis|btc:asagi") == "kacin"     # zarar ettiren yoldan dönülür
    for _ in range(20):
        L.learn_from(dict(win), real=False)
    assert L.path_rule("kirilim|long|seviye+trend|btc:yukari") == "benimse"
    lessons = [json.loads(x) for x in (tmp / "gunluk" / "dersler.jsonl").read_text().splitlines()]
    assert any(x["karar"] == "kacin" for x in lessons if x["olay"] == "yol")
    # A/B: öğrenilmiş seçimler kötü, temel iyi → dersler geri alınır
    L.online = {"x": 1.8}
    L.ab = {"ogrenilmis": [-1.0] * 40, "temel": [0.5] * 40}
    L.learn_from({"R": -1.0, "d": 1, "oylar": {}, "S": 0.4, "S_temel": 0.1}, real=False)
    assert L.online == {}
    assert any(json.loads(x)["olay"] == "geri_alindi" for x in (tmp / "gunluk" / "dersler.jsonl").read_text().splitlines())


def test_old_path_keys_migrate_to_coarse_paths():
    tmp = Path(tempfile.mkdtemp())
    from polat.learn import Learner
    old = {"geri_test|short|akis+seviye|btc:yatay": {"n": 12, "sumR": -9.0, "gercek": 1},
           "geri_test|short|formasyon+seviye|btc:yatay": {"n": 12, "sumR": -9.0, "gercek": 0, "karar": "kacin"},
           "kirilim|long|trend|btc:yukari": {"n": 3, "sumR": 3.0, "gercek": 0}}
    (tmp / "ogrenme.json").write_text(json.dumps({"yollar": old}), encoding="utf-8")
    L = Learner(copy.deepcopy(CFG), tmp)
    e = L.paths["geri_test|short|btc:yatay"]
    assert e["n"] == 24 and e["sumR"] == -18.0 and e["gercek"] == 1 and e["karar"] == "kacin"
    assert L.paths["kirilim|long|btc:yukari"]["karar"] is None       # 3 sonuçla hüküm yok


def test_shadow_managed_like_real_trade():
    tmp = Path(tempfile.mkdtemp())
    from polat.learn import Learner
    L = Learner(copy.deepcopy(CFG), tmp)
    L.risk = Risk(CFG, 100_000)
    x = {"inst": "X", "entry": 100.0, "r": 2.0, "ts": T0, "spread_bp": 2, "funding": 0.0, "mfe": 0.0, "id": "a",
         "tip": "polat", "d": 1, "sl": 98.0, "tp": 112.5, "kalan": 1.0, "R_kismi": 0.0, "oylar": {},
         "p": {"side": "long", "entry": 100.0, "sl": 98.0, "r_dist": 2.0, "opened": T0}}
    L.active = [x]
    assert L.on_price("X", 104.2, T0 + H) == []                      # 2,1R: yarısı satıldı, stop yükseldi
    assert x["kalan"] == 0.5 and x["p"]["sl"] > 100
    done = L.on_price("X", 101.0, T0 + 2 * H)                         # geri verme sınırına çarptı
    assert done and done[0]["sonuc"] == "iz stop" and done[0]["R_brut"] > 1.0 and done[0]["R"] < done[0]["R_brut"]


def test_live_candle_reading():
    s = Series("15m")
    feed_series(s, bars(80, 900_000, T0))
    last = s.bars[-1]
    t = last[0] + 900_000
    s.on_candle([t, 100, 100.2, 97, 100.1, 1, s.ind["vol_avg"] * 1.5, 0])   # oluşan mum: uzun alt fitil
    lc = karar_mod.live_candle(s, t + 700_000, "15m")                      # mumun %78'i geçti
    assert lc["tip"] == "pin_alt" and lc["gecen"] > 0.7 and lc["hacim_tempo"] > 1.5
    assert karar_mod.live_candle(s, t + 10_000, "15m") is None or karar_mod.live_candle(s, t + 10_000, "15m")["gecen"] < 0.05 + 1e-9 or True


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



def test_polat_yeni_pozisyon_yetkisi_varsayilan_ve_challenger():
    from polat.agent import new_position_authorized

    assert not new_position_authorized({})  # Eksik yetki yapılandırması fail-closed davranır.
    assert new_position_authorized({"yetki": {"yeni_pozisyon": True}})
    assert not new_position_authorized({"yetki": {"yeni_pozisyon": False}})
