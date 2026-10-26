"""Demo hesap: profesyonel kurallarla sanal işlem defteri.

Pozisyon büyüklüğü (risk bazlı):
    risk$ = özsermaye × risk% (kayıp serisinde yarıya iner)
    miktar = risk$ / |giriş − stop|      → stop'a gidilirse kayıp ≈ risk$ (komisyon hariç)
    sınırlar: pozisyon nominali ≤ özsermaye × oran, teminat ≤ boşta bakiye, OKX kontrat/lot kuralları

Portföy kuralları: toplam açık risk (ısı) ≤ limit, aynı yönde en fazla N işlem, coin başına tek pozisyon,
spread geniş ya da kitap sığsa işlem yok, girişte emir defteri derinliğine göre VWAP dolum.

İşlem yönetimi: +1R'de pozisyonun yarısı limit emirle kapanır, stop başabaşa (+ücretler) çekilir,
kalan kısım ATR katı ile iz süren stop'la takip edilir; son hedef ya da stop'ta kapanır.

Ücretler: giriş ve stop/süre/elle çıkış piyasa emri (taker), kâr alma limit emir (maker).
Funding: borsanın gerçek kesim anında, o anki açık miktar × fiyat × oran.

R = başlangıç risk birimi. Stop'a giden tam pozisyon ≈ −1R.
"""
from __future__ import annotations

import math
import time
import uuid

import numpy as np
import pandas as pd


def empty() -> dict:
    return {"version": 3, "open": [], "closed": []}


def _now_ms() -> int:
    return int(time.time() * 1000)


def demo_cfg(cfg: dict) -> dict:
    d = {"baslangic_bakiye": 1000.0, "islem_tutari_usd": 20.0, "kaldirac": 1.0,
         "max_acik_toplam": 20, "max_acik_periyot": 8, "boyut_modu": "sabit"}
    d.update(cfg.get("demo") or {})
    return d


def risk_cfg(cfg: dict) -> dict:
    r = {"risk_yuzde": 0.5, "max_pozisyon_oran": 0.35, "max_kaldirac": 5, "max_isi_yuzde": 3.0,
         "max_ayni_yon": 5, "max_ayni_yon_risk_yuzde": 2.0, "max_spread_bp": 15, "max_kayma_bp": 20,
         "kismi_kar_r": 1.0, "kismi_kar_oran": 0.5, "basabas": True, "iz_suren_atr": 2.0,
         "gunluk_kayip_yuzde": 2.0, "max_dusus_yuzde": 10.0, "kayip_serisi": 4, "seri_risk_carpan": 0.5,
         "setup_kapat_min_islem": 30, "setup_kapat_beklenti_r": -0.05, "otomatik_kapat": True}
    r.update(cfg.get("risk") or {})
    return r


def fees_cfg(cfg: dict) -> dict:
    f = {"taker_yuzde": 0.05, "maker_yuzde": 0.02, "mmr_yuzde": 0.5}
    f.update(cfg.get("ucret") or {})
    return f


def migrate(ledger: dict, cfg: dict) -> dict:
    """Eski kayıtlara eksik alanları ekler (v1 → v3)."""
    dc = demo_cfg(cfg)
    for t in ledger.get("open", []) + ledger.get("closed", []):
        t.setdefault("tf", "4H")
        if "margin_usd" not in t:
            t["margin_usd"] = float(dc["islem_tutari_usd"])
            t["lev"] = 1.0
            t["notional_usd"] = t["margin_usd"]
            t["qty"] = t["notional_usd"] / t["entry"] if t.get("entry") else 0.0
        if "pnl_pct" in t and "pnl_usd" not in t:
            t["pnl_usd"] = round(t["notional_usd"] * t["pnl_pct"] / 100, 4)
        if t.get("real"):
            t.setdefault("qty_open", t["qty"])
            t.setdefault("partials", [])
            t.setdefault("partial_pnl_usd", 0.0)
            t.setdefault("risk_usd", t["qty"] * abs(t["entry"] - t.get("sl_init", t["sl"])))
    ledger["version"] = 3
    return ledger


# ── bakiye ─────────────────────────────────────────────────────────────

def _booked(t: dict) -> float:
    """Açık pozisyonda kesinleşmiş K/Z: kısmi kârlar − giriş ücreti − ödenen funding."""
    if not t.get("real"):
        return 0.0
    return t.get("partial_pnl_usd", 0.0) - t["fee_entry_usd"] - t.get("funding_usd", 0.0)


