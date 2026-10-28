"""Yürütme. İki uygulama aynı arayüzü paylaşır:

  PaperBroker → Polat'ın kendi sanal hesabı, OKX gibi davranır:
     * kontrat yuvarlama (ctVal, lotSz, minSz), izole marj, kaldıraç
     * piyasa emri gerçek emir defterinde kademe kademe dolar (kayma gerçekçi), taker ücreti
     * stop = borsa tarafı stop-market, reduce-only: işlem fiyatı tetiği geçtiği an, o anki defterden dolar
     * hedef = reduce-only limit emir: fiyat hedefin İÇİNDEN geçince maker ücretiyle dolar
     * funding kesimi gerçek orandan, likidasyon izole marj + bakım marjıyla
  OkxBroker  → OKX REST (demo: x-simulated-trading). Anahtar verilene kadar kullanılmaz. Canlı (gerçek para)
               mod kod tarafından reddedilir.
"""
from __future__ import annotations

import math
import uuid

from .market import Coin


def round_contracts(qty_base: float, meta: dict) -> float:
    ct, lot, mn = meta.get("ctVal", 1.0), meta.get("lotSz", 1.0), meta.get("minSz", 1.0)
    n = math.floor(qty_base / ct / lot + 1e-9) * lot
    return n if n >= mn else 0.0


def walk(levels: list[tuple[float, float]], qty: float) -> tuple[float | None, float]:
    """Defterde kademe kademe dolum: (ortalama fiyat, dolan miktar)."""
    got = cost = 0.0
    for px, sz in levels:
        take = min(sz, qty - got)
        got += take
        cost += take * px
        if got >= qty - 1e-12:
            break
    return (cost / got if got else None), got


