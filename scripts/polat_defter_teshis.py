"""Polat emir defteri teşhisi: gerçek OKX 'books' akışı, Polat'ın kendi OrderBook sınıfıyla.

Ölçülen:
  1) İlk snapshot'ın checksum'ı tutuyor mu?
  2) Sonraki güncellemelerin kaçı tutuyor?
  3) Polat'ın kurtarma yolu (aynı kanala tekrar 'subscribe', unsubscribe olmadan) yeni snapshot getiriyor mu?
  4) Doğru kurtarma (önce unsubscribe, onay, sonra subscribe) yeni snapshot getiriyor mu?
Yalnızca halka açık piyasa verisi okunur. Emir yok, Polat'ın çalışan koşusuna dokunulmaz.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from polat.market import OrderBook  # noqa: E402
from polat.wsclient import WS, dumps, loads  # noqa: E402

URL = "wss://ws.okx.com:443/ws/v5/public"
INSTS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
OUT: dict = {"olaylar": [], "coin": {}}


def variant_checksums(book: OrderBook) -> dict:
    """Aynı defter için olası checksum biçimleri (hangisi OKX ile eşleşiyor, görmek için)."""
    b, a = book.top(25)

    def crc(parts):
        v = zlib.crc32(":".join(parts).encode()) & 0xFFFFFFFF
        return v - 2 ** 32 if v >= 2 ** 31 else v

    inter = []
    for i in range(max(len(b), len(a))):
        if i < len(b):
            inter += [b[i][1][0], b[i][1][1]]
        if i < len(a):
            inter += [a[i][1][0], a[i][1][1]]
    bids_then_asks = [x for lv in b for x in lv[1]] + [x for lv in a for x in lv[1]]
    return {"polat_bicimi": crc(inter), "once_alis_sonra_satis": crc(bids_then_asks)}


async def main(seconds: float = 45.0):
    ws = await WS.connect(URL)
    await ws.send_text(dumps({"op": "subscribe", "args": [{"channel": "books", "instId": i} for i in INSTS]}))
    books = {i: OrderBook() for i in INSTS}
    st = {i: {"snapshot": 0, "update": 0, "ok": 0, "fail": 0, "ilk_snapshot_ok": None,
              "ilk_fail_ornek": None, "ham_ilk_mesajlar": []} for i in INSTS}
    t0 = time.time()
    phase = 0
    while time.time() - t0 < seconds:
        el = time.time() - t0
        if phase == 0 and el > 15:                       # Polat'ın kurtarma yolu: tekrar subscribe
            await ws.send_text(dumps({"op": "subscribe", "args": [{"channel": "books", "instId": INSTS[0]}]}))
            OUT["olaylar"].append({"t": round(el, 1), "olay": "tekrar_subscribe (unsubscribe yok)", "inst": INSTS[0]})
            phase = 1
        elif phase == 1 and el > 28:                     # doğru kurtarma: unsubscribe → subscribe
            await ws.send_text(dumps({"op": "unsubscribe", "args": [{"channel": "books", "instId": INSTS[1]}]}))
            OUT["olaylar"].append({"t": round(el, 1), "olay": "unsubscribe", "inst": INSTS[1]})
            await asyncio.sleep(1.0)
            await ws.send_text(dumps({"op": "subscribe", "args": [{"channel": "books", "instId": INSTS[1]}]}))
            OUT["olaylar"].append({"t": round(time.time() - t0, 1), "olay": "subscribe", "inst": INSTS[1]})
            phase = 2
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
        except asyncio.TimeoutError:
            continue
        if raw == "pong":
            continue
        msg = loads(raw)
        if "event" in msg:
            OUT["olaylar"].append({"t": round(time.time() - t0, 1), "olay": "sunucu:" + str(msg.get("event")),
                                   "kod": msg.get("code"), "mesaj": (msg.get("msg") or "")[:120],
                                   "arg": msg.get("arg")})
            continue
        inst = (msg.get("arg") or {}).get("instId")
        if inst not in books:
            continue
        action = msg.get("action", "snapshot")
        s = st[inst]
        if len(s["ham_ilk_mesajlar"]) < 3:
            s["ham_ilk_mesajlar"].append(raw[:4000] if isinstance(raw, str) else raw[:4000].decode(errors="replace"))
        for d in msg.get("data") or []:
            is_snap = action == "snapshot"
            s["snapshot" if is_snap else "update"] += 1
            if is_snap:
                OUT["olaylar"].append({"t": round(time.time() - t0, 1), "olay": "snapshot_geldi", "inst": inst})
            ok = books[inst].apply(d, is_snap)
            if is_snap and s["ilk_snapshot_ok"] is None:
                s["ilk_snapshot_ok"] = ok
                s["ilk_snapshot_checksum"] = {"okx": d.get("checksum"), **variant_checksums(books[inst]),
                                              "alis_kademe": len(books[inst].bids), "satis_kademe": len(books[inst].asks),
                                              "seqId": d.get("seqId"), "prevSeqId": d.get("prevSeqId")}
            s["ok" if ok else "fail"] += 1
            if not ok and s["ilk_fail_ornek"] is None:
                s["ilk_fail_ornek"] = {"action": action, "okx": d.get("checksum"), **variant_checksums(books[inst]),
                                       "seqId": d.get("seqId"), "prevSeqId": d.get("prevSeqId")}
    await ws.close()
    OUT["coin"] = st
    Path("defter_teshis.json").write_text(json.dumps(OUT, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: {kk: v[kk] for kk in ("snapshot", "update", "ok", "fail", "ilk_snapshot_ok")}
                      for k, v in st.items()}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