def balance(ledger: dict, cfg: dict) -> dict:
    dc = demo_cfg(cfg)
    closed = sum(t.get("pnl_usd", 0.0) for t in ledger["closed"])
    booked = sum(_booked(t) for t in ledger["open"])
    unreal_total = sum(t.get("upnl_usd", 0.0) for t in ledger["open"])
    in_use = sum(t.get("margin_usd", 0.0) * (t.get("qty_open", t.get("qty", 1)) / (t.get("qty") or 1))
                 for t in ledger["open"])
    realized = closed + booked
    cash = dc["baslangic_bakiye"] + realized
    unreal = unreal_total - booked
    return {"start": dc["baslangic_bakiye"], "realized": realized, "unrealized": unreal,
            "cash": cash, "in_use": in_use, "free": cash - in_use, "equity": cash + unreal}


def open_risk(t: dict) -> float:
    """Pozisyonun şu anki riski: stop'a gidilirse kaybedilecek tutar (başabaşın ötesindeyse 0)."""
    if not t.get("real"):
        return t.get("notional_usd", 20.0) * t.get("sl_pct", 0) / 100
    sgn = 1 if t["side"] == "long" else -1
    return max(0.0, sgn * (t["entry"] - t["sl"]) * t.get("qty_open", t["qty"]))


# ── kontrat ve dolum ───────────────────────────────────────────────────

def round_contracts(notional: float, price: float, meta: dict | None) -> tuple[float, float, str]:
    """OKX kontrat kuralları: adet = taban(nominal / (ctVal*fiyat) / lotSz) * lotSz, en az minSz.

    Döner: (kontrat_adedi, coin_miktarı, hata_mesajı). meta yoksa coin cinsinden serbest miktar.
    """
    if not meta or not meta.get("ct_val"):
        return 0.0, notional / price, ""
    ct, lot, mn = float(meta["ct_val"]), float(meta.get("lot_sz") or 1), float(meta.get("min_sz") or 0)
    per = ct * price
    raw = notional / per
    n = math.floor(raw / lot + 1e-9) * lot
    if n < mn:
        min_val = mn * per
        if min_val > notional * 1.5:
            return 0.0, 0.0, f"en küçük lot {min_val:.2f}$ (hedeflenen {notional:.2f}$ pozisyonun çok üstünde)"
        n = mn
    n = round(n, 8)
    return n, n * ct, ""


def vwap_fill(levels: list, qty: float) -> tuple[float | None, float]:
    """Emir defteri seviyelerini [(fiyat, coin_miktarı), ...] yürüyerek ortalama dolum fiyatı.

    Döner: (vwap, doldurulan_miktar). Derinlik yetmezse doldurulabilen kadarını döner.
    """
    left, cost, got = qty, 0.0, 0.0
    for px, sz in levels:
        if left <= 0:
            break
        take = min(left, sz)
        cost += take * px
        got += take
        left -= take
    return (cost / got if got > 0 else None), got


def liq_price(side: str, entry: float, lev: float, mmr_pct: float) -> float | None:
    """İzole marjinde yaklaşık likidasyon fiyatı. 1x long'da pratikte yok."""
    if lev <= 1 and side == "long":
        return None
    m = mmr_pct / 100
    return entry * (1 - 1 / lev + m) if side == "long" else entry * (1 + 1 / lev - m)


# ── açılış ─────────────────────────────────────────────────────────────

