"""Genel Müdür (v9 Faz 0): bütün yöneticilerin üstündeki tek defter ve tek beyin.

Yöneticiler ayrı departmanlar olarak kalır (farklı tarz = çeşitlendirme); Genel Müdür firmanın toplam resmine bakar:
  - Çatışma Hakemi: aynı coinde bir yönetici gerçek pozisyondayken başka yönetici TERS yönde gerçek işlem açamaz
    (OKX tek hesapta long ve short'u birbirinden düşer; iki kat komisyon, sıfır net pozisyon). Yeni gelen gölgede izlenir.
  - Coin risk tavanı: bir coinde tüm yöneticilerin toplam riski firma sermayesinin %X'ini geçemez. Tavana yaklaşan
    fikir küçültülür; anlamlı boyut kalmıyorsa gölgede izlenir.
  - Coin kanıt sınıfı (yetkinlik çemberi): A = 3 yıldan eski (tam boyut), B = 1–3 yıl (yarım boyut),
    C = 1 yıldan yeni listeleme (gölge). Kanıt: 2025 Binance listelemeleri Mart 2026'da ortalama −%71 (Messari);
    OKX vadeli listelemelerinin %71'i zirvesini ilk gün gördü (BitMEX 2025 Ç1).
    Sınıflar öğrenir: bir yöneticinin C işlemleri gölgede 30+ işlemde kendini kanıtlarsa C onun için yarım boyuta,
    B işlemleri kanıtlarsa B tam boyuta çıkar; kaybettirirse bir basamak iner.
  - Denetçi: tarz dışı işlem, ters pozisyon, tavan aşımı ve döngü hızını raporlar.
Öğrenme asla yavaşlamaz: Genel Müdür'ün reddettiği ya da gölgeye aldığı her fikir gölgede takip edilir.
"""
from __future__ import annotations

import time

from . import ledger as lg

CLASS_STEPS = ["golge", "yarim", "tam"]
STEP_MULT = {"golge": 0.0, "yarim": 0.5, "tam": 1.0}
CLASS_DEFAULT = {"A": "tam", "B": "yarim", "C": "golge"}
CLASS_TEXT = {"A": "A (3 yıl+ geçmiş)", "B": "B (1–3 yıl)", "C": "C (1 yıldan yeni)"}
DAY = 86_400_000


def coin_class(meta: dict | None, now_ms: int, fc: dict) -> str:
    if meta is None:
        return "A"                               # çevrimdışı (test/backtest): sınıf bilgisi yok, ölçek uygulanmaz
    lt = float(meta.get("list_ts") or 0)
    if lt <= 0:
        return "B"                               # borsa listeleme tarihini vermediyse temkinli: yarım boyut
    age = (now_ms - lt) / DAY
    if age >= fc.get("sinif_a_gun", 1095):
        return "A"
    if age >= fc.get("sinif_c_gun", 365):
        return "B"
    return "C"


def class_state(desk, mid: str, cls: str) -> str:
    st = ((desk.st.get("classes") or {}).get(f"{mid}|{cls}") or {}).get("state")
    return st or CLASS_DEFAULT[cls]


def class_tick(desk, ledger: dict, since_ms: int, now_ms: int, min_n: int = 30) -> list[str]:
    """Sınıf öğrenmesi (saatlik): yönetici × coin sınıfı için gerçek + gölge sonuçlar (R boyuttan bağımsızdır)."""
    segs = desk.st.setdefault("classes", {})
    groups: dict[str, list] = {}
    for t in ledger["closed"]:
        cls = t.get("coin_class")
        if not cls or not t.get("mgr") or t.get("carry") or (t.get("exit_ts") or 0) < since_ms:
            continue
        groups.setdefault(f"{t['mgr']}|{cls}", []).append(t["r"])
    msgs = []
    for key, rs in groups.items():
        mid, cls = key.split("|")
        n = len(rs)
        avg = sum(rs) / n
        gl = -sum(r for r in rs if r <= 0)
        pf = (sum(r for r in rs if r > 0) / gl) if gl > 0 else 9.0
        cur = class_state(desk, mid, cls)
        rec = segs.setdefault(key, {})
        rec.update({"n": n, "avg_r": round(avg, 3), "pf": round(pf, 2), "checked": now_ms})
        if n < min_n:
            continue
        i = CLASS_STEPS.index(cur)
        m = desk.by_id.get(mid)
        name = m.name if m else mid
        if avg >= 0.10 and pf >= 1.2 and i < len(CLASS_STEPS) - 1 and now_ms - (rec.get("since") or 0) >= 7 * DAY:
            rec["state"], rec["since"] = CLASS_STEPS[i + 1], now_ms
            msgs.append(f"📈 <b>Genel Müdür</b>: {name}, {CLASS_TEXT[cls]} coinlerde kendini kanıtladı "
                        f"({n} işlem, ort. {avg:+.2f}R, PF {pf:.2f}) → {CLASS_STEPS[i + 1]} boyut.")
        elif (avg <= -0.10 or pf < 0.8) and i > 0 and now_ms - (rec.get("since") or 0) >= 7 * DAY:
            rec["state"], rec["since"] = CLASS_STEPS[i - 1], now_ms
            msgs.append(f"📉 <b>Genel Müdür</b>: {name}, {CLASS_TEXT[cls]} coinlerde kaybettiriyor "
                        f"({n} işlem, ort. {avg:+.2f}R) → {CLASS_STEPS[i - 1]}.")
    return msgs


