"""Trade günlüğü: hatalar en büyük verimiz.

Her şey JSONL'e yazılır (satır satır, ekleme; bozulmaz):
  kararlar.jsonl → zorunlu koşulları tutan HER kurulum: girildi mi, girilmediyse hangi kural/veto yüzünden,
                   puan, kategoriler, oylar, tetik, plan (son 2 UTC günü)
  arsiv/         → daha eski günler: kararlar-GG.jsonl.gz + kararlar_ozet.json (gün başına görülen/girilen/engel)
  arsiv/okuma-GG.jsonl.gz → ham okumalar (~100 KB/karar; günde ~25 MB sıkıştırılmış, o yüzden seçici):
                   girilen her kurulumun okumaları + her OKUMA_ORNEK. kurulumdan biri. Ham okuma olmadan
                   oylar yeniden hesaplanamaz, bu yüzden girilenler tam tutulur.
                   (28.10.2026: ham okuma kararlar.jsonl'da günde ~100 MB olup GitHub'ın 100 MB dosya
                   sınırına dayandı → polat-veri gönderimleri durdu. Kayıt boyutu bu yüzden küçültüldü.)
  islemler.jsonl → açılış, kısmi, stop taşıma, kapanış; kapanışta R, MFE/MAE, ücret, funding, kayma, süre, neden
  olaylar.jsonl  → risk durdurmaları, seçici değişiklikleri, bağlantı kopmaları, gecikme ölçümleri
Rapor: son 1 / 50 / 500 gün; kuruluma, çıkış nedenine, seansa, teyit sayısına, BTC rejimine göre kırılım.
"""
from __future__ import annotations

import gzip
import json
import queue
import random
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DISCLAIMER = "Yatırım tavsiyesi değildir."
HEAVY = ("okuma",)                  # karar kaydının ağır alanı → arsiv/okuma-GG.jsonl.gz (seçici)
OKUMA_ORNEK = 100                   # girilmeyen kurulumlardan yaklaşık her 100'de biri ham okumayla tutulur
LIVE_DAYS = 2                       # kararlar.jsonl'de tutulan UTC gün sayısı (bugün + dün)
MAX_LIVE_BYTES = 40_000_000         # güvenlik: canlı dosya bunu aşarsa gün beklenmeden arşivlenir


def _day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _keep_okuma(rec: dict) -> bool:
    """Girilen her kurulumun ham okuması tutulur; girilmeyenlerden yaklaşık her OKUMA_ORNEK'te biri (rastgele)."""
    return bool(rec.get("girildi")) or random.random() < 1.0 / OKUMA_ORNEK


def _skip_key(engel) -> str:
    return (engel or "?").split(":")[0][:60]


