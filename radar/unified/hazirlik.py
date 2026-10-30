"""İşleme hazır olma göstergesi — YALNIZ İZLEME.

Bu modül hiçbir kapıyı gevşetmez, değiştirmez ve karar vermez. Karar motorunun zaten yazdığı sebep kodlarını
(reason_codes), maliyet alanlarını ve plan bilgisini okuyup her kararın kapı durumlarını üretir:

  - kapı durumu: GEÇTİ / GÖLGE / RED / VERİ YOK (ölçülemeyen ya da önceki kapıda engellendiği için hiç
    değerlendirilmeyen kapı VERİ YOK'tur; bu kapılar yüzdeye girmez),
  - hazırlık skoru: yalnız ölçülebilir kapıların geçen oranı (0–100). Bu bir olasılık DEĞİLDİR.
  - tarihsel geçiş olasılığı: aynı strateji + yön + rejim + ana engel grubunda en az MIN_KANIT gözlem varsa hesaplanır;
    yoksa "kanıt yetersiz (n/30)" yazılır. Zaman tahmini yapılmaz.

Çıktılar: her karar satırında `hazirlik` alanı (journal) ve birlesik/hazirlik.json (son 24 saat engel özeti + grup
istatistikleri).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

GEC, GOLGE, RED, YOK, HES = "GEÇTİ", "GÖLGE", "RED", "VERİ YOK", "HESAPLANMADI"
MIN_KANIT = 30
DAY_MS = 86_400_000
OPEN_DEC = ("OPEN", "REDUCE_SIZE")
GATES = (
    ("sinyal", "Sinyal / fırsat oluştu"),
    ("veri_kalite", "Veri kalitesi yeterli"),
    ("veri_kapsam", "Veri kapsamı yeterli"),
    ("yetki", "Strateji yetkisi"),
    ("politika", "Politika uygunluğu"),
    ("sinif", "Coin yaş sınıfı"),
    ("maliyet", "Maliyet sonrası avantaj"),
    ("likidite", "Likidite / dolum"),
    ("rejim", "Rejim / çelişki"),
    ("riskgate", "RiskGate"),
)
GATE_FAMILY = {"veri_kalite": "VERI", "veri_kapsam": "VERI", "yetki": "YETKI", "politika": "POLITIKA",
               "sinif": "SINIF", "maliyet": "MALIYET", "likidite": "LIKIDITE", "rejim": "REJIM"}
MALIYET_CODES = ("MALIYET_SONRASI_AVANTAJ_YOK", "AVANTAJ_TAMPONDAN_KUCUK", "BRUT_AVANTAJ_BILINMIYOR", "MALIYET_GUVENI_DUSUK")
LIKIDITE_CODES = ("DERINLIK_YETERSIZ", "KISMI_DOLUM_YUKSEK")
POL_TAGS = (("oynaklık", "ATR"), ("stop kapısı", "STOP"), ("kalabalık", "KALABALIK"),
            ("Kaan kuralı", "KAAN_YATAY"), ("rejim kapısı", "REJIM"))
SUMMARY_KEYS = (("YETKI_GOLGE", "YETKI"), ("POLITIKA", "POLITIKA"), ("MALIYET", "MALIYET"), ("VERI", "VERI"))
_FILE = re.compile(r"^kararlar-(\d{4}-\d{2}-\d{2})\.jsonl$")


def norm(code: str) -> str:
    """Politika gerekçe metnini kısa etikete çevirir (POLITIKA:ATR gibi). Diğer kodlar aynen kalır."""
    if code.startswith("POLITIKA:"):
        tail = code[len("POLITIKA:"):]
        for needle, tag in POL_TAGS:
            if needle in tail:
                return f"POLITIKA:{tag}"
        return "POLITIKA:DIGER"
    return code


def family(code: str) -> str | None:
    if code.startswith("YETKI_GOLGE"):
        return "YETKI"
    if code.startswith("POLITIKA"):
        return "POLITIKA"
    if code.startswith("SINIF_C"):
        return "SINIF"
    if code in MALIYET_CODES:
        return "MALIYET"
    if code.startswith(("VERI_SORUNU", "ZORUNLU", "KAPSAM")):
        return "VERI"
    if code.startswith("CELISKI"):
        return "REJIM"
    if code in LIKIDITE_CODES:
        return "LIKIDITE"
    return None


def neden(code: str, rec: Mapping[str, Any]) -> str:
    """Kart üzerinde gösterilen kısa, Türkçe neden."""
    costs = rec.get("costs") or {}
    tc = costs.get("total_cost_bp")
    na = rec.get("net_edge_bp")
    na_txt = f"{na:+.1f} bp" if isinstance(na, (int, float)) else "—"
    tc_txt = f"{tc:.1f} bp" if isinstance(tc, (int, float)) else "—"
    fixed = {
        "YETKI_GOLGE": "Canlı kanıt/yetki yetersiz",
        "POLITIKA:ATR": "Günlük oynaklık %5 sınırını aşıyor",
        "POLITIKA:REJIM": "Yön BTC rejimiyle uyumsuz",
        "POLITIKA:STOP": "Stop mesafesi politika sınırını aşıyor",
        "POLITIKA:KALABALIK": "Funding kalabalık tarafı gösteriyor",
        "POLITIKA:KAAN_YATAY": "BTC yatay: kırılım kuralı gölgede",
        "POLITIKA:DIGER": "Politika kapısı",
        "SINIF_C": "Coin 1 yıldan genç",
        "MALIYET_SONRASI_AVANTAJ_YOK": f"Gerçek maliyet {tc_txt} · net avantaj {na_txt}",
        "AVANTAJ_TAMPONDAN_KUCUK": f"Net avantaj {na_txt} · belirsizlik tamponundan küçük",
        "BRUT_AVANTAJ_BILINMIYOR": "Beklenen avantaj bilinmiyor (geçmiş kanıt yok)",
        "MALIYET_GUVENI_DUSUK": "Maliyet girdileri güvenilmez",
        "DERINLIK_YETERSIZ": "Emir defteri derinliği yetersiz",
        "KISMI_DOLUM_YUKSEK": "Emrin büyük kısmı dolmazdı",
        "KAPSAM_DUSUK": "Veri kapsamı düşük",
        "CELISKI_KAYBETTI": "Aynı coinde ters yönlü daha güçlü fırsat var",
        "CELISKI_REJIM_BELIRSIZ": "Aynı coinde çelişki var, rejim belirsiz",
        "TEK_KIRILIM": "Yalnız kırılım kanıtı var: boyut yarım",
    }
    if code in fixed:
        return fixed[code]
    for prefix, text in (("VERI_SORUNU", "Veri sorunu"), ("ZORUNLU_EKSIK", "Zorunlu veri eksik"),
                         ("ZORUNLU_BAYAT", "Zorunlu veri bayat"), ("ZORUNLU_GECERSIZ", "Zorunlu veri geçersiz")):
        if code.startswith(prefix):
            tail = code[len(prefix):].lstrip(":")
            return f"{text}{': ' + tail if tail else ''}"
    return code


def evaluate(rec: Mapping[str, Any]) -> dict:
    """Bir karar kaydının kapı durumları, hazırlık skoru, engel ve nedenleri. Yalnız okur; kapı mantığını çalıştırmaz."""
    dec = str(rec.get("decision") or "")
    codes = [norm(str(c)) for c in (rec.get("reason_codes") or [])]
    plan = rec.get("plan") or {}
    fail = RED if dec == "REJECT" else GOLGE
    has = lambda *pre: any(c.startswith(pre) for c in codes)  # noqa: E731
    S: dict[str, str] = {"sinyal": GEC}
    stop = False
    # 1) veri kalitesi / kapsam: başarısızsa sonrakiler HESAPLANMADI. Yetki/politika/sınıf artık her zaman hesaplanır.
    S["veri_kalite"] = fail if has("VERI_SORUNU") else GEC
    stop = has("VERI_SORUNU")
    if stop:
        S["veri_kapsam"] = HES
    else:
        S["veri_kapsam"] = fail if has("KAPSAM", "ZORUNLU") else GEC
        stop = has("KAPSAM", "ZORUNLU")
    for key, cond in (("yetki", has("YETKI_GOLGE")), ("politika", has("POLITIKA")), ("sinif", has("SINIF_C"))):
        if stop:
            S[key] = HES
            continue
        if key == "sinif" and not plan.get("coin_class") and not cond:
            S[key] = YOK
        else:
            S[key] = fail if cond else GEC
    # 6–9) maliyet her zaman hesaplanır (kapılardan bağımsız); likidite, rejim/çelişki ve RiskGate
    S["maliyet"] = fail if has(*MALIYET_CODES) else (YOK if rec.get("net_edge_bp") is None else GEC)
    S["likidite"] = fail if has(*LIKIDITE_CODES) else (YOK if (rec.get("costs") or {}).get("notional_usd") is None else GEC)
    if has("CELISKI"):
        S["rejim"] = fail
    else:
        S["rejim"] = YOK if plan.get("rejim") is None else GEC
    S["riskgate"] = GEC if dec in OPEN_DEC else HES
    kapilar = [{"ad": name, "etiket": label, "durum": S[name]} for name, label in GATES]
    olculen = [k for k in kapilar if k["durum"] not in (YOK, HES)]
    gecen = sum(1 for k in olculen if k["durum"] == GEC)
    skor = round(gecen / len(olculen) * 100) if olculen else None
    # hard block: ilk başarısız kapının ailesinden ilk sebep kodu
    engel = None
    if dec not in OPEN_DEC:
        for k in kapilar:
            if k["durum"] in (GOLGE, RED) and k["ad"] in GATE_FAMILY:
                fam = GATE_FAMILY[k["ad"]]
                engel = next((c for c in codes if family(c) == fam), fam)
                break
    aile = family(engel) if engel else None
    if dec in OPEN_DEC:
        metin = None
    elif engel:
        metin = f"İşlem açılamaz{' (red)' if dec == 'REJECT' else ''}: {engel}"
    else:
        metin = "İşlem açılamaz: sebep kodu yok"
    ordered = ([engel] if engel else []) + [c for c in codes if c != engel]
    nedenler = []
    for c in ordered:
        t = neden(c, rec)
        if t not in nedenler:
            nedenler.append(t)
    main = "ACIK" if dec in OPEN_DEC else (aile or "DIGER")
    grup = f"{rec.get('strategy') or '?'}|{rec.get('side') or '-'}|{plan.get('rejim') or '?'}|{main}"
    return {"skor": skor, "gecen": gecen, "olculen": len(olculen), "kapilar": kapilar, "engel": engel,
            "engel_aile": aile, "engel_metni": metin, "nedenler": nedenler[:2], "tum_nedenler": nedenler,
            "grup": grup}


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _rows(root: Path, now_ms: int):
    if not root.exists():
        return []
    out = []
    cutoff = _day(now_ms - 14 * DAY_MS)
    for f in sorted(root.glob("kararlar-*.jsonl")):
        m = _FILE.match(f.name)
        if not m or m.group(1) < cutoff:
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def build(root: str | Path, now_ms: int) -> dict:
    """Son 24 saat engel özeti ve tarihsel geçiş grupları (kararlar-*.jsonl üzerinden)."""
    root = Path(root)
    rows = _rows(root, now_ms)
    first: dict[str, dict] = {}
    acik: dict[str, bool] = {}
    engel24 = {k: 0 for k, _ in SUMMARY_KEYS}
    engel24["RED"] = 0
    toplam24 = 0
    for r in sorted(rows, key=lambda x: x.get("kayit_ms") or x.get("ts_ms") or 0):
        h = r.get("hazirlik") or evaluate(r)
        key = r.get("idempotency_key") or f"{r.get('symbol')}|{r.get('strategy')}|{r.get('side')}"
        if key not in first:
            first[key] = h
            acik[key] = False
        if r.get("decision") in OPEN_DEC:
            acik[key] = True
        ts = r.get("kayit_ms") or r.get("ts_ms") or 0
        if ts >= now_ms - DAY_MS:
            toplam24 += 1
            if r.get("decision") == "REJECT":
                engel24["RED"] += 1
            for name, fam in SUMMARY_KEYS:
                if h.get("engel_aile") == fam:
                    engel24[name] += 1
    gruplar: dict[str, dict] = {}
    for key, h in first.items():
        g = gruplar.setdefault(h["grup"], {"n": 0, "acik": 0})
        g["n"] += 1
        g["acik"] += 1 if acik[key] else 0
    return {"ts": now_ms, "esik": MIN_KANIT, "engel_24s": {**engel24, "toplam": toplam24},
            "gruplar": gruplar}


def write_summary(root: str | Path, now_ms: int) -> dict:
    root = Path(root)
    out = build(root, now_ms)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / "hazirlik.tmp"
    tmp.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(root / "hazirlik.json")
    return out
