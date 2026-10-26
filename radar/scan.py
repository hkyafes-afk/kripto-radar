"""Ana tarama: veri çek → özellik → sinyal → harvest → defter → bildirim → dashboard."""
from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import harvest as hv
from . import ledger as lg
from . import notify as nt
from . import other_exchanges as ox
from . import signals as sg
from . import store
from .config import load_config

VERSION = "3.0"


def _f(x, d=float("nan")):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def build_universe(client, cfg) -> tuple[list[dict], dict[str, float]]:
    t = cfg["tarama"]
    excl = {s.upper() for s in t["haric"]}
    insts = {i["instId"]: i for i in client.instruments("SWAP")
             if i.get("settleCcy") == "USDT" and i.get("state") == "live"
             and i.get("ctType", "linear") == "linear"}
    spot_vol: dict[str, float] = {}
    try:
        for x in client.tickers("SPOT"):
            iid = x.get("instId", "")
            if iid.endswith("-USDT"):
                spot_vol[iid[:-5]] = _f(x.get("volCcy24h"), 0.0)   # spot: quote cinsinden
    except Exception as e:  # noqa: BLE001
        print(f"[uyarı] spot ticker alınamadı: {e}")
    rows = []
    now_ms = time.time() * 1000
    for x in client.tickers("SWAP"):
        iid = x.get("instId", "")
        if iid not in insts or not iid.endswith("-USDT-SWAP"):
            continue
        base = iid.split("-")[0]
        if base in excl:
            continue
        last = _f(x.get("last"))
        vol_usd = _f(x.get("volCcy24h"), 0.0) * last
        if vol_usd < t["min_hacim_usd"]:
            continue
        lt = _f(insts[iid].get("listTime"), 0.0)
        if lt and now_ms - lt < 35 * 86400 * 1000:    # en az 35 günlük veri gerek
            continue
        open24 = _f(x.get("open24h"))
        rows.append({
            "inst": iid, "sym": base, "price": last, "vol24_usd": vol_usd,
            "chg24": (last / open24 - 1) * 100 if open24 and open24 == open24 else float("nan"),
            "high24": _f(x.get("high24h")), "low24": _f(x.get("low24h")),
        })
    rows.sort(key=lambda r: r["vol24_usd"], reverse=True)
    return rows[: t["coin_sayisi"]], spot_vol


def btc_info(client, cfg) -> dict:
    try:
        d1 = client.candles("BTC-USDT-SWAP", "1D", 300)
        reg = sg.btc_regime_series(d1)
        e200 = float(sg.ind.ema(d1["close"], 200).iloc[-1])
        fd = sg.daily_features(d1, cfg).iloc[-1]
        regime = int(reg.iloc[-1])
        return {"regime": regime, "ema200": e200, "close_d": float(d1["close"].iloc[-1]),
                "d_trend": int(fd["d_trend"]), "tsmom": float(fd["tsmom"]),
                "regime_text": {1: "Boğa (EMA200 üstü)", -1: "Ayı (EMA200 altı)"}.get(regime, "Bilinmiyor")}
    except Exception as e:  # noqa: BLE001
        print(f"[uyarı] BTC rejimi alınamadı: {e}")
        return {"regime": 0, "regime_text": "Bilinmiyor", "ema200": None}


def _interval_h(fr: dict) -> float:
    a, b = _f(fr.get("fundingTime")), _f(fr.get("nextFundingTime"))
    if a == a and b == b and b > a:
        h = round((b - a) / 3.6e6, 2)
        if 0.5 <= h <= 24:
            return h
    return 8.0


def fetch_funding(client, universe) -> dict[str, dict]:
    bulk = client.funding_rates_all()
    out = {}
    if bulk:
        for r in universe:
            if r["inst"] in bulk:
                out[r["inst"]] = bulk[r["inst"]]
    missing = [r["inst"] for r in universe if r["inst"] not in out]

    def one(inst):
        try:
            return inst, client.funding_rate(inst)
        except Exception as e:  # noqa: BLE001
            print(f"[uyarı] funding {inst}: {e}")
            return inst, {}

    with ThreadPoolExecutor(6) as ex:
        for inst, fr in ex.map(one, missing):
            out[inst] = fr
    return out


