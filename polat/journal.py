"""Trade günlüğü: hatalar en büyük verimiz.

Her şey JSONL'e yazılır (satır satır, ekleme; bozulmaz):
  kararlar.jsonl → zorunlu koşulları tutan HER kurulum: girildi mi, girilmediyse hangi kural/veto yüzünden,
                   o anki bütün okumalar (yapı, seviye, mum, hacim, türev, akış)
  islemler.jsonl → açılış, kısmi, stop taşıma, kapanış; kapanışta R, MFE/MAE, ücret, funding, kayma, süre, neden
  olaylar.jsonl  → risk durdurmaları, seçici değişiklikleri, bağlantı kopmaları, gecikme ölçümleri
Rapor: son 1 / 50 / 500 gün; kuruluma, çıkış nedenine, seansa, teyit sayısına, BTC rejimine göre kırılım.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from collections import defaultdict
from pathlib import Path

DISCLAIMER = "Yatırım tavsiyesi değildir."


class Journal:
    """Yazma ayrı bir iş parçacığında yapılır (JSON'a çevirme + diske yazma karar döngüsünü bekletmez).
    Okumadan önce kuyruk boşaltılır, böylece okunan her zaman günceldir."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.files = {k: self.root / f"{k}.jsonl" for k in ("kararlar", "islemler", "olaylar")}
        self.q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._t = threading.Thread(target=self._run, daemon=True, name="gunluk")
        self._t.start()

    def _run(self):
        while True:
            kind, rec = self.q.get()
            try:
                line = json.dumps(rec, ensure_ascii=False, default=_default) + "\n"
                with self._lock, self.files[kind].open("a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self.q.task_done()

    def write(self, kind: str, rec: dict):
        rec.setdefault("ts", int(time.time() * 1000))
        self.q.put((kind, rec))

    def flush(self):
        self.q.join()

    def read(self, kind: str) -> list[dict]:
        self.flush()
        p = self.files[kind]
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


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
    decisions = j.read("kararlar")
    out = {"uyari": DISCLAIMER, "pencereler": {}}
    for days in (1, 50, 500):
        cut = now_ms - days * 86_400_000
        tr = [t for t in closes if t["ts"] >= cut]
        dc = [d for d in decisions if d["ts"] >= cut]
        by = {}
        for key in ("kurulum", "neden", "seans", "btc_rejim", "teyit_sayisi", "yon"):
            g = defaultdict(list)
            for t in tr:
                g[str(t.get(key))].append(t)
            by[key] = {k: _stats(v) for k, v in sorted(g.items())}
        skip = defaultdict(int)
        for d in dc:
            if not d.get("girildi"):
                skip[d.get("engel", "?").split(":")[0][:60]] += 1
        errors = defaultdict(int)
        for t in tr:
            for e in t.get("hatalar", []):
                errors[e] += 1
        out["pencereler"][f"{days}_gun"] = {"genel": _stats(tr), "kirilim": by,
                                            "girilmeyen_kurulum": dict(sorted(skip.items(), key=lambda x: -x[1])),
                                            "hata_siniflari": dict(errors),
                                            "kurulum_gorulen": len(dc), "kurulum_girilen": sum(1 for d in dc if d.get("girildi"))}
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