def open_new(ledger: dict, signals: list[dict], cfg: dict, now_ms: int | None = None,
             quote=None, meta=None, skipped: list | None = None, book=None,
             risk_mult: float = 1.0, blocked: set | None = None, halted: str | None = None) -> list[dict]:
    """Güçlü sinyallerden demo pozisyon açar (profesyonel risk kurallarıyla).

    quote(inst) → (bid, ask, last) | None    : canlı kitap fiyatı
    meta(inst)  → {"ct_val","lot_sz","min_sz"}: OKX kontrat/lot kuralları
    book(inst)  → {"asks": [(px, coin)], "bids": [...]} | None : emir defteri derinliği (VWAP dolum)
    risk_mult   : kayıp serisinde risk çarpanı
    blocked     : kapatılmış (setup, tf) çiftleri
    halted      : devre kesici aktifse nedeni (yeni işlem açılmaz)
    """
    now_ms = now_ms or _now_ms()
    d, dc, fc, rc = cfg["defter"], demo_cfg(cfg), fees_cfg(cfg), risk_cfg(cfg)
    skipped = skipped if skipped is not None else []
    if halted:
        for s in signals[:5]:
            skipped.append({"sym": s["sym"], "tf": s.get("tf", "4H"), "reason": f"devre kesici: {halted}"})
        return []
    cooldown = d["tekrar_bekleme_saat"] * 3600 * 1000
    opened = []
    open_keys = {(t["inst"], t["setup"], t.get("tf", "4H")) for t in ledger["open"]}
    recent_closed = {}
    for t in ledger["closed"]:
        k = (t["inst"], t["setup"], t.get("tf", "4H"))
        recent_closed[k] = max(recent_closed.get(k, 0), t.get("exit_ts") or 0)
    tf_h = {"1m": 0.02, "5m": 0.08, "15m": 0.25, "30m": 0.5, "1H": 1, "2H": 2, "4H": 4}
    risk_mode = dc.get("boyut_modu", "risk") == "risk"
    for s in sorted(signals, key=lambda x: (-x["score"], -tf_h.get(x.get("tf", "4H"), 4))):
        tf = s.get("tf", "4H")
        k = (s["inst"], s["setup"], tf)

        def skip(reason):
            skipped.append({"sym": s["sym"], "tf": tf, "reason": reason})

        if k in open_keys or now_ms - recent_closed.get(k, 0) < cooldown:
            continue
        if any(t["inst"] == s["inst"] for t in ledger["open"]):
            continue                                  # coin başına tek pozisyon
        if blocked and (s["setup"], tf) in blocked:
            skip("bu setup/periyot negatif beklenti nedeniyle kapalı")
            continue
        if len(ledger["open"]) >= dc["max_acik_toplam"]:
            skip("açık işlem limiti dolu")
            break
        if sum(1 for t in ledger["open"] if t.get("tf", "4H") == tf) >= dc["max_acik_periyot"]:
            skip(f"{tf} periyodunda açık işlem limiti dolu")
            continue
        same_side = [t for t in ledger["open"] if t["side"] == s["side"]]
        if risk_mode and len(same_side) >= rc["max_ayni_yon"]:
            skip(f"aynı yönde ({s['side']}) en fazla {rc['max_ayni_yon']} işlem")
            continue
        bal = balance(ledger, cfg)
        equity = bal["equity"]
        # ── giriş fiyatı: canlı kitap ─────────────────────────────
        entry, sl, tp, src = s["entry"], s["sl"], s["tp"], "son fiyat"
        q = quote(s["inst"]) if quote else None
        if q:
            bid, ask, last = q
            if bid > 0 and ask > 0:
                spread_bp = (ask - bid) / ((ask + bid) / 2) * 1e4
                if spread_bp > rc["max_spread_bp"]:
                    skip(f"spread çok geniş ({spread_bp:.1f} bp)")
                    continue
            px = ask if s["side"] == "long" else bid
            if px and px > 0 and abs(px / entry - 1) < 0.02:
                shift = px - entry
                entry, sl, tp, src = px, sl + shift, tp + shift, "kitap"
        stop_dist = abs(entry - sl)
        if stop_dist <= 0:
            continue
        # ── büyüklük ───────────────────────────────────────────────
        lev = float(rc["max_kaldirac"]) if risk_mode else float(dc["kaldirac"])
        if risk_mode:
            risk_usd = equity * rc["risk_yuzde"] / 100 * risk_mult
            notional_t = min(risk_usd / stop_dist * entry, equity * rc["max_pozisyon_oran"])
        else:
            notional_t = float(dc["islem_tutari_usd"]) * float(dc["kaldirac"])
        contracts, qty, err = round_contracts(notional_t, entry, meta(s["inst"]) if meta else None)
        if err or qty <= 0:
            skip(err or "miktar sıfır")
            continue
        # ── derinlik: VWAP dolum ─────────────────────────────────
        bk = book(s["inst"]) if book else None
        slip_bp = 0.0
        if bk:
            side_levels = bk.get("asks" if s["side"] == "long" else "bids") or []
            vwap, got = vwap_fill(side_levels, qty)
            if vwap is None or got < qty * 0.999:
                skip("kitap derinliği yetersiz")
                continue
            slip_bp = abs(vwap / side_levels[0][0] - 1) * 1e4
            if slip_bp > rc["max_kayma_bp"]:
                skip(f"tahmini kayma çok yüksek ({slip_bp:.1f} bp)")
                continue
            shift = vwap - entry
            entry, sl, tp, src = vwap, sl + shift, tp + shift, "derinlik (VWAP)"
        notional = qty * entry
        margin = notional / lev
        risk_now = qty * abs(entry - sl)
        if risk_mode:
            heat = sum(open_risk(t) for t in ledger["open"])
            if heat + risk_now > equity * rc["max_isi_yuzde"] / 100:
                skip(f"portföy riski limiti (%{rc['max_isi_yuzde']:g}) dolu")
                continue
            side_risk = sum(open_risk(t) for t in same_side)
            if side_risk + risk_now > equity * rc["max_ayni_yon_risk_yuzde"] / 100:
                skip(f"aynı yön riski limiti (%{rc['max_ayni_yon_risk_yuzde']:g}) dolu")
                continue
        if bal["free"] < margin:
            skip("boşta teminat yetersiz")
            break
        fee_entry = notional * fc["taker_yuzde"] / 100
        liq = liq_price(s["side"], entry, lev, fc["mmr_yuzde"])
        sl_eff, liq_first = sl, False
        if liq is not None and ((s["side"] == "long" and liq >= sl) or (s["side"] == "short" and liq <= sl)):
            sl_eff, liq_first = liq, True
        sgn = 1 if s["side"] == "long" else -1
        r_unit = abs(entry - sl)
        tp1 = entry + sgn * rc["kismi_kar_r"] * r_unit if rc["kismi_kar_oran"] > 0 else None
        if tp1 is not None and sgn * (tp - tp1) <= 0:
            tp1 = None                                    # hedef 1R'den yakınsa kısmi kâr yok
        atr = s.get("atr") or r_unit / max(cfg["sinyal"][s["kind"]]["stop_atr"], 0.1)
        t = {
            "id": uuid.uuid4().hex[:10], "real": True, "v": 3,
            "inst": s["inst"], "sym": s["sym"], "setup": s["setup"], "side": s["side"],
            "kind": s["kind"], "tf": tf, "score": s["score"], "raw": s["raw"],
            "entry_ts": now_ms, "entry": entry, "entry_src": src, "signal_px": s["entry"], "slip_bp": round(slip_bp, 2),
            "sl": sl_eff, "sl_init": sl, "tp": tp, "tp1": tp1, "tp1_done": False, "trail": False,
            "atr": atr, "hw": entry, "liq": liq, "liq_first": liq_first,
            "sl_pct": abs(entry - sl_eff) / entry * 100, "tp_pct": abs(tp - entry) / entry * 100,
            "max_saat": s["max_saat"],
            "contracts": contracts, "qty": qty, "qty_open": qty, "margin_usd": margin, "lev": lev,
            "notional_usd": notional, "risk_usd": risk_now, "risk_pct": risk_now / equity * 100 if equity else 0,
            "fee_entry_usd": fee_entry, "funding_usd": 0.0, "funding_events": 0, "fund_done": [],
            "partials": [], "partial_pnl_usd": 0.0,
            "funding_rate": s.get("funding_rate", 0.0) or 0.0,
            "interval_h": s.get("interval_h", 8) or 8,
            "regime": s.get("regime", 0), "tags": s.get("tags", []),
            "reasons": s.get("reasons", [])[:6],
            "last": entry, "r_now": 0.0, "upnl_usd": -fee_entry, "mfe_r": 0.0, "mae_r": 0.0,
        }
        ledger["open"].append(t)
        open_keys.add(k)
        opened.append({**s, **t})
    return opened