def opposite_holder(desk, idea: dict) -> dict | None:
    """Aynı coinde başka yöneticinin ters yönde GERÇEK pozisyonu ya da emri (Elif'in hedge'li çifti sayılmaz)."""
    inst, side = idea["inst"], idea["side"]
    for t in desk.ledger["open"]:
        if (t.get("mgr") and t["mgr"] != idea["mgr"] and not t.get("shadow") and not t.get("carry")
                and t["inst"] == inst and t["side"] != side):
            return t
    for o in desk.ledger["orders"]:
        i = o["idea"]
        if o["mgr"] != idea["mgr"] and not o.get("shadow") and i["inst"] == inst and i["side"] != side:
            return {"mgr": o["mgr"], "side": i["side"], "inst": inst}
    return None


def coin_room(desk, inst: str) -> tuple[float, float, float]:
    """Döner: (tavan $, kullanılan $, kalan $)."""
    cap = desk.firm_equity() * desk.fc.get("coin_tavan_yuzde", 1.25) / 100
    used = desk.coin_risk(inst)
    return cap, used, max(0.0, cap - used)


def audit(desk, meta, now_ms: int) -> dict:
    """Denetçi raporu (panel): ters pozisyonlar, coin tavanı, sınıf dağılımı, tarz dışı işler."""
    live = [t for t in desk.ledger["open"] if t.get("mgr") and not t.get("shadow")]
    by_inst: dict[str, list] = {}
    for t in live:
        if not t.get("carry"):
            by_inst.setdefault(t["inst"], []).append(t)
    conflicts, crowd = [], []
    feq = desk.firm_equity()
    cap = feq * desk.fc.get("coin_tavan_yuzde", 1.25) / 100
    for inst, ts in by_inst.items():
        sides = {t["side"] for t in ts}
        names = sorted({desk.by_id[t["mgr"]].name if t["mgr"] in desk.by_id else t["mgr"] for t in ts})
        if len(sides) > 1:
            conflicts.append({"sym": ts[0]["sym"], "who": [f"{desk.by_id[t['mgr']].name if t['mgr'] in desk.by_id else t['mgr']} "
                                                            f"{t['side']}" for t in ts]})
        risk = sum(lg.open_risk(t) for t in ts)
        if len(ts) >= 2 or risk > cap:
            crowd.append({"sym": ts[0]["sym"], "n": len(ts), "who": names, "risk": round(risk, 2),
                          "cap": round(cap, 2), "over": risk > cap * 1.001})
    classes = {"A": 0, "B": 0, "C": 0}
    off_style = []
    for t in live:
        cls = t.get("coin_class") or coin_class(meta(t["inst"]) if meta else None, now_ms, desk.fc)
        classes[cls] = classes.get(cls, 0) + 1
        if t.get("carry") and cls == "C":
            off_style.append({"sym": t["sym"], "mgr": "Elif", "why": "fonlama çifti genç bir coinde (eski likidite kuralıyla açılmış)"})
    crowd.sort(key=lambda x: -x["risk"])
    # Filtre karnesi: elenen fikirler gölgede nasıl sonuçlandı? (ort. R > 0 → filtre kazandıran işlemleri eliyor)
    filt: dict[str, list] = {}
    for t in desk.ledger["closed"]:
        if t.get("filter_kind"):
            filt.setdefault(t["filter_kind"], []).append(t.get("r") or 0.0)
    filters = []
    for k, rs in filt.items():
        n = len(rs)
        avg = sum(rs) / n
        filters.append({"kind": k, "n": n, "avg_r": round(avg, 3),
                        "verdict": ("az veri" if n < 20 else "filtre kazananları eliyor → gözden geçir" if avg > 0.1
                                    else "filtre işini yapıyor" if avg < 0 else "nötr")})
    filters.sort(key=lambda x: -x["n"])
    open_f = sum(1 for t in desk.ledger["open"] if t.get("filter_kind"))
    return {"conflicts": conflicts, "crowd": crowd[:12], "classes": classes, "off_style": off_style,
            "filters": filters, "filtered_open": open_f,
            "coin_cap_usd": round(cap, 2), "coin_cap_pct": desk.fc.get("coin_tavan_yuzde", 1.25),
            "class_state": {k: v.get("state") for k, v in (desk.st.get("classes") or {}).items() if v.get("state")},
            "ts": now_ms}