def oi_changes(client, universe, state, now_ms) -> tuple[dict[str, float], str]:
    """24s OI değişimi. Önce kendi saatlik anlık görüntülerimiz, yetmezse OKX geçmişi."""
    snaps = state.setdefault("oi_snap", {})
    try:
        cur = client.open_interest_all()
    except Exception as e:  # noqa: BLE001
        print(f"[uyarı] OI alınamadı: {e}")
        cur = {}
    keep_from = now_ms - 30 * 3600 * 1000
    uni = {r["inst"] for r in universe}
    for inst in list(snaps):
        if inst not in uni:
            snaps.pop(inst)
    for inst in uni:
        arr = [p for p in snaps.get(inst, []) if p[0] >= keep_from]
        if inst in cur:
            arr.append([now_ms, round(cur[inst])])
        snaps[inst] = arr[-32:]
    out, need = {}, []
    for inst in uni:
        arr = snaps.get(inst, [])
        old = [p for p in arr if now_ms - p[0] >= 20 * 3600 * 1000]
        if old and arr and old[-1][1] > 0:
            # 24 saate en yakın eski görüntü
            ref = min(old, key=lambda p: abs((now_ms - p[0]) - 24 * 3600 * 1000))
            out[inst] = (arr[-1][1] / ref[1] - 1) * 100
        else:
            need.append(inst)
    src = "anlık görüntü"
    if need:
        src = "OKX geçmişi" if len(need) == len(uni) else "karma"

        def one(inst):
            try:
                s = client.oi_history(inst, "1H", 30)
                if len(s) >= 20 and s.iloc[0] > 0:
                    ref = s[s.index <= s.index[-1] - pd.Timedelta(hours=24)]
                    base = ref.iloc[-1] if len(ref) else s.iloc[0]
                    return inst, (s.iloc[-1] / base - 1) * 100
            except Exception:  # noqa: BLE001
                pass
            return inst, None

        with ThreadPoolExecutor(4) as ex:
            for inst, v in ex.map(one, need):
                if v is not None:
                    out[inst] = v
    return out, src


def scan_coin(client, row, cfg, funding, oi_chg, setups, regime, use_taker=True) -> dict:
    inst = row["inst"]
    df4 = client.candles(inst, cfg["tarama"]["periyot"], 300)
    d1 = client.candles(inst, "1D", 120)
    if len(df4) < 120 or len(d1) < 40:
        raise ValueError(f"yetersiz veri ({len(df4)} 4S, {len(d1)} 1G)")
    f4 = sg.frame_features(df4, cfg).iloc[-1]
    fd = sg.daily_features(d1, cfg).iloc[-1]
    fr = funding.get(inst) or {}
    rate = _f(fr.get("fundingRate"))
    iv = _interval_h(fr)
    feat = {k: _f(v) for k, v in f4.items()}
    feat.update({
        "price": row["price"] if row["price"] == row["price"] else feat["close"],
        "d_trend": _f(fd["d_trend"], 0), "tsmom": _f(fd["tsmom"], 0),
        "funding_rate": rate, "interval_h": iv,
        "funding_ann": rate * (8760 / iv) * 100 if rate == rate else float("nan"),
        "oi_chg24": _f(oi_chg.get(inst)),
    })
    if row.get("chg24") == row.get("chg24"):
        feat["ret24"] = row["chg24"] / 100
    taker_src = "vekil"
    if use_taker:
        try:
            tv = client.taker_volume(inst, "1H", 48)
            if len(tv) >= 20:
                last = tv.iloc[-24:]
                buy, sell = last["buy"].sum(), last["sell"].sum()
                if buy + sell > 0:
                    delta = buy - sell
                    feat["taker_imb"] = delta / (buy + sell)
                    feat["cvd_div"] = (1 if (feat["ret24"] < -0.01 and delta > 0) else
                                       -1 if (feat["ret24"] > 0.01 and delta < 0) else 0)
                    taker_src = "gerçek"
        except Exception:  # noqa: BLE001
            pass
    sigs = sg.evaluate_coin(feat, regime, setups, cfg)
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
        "taker_src": taker_src,
        "best": {"label": best["label"], "score": best["score"], "side": best["side"]} if best else None,
    }
    return {"coin": coin, "signals": sigs, "funding_rate": rate, "interval_h": iv}