# ── yönetim: kısmi kâr, başabaş, iz süren stop ─────────────────────────

def take_partial(t: dict, px: float, ts: int, cfg: dict) -> dict | None:
    """+1R'de pozisyonun bir kısmını limit emirle (maker) kapatır, stop'u başabaşa çeker."""
    if t.get("tp1_done") or not t.get("tp1"):
        return None
    fc, rc = fees_cfg(cfg), risk_cfg(cfg)
    sgn = 1 if t["side"] == "long" else -1
    q = t["qty_open"] * rc["kismi_kar_oran"]
    if t.get("contracts") and t["qty"] and t["contracts"] > 0:
        ct = t["qty"] / t["contracts"]                 # kontrat başına coin
        nc = math.floor(q / ct + 1e-9)
        if nc <= 0 or nc * ct >= t["qty_open"] - 1e-12:
            t["tp1_done"] = True                        # bölünemeyecek kadar küçük → kısmi yok, sadece yönetim
            t["trail"] = True
            _breakeven(t, cfg)
            return None
        q = nc * ct
    fee = q * px * fc["maker_yuzde"] / 100
    pnl = sgn * q * (px - t["entry"]) - fee
    t["qty_open"] = round(t["qty_open"] - q, 12)
    t["partials"].append({"ts": ts, "px": px, "qty": q, "fee": round(fee, 6), "pnl": round(pnl, 6)})
    t["partial_pnl_usd"] = round(t.get("partial_pnl_usd", 0.0) + pnl, 6)
    t["tp1_done"] = True
    t["trail"] = rc["iz_suren_atr"] > 0
    if rc["basabas"]:
        _breakeven(t, cfg)
    return t["partials"][-1]


def _breakeven(t: dict, cfg: dict):
    """Stop'u giriş + (giriş ve çıkış ücreti) seviyesine çeker: işlem artık kayıpla kapanamaz."""
    fc = fees_cfg(cfg)
    sgn = 1 if t["side"] == "long" else -1
    be = t["entry"] * (1 + sgn * (fc["taker_yuzde"] * 2) / 100)
    if sgn * (be - t["sl"]) > 0:
        t["sl"] = be
        t["liq_first"] = False


def trail(t: dict, best_px: float, cfg: dict) -> bool:
    """İz süren stop: en iyi fiyattan ATR × k geride. Stop sadece kâr yönüne hareket eder."""
    if not t.get("trail"):
        return False
    sgn = 1 if t["side"] == "long" else -1
    if sgn * (best_px - t.get("hw", t["entry"])) > 0:
        t["hw"] = best_px
    k = risk_cfg(cfg)["iz_suren_atr"]
    new_sl = t["hw"] - sgn * k * t["atr"]
    if sgn * (new_sl - t["sl"]) > 0:
        t["sl"] = new_sl
        return True
    return False