class PerfMeter:
    """Döngü hızı sayacı: her adımın süresi; eşik aşılırsa hangi adımın yavaşlattığı söylenir."""

    def __init__(self):
        self.parts: dict[str, list] = {}
        self.loop_last = 0.0
        self.loop_max = 0.0
        self.loop_sum = 0.0
        self.loops = 0
        self.slow = 0
        self.last_alert = 0.0

    def timed(self, name: str, fn, *a, **kw):
        t0 = time.perf_counter()
        try:
            return fn(*a, **kw)
        finally:
            self.add(name, time.perf_counter() - t0)

    def add(self, name: str, dt: float):
        s = self.parts.setdefault(name, [0.0, 0.0, 0, 0.0])     # son, en çok, sayı, toplam
        s[0], s[1], s[2], s[3] = dt, max(s[1], dt), s[2] + 1, s[3] + dt

    def loop_done(self, dt: float):
        self.loop_last, self.loop_max = dt, max(self.loop_max, dt)
        self.loop_sum += dt
        self.loops += 1

    def top(self, k: int = 3) -> list[tuple[str, float]]:
        return sorted(((n, s[0]) for n, s in self.parts.items()), key=lambda x: -x[1])[:k]

    def report(self) -> dict:
        return {"loop_last_s": round(self.loop_last, 3), "loop_max_s": round(self.loop_max, 3),
                "loop_avg_s": round(self.loop_sum / self.loops, 4) if self.loops else None, "slow": self.slow,
                "parts": {n: {"last": round(s[0], 3), "max": round(s[1], 3), "avg": round(s[3] / s[2], 4) if s[2] else None}
                          for n, s in sorted(self.parts.items(), key=lambda x: -x[1][1])[:14]}}


# ── Yatırım Komitesi: Ortak Masa güven kuralı (v9 Faz 4) ─────────────────────────────────────────────
# Oy ağırlığı önce 5 yıllık testten (ön bilgi), 30+ gerçek işlemden sonra canlı karneden gelir.
FAMILY = {"kemal": "trend", "kaan": "trend", "selin": "sıkışma", "murat": "geri çekilme", "ayse": "dönüş", "deniz": "kısa vade"}
PRIOR = {"kaan": 1.0, "selin": 0.75, "kemal": 0.5, "murat": 0.0, "ayse": 0.0, "deniz": 0.0}   # v8 testi: +0,17 / +0,13 / +0,05R


def vote_weight(desk, mid: str, side: str) -> tuple[float, str]:
    if desk.segment(mid, side) == "golge":
        return 0.0, "gölgede"
    seg = ((desk.st.get("segments") or {}).get(f"{mid}|{side}") or {}).get("real") or {}
    n, avg, pf = seg.get("n", 0), seg.get("avg_r"), seg.get("pf")
    if n >= 30 and avg is not None:
        if avg >= 0.10 and (pf or 0) >= 1.2:
            return 1.0, f"canlıda kanıtlı ({n} işlem, {avg:+.2f}R)"
        if avg <= 0:
            return 0.25, f"canlıda kaybettiriyor ({n} işlem, {avg:+.2f}R)"
    w = float((((desk.cfg.get("ekip") or {}).get("ortak") or {}).get("on_bilgi") or {}).get(mid, PRIOR.get(mid, 0.0)))
    return w, "5 yıllık test ön bilgisi"


def committee_threshold(desk) -> tuple[float, str]:
    oc = (desk.cfg.get("ekip") or {}).get("ortak") or {}
    base = float(oc.get("esik", 1.25))
    cl = [t for t in desk.ledger["closed"] if t.get("mgr") == "ortak" and not t.get("shadow") and t.get("contrib")]
    if len(cl) >= 20:
        avg = sum(t.get("r", 0) for t in cl) / len(cl)
        if avg < 0:
            return min(base + 0.5, 3.0), f"masanın karnesi eksi ({len(cl)} işlem, {avg:+.2f}R) → eşik yükseldi"
    return base, "normal eşik"
