"""Salt okunur teşhis: Kaan ve Deniz neden işlem açmıyor?

Canlı sisteme, ledger'a, ayarlara dokunmaz; sadece OKX public mumlarını okuyup rapor yazar.
  Kaan : her coinde son 30 kapanmış günde 20 günlük kanal kırılımı (+1,5x hacim, 50G ortalama) kaç kez oldu,
         dün hangi koşul tutmadı, team.a_kaan gerçekten fikir üretti mi.
  Deniz: BTC/ETH 15dk son ~3 günde likidite / trend / RSI koşulları kaç mumda tuttu, team.a_deniz fikir üretti mi.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import quant, team  # noqa: E402
from radar.config import load_config  # noqa: E402
from radar.okx import OKXClient  # noqa: E402
from radar.scan import build_universe  # noqa: E402

DAYS = 30


def ok(x) -> bool:
    return x is not None and x == x


def mgr(mid):
    return next(m for m in team.ROSTER if m.id == mid)


def ctx_for(F, i, row, tf, bar_ms):
    c = quant.ctx_at(F, i)
    c.update({"price": c["close"], "inst": row["inst"], "sym": row["sym"], "tf": tf, "bar_ms": bar_ms})
    return c


def kaan(client, cfg, uni, out):
    K = mgr("kaan")
    ev = Counter()
    fails = Counter()
    rows_md = []
    recent = []
    for row in uni:
        try:
            df = client.candles(row["inst"], "1D", 300, closed_only=True)
        except Exception as e:  # noqa: BLE001
            ev["veri hatası"] += 1
            print("hata", row["sym"], e)
            continue
        if len(df) < 120:
            ev["120 günden kısa geçmiş"] += 1
            continue
        F = quant.features(df)
        n_long = n_short = n_idea = 0
        for i in range(-DAYS, 0):
            c = ctx_for(F, i, row, "1D", 86_400_000)
            if not (ok(c["hh20"]) and ok(c["sma50"]) and ok(c["atr"])):
                continue
            vr = c.get("vol_ratio") or 0
            lc = c["close"] > c["hh20"] and vr >= 1.5 and c["close"] > c["sma50"]
            sc = c["close"] < c["ll20"] and vr >= 1.5 and c["close"] < c["sma50"]
            n_long += lc
            n_short += sc
            # BTC havası bilinmiyor → short çekirdeği için iki durumu da dene (canlıda btc_bear True)
            c["btc_bear"], c["btc_bull"] = True, False
            idea = None
            try:
                idea = team.a_kaan(c, K, cfg)
            except Exception as e:  # noqa: BLE001
                ev[f"a_kaan hata: {type(e).__name__}"] += 1
            if idea:
                n_idea += 1
                recent.append((str(F.index[i].date()), row["sym"], idea["side"], idea.get("shadow_only", False)))
        # dün (son kapanmış gün) hangi koşul tutmadı
        c = ctx_for(F, -1, row, "1D", 86_400_000)
        vr = c.get("vol_ratio") or 0
        if ok(c["hh20"]):
            near = c["close"] / c["hh20"] - 1
            fails["long: 20G zirve kırılmadı"] += c["close"] <= c["hh20"]
            fails["long: hacim < 1,5x"] += vr < 1.5
            fails["long: 50G ortalamanın altında"] += c["close"] <= (c["sma50"] if ok(c["sma50"]) else 9e99)
            fails["short: 20G dip kırılmadı"] += c["close"] >= c["ll20"]
            rows_md.append((row["sym"], n_long, n_short, n_idea, near * 100, vr))
        ev["long kırılım günü"] += n_long
        ev["short kırılım günü"] += n_short
        ev["a_kaan fikir"] += n_idea
    out.append(f"## Kaan — son {DAYS} kapanmış gün, {len(uni)} coin\n")
    out.append("| Ölçü | Sayı |\n|---|---|")
    out += [f"| {k} | {v} |" for k, v in ev.items()]
    out.append("\n**Dün (son kapanmış gün) tutmayan koşul — coin sayısı**\n")
    out.append("| Koşul | Coin |\n|---|---|")
    out += [f"| {k} | {v} |" for k, v in fails.items()]
    out.append("\n**20G zirveye en yakın 15 coin (dün)**\n")
    out.append("| Coin | 30 günde long kırılım | short kırılım | a_kaan fikir | zirveye uzaklık % | hacim x |\n|---|---|---|---|---|---|")
    for s, a, b, n, d, v in sorted(rows_md, key=lambda r: -r[4])[:15]:
        out.append(f"| {s} | {a} | {b} | {n} | {d:+.1f} | {v:.2f} |")
    out.append("\n**a_kaan'ın ürettiği son 15 fikir**\n")
    out.append("| Gün | Coin | Yön | Gölge |\n|---|---|---|---|")
    for r in sorted(recent)[-15:]:
        out.append(f"| {r[0]} | {r[1]} | {r[2]} | {'evet' if r[3] else 'hayır'} |")


def deniz(client, cfg, out):
    D = mgr("deniz")
    out.append("\n## Deniz — BTC/ETH 15dk, son ~90 mum (~22 saat; 300 mumun 210'u 200'lük ortalama ısınması)\n")
    out.append("| Coin | likidite (hacim > 24s ort.) | trend yukarı (c>200, 50>200) | trend aşağı | RSI<40 | RSI>60 | "
               "long çekirdek | short çekirdek | a_deniz fikir |\n|---|---|---|---|---|---|---|---|---|")
    for sym in team.DENIZ_COINS:
        row = {"inst": f"{sym}-USDT-SWAP", "sym": sym}
        df = client.candles(row["inst"], "15m", 300, closed_only=True)
        if len(df) < 250:
            # 200 mumluk ortalama için 300 mum yetmezse geçmişten tamamla
            pass
        F = quant.features(df)
        cnt = Counter()
        for i in range(-min(288, len(F) - 210), 0):
            c = ctx_for(F, i, row, "15m", 900_000)
            if not (ok(c["sma200"]) and ok(c.get("vol_ma96"))):
                cnt["eksik veri"] += 1
                continue
            liq = c["volume"] > c["vol_ma96"]
            up = c["close"] > c["sma200"] and c["sma50"] > c["sma200"]
            dn = c["close"] < c["sma200"] and c["sma50"] < c["sma200"]
            cnt["liq"] += liq
            cnt["up"] += up
            cnt["dn"] += dn
            cnt["r40"] += c["rsi"] < 40
            cnt["r60"] += c["rsi"] > 60
            cnt["lc"] += liq and up and c["rsi"] < 40
            cnt["sc"] += liq and dn and c["rsi"] > 60
            c.update({"d_up": True, "d_dn": True, "btc_bull": True, "btc_bear": True})
            try:
                cnt["idea"] += bool(team.a_deniz(c, D, cfg))
            except Exception as e:  # noqa: BLE001
                cnt[f"hata {type(e).__name__}"] += 1
        out.append(f"| {sym} | {cnt['liq']} | {cnt['up']} | {cnt['dn']} | {cnt['r40']} | {cnt['r60']} | {cnt['lc']} | "
                   f"{cnt['sc']} | {cnt['idea']} |")
        if cnt["eksik veri"]:
            out.append(f"\n({sym}: {cnt['eksik veri']} mumda 200'lük ortalama/24s hacim için veri yetmedi)\n")


def main():
    cfg = load_config()
    client = OKXClient()
    uni, _ = build_universe(client, cfg)
    out = ["# Teşhis: Kaan ve Deniz\n"]
    kaan(client, cfg, uni, out)
    deniz(client, cfg, out)
    md = "\n".join(out)
    print(md)
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        Path(p).write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()