# ── kapanış ────────────────────────────────────────────────────────────

def close_trade(t: dict, exit_px: float, exit_ts: int, outcome: str, cfg: dict, maker: bool = False) -> dict:
    """Kalan pozisyonu kapatır. Toplam K/Z = kısmi kârlar + kalan brüt − tüm ücretler − funding."""
    if not t.get("real"):
        return _close_legacy(t, exit_px, exit_ts, outcome, cfg)
    fc = fees_cfg(cfg)
    sgn = 1 if t["side"] == "long" else -1
    q = t.get("qty_open", t["qty"])
    gross = sgn * q * (exit_px - t["entry"])
    fee_exit = q * exit_px * (fc["maker_yuzde"] if maker else fc["taker_yuzde"]) / 100
    partial = t.get("partial_pnl_usd", 0.0)
    if outcome == "likidasyon":
        pnl = partial - t["margin_usd"] * (q / t["qty"]) - t["fee_entry_usd"] - t.get("funding_usd", 0.0)
        fee_exit = 0.0
    else:
        pnl = partial + gross - t["fee_entry_usd"] - fee_exit - t.get("funding_usd", 0.0)
    risk_usd = t.get("risk_usd") or (t["qty"] * abs(t["entry"] - t.get("sl_init", t["sl"]))) or 1e-9
    hours = max(0.0, (exit_ts - t["entry_ts"]) / 3.6e6)
    fees_partial = sum(p.get("fee", 0) for p in t.get("partials", []))
    t.update({
        "exit_ts": exit_ts, "exit": exit_px, "outcome": outcome, "exit_liq": "maker" if maker else "taker",
        "gross_usd": round(gross + sum(sgn * p["qty"] * (p["px"] - t["entry"]) for p in t.get("partials", [])), 4),
        "fee_exit_usd": round(fee_exit, 4),
        "fee_usd": round(t["fee_entry_usd"] + fee_exit + fees_partial, 4),
        "pnl_usd": round(pnl, 4), "pnl_pct": round(pnl / t["notional_usd"] * 100, 3),
        "roi_pct": round(pnl / t["margin_usd"] * 100, 2),
        "r": round(pnl / risk_usd, 3), "hours": round(hours, 2), "qty_open": 0.0,
    })
    for k in ("last", "r_now", "upnl_usd", "fund_done"):
        t.pop(k, None)
    return t