class Journal:
    """Yazma ayrı bir iş parçacığında yapılır (JSON'a çevirme + diske yazma karar döngüsünü bekletmez).
    Okumadan önce kuyruk boşaltılır, böylece okunan her zaman günceldir."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.arsiv = self.root / "arsiv"
        self.files = {k: self.root / f"{k}.jsonl" for k in ("kararlar", "islemler", "olaylar")}
        self.summary_file = self.arsiv / "kararlar_ozet.json"
        self._lock = threading.Lock()
        self._dec: list[tuple[int, bool, str]] = []        # canlı dosyadaki kararlar: (ts, girildi, engel anahtarı)
        self._summary: dict = {}
        self.rotate(force_scan=True)                        # eski biçimli/büyük dosyayı da hafifletir
        self.q: queue.Queue = queue.Queue()
        self._t = threading.Thread(target=self._run, daemon=True, name="gunluk")
        self._t.start()

    # --- yazma ---------------------------------------------------------------------------------
    def _run(self):
        while True:
            kind, rec = self.q.get()
            try:
                if kind == "kararlar":
                    heavy = {k: rec.pop(k) for k in HEAVY if k in rec}
                    with self._lock:                 # satır + dizin aynı kilitte: döndürme araya giremez
                        if heavy and _keep_okuma(rec):
                            self._okuma_nolock(rec, heavy)
                        self._append_nolock("kararlar", rec)
                        self._dec.append((int(rec.get("ts") or 0), bool(rec.get("girildi")), _skip_key(rec.get("engel"))))
                else:
                    with self._lock:
                        self._append_nolock(kind, rec)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self.q.task_done()

    def _append_nolock(self, kind: str, rec: dict):
        line = json.dumps(rec, ensure_ascii=False, default=_default) + "\n"
        with self.files[kind].open("a", encoding="utf-8") as f:
            f.write(line)

    def _okuma_nolock(self, rec: dict, heavy: dict):
        """Ham okumayı günlük gzip arşivine ekler (gzip çoklu üye olarak eklenir)."""
        self.arsiv.mkdir(parents=True, exist_ok=True)
        ts = int(rec.get("ts") or 0)
        line = json.dumps({"ts": ts, "inst": rec.get("inst"), "girildi": rec.get("girildi"), **heavy},
                          ensure_ascii=False, default=_default) + "\n"
        with gzip.open(self.arsiv / f"okuma-{_day(ts)}.jsonl.gz", "at", encoding="utf-8") as g:
            g.write(line)

    def read_okuma(self) -> list[dict]:
        """Arşivlenmiş ham okumalar (girilen kurulumlar + örnek). Yazma sürerken okuma için kilit."""
        self.flush()
        out: list[dict] = []
        with self._lock:
            for p in sorted(self.arsiv.glob("okuma-*.jsonl.gz")):
                with gzip.open(p, "rt", encoding="utf-8") as f:
                    for line in f:
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        return out

    def write(self, kind: str, rec: dict):
        rec.setdefault("ts", int(time.time() * 1000))
        self.q.put((kind, rec))

    def flush(self):
        if hasattr(self, "q"):
            self.q.join()

    def read(self, kind: str) -> list[dict]:
        self.flush()
        p = self.files[kind]
        if not p.exists():
            return []
        out = []
        with p.open(encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    # --- döndürme / arşiv --------------------------------------------------------------------------
    def _load_summary(self) -> dict:
        try:
            return json.loads(self.summary_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def rotate(self, now_ms: int | None = None, force_scan: bool = False):
        """Canlı karar dosyasında son LIVE_DAYS UTC günü kalır; eskiler arsiv/'e sıkıştırılır ve günlük özeti yazılır.
        Eski biçimli kayıtların ham okumaları seçici olarak arsiv/okuma-GG'ye taşınır. Ucuz: yalnızca gerekince dosyayı tarar."""
        now_ms = now_ms or int(time.time() * 1000)
        keep_from = _day(now_ms - (LIVE_DAYS - 1) * 86_400_000)
        p = self.files["kararlar"]
        if not force_scan:
            with self._lock:
                oldest = self._dec[0][0] if self._dec else None
            big = p.exists() and p.stat().st_size > MAX_LIVE_BYTES
            if not big and (oldest is None or _day(oldest) >= keep_from):
                return
        self.flush()
        with self._lock:
            self._summary = self._load_summary()
            if not p.exists():
                self._dec = []
                return
            big = p.stat().st_size > MAX_LIVE_BYTES
            self.arsiv.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            arch: dict[str, list[str]] = defaultdict(list)
            okuma: dict[str, list[str]] = defaultdict(list)
            dec: list[tuple[int, bool, str]] = []
            with p.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as live:
                for line in src:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    heavy = {k: rec.pop(k) for k in HEAVY if k in rec}
                    ts = int(rec.get("ts") or 0)
                    d = _day(ts)
                    if heavy and _keep_okuma(rec):       # eski biçimli kayıtların ham okuması da seçici tutulur
                        okuma[d].append(json.dumps({"ts": ts, "inst": rec.get("inst"), "girildi": rec.get("girildi"),
                                                    **heavy}, ensure_ascii=False, default=_default) + "\n")
                    out = json.dumps(rec, ensure_ascii=False, default=_default) + "\n"
                    if d < keep_from or big:
                        arch[d].append(out)
                        s = self._summary.setdefault(d, {"gorulen": 0, "girilen": 0, "engel": {}})
                        s["gorulen"] += 1
                        if rec.get("girildi"):
                            s["girilen"] += 1
                        else:
                            k = _skip_key(rec.get("engel"))
                            s["engel"][k] = s["engel"].get(k, 0) + 1
                    else:
                        live.write(out)
                        dec.append((ts, bool(rec.get("girildi")), _skip_key(rec.get("engel"))))
            for d, lines in arch.items():
                with gzip.open(self.arsiv / f"kararlar-{d}.jsonl.gz", "at", encoding="utf-8") as g:
                    g.writelines(lines)
            for d, lines in okuma.items():
                with gzip.open(self.arsiv / f"okuma-{d}.jsonl.gz", "at", encoding="utf-8") as g:
                    g.writelines(lines)
            if arch:
                self.summary_file.write_text(json.dumps(self._summary, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            tmp.replace(p)
            self._dec = dec

    def decision_counts(self, cut_ms: int) -> tuple[int, int, Counter]:
        """cut_ms'den bu yana görülen / girilen kurulum ve girilmeyenlerin engel dağılımı (arşiv özetleri + canlı)."""
        self.flush()
        cut_day = _day(cut_ms)
        seen, taken, skip = 0, 0, Counter()
        with self._lock:
            for d, s in self._summary.items():
                if d >= cut_day:
                    seen += s["gorulen"]
                    taken += s["girilen"]
                    skip.update(s["engel"])
            for ts, girildi, k in self._dec:
                if ts >= cut_ms:
                    seen += 1
                    if girildi:
                        taken += 1
                    else:
                        skip[k] += 1
        return seen, taken, skip


def _default(o):
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def _stats(trades: list[dict]) -> dict:
    n = len(trades)
    if not n:
        return {"islem": 0}
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0]
    loss = [r for r in rs if r <= 0]
    pnl = sum(t["pnl_net"] for t in trades)
    eq, peak, mdd = 0.0, 0.0, 0.0
    for t in trades:
        eq += t["pnl_net"]
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return {"islem": n, "kazanma": round(len(wins) / n * 100, 1), "ort_r": round(sum(rs) / n, 3),
            "toplam_r": round(sum(rs), 2), "kar_faktoru": round(sum(wins) / -sum(loss), 2) if loss and sum(loss) else None,
            "net_usd": round(pnl, 2), "max_dusus_usd": round(mdd, 2),
            "ort_kayma_bp": round(sum(t.get("slip_bp", 0) for t in trades) / n, 2),
            "ort_sure_saat": round(sum(t.get("sure_saat", 0) for t in trades) / n, 1)}


def report(j: Journal, now_ms: int | None = None) -> dict:
    now_ms = now_ms or int(time.time() * 1000)
    closes = [x for x in j.read("islemler") if x.get("olay") == "kapanis"]
    out = {"uyari": DISCLAIMER, "pencereler": {}}
    for days in (1, 50, 500):
        cut = now_ms - days * 86_400_000
        tr = [t for t in closes if t["ts"] >= cut]
        seen, taken, skip = j.decision_counts(cut)
        by = {}
        for key in ("kurulum", "neden", "seans", "btc_rejim", "teyit_sayisi", "yon"):
            g = defaultdict(list)
            for t in tr:
                g[str(t.get(key))].append(t)
            by[key] = {k: _stats(v) for k, v in sorted(g.items())}
        errors = defaultdict(int)
        for t in tr:
            for e in t.get("hatalar", []):
                errors[e] += 1
        out["pencereler"][f"{days}_gun"] = {"genel": _stats(tr), "kirilim": by,
                                            "girilmeyen_kurulum": dict(sorted(skip.items(), key=lambda x: -x[1])),
                                            "hata_siniflari": dict(errors),
                                            "kurulum_gorulen": seen, "kurulum_girilen": taken}
    return out


def report_md(rep: dict) -> str:
    L = ["# Polat günlük raporu", "", f"_{rep['uyari']}_", ""]
    k = rep.get("kiyas")
    if k:
        L += ["## Kıyaslama (maliyetler düşülmüş, net R)", "", f"**Hüküm:** {k.get('hukum')}", "",
              "| | sinyal | kazanma | ort. net R | toplam net R |", "|---|---|---|---|---|"]
        for nm in ("polat_tum_sinyaller", "polat_girilen", "polat_girilmeyen", "rastgele", "trend_naif"):
            x = k.get(nm) or {}
            L.append(f"| {nm} | {x.get('n', 0)} | %{x.get('kazanma', '-')} | {x.get('ort_R_net', '-')} | {x.get('toplam_R_net', '-')} |")
        L += ["", f"Polat özsermaye: %{k.get('polat_ozsermaye_yuzde')} · BTC al-tut: %{k.get('btc_al_tut_yuzde')}", ""]
    o = rep.get("ogrenme")
    if o:
        m = o.get("meta", {})
        L += ["## Öğrenme (walk-forward)", "",
              f"Örnek {m.get('ornek', 0)} (eğitim {m.get('egitim', '-')}, doğrulama {m.get('dogrulama', '-')}) · "
              f"uygulanan katsayı {m.get('uygulanan', 0)} · doğrulamada tutmadığı için reddedilen {m.get('reddedilen', 0)}", ""]
        if o.get("en_guclu"):
            L.append("En çok işe yarayan okumalar: " + ", ".join(f"{a} ×{b}" for a, b in o["en_guclu"][:10]))
        if o.get("en_zayif"):
            L.append("En az işe yarayan okumalar: " + ", ".join(f"{a} ×{b}" for a, b in o["en_zayif"][:10]))
        L.append("")
    for name, w in rep["pencereler"].items():
        g = w["genel"]
        L.append(f"## Son {name.replace('_gun', '')} gün")
        L.append("")
        if not g.get("islem"):
            L.append(f"Kapanmış işlem yok. Görülen kurulum: {w['kurulum_gorulen']}, girilen: {w['kurulum_girilen']}.")
        else:
            L.append(f"İşlem {g['islem']} · kazanma %{g['kazanma']} · ort. {g['ort_r']}R · toplam {g['toplam_r']}R · "
                     f"net {g['net_usd']}$ · maks. düşüş {g['max_dusus_usd']}$ · kâr faktörü {g['kar_faktoru']}")
            for key, title in (("kurulum", "Kuruluma göre"), ("neden", "Çıkış nedenine göre"), ("seans", "Seansa göre"),
                               ("teyit_sayisi", "Teyit sayısına göre")):
                L += ["", f"**{title}**", "", "| | işlem | kazanma | ort. R | net $ |", "|---|---|---|---|---|"]
                for k, s in w["kirilim"][key].items():
                    L.append(f"| {k} | {s['islem']} | %{s.get('kazanma', 0)} | {s.get('ort_r', 0)} | {s.get('net_usd', 0)} |")
        if w["girilmeyen_kurulum"]:
            L += ["", "**Girilmeyen kurulumlar (engel)**", ""]
            for k, n in list(w["girilmeyen_kurulum"].items())[:10]:
                L.append(f"- {k}: {n}")
        if w["hata_siniflari"]:
            L += ["", "**Hata sınıfları**", ""]
            for k, n in w["hata_siniflari"].items():
                L.append(f"- {k}: {n}")
        L.append("")
    return "\n".join(L)
