"""Karne — tek ajan (Baş Trader) mimarisi.

Tek karar verici Baş Trader'dır. Eski yönetici adları (Kemal, Selin, Kaan, Murat, Turbo…) bağımsız ajan DEĞİL, yalnız
strateji / sinyal kaynağı etiketidir. Karne üç katmandır:
  1) Baş Trader toplamı      : bütün gerçek (paper) işlemler tek portföy olarak
  2) Strateji / sinyal kaynağı: gerçek ve gölge sonuçlar AYRI (gölge = sermayesiz izlenen, elenen ya da yetkisiz fikir)
  3) Filtre karnesi          : bir fikri gölgeye gönderen her filtre; elediği işlemler kaybettirdiyse filtre haklıdır
Her satırda: işlem sayısı, isabet, ortalama R, toplam R, kâr faktörü, ortalama R'nin %95 güven aralığı (bootstrap).
Hiçbir değer uydurulmaz: işlem yoksa "veri yok" yazılır.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from .ci import mean_ci

# eski yönetici kimliği → strateji / sinyal kaynağı etiketi (yalnız etiket)
SOURCE = {
    "kemal": ("swing_trend", "Trend"),
    "selin": ("compression_breakout", "Sıkışma kırılımı"),
    "kaan": ("controlled_breakout", "Kontrollü kırılım"),
    "ayse": ("panic_reversal", "Panik dönüşü"),
    "murat": ("legacy_murat", "Geri çekilme sinyali"),
    "turbo": ("legacy_turbo", "Trend sermaye kolu"),
    "ortak": ("legacy_ortak", "Ortak sinyal"),
    "bas": ("bas_skor", "Baş Trader skor sinyali"),
    "elif": ("carry", "Funding carry"),
    "deniz": ("legacy_deniz", "Mikro sinyal (kapalı)"),
}
MIN_KANIT = 30


def _label(key: str) -> str:
    lab = {v[0]: v[1] for v in SOURCE.values()}
    lab.update({"short_trend": "Short trend", "intraday_trend": "Gün içi trend", "microstructure": "Mikro yapı"})
    return lab.get(key, key)


def source_of(t: Mapping) -> tuple[str, str]:
    if t.get("carry"):
        return SOURCE["elif"]
    mid = str(t.get("mgr") or t.get("strategy") or "")
    if mid in SOURCE:
        return SOURCE[mid]
    if t.get("strategy"):
        return str(t["strategy"]), str(t["strategy"])
    setup = str(t.get("setup") or "?")
    return f"setup_{setup}", f"Eski setup: {setup}"


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def stats(trades: Iterable[Mapping]) -> dict[str, Any]:
    rs = [r for r in (_num(t.get("r")) for t in trades) if r is not None]
    n = len(rs)
    if n == 0:
        return {"n": 0, "durum": "veri yok"}
    wins = [r for r in rs if r > 0]
    loss = [r for r in rs if r < 0]
    pf = (sum(wins) / -sum(loss)) if loss and sum(loss) < 0 else None
    lo, hi = mean_ci(rs)
    avg = sum(rs) / n
    if n < MIN_KANIT:
        durum = f"kanıt yetersiz ({n}/{MIN_KANIT} işlem)"
    elif lo is not None and lo > 0:
        durum = "kârlı (güven aralığı sıfırın üstünde)"
    elif hi is not None and hi < 0:
        durum = "zararlı (güven aralığı sıfırın altında)"
    else:
        durum = "belirsiz (güven aralığı sıfırı içeriyor)"
    return {"n": n, "isabet": round(len(wins) / n * 100, 1), "ort_r": round(avg, 3), "toplam_r": round(sum(rs), 2),
            "pf": round(pf, 2) if pf is not None else None, "ga_alt": None if lo is None else round(lo, 3),
            "ga_ust": None if hi is None else round(hi, 3),
            "pnl_usd": round(sum(_num(t.get("pnl_usd")) or 0.0 for t in trades), 2), "durum": durum}


def guven(n: int) -> str:
    """Örnek sayısına göre güven etiketi. Örnek sayısı işlem AÇILIŞINI ENGELLEMEZ; yalnız yorumu belirtir."""
    if n < 5:
        return "kanıt yok"
    if n < 10:
        return "çok düşük"
    if n < 20:
        return "ön eğilim"
    if n < 30:
        return "orta"
    return "kullanılabilir örnek"


def filter_verdict(s: Mapping) -> str:
    """Gölgeye gönderilen (elenen) işlemler için: kaybettirdilerse filtre haklıdır."""
    if not s.get("n"):
        return "veri yok"
    if s["n"] < MIN_KANIT:
        return f"kanıt yetersiz ({s['n']}/{MIN_KANIT})"
    if s.get("ga_ust") is not None and s["ga_ust"] < 0:
        return "filtre haklı (elenenler kaybettiriyor)"
    if s.get("ga_alt") is not None and s["ga_alt"] > 0:
        return "filtre kâr kaçırıyor (elenenler kazandırıyor)"
    return "belirsiz"


def build(closed: Iterable[Mapping], unified_closed: Iterable[Mapping] = (), now_ms: int | None = None,
          golge_closed: Iterable[Mapping] = ()) -> dict:
    """closed: LEGACY defter (eski sistem; yeni Baş Trader performansı DEĞİLDİR). golge_closed: yeni Baş Trader'ın
    gölge sanal defterinde kapanan işlemler (net USD / net R, maliyetler düşülmüş)."""
    closed = list(closed)
    gcl = [dict(c, pnl_usd=c.get("net_usd")) for c in golge_closed]
    real = [t for t in closed if not t.get("shadow")]
    shadow = [t for t in closed if t.get("shadow")]
    ucl = [dict(c, strategy=c.get("strategy"), pnl_usd=c.get("net_usd")) for c in unified_closed]
    out: dict[str, Any] = {"ts": now_ms, "bas_trader": stats(real + ucl), "bas_trader_golge": stats(shadow),
                           "yeni_golge": stats(gcl), "yeni_kaynaklar": [], "kaynaklar": [], "filtreler": []}
    gk: dict[str, list] = {}
    for c in gcl:
        for s_ in (c.get("strategies") or [c.get("strategy") or "?"]):
            gk.setdefault(str(s_), []).append(c)
    out["yeni_kaynaklar"] = sorted(({"kaynak": k, "etiket": _label(k), "golge_sanal": stats(v)} for k, v in gk.items()),
                                   key=lambda r: -r["golge_sanal"]["n"])
    keys: dict[str, str] = {}
    for t in closed + ucl:
        k, label = source_of(t)
        keys.setdefault(k, label)
    for k, label in keys.items():
        g = [t for t in real if source_of(t)[0] == k]
        u = [t for t in ucl if source_of(t)[0] == k]
        s = [t for t in shadow if source_of(t)[0] == k]
        out["kaynaklar"].append({"kaynak": k, "etiket": label, "gercek": stats(g + u), "golge": stats(s)})
    out["kaynaklar"].sort(key=lambda r: -(r["gercek"]["n"] + r["golge"]["n"]))
    op = [c for c in gcl if c.get("operational")]                 # YETKI_GOLGE operasyonel shadow kapanışları
    out["operasyonel_shadow"] = {**stats(op), "guven": guven(len(op)), "kaynak": "yalnız kapanmış sanal işlemlerin net R'si"}
    groups: dict[str, list] = {}
    for t in shadow:
        kind = t.get("filter_kind") or ("sınıf C / yetki gölgesi" if not t.get("filtered") else "diğer")
        groups.setdefault(kind, []).append(t)
    for kind, ts in groups.items():
        s = stats(ts)
        out["filtreler"].append({"filtre": kind, **s, "karar": filter_verdict(s)})
    out["filtreler"].sort(key=lambda r: -r["n"])
    return out


def _row(s: Mapping) -> str:
    if not s.get("n"):
        return "0 | – | – | – | – | – | veri yok"
    ga = "–" if s.get("ga_alt") is None else f"{s['ga_alt']:+.2f} … {s['ga_ust']:+.2f}"
    return (f"{s['n']} | %{s['isabet']} | {s['ort_r']:+.2f} | {s['toplam_r']:+.1f} | {s['pf'] if s['pf'] is not None else '–'}"
            f" | {ga} | {s['durum']}")


def to_md(k: Mapping, title: str) -> str:
    H = "| İşlem | İsabet | Ort. R | Toplam R | PF | %95 GA (ort. R) | Durum |"
    L = [f"# {title}", "", "Tek karar verici **Baş Trader**. Aşağıdaki adlar bağımsız ajan değil, strateji / sinyal kaynağı "
         "etiketidir.", "", "## Yeni Baş Trader — gölge sanal portföy (gerçek emir yok, maliyetler düşülmüş)", "",
         "| Kapsam " + H, "|---" * 8 + "|", f"| Gölge sanal (net) | {_row(k.get('yeni_golge') or {})} |"]
    for r in k.get("yeni_kaynaklar") or []:
        L.append(f"| ↳ {r['etiket']} | {_row(r['golge_sanal'])} |")
    L += ["", "## Eski sistem (legacy defter) — yeni Baş Trader performansı DEĞİLDİR", "", "| Kapsam " + H,
          "|---" * 8 + "|", f"| Legacy gerçek (paper) | {_row(k['bas_trader'])} |",
          f"| Legacy gölge | {_row(k['bas_trader_golge'])} |", "",
          "## Legacy: strateji / sinyal kaynağı", "", "| Kaynak | Tür " + H, "|---" * 9 + "|"]
    for r in k["kaynaklar"]:
        L.append(f"| {r['etiket']} | gerçek | {_row(r['gercek'])} |")
        L.append(f"| {r['etiket']} | gölge | {_row(r['golge'])} |")
    L += ["", "## Filtre karnesi (gölgeye gönderilen fikirler)", "",
          "Elenen işlemler kaybettirdiyse filtre haklıdır (güven aralığının üst sınırı sıfırın altında).", "",
          "| Filtre " + H + " Filtre kararı |", "|---" * 9 + "|"]
    if not k["filtreler"]:
        L.append("| – | 0 | – | – | – | – | – | veri yok | – |")
    for r in k["filtreler"]:
        L.append(f"| {r['filtre']} | {_row(r)} | {r['karar']} |")
    L += ["", f"_Güven aralığı: bootstrap, 2000 tekrar, sabit tohum. {MIN_KANIT} işlemden azsa sonuç kanıt sayılmaz._"]
    return "\n".join(L) + "\n"


def _tg_row(s: Mapping) -> str:
    if not s.get("n"):
        return "veri yok"
    ga = "" if s.get("ga_alt") is None else f" · GA {s['ga_alt']:+.2f}…{s['ga_ust']:+.2f}"
    return (f"{s['n']} işlem · %{s['isabet']} · ort {s['ort_r']:+.2f}R · top {s['toplam_r']:+.1f}R · "
            f"PF {s['pf'] if s['pf'] is not None else '–'}{ga}")


def to_telegram(k: Mapping, esc=lambda s: s) -> str:
    L = ["📒 <b>Karne · Baş Trader</b>", f"👑 Yeni Baş Trader (gölge sanal, net): {_tg_row(k.get('yeni_golge') or {})}",
         "", "<b>Eski sistem (legacy)</b>", f"Gerçek: {_tg_row(k['bas_trader'])}",
         f"Gölge: {_tg_row(k['bas_trader_golge'])}", "", "<b>Legacy strateji / sinyal kaynağı</b>"]
    for r in k["kaynaklar"][:10]:
        L.append(f"• {esc(r['etiket'])}: gerçek {_tg_row(r['gercek'])} | gölge {_tg_row(r['golge'])}")
    if k["filtreler"]:
        L += ["", "<b>Filtre karnesi</b>"]
        for r in k["filtreler"][:8]:
            L.append(f"• {esc(r['filtre'])}: {_tg_row(r)} → {r['karar']}")
    return "\n".join(L)