class PaperBroker:
    name = "sanal"

    def __init__(self, cfg: dict, equity: float):
        y = cfg["yurutme"]
        self.taker, self.maker, self.mmr = y["ucret_taker"], y["ucret_maker"], y["bakim_marji"]
        self.cash = equity
        self.positions: dict[str, dict] = {}
        self.pending: dict[str, dict] = {}     # tetiklenmiş stop-market emirleri (dolum penceresi)
        self.stop_window_ms = y.get("stop_dolum_penceresi_ms", 250)

    # --- durum -----------------------------------------------------------------------------
    def to_state(self) -> dict:
        return {"cash": self.cash, "positions": self.positions, "pending": self.pending}

    def load_state(self, st: dict):
        self.cash = st["cash"]
        self.positions = st["positions"]
        self.pending = st.get("pending", {})

    def equity(self, coins: dict[str, Coin]) -> float:
        e = self.cash
        for p in self.positions.values():
            c = coins.get(p["inst"])
            px = c.price() if c and c.price() else p["entry"]
            e += p["margin"] + self._upnl(p, px)
        return e

    @staticmethod
    def _upnl(p, px):
        return (px - p["entry"]) * p["qty"] * (1 if p["side"] == "long" else -1)

    def liq_price(self, side: str, entry: float, lev: float) -> float:
        # izole: marj = nominal/kaldıraç; likidasyon: zarar = marj − bakım marjı
        if side == "long":
            return entry * (1 - 1 / lev + self.mmr)
        return entry * (1 + 1 / lev - self.mmr)

    # --- emirler ---------------------------------------------------------------------------
    def _fill_market(self, c: Coin, side_buy: bool, qty: float) -> tuple[float | None, float, float]:
        if c.book.ok:
            lv = c.book.levels("asks" if side_buy else "bids", 400, c.ct)
            px, got = walk(lv, qty)
            if px is None or got < qty * 0.999:
                return None, got, 0.0
        else:
            ref = c.ask if side_buy else c.bid
            if not ref:
                return None, 0.0, 0.0
            px, got = ref * (1.0002 if side_buy else 0.9998), qty     # defter yoksa +2 bp kayma varsay
        mid = (c.bid + c.ask) / 2 if c.bid and c.ask else px
        slip = abs(px / mid - 1) * 1e4 if mid else 0.0
        return px, got, slip

    def open(self, c: Coin, side: str, qty_base: float, lev: float, sl: float, tp: float, ts: int, tag: dict) -> dict:
        qty_c = round_contracts(qty_base, c.meta)
        if qty_c <= 0:
            return {"ok": False, "neden": "minimum kontrat altında"}
        qty = qty_c * c.ct
        px, got, slip = self._fill_market(c, side == "long", qty)
        if px is None:
            return {"ok": False, "neden": "defterde yeterli derinlik yok"}
        notional = px * qty
        margin = notional / lev
        fee = notional * self.taker
        if margin + fee > self.cash:
            return {"ok": False, "neden": "serbest bakiye yetersiz"}
        self.cash -= margin + fee
        pid = uuid.uuid4().hex[:10]
        p = {"id": pid, "inst": c.inst, "side": side, "qty": qty, "qty0": qty, "entry": px, "lev": lev,
             "margin": margin, "sl": sl, "tp": tp, "liq": self.liq_price(side, px, lev),
             "opened": ts, "fees": fee, "funding": 0.0, "realized": 0.0, "slip_bp": round(slip, 2),
             "last_funding_ts": c.next_funding_ts or 0, **tag}
        self.positions[pid] = p
        return {"ok": True, "pos": p}

    def amend_stop(self, pid: str, sl: float):
        if pid in self.positions:
            self.positions[pid]["sl"] = sl

    def amend_tp(self, pid: str, tp: float | None):
        if pid in self.positions:
            self.positions[pid]["tp"] = tp

    def close(self, c: Coin, pid: str, frac: float, ts: int, reason: str, px_hint: float | None = None,
              maker: bool = False, exact: bool = False) -> dict | None:
        p = self.positions.get(pid)
        if not p:
            return None
        qty = p["qty"] if frac >= 0.999 else round_contracts(p["qty"] * frac, c.meta) * c.ct
        if qty <= 0:
            return None
        if (maker or exact) and px_hint is not None:
            px = px_hint
            ref = (c.bid + c.ask) / 2 if c.bid and c.ask else px_hint
            slip = 0.0 if maker else abs(px / ref - 1) * 1e4
        else:
            px, got, slip = self._fill_market(c, p["side"] == "short", qty)
            if px is None:                     # derinlik yetmezse ipucu fiyatına 10 bp kötü
                px = (px_hint or c.price()) * (0.999 if p["side"] == "long" else 1.001)
                slip = 10.0
        fee = px * qty * (self.maker if maker else self.taker)
        pnl = (px - p["entry"]) * qty * (1 if p["side"] == "long" else -1)
        share = qty / p["qty"]
        m_ret = p["margin"] * share
        self.cash += m_ret + pnl - fee
        p["margin"] -= m_ret
        p["qty"] -= qty
        p["fees"] += fee
        p["realized"] += pnl
        ev = {"pid": pid, "inst": p["inst"], "reason": reason, "px": px, "qty": qty, "pnl": pnl, "fee": fee,
              "slip_bp": round(slip, 2), "ts": ts, "full": p["qty"] <= 1e-12}
        if ev["full"]:
            ev["pos"] = self.positions.pop(pid)
        return ev

    # --- olaylar ---------------------------------------------------------------------------
    def on_price(self, c: Coin, px: float, ts: int) -> list[dict]:
        """Her işlem fiyatında: likidasyon (mark fiyatıyla), stop, hedef.
        Gerçekçilik:
          * stop-market tetiklenince hemen o anki defterden değil, sonraki stop_dolum_penceresi_ms (250 ms) içinde
            görülen EN KÖTÜ fiyat ile defter dolumunun kötüsünden dolar (hızlı düşüşte zincirleme kayma)
          * hedef (reduce-only limit) ancak fiyat hedefin en az 1 tick ÖTESİNDE işlem görünce dolar (sıradaki emirler önce dolar)"""
        evs = []
        tick = c.meta.get("tickSz", 0.0) or 0.0
        for pid, p in list(self.positions.items()):
            if p["inst"] != c.inst:
                continue
            L = p["side"] == "long"
            pend = self.pending.get(pid)
            if pend:
                pend["worst"] = min(pend["worst"], px) if L else max(pend["worst"], px)
                if ts - pend["ts"] >= self.stop_window_ms:
                    evs += self._finish_stop(c, pid, ts)
                continue
            mk = c.mark or px
            if (L and mk <= p["liq"]) or (not L and mk >= p["liq"]):
                ev = self._liquidate(pid, ts)
                evs.append(ev)
                continue
            if p["sl"] and ((L and px <= p["sl"]) or (not L and px >= p["sl"])):
                bpx, got, _ = self._fill_market(c, not L, p["qty"])
                self.pending[pid] = {"ts": ts, "book": bpx if bpx else px, "worst": px, "trigger": p["sl"]}
                continue
            if p.get("tp") and ((L and px >= p["tp"] + tick and px > p["tp"]) or (not L and px <= p["tp"] - tick and px < p["tp"])):
                ev = self.close(c, pid, 1.0, ts, "hedef", px_hint=p["tp"], maker=True)
                if ev:
                    evs.append(ev)
        return evs

    def _finish_stop(self, c: Coin, pid: str, ts: int) -> list[dict]:
        pend = self.pending.pop(pid, None)
        p = self.positions.get(pid)
        if not pend or not p:
            return []
        L = p["side"] == "long"
        fill = min(pend["book"], pend["worst"]) if L else max(pend["book"], pend["worst"])
        ev = self.close(c, pid, 1.0, ts, "stop", px_hint=fill, exact=True)
        if ev:
            ev["tetik"] = pend["trigger"]
            ev["slip_bp"] = round(abs(fill / pend["trigger"] - 1) * 1e4, 2)
            return [ev]
        return []

    def flush(self, coins: dict, now_ms: int) -> list[dict]:
        """İşlem gelmese de dolum penceresi dolan stopları kapat (ajan 50 ms'de bir çağırır)."""
        evs = []
        for pid, pend in list(self.pending.items()):
            if now_ms - pend["ts"] >= self.stop_window_ms:
                p = self.positions.get(pid)
                c = coins.get(p["inst"]) if p else None
                if c:
                    evs += self._finish_stop(c, pid, now_ms)
                else:
                    self.pending.pop(pid, None)
        return evs

    def _liquidate(self, pid: str, ts: int) -> dict:
        p = self.positions.pop(pid)
        loss = p["margin"]                     # izole marjın tamamı gider
        ev = {"pid": pid, "inst": p["inst"], "reason": "likidasyon", "px": p["liq"], "qty": p["qty"],
              "pnl": -loss, "fee": 0.0, "slip_bp": 0.0, "ts": ts, "full": True, "pos": p}
        p["realized"] += -loss
        return ev

    def on_funding(self, c: Coin, now_ms: int) -> list[dict]:
        """Funding zamanı geçtiyse pozisyona uygula (long pozitif oranda öder)."""
        evs = []
        for p in self.positions.values():
            if p["inst"] != c.inst or c.funding is None:
                continue
            ft = p.get("last_funding_ts") or 0
            if ft and now_ms >= ft and p["opened"] < ft:
                amt = (c.mark or c.price()) * p["qty"] * c.funding * (1 if p["side"] == "long" else -1)
                self.cash -= amt
                p["funding"] += amt
                p["last_funding_ts"] = c.next_funding_ts if c.next_funding_ts > ft else ft + 8 * 3_600_000
                evs.append({"pid": p["id"], "inst": p["inst"], "funding": amt, "ts": now_ms})
            elif not ft:
                p["last_funding_ts"] = c.next_funding_ts
        return evs