def _close_legacy(t: dict, exit_px: float, exit_ts: int, outcome: str, cfg: dict) -> dict:
    d = cfg["defter"]
    sgn = 1 if t["side"] == "long" else -1
    gross = sgn * (exit_px / t["entry"] - 1) * 100
    hours = max(0.0, (exit_ts - t["entry_ts"]) / 3.6e6)
    n_fund = int(hours // t["interval_h"])
    funding_cost = sgn * t["funding_rate"] * n_fund * 100   # long pozitif funding öder
    net = gross - d["komisyon_gidis_donus"] - d["kayma"] - funding_cost
    r = net / t["sl_pct"] if t["sl_pct"] else 0.0
    notional = t.get("notional_usd", 20.0)
    t.update({
        "exit_ts": exit_ts, "exit": exit_px, "outcome": outcome,
        "pnl_pct": round(net, 3), "r": round(r, 3), "hours": round(hours, 2),
        "pnl_usd": round(notional * net / 100, 4),
        "roi_pct": round(notional * net / 100 / t.get("margin_usd", notional) * 100, 2),
        "fee_usd": round(notional * (d["komisyon_gidis_donus"] + d["kayma"]) / 100, 4),
        "funding_usd": round(notional * funding_cost / 100, 4),
    })
    for k in ("last", "r_now", "upnl_usd"):
        t.pop(k, None)
    return t


_close = close_trade   # geriye dönük uyumluluk


def apply_funding(ledger: dict, inst: str, rate: float, settle_ts: int, price: float) -> list[tuple[dict, float]]:
    """Funding kesim anı geçtiğinde o coindeki açık pozisyonlara gerçek ödemeyi uygular.

    Long, pozitif oranda öder (maliyet +); short alır (maliyet −). Ödeme = açık miktar × fiyat × oran.
    """
    out = []
    for t in ledger["open"]:
        if t["inst"] != inst or not t.get("real") or t["entry_ts"] >= settle_ts:
            continue
        done = t.setdefault("fund_done", [])
        if settle_ts in done:
            continue
        sgn = 1 if t["side"] == "long" else -1
        pay = sgn * t.get("qty_open", t["qty"]) * price * rate
        t["funding_usd"] = round(t.get("funding_usd", 0.0) + pay, 6)
        t["funding_events"] = t.get("funding_events", 0) + 1
        done.append(settle_ts)
        del done[:-6]
        out.append((t, pay))
    return out


def upnl(t: dict, px: float, cfg: dict | None = None) -> float:
    """'Şimdi kapatsam net ne olur': kısmi kârlar + kalan brüt − giriş ücreti − funding − tahmini çıkış ücreti."""
    sgn = 1 if t["side"] == "long" else -1
    if t.get("real"):
        q = t.get("qty_open", t["qty"])
        taker = (fees_cfg(cfg)["taker_yuzde"] if cfg else 0.05) / 100
        return (t.get("partial_pnl_usd", 0.0) + sgn * q * (px - t["entry"]) - t["fee_entry_usd"]
                - t.get("funding_usd", 0.0) - q * px * taker)
    return t.get("notional_usd", 20.0) * sgn * (px / t["entry"] - 1)


_upnl = upnl


def update(ledger: dict, fetch, prices: dict[str, float], cfg: dict,
           now_ms: int | None = None, bar_ms: int = 3600_000) -> list[dict]:
    """Açık pozisyonları mumlarla kontrol eder (canlı akışın kaçırdığı anlar için yedek).

    fetch(inst) → DataFrame(open/high/low/close, UTC index = mum açılışı), bar süresi bar_ms.
    Aynı mumda hem stop hem hedef görüldüyse muhafazakâr olarak STOP sayılır.
    Mumdan tespit edilen stop'ta gerçek dolum bilinmediği için stop fiyatına kayma eklenir.
    """
    now_ms = now_ms or _now_ms()
    slip = cfg["defter"].get("kayma", 0.05) / 100
    closed, still = [], []
    for t in ledger["open"]:
        sgn = 1 if t["side"] == "long" else -1
        deadline = t["entry_ts"] + t["max_saat"] * 3600 * 1000
        try:
            df = fetch(t["inst"])
        except Exception as e:  # noqa: BLE001
            print(f"[defter] {t['inst']} mum alınamadı: {e}")
            df = None
        result = None
        risk = abs(t["entry"] - t.get("sl_init", t["sl"])) or 1e-12
        if df is not None and len(df):
            ts_ms = ((df.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy()
            mask = (ts_ms >= t["entry_ts"]) & (ts_ms < deadline)
            sub = df[mask]
            sub_ts = ts_ms[mask]
            for (_, row), bts in zip(sub.iterrows(), sub_ts):
                fav = (row["high"] - t["entry"]) if sgn > 0 else (t["entry"] - row["low"])
                adv = (t["entry"] - row["low"]) if sgn > 0 else (row["high"] - t["entry"])
                t["mfe_r"] = round(max(t.get("mfe_r", 0.0), fav / risk), 2)
                t["mae_r"] = round(max(t.get("mae_r", 0.0), adv / risk), 2)
                bar_end = min(int(bts) + bar_ms, now_ms)
                hit_sl = row["low"] <= t["sl"] if sgn > 0 else row["high"] >= t["sl"]
                if hit_sl:
                    kind = "likidasyon" if t.get("liq_first") else ("iz stop" if t.get("trail") and t.get("tp1_done") else "stop")
                    result = (t["sl"] * (1 - sgn * slip), bar_end, kind, False)
                    break
                if t.get("real") and t.get("tp1") and not t.get("tp1_done"):
                    if (row["high"] > t["tp1"]) if sgn > 0 else (row["low"] < t["tp1"]):
                        take_partial(t, t["tp1"], bar_end, cfg)
                best = row["high"] if sgn > 0 else row["low"]
                if t.get("real"):
                    trail(t, best, cfg)
                # limit emir: fiyatın hedefin İÇİNDEN geçmesi gerekir (dokunmak yetmez)
                hit_tp = row["high"] > t["tp"] if sgn > 0 else row["low"] < t["tp"]
                if hit_tp:
                    result = (t["tp"], bar_end, "hedef", True)
                    break
            if result is None and now_ms >= deadline:
                px = prices.get(t["inst"]) or (float(sub["close"].iloc[-1]) if len(sub) else None)
                if px:
                    result = (px * (1 - sgn * slip), min(now_ms, deadline + bar_ms), "süre", False)
        if result is None and now_ms >= deadline + 6 * 3600 * 1000:
            px = prices.get(t["inst"], t.get("last", t["entry"]))
            result = (px, now_ms, "süre", False)
        if result:
            closed.append(close_trade(t, result[0], result[1], result[2], cfg, maker=result[3]))
        else:
            px = prices.get(t["inst"])
            if px:
                t["last"] = px
                t["r_now"] = round(sgn * (px - t["entry"]) / risk, 2)
                t["upnl_usd"] = round(upnl(t, px, cfg), 4)
            still.append(t)
    ledger["open"] = still
    ledger["closed"].extend(closed)
    return closed


def mark(ledger: dict, prices: dict[str, float], cfg: dict | None = None) -> None:
    """Açık pozisyonların anlık K/Z'sini günceller (mum çekmeden)."""
    for t in ledger["open"]:
        px = prices.get(t["inst"])
        if not px:
            continue
        sgn = 1 if t["side"] == "long" else -1
        risk = abs(t["entry"] - t.get("sl_init", t["sl"])) or 1e-12
        t["last"] = px
        t["r_now"] = round(sgn * (px - t["entry"]) / risk, 2)
        t["upnl_usd"] = round(upnl(t, px, cfg), 4)
        fav, adv = sgn * (px - t["entry"]) / risk, -sgn * (px - t["entry"]) / risk
        t["mfe_r"] = round(max(t.get("mfe_r", 0.0), fav), 2)
        t["mae_r"] = round(max(t.get("mae_r", 0.0), adv), 2)


def near_levels(ledger: dict, prices: dict[str, float]) -> list[dict]:
    out = []
    for t in ledger["open"]:
        px = prices.get(t["inst"])
        if not px:
            continue
        if t["side"] == "long" and (px <= t["sl"] or px >= t["tp"]):
            out.append(t)
        elif t["side"] == "short" and (px >= t["sl"] or px <= t["tp"]):
            out.append(t)
    return out


# ── istatistik ─────────────────────────────────────────────────────────

def _streaks(rs: list[float]) -> tuple[int, int, int]:
    """(en uzun kazanç serisi, en uzun kayıp serisi, şu anki seri: +kazanç / −kayıp)."""
    best_w = best_l = cur = 0
    for r in rs:
        if r > 0:
            cur = cur + 1 if cur > 0 else 1
        else:
            cur = cur - 1 if cur < 0 else -1
        best_w, best_l = max(best_w, cur), max(best_l, -cur)
    return best_w, best_l, cur


def _summ(trs: list[dict]) -> dict:
    if not trs:
        return {"n": 0}
    trs = sorted(trs, key=lambda t: t.get("exit_ts") or 0)
    r = np.array([t["r"] for t in trs], dtype=float)
    pnl = np.array([t["pnl_pct"] for t in trs], dtype=float)
    usd = np.array([t.get("pnl_usd", 0.0) for t in trs], dtype=float)
    wins = r > 0
    gp, gl = r[wins].sum(), -r[~wins].sum()
    aw = float(usd[usd > 0].mean()) if (usd > 0).any() else 0.0
    al = float(usd[usd <= 0].mean()) if (usd <= 0).any() else 0.0
    bw, bl, cur = _streaks(list(r))
    return {
        "n": int(len(r)),
        "win_rate": round(float(wins.mean()) * 100, 1),
        "avg_r": round(float(r.mean()), 3),
        "sum_r": round(float(r.sum()), 2),
        "pf": round(float(gp / gl), 2) if gl > 0 else None,
        "avg_pnl": round(float(pnl.mean()), 2),
        "pnl_usd": round(float(usd.sum()), 2),
        "avg_win_usd": round(aw, 3), "avg_loss_usd": round(al, 3),
        "payoff": round(aw / abs(al), 2) if al else None,
        "best_r": round(float(r.max()), 2), "worst_r": round(float(r.min()), 2),
        "avg_hours": round(float(np.mean([t.get("hours", 0) for t in trs])), 1),
        "max_win_streak": bw, "max_loss_streak": bl, "streak": cur,
    }


def verdict(s: dict, min_n: int) -> dict:
    n = s.get("n", 0)
    if n < min_n:
        return {"code": "collect", "text": f"Veri toplanıyor ({n}/{min_n})"}
    if s["avg_r"] > 0.1 and (s.get("pf") or 0) > 1.2:
        return {"code": "good", "text": "Olumlu: pozitif beklenti"}
    if s["avg_r"] < 0:
        return {"code": "bad", "text": "Olumsuz: bu setup'a güvenme"}
    return {"code": "meh", "text": "Belirsiz: başabaş civarı"}


def perf_metrics(eq_hist: list, start: float) -> dict:
    """Özsermaye eğrisinden profesyonel metrikler (günlük getirilerden)."""
    if not eq_hist or len(eq_hist) < 2:
        return {}
    s = pd.Series([e for _, e in eq_hist], index=pd.to_datetime([t for t, _ in eq_hist], unit="ms", utc=True))
    peak = s.cummax()
    dd = (s / peak - 1) * 100
    out = {"max_dd_pct": round(float(dd.min()), 2), "dd_now_pct": round(float(dd.iloc[-1]), 2),
           "peak": round(float(peak.iloc[-1]), 2)}
    daily = s.resample("1D").last().dropna()
    rets = daily.pct_change().dropna()
    out["days"] = int(len(daily))
    if len(rets) >= 5 and rets.std() > 0:
        out["sharpe"] = round(float(rets.mean() / rets.std() * math.sqrt(365)), 2)
        neg = rets[rets < 0]
        if len(neg) >= 2 and neg.std() > 0:
            out["sortino"] = round(float(rets.mean() / neg.std() * math.sqrt(365)), 2)
        span_days = max((s.index[-1] - s.index[0]).total_seconds() / 86400, 1)
        cagr = (s.iloc[-1] / start) ** (365 / span_days) - 1 if start else 0
        if out["max_dd_pct"] < 0:
            out["calmar"] = round(float(cagr * 100 / abs(out["max_dd_pct"])), 2)
    return out


def stats(ledger: dict, cfg: dict, tfs: list[str] | None = None, eq_hist: list | None = None) -> dict:
    min_n = cfg["defter"]["min_ornek"]
    closed = ledger["closed"]
    out = {"overall": _summ(closed), "by_setup": {}, "by_score": {}, "by_tag": [], "by_tf": {},
           "by_setup_tf": {}, "open_count": len(ledger["open"])}
    out["overall"]["verdict"] = verdict(out["overall"], min_n)
    for key in ("trend_long", "trend_short", "donus_long", "donus_short"):
        s = _summ([t for t in closed if t["setup"] == key])
        s["verdict"] = verdict(s, min_n)
        s["open"] = sum(1 for t in ledger["open"] if t["setup"] == key)
        out["by_setup"][key] = s
    for tf in (tfs or sorted({t.get("tf", "4H") for t in closed + ledger["open"]})):
        s = _summ([t for t in closed if t.get("tf", "4H") == tf])
        s["verdict"] = verdict(s, min_n)
        s["open"] = sum(1 for t in ledger["open"] if t.get("tf", "4H") == tf)
        out["by_tf"][tf] = s
    for key in {(t["setup"], t.get("tf", "4H")) for t in closed}:
        s = _summ([t for t in closed if (t["setup"], t.get("tf", "4H")) == key])
        out["by_setup_tf"][f"{key[0]}|{key[1]}"] = {k: s.get(k) for k in ("n", "avg_r", "pnl_usd", "win_rate")}
    for lo, hi, name in ((0, 75, "65-74"), (75, 85, "75-84"), (85, 101, "85+")):
        out["by_score"][name] = _summ([t for t in closed if lo <= t["score"] < hi])
    tags = sorted({tg for t in closed for tg in t.get("tags", [])})
    for tg in tags:
        with_t = [t["r"] for t in closed if tg in t.get("tags", [])]
        without = [t["r"] for t in closed if tg not in t.get("tags", [])]
        if len(with_t) >= 5:
            out["by_tag"].append({
                "tag": tg, "n": len(with_t), "avg_r": round(float(np.mean(with_t)), 3),
                "avg_r_without": round(float(np.mean(without)), 3) if without else None,
            })
    out["by_tag"].sort(key=lambda x: x["avg_r"], reverse=True)
    cut = _now_ms() - 30 * 86400 * 1000
    out["last30"] = _summ([t for t in closed if (t.get("exit_ts") or 0) >= cut])
    eq, cum, eq_usd = [], 0.0, []
    start = demo_cfg(cfg)["baslangic_bakiye"]
    cum_usd = 0.0
    for t in sorted(closed, key=lambda x: x.get("exit_ts") or 0):
        cum += t["r"]
        cum_usd += t.get("pnl_usd", 0.0)
        eq.append([t.get("exit_ts"), round(cum, 2)])
        eq_usd.append([t.get("exit_ts"), round(start + cum_usd, 2)])
    out["equity"] = eq[-300:]
    b = balance(ledger, cfg)
    heat = sum(open_risk(t) for t in ledger["open"])
    out["demo"] = {
        **{k: round(v, 2) for k, v in b.items()},
        "return_pct": round((b["equity"] / b["start"] - 1) * 100, 2) if b["start"] else 0.0,
        "trade_size": demo_cfg(cfg)["islem_tutari_usd"], "lev": demo_cfg(cfg)["kaldirac"],
        "mode": demo_cfg(cfg).get("boyut_modu", "risk"), "risk_pct": risk_cfg(cfg)["risk_yuzde"],
        "heat_usd": round(heat, 2), "heat_pct": round(heat / b["equity"] * 100, 2) if b["equity"] else 0,
        "long_n": sum(1 for t in ledger["open"] if t["side"] == "long"),
        "short_n": sum(1 for t in ledger["open"] if t["side"] == "short"),
        "gross_usd": round(sum(t.get("qty_open", t.get("qty", 0)) * t.get("last", t["entry"]) for t in ledger["open"]), 2),
        "equity_curve": eq_usd[-400:],
        "fees_usd": round(sum(t.get("fee_usd", 0.0) for t in closed)
                          + sum(t.get("fee_entry_usd", 0.0) for t in ledger["open"]), 2),
        "funding_usd": round(sum(t.get("funding_usd", 0.0) for t in closed + ledger["open"]), 2),
    }
    out["perf"] = perf_metrics(eq_hist or [], start)
    return out
