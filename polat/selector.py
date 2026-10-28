"""Coin seçici: bütün USDT vadeli piyasayı izler, yüksek ve yükselen hacme göre en iyi N coini derin okumaya alır.

Puan = ağırlık_hacim × (24s hacim sırası yüzdeliği) + ağırlık_ivme × (hacim ivmesi sırası yüzdeliği)
  hacim ivmesi = son kapanmış 1S mumun hacmi / önceki 24 mumun ortalaması
Histerezis: listedeki coin ancak cikis_sirasi'nin dışına düşünce çıkar (sürekli girip çıkma olmaz).
Açık pozisyonu olan coin listeden asla çıkmaz. Sabit referanslar (BTC, ETH) hep listededir.
"""
from __future__ import annotations


def _pct_rank(vals: dict[str, float]) -> dict[str, float]:
    """Yüzdelik sıra; eşit değerler ortalama sırayı alır (sıralama keyfine göre avantaj olmaz)."""
    items = sorted(vals.items(), key=lambda x: x[1])
    n = len(items)
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        r = ((i + 1) + (j + 1)) / 2 / n
        for k in range(i, j + 1):
            out[items[k][0]] = r
        i = j + 1
    return out


def volume_accel(h1: list) -> float | None:
    closed = [b for b in h1 if b[7]] if h1 and len(h1[0]) > 7 else list(h1 or [])
    if len(closed) < 8:
        return None
    last = closed[-1][6]
    prev = [b[6] for b in closed[-25:-1]]
    avg = sum(prev) / len(prev) if prev else 0
    return last / avg if avg else None


def rank(coins: dict, h1_all: dict, cfg: dict) -> list[tuple[str, float, dict]]:
    sc = cfg["secici"]
    vol, acc, info = {}, {}, {}
    for inst, c in coins.items():
        if c.vol24_usd < sc["min_hacim_usd"]:
            continue
        sp = c.spread_bp()
        if sp is not None and sp > sc["max_spread_bp"]:
            continue
        vol[inst] = c.vol24_usd
        a = volume_accel(list(h1_all.get(inst) or []))
        acc[inst] = a if a is not None else 1.0
        info[inst] = {"hacim24": round(c.vol24_usd), "ivme": round(acc[inst], 2), "spread_bp": None if sp is None else round(sp, 2)}
    rv, ra = _pct_rank(vol), _pct_rank(acc)
    out = []
    for inst in vol:
        s = sc["agirlik_hacim"] * rv[inst] + sc["agirlik_ivme"] * ra[inst]
        out.append((inst, s, info[inst]))
    return sorted(out, key=lambda x: -x[1])


def choose(ranked: list, current: set[str], keep: set[str], cfg: dict) -> tuple[set[str], list[dict]]:
    sc = cfg["secici"]
    n, exit_rank = sc["izlenen"], sc["cikis_sirasi"]
    pos = {inst: i for i, (inst, _, _) in enumerate(ranked)}
    chosen = set(sc.get("sabit", [])) | set(keep)
    for inst in current:                                   # histerezis
        if pos.get(inst, 10 ** 9) < exit_rank:
            chosen.add(inst)
    for inst, _, _ in ranked:
        if len(chosen) >= n:
            break
        chosen.add(inst)
    # tavan n: zorunlular (sabit + açık pozisyon) her zaman kalır, kalan yerler en iyi sıradakilere
    if len(chosen) > n:
        must = set(sc.get("sabit", [])) | set(keep)
        rest = sorted((x for x in chosen if x not in must), key=lambda x: pos.get(x, 10 ** 9))
        chosen = must | set(rest[:max(n - len(must), 0)])
    changes = [{"inst": x, "olay": "girdi"} for x in sorted(chosen - current)] + \
              [{"inst": x, "olay": "cikti"} for x in sorted(current - chosen)]
    return chosen, changes