class OkxBroker:
    """OKX REST adaptörü (demo). Anahtar verilmeden çalışmaz; canlı mod reddedilir.

    Not: Bu sınıf OKX'e karşı canlı test edilmedi (anahtar yok). Anahtar geldiğinde ilk iş, küçük bir demo emirle
    uçtan uca doğrulamadır. Stop'lar borsa tarafında: ordType=conditional, slOrdPx=-1 (piyasa), reduceOnly.
    """
    name = "okx_demo"

    def __init__(self, cfg: dict, rest):
        if cfg["yurutme"]["mod"] == "okx_canli":
            raise RuntimeError("Gerçek para modu kapalı: kullanıcının açık onayı olmadan açılmaz.")
        if not rest.key:
            raise RuntimeError("OKX demo anahtarı yok (POLAT_OKX_KEY / SECRET / PASSPHRASE).")
        self.rest = rest
        self.positions: dict[str, dict] = {}
        self.algo: dict[str, str] = {}

    async def setup(self, inst: str, lev: float):
        await self.rest.post("/api/v5/account/set-leverage", {"instId": inst, "lever": str(int(lev)), "mgnMode": "isolated"})

    async def open(self, c: Coin, side: str, qty_base: float, lev: float, sl: float, tp: float, ts: int, tag: dict) -> dict:
        qty_c = round_contracts(qty_base, c.meta)
        if qty_c <= 0:
            return {"ok": False, "neden": "minimum kontrat altında"}
        await self.setup(c.inst, lev)
        r = await self.rest.post("/api/v5/trade/order", {"instId": c.inst, "tdMode": "isolated",
                                                          "side": "buy" if side == "long" else "sell",
                                                          "ordType": "market", "sz": f"{qty_c:g}"})
        ord_id = r[0]["ordId"]
        d = await self.rest.get("/api/v5/trade/order", {"instId": c.inst, "ordId": ord_id}, signed=True)
        px = float(d[0].get("avgPx") or c.price())
        await self._place_stop(c.inst, side, qty_c, sl, ord_id)
        p = {"id": ord_id, "inst": c.inst, "side": side, "qty": qty_c * c.ct, "qty0": qty_c * c.ct, "entry": px,
             "lev": lev, "sl": sl, "tp": tp, "opened": ts, "fees": abs(float(d[0].get("fee") or 0)),
             "funding": 0.0, "realized": 0.0, **tag}
        self.positions[ord_id] = p
        return {"ok": True, "pos": p}

    async def _place_stop(self, inst, side, qty_c, sl, pid):
        r = await self.rest.post("/api/v5/trade/order-algo", {
            "instId": inst, "tdMode": "isolated", "side": "sell" if side == "long" else "buy",
            "ordType": "conditional", "sz": f"{qty_c:g}", "reduceOnly": True,
            "slTriggerPx": f"{sl:.10g}", "slOrdPx": "-1", "slTriggerPxType": "last"})
        self.algo[pid] = r[0]["algoId"]