def run(site: Path, cfg_path=None, client=None, notify: bool = True, demo: bool = False) -> dict:
    t0 = time.time()
    cfg = load_config(cfg_path)
    site = Path(site)
    state = store.load(site / "state.json", {})
    ledger = store.load(site / "ledger.json", lg.empty())
    now_ms = int(time.time() * 1000)
    if client is None:
        from .okx import OKXClient
        client = OKXClient()
    setups = sg.build_setups(cfg)
    errors: list[str] = []

    universe, spot_vol = build_universe(client, cfg)
    if not universe:
        raise RuntimeError("Taranacak coin bulunamadı (borsa verisi boş).")
    btc = btc_info(client, cfg)
    btc_row = next((r for r in universe if r["sym"] == "BTC"), None)
    if btc_row:
        btc.update({"price": btc_row["price"], "chg24": btc_row["chg24"]})
    regime = btc.get("regime", 0)
    funding = fetch_funding(client, universe)
    oi_chg, oi_src = oi_changes(client, universe, state, now_ms)

    results = []
    with ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(scan_coin, client, r, cfg, funding, oi_chg, setups, regime): r
                for r in universe}
        for fu in as_completed(futs):
            r = futs[fu]
            try:
                results.append(fu.result())
            except Exception as e:  # noqa: BLE001
                errors.append(f"{r['sym']}: {e}")
    if len(results) < max(5, len(universe) // 3):
        raise RuntimeError(f"Çok az coin taranabildi ({len(results)}/{len(universe)}). "
                           f"İlk hatalar: {errors[:3]}")

    coins = sorted([x["coin"] for x in results], key=lambda c: c["vol24_usd"], reverse=True)
    all_sigs = sorted([s for x in results for s in x["signals"]], key=lambda s: s["score"], reverse=True)
    thr, watch = cfg["sinyal"]["guclu_esik"], cfg["sinyal"]["izle_esik"]
    strong = [s for s in all_sigs if s["score"] >= thr]

    # ── diğer borsalar (bağlam) ─────────────────────────────────────
    other, ex_status = ({}, {})
    if not demo and cfg["borsa"].get("funding_karsilastir"):
        other, ex_status = ox.fetch_all(cfg["borsa"]["funding_karsilastir"])

    # ── funding harvest ─────────────────────────────────────────────
    h = cfg["harvest"]
    pre = []
    for x in results:
        c = x["coin"]
        if (x["funding_rate"] == x["funding_rate"] and x["funding_rate"] > 0
                and spot_vol.get(c["sym"], 0) >= h["min_spot_hacim_usd"]
                and c["vol24_usd"] >= h["min_perp_hacim_usd"]):
            pre.append(c)
    pre.sort(key=lambda c: c["funding_ann"], reverse=True)
    pre = pre[:45]

    def fh(c):
        try:
            return c, client.funding_history(c["inst"], h["gun"])
        except Exception as e:  # noqa: BLE001
            print(f"[uyarı] funding geçmişi {c['sym']}: {e}")
            return c, None

    hrows = []
    with ThreadPoolExecutor(6) as ex:
        for c, hist in ex.map(fh, pre):
            hrows.append({
                "sym": c["sym"], "inst": c["inst"], "vol24_usd": c["vol24_usd"],
                "spot_vol_usd": spot_vol.get(c["sym"], 0), "funding_ann": c["funding_ann"],
                "interval_h": c["interval_h"], "stats": hv.funding_stats(hist, c["interval_h"]),
                "other": other.get(c["sym"]),
            })
    harvest = hv.rank_candidates(hrows, cfg)
    for c in coins:
        c["other_funding"] = other.get(c["sym"])

    # ── defter ──────────────────────────────────────────────────────
    prices = {c["inst"]: c["price"] for c in coins}

    def fetch_1h(inst):
        return client.candles(inst, "1H", 300, closed_only=False)

    closed_now = lg.update(ledger, fetch_1h, prices, cfg, now_ms)
    opened_now = lg.open_new(ledger, strong, cfg, now_ms)
    lstats = lg.stats(ledger, cfg)

    # ── çıktı ───────────────────────────────────────────────────────
    tz = ZoneInfo(cfg["bildirim"]["saat_dilimi"])
    payload = {
        "version": VERSION, "demo": demo,
        "generated_at": now_ms,
        "generated_local": datetime.fromtimestamp(now_ms / 1000, tz).strftime("%d.%m.%Y %H:%M"),
        "exchange": "OKX", "timeframe": cfg["tarama"]["periyot"],
        "duration_s": round(time.time() - t0, 1),
        "api_calls": getattr(client, "calls", None),
        "btc": btc,
        "thresholds": {"strong": thr, "watch": watch},
        "counts": {"universe": len(universe), "scanned": len(results), "errors": len(errors),
                   "signals": len(all_sigs), "strong": len(strong)},
        "signals": [s for s in all_sigs if s["raw"] >= 40][:200],
        "coins": coins,
        "harvest": harvest,
        "exchanges_status": ex_status,
        "oi_source": oi_src,
        "taker_real": sum(1 for c in coins if c["taker_src"] == "gerçek"),
        "ledger": {
            "stats": lstats,
            "open": sorted(ledger["open"], key=lambda t: t["entry_ts"], reverse=True),
            "closed": sorted(ledger["closed"], key=lambda t: t.get("exit_ts") or 0, reverse=True)[:60],
        },
        "errors": errors[:20],
        "dashboard_url": cfg["dashboard"]["url"],
        "repo": __import__("os").environ.get("GITHUB_REPOSITORY", ""),
    }

    from .dashboard import write_site
    write_site(site, payload)
    store.dump(site / "ledger.json", ledger)

    # ── bildirimler ─────────────────────────────────────────────────
    state["last_scan"] = now_ms
    state["consecutive_failures"] = 0
    muted = (state.get("mute_until") or 0) > now_ms
    if notify and not muted:
        if opened_now:
            nt.broadcast(msg_new_signals(opened_now, payload, cfg), state, cfg,
                         buttons=_dash_button(payload))
        if closed_now:
            nt.broadcast(msg_closed(closed_now), state, cfg)
        local = datetime.now(tz)
        today = local.strftime("%Y-%m-%d")
        if local.hour >= cfg["bildirim"]["gunluk_ozet_saat"] and state.get("last_daily") != today:
            nt.broadcast(msg_daily(payload, cfg), state, cfg, buttons=_dash_button(payload))
            state["last_daily"] = today
    store.dump(site / "state.json", state, compact=True)
    (site / "ozet.md").write_text(summary_md(payload), encoding="utf-8")
    print(f"✓ Tarama bitti: {len(results)}/{len(universe)} coin, {len(all_sigs)} sinyal "
          f"({len(strong)} güçlü), {len(harvest)} harvest, defter: +{len(opened_now)} açıldı "
          f"/ {len(closed_now)} kapandı, {payload['duration_s']} sn, {payload['api_calls']} istek")
    return payload


def record_failure(site: Path, err: str, cfg_path=None) -> None:
    cfg = load_config(cfg_path)
    site = Path(site)
    state = store.load(site / "state.json", {})
    state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
    state["last_error"] = err[-800:]
    if state["consecutive_failures"] == 2:
        nt.broadcast("⚠️ <b>Kripto Radar</b>: son 2 tarama başarısız oldu.\n<code>"
                     + nt.esc(err[-500:]) + "</code>\nGitHub → Actions sekmesinden logu kontrol et.",
                     state, cfg)
    store.dump(site / "state.json", state, compact=True)


# ── mesaj şablonları ──────────────────────────────────────────────────

def _dash_button(p):
    url = p.get("dashboard_url")
    return [[{"text": "📊 Dashboard'u aç", "url": url}]] if url else None


def _emoji(side):
    return "🟢" if side == "long" else "🔴"


def _sig_block(s) -> str:
    fp = nt.fmt_price
    sgn_sl = "−" if s["side"] == "long" else "+"
    sgn_tp = "+" if s["side"] == "long" else "−"
    lines = [
        f"{_emoji(s['side'])} <b>{nt.esc(s['sym'])}</b> · {s['label']} · <b>{s['score']}</b>/100"
        + (f" <i>(ham {s['raw']})</i>" if s["raw"] != s["score"] else ""),
        f"Fiyat {fp(s['entry'])} · Stop {fp(s['sl'])} ({sgn_sl}{s['sl_pct']:.1f}%) · "
        f"Hedef {fp(s['tp'])} ({sgn_tp}{s['tp_pct']:.1f}%)",
    ]
    lines += [f"<i>{nt.esc(r)}</i>" for r in s.get("reasons", [])[:4]]
    return "\n".join(lines)


def msg_new_signals(opened, p, cfg) -> str:
    mx = cfg["bildirim"]["max_sinyal_mesaj"]
    btc = p["btc"]
    head = (f"🚨 <b>Yeni güçlü sinyal{'ler' if len(opened) > 1 else ''}</b> · OKX {p['timeframe']}\n"
            f"BTC {nt.fmt_price(btc.get('price'))} · {btc.get('regime_text', '')}\n")
    blocks = [_sig_block(s) for s in sorted(opened, key=lambda s: -s["score"])[:mx]]
    more = f"\n…ve {len(opened) - mx} sinyal daha (dashboard'da)" if len(opened) > mx else ""
    foot = "\n\n<i>Deftere kağıt üstü işlem olarak yazıldı. Yatırım tavsiyesi değildir.</i>"
    return head + "\n\n".join(blocks) + more + foot


def msg_closed(closed) -> str:
    icon = {"hedef": "✅", "stop": "❌", "süre": "⏱"}
    lines = ["📒 <b>Defter güncellendi</b>"]
    for t in closed:
        lines.append(f"{icon.get(t['outcome'], '•')} {nt.esc(t['sym'])} {sg.SETUP_LABELS[t['setup']]}"
                     f" → {t['outcome']} · <b>{t['r']:+.2f}R</b> ({t['pnl_pct']:+.2f}%)")
    return "\n".join(lines)


def msg_daily(p, cfg) -> str:
    btc = p["btc"]
    st = p["ledger"]["stats"]
    o = st["overall"]
    sigs = p["signals"]
    longs = [s for s in sigs if s["side"] == "long"][:3]
    shorts = [s for s in sigs if s["side"] == "short"][:3]
    L = [f"☀️ <b>Günlük Kripto Radar özeti</b> · {p['generated_local']}",
         f"BTC {nt.fmt_price(btc.get('price'))} ({(btc.get('chg24') or 0):+.1f}%) · {btc.get('regime_text', '')}",
         f"Taranan {p['counts']['scanned']} coin · {p['counts']['strong']} güçlü sinyal", ""]
    if longs:
        L.append("🟢 <b>En iyi long</b>: " + ", ".join(f"{s['sym']} {s['score']}" for s in longs))
    if shorts:
        L.append("🔴 <b>En iyi short</b>: " + ", ".join(f"{s['sym']} {s['score']}" for s in shorts))
    if p["harvest"]:
        L.append("💰 <b>Funding harvest</b>: " + ", ".join(
            f"{h['sym']} %{h['ann_30d']:.0f}" for h in p["harvest"][:3]))
    L.append("")
    if o.get("n"):
        L.append(f"📒 <b>Defter</b>: {o['n']} kapanmış · isabet %{o['win_rate']} · "
                 f"ort. {o['avg_r']:+.2f}R · toplam {o['sum_r']:+.1f}R")
        for k, s in st["by_setup"].items():
            if s.get("n"):
                L.append(f"  • {sg.SETUP_LABELS[k]}: {s['n']} işlem, ort. {s['avg_r']:+.2f}R → {s['verdict']['text']}")
    else:
        L.append(f"📒 Defter: {st['open_count']} açık, henüz kapanmış işlem yok.")
    return "\n".join(L)


def summary_md(p) -> str:
    btc = p["btc"]
    s = p["signals"]
    st = p["ledger"]["stats"]["overall"]
    lines = [
        f"# Kripto Radar özeti — {p['generated_local']}",
        "",
        f"**BTC:** {nt.fmt_price(btc.get('price'))} ({(btc.get('chg24') or 0):+.1f}%) — {btc.get('regime_text', '')}. "
        f"{p['counts']['scanned']} coin tarandı, {p['counts']['strong']} güçlü sinyal çıktı.",
        "",
        "## Güçlü sinyaller",
    ]
    strong = [x for x in s if x["score"] >= p["thresholds"]["strong"]][:10]
    if strong:
        for x in strong:
            lines.append(f"- **{x['sym']}** {x['label']} — {x['score']}/100, fiyat {nt.fmt_price(x['entry'])}, "
                         f"stop {nt.fmt_price(x['sl'])}, hedef {nt.fmt_price(x['tp'])}")
    else:
        lines.append("- Şu an eşik üstü sinyal yok. Bu da bir bilgidir: zorlamadan beklemek iyi bir pozisyondur.")
    lines += ["", "## Funding harvest"]
    for h in p["harvest"][:5]:
        lines.append(f"- **{h['sym']}**: 30g ort. %{h['ann_30d']}/yıl, stabilite {h['stability']}, "
                     f"başabaş {h['breakeven_days']} gün")
    lines += ["", "## Defter karnesi"]
    if st.get("n"):
        lines.append(f"{st['n']} kapanmış işlem, isabet %{st['win_rate']}, ortalama {st['avg_r']:+.2f}R. "
                     f"Durum: {st['verdict']['text']}.")
    else:
        lines.append("Henüz kapanmış işlem yok; karne için en az 30 işlem gerekir.")
    lines += ["", "_Karar destek aracıdır, otomatik emir vermez, yatırım tavsiyesi değildir._"]
    return "\n".join(lines) + "\n"


def karne_md(p) -> str:
    st = p["ledger"]["stats"]
    L = [f"# Haftalık karne — {p['generated_local']}", "",
         "| Setup | İşlem | İsabet | Ort. R | Toplam R | PF | Durum |", "|---|---|---|---|---|---|---|"]
    for k, s in st["by_setup"].items():
        if s.get("n"):
            L.append(f"| {sg.SETUP_LABELS[k]} | {s['n']} | %{s['win_rate']} | {s['avg_r']:+.2f} | "
                     f"{s['sum_r']:+.1f} | {s.get('pf') or '-'} | {s['verdict']['text']} |")
        else:
            L.append(f"| {sg.SETUP_LABELS[k]} | 0 | - | - | - | - | Veri yok |")
    o = st["overall"]
    L += ["", f"**Genel:** {o.get('n', 0)} işlem" + (
        f", isabet %{o['win_rate']}, ort. {o['avg_r']:+.2f}R, toplam {o['sum_r']:+.1f}R" if o.get("n") else "")]
    return "\n".join(L) + "\n"
