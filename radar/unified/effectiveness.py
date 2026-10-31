"""Etkinlik denetçisi: hangi strateji gerçekten para kazandırıyor, hangisi şans? (yalnız ölçer; hiçbir şey açmaz/kapatmaz)

Kaynaklar (yalnız okunur):
  - legacy defter kapanmış işlemleri (`site/ledger.json` → closed). `shadow: true` olanlar gölge işlemleridir.
  - birleşik paper defteri kapanmışları (`site/birlesik/yurutme.json` → closed).
Çıktı: kapsam başına `EffectivenessSnapshot` (bootstrap güven aralığı, yoğunlaşma, maliyet payı) + karar:
  - terfi_adayi   : n ≥ `terfi_min_islem` (100) VE ortalama R'nin %95 GA alt sınırı > 0 VE düzeltilmiş Sharpe olasılığı ≥ 0,95
  - kapat_adayi   : n ≥ 30 VE GA üst sınırı < 0
  - kanit_yetersiz: diğer her durum (varsayılan; "kazanıyor gibi" görünen küçük örnek kanıt değildir)
Deneme sayısı: aynı veride kaç kural/varyant denendiyse (`denemeler.json`) Deflated Sharpe o kadar sıkı olur
(Bailey & López de Prado 2014). Deneme kaydı yoksa 1 sayılır ve bu açıkça not edilir.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable, Mapping, Sequence

from .models import LEGACY_STRATEGY, EffectivenessSnapshot, is_finite_number, to_jsonable

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()

AUDIT_DEFAULTS = {"terfi_min_islem": 100, "kapat_min_islem": 30, "dsr_esik": 0.95}


def audit_settings(cfg: Mapping | None) -> dict:
    s = dict(AUDIT_DEFAULTS)
    s.update((((cfg or {}).get("birlesik") or {}).get("etkinlik")) or {})
    return s


# ── istatistik ───────────────────────────────────────────────────────

def _moments(x: Sequence[float]) -> tuple[float, float, float, float]:
    n = len(x)
    m = sum(x) / n
    var = sum((v - m) ** 2 for v in x) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    if sd == 0:
        return m, 0.0, 0.0, 3.0
    skew = sum(((v - m) / sd) ** 3 for v in x) / n
    kurt = sum(((v - m) / sd) ** 4 for v in x) / n
    return m, sd, skew, kurt


def expected_max_sharpe(n_trials: int, sr_var: float) -> float:
    """N bağımsız denemede, gerçek avantaj SIFIR iken beklenen en yüksek Sharpe (işlem başına)."""
    if n_trials <= 1 or sr_var <= 0:
        return 0.0
    a = _N.inv_cdf(1 - 1 / n_trials)
    b = _N.inv_cdf(1 - 1 / (n_trials * math.e))
    return math.sqrt(sr_var) * ((1 - EULER_GAMMA) * a + EULER_GAMMA * b)


def deflated_sharpe(r_values: Sequence[float], n_trials: int = 1) -> dict | None:
    """İşlem başına Sharpe (ort/std R), deneme sayısına göre düzeltilmiş olasılık (DSR). n < 10 → None."""
    x = [float(v) for v in r_values if is_finite_number(v)]
    n = len(x)
    if n < 10:
        return None
    m, sd, skew, kurt = _moments(x)
    if sd == 0:
        return None
    sr = m / sd
    sr_var = (1 - skew * sr + (kurt - 1) / 4 * sr ** 2) / (n - 1)       # Sharpe tahmininin varyansı
    sr0 = expected_max_sharpe(max(1, int(n_trials)), sr_var)
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    z = (sr - sr0) * math.sqrt(n - 1) / denom
    return {"sharpe_islem": sr, "sharpe_esik": sr0, "dsr": _N.cdf(z), "denemeler": int(n_trials), "n": n}


# ── işlem normalizasyonu ─────────────────────────────────────────────

def _legacy_rows(closed: Iterable[Mapping]) -> list[dict]:
    rows = []
    for t in closed:
        r = t.get("r")
        if not is_finite_number(r):
            continue
        mgr = str(t.get("mgr") or "")
        strategy = LEGACY_STRATEGY.get(mgr, (f"legacy_{mgr or 'bilinmeyen'}", None))[0]
        fees = float(t["fee_usd"]) if is_finite_number(t.get("fee_usd")) else \
            sum(float(t.get(k) or 0.0) for k in ("fee_entry_usd", "fee_exit_usd"))
        rows.append({"kaynak": "legacy", "strategy": strategy, "mgr": mgr, "golge": bool(t.get("shadow")),
                     "r": float(r), "net_usd": float(t.get("pnl_usd") or 0.0), "cost_usd": fees,
                     "funding_usd": float(t.get("funding_usd") or 0.0), "rejim": _regime_txt(t.get("regime")),
                     "ufuk": _horizon(t.get("tf")), "ts": int(t.get("exit_ts") or 0), "carry": bool(t.get("carry"))})
    return rows


def _unified_rows(closed: Iterable[Mapping]) -> list[dict]:
    rows = []
    for c in closed:
        r = c.get("r")
        if not is_finite_number(r):
            continue
        raw_scope = str(c.get("execution_scope") or "unknown")
        mode = raw_scope if raw_scope in ("unified", "shadow") else "unknown"
        rows.append({"kaynak": "birlesik", "strategy": c.get("strategy") or "?", "mgr": None,
                     "golge": mode == "shadow", "mode": mode,
                     "r": float(r), "net_usd": float(c.get("net_usd") or 0.0), "cost_usd": float(c.get("fees_usd") or 0.0),
                     "funding_usd": float(c.get("funding_usd") or 0.0), "rejim": c.get("regime") or "?",
                     "ufuk": c.get("horizon") or _horizon(c.get("timeframe")),
                     "entry_ts": int(c.get("opened_ts") or 0), "ts": int(c.get("closed_ts") or 0),
                     "carry": False})
    return rows


def _regime_txt(v: Any) -> str:
    if v in (1, "1"):
        return "boga"
    if v in (-1, "-1"):
        return "ayi"
    if v in (0, "0"):
        return "yatay"
    return str(v) if v is not None else "?"


def _horizon(tf: Any) -> str:
    tf = str(tf or "")
    if tf in ("1D", "1W", "3D"):
        return "swing"
    if tf in ("1H", "2H", "4H"):
        return "intraday"
    if tf in ("1m", "5m", "15m", "30m"):
        return "micro"
    return "?"


# ── denetçi ──────────────────────────────────────────────────────────

class EffectivenessAuditor:
    def __init__(self, cfg: Mapping | None = None, root: str | Path | None = None):
        self.cfg = dict(cfg or {})
        self.s = audit_settings(self.cfg)
        self.root = Path(root) if root else None

    # deneme kaydı ------------------------------------------------------
    @property
    def trials_path(self) -> Path | None:
        return self.root / "denemeler.json" if self.root else None

    def trials(self) -> dict[str, int]:
        p = self.trials_path
        try:
            return {k: int(v) for k, v in json.loads(p.read_text(encoding="utf-8")).items()} if p and p.exists() else {}
        except (OSError, ValueError):
            return {}

    def register_trials(self, strategy: str, n: int) -> int:
        """Bir strateji için denenen kural/parametre varyantı sayısını artırır (aynı veride her deneme sayılır)."""
        t = self.trials()
        t[strategy] = t.get(strategy, 0) + int(n)
        if self.trials_path:
            self.root.mkdir(parents=True, exist_ok=True)
            self.trials_path.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
        return t[strategy]

    # değerlendirme -----------------------------------------------------
    def snapshot(self, scope: str, rows: Sequence[dict], now: int, n_trials: int = 1) -> dict:
        rs = [r["r"] for r in rows]
        net = sum(r["net_usd"] for r in rows)
        cost = sum(r["cost_usd"] for r in rows)
        fund = sum(r["funding_usd"] for r in rows)
        snap = EffectivenessSnapshot.from_r(scope, now, rs, net_pnl_usd=net, cost_usd=cost, funding_usd=fund)
        d = to_jsonable(snap)
        gross = net + cost
        d["maliyet_payi"] = (cost / gross) if gross > 0 else None             # brüt kârın ne kadarı maliyete gitti
        d["dsr"] = deflated_sharpe(rs, n_trials)
        d["karar"], d["karar_tr"] = self.verdict(snap, d["dsr"])
        if scope.startswith("strateji:") and ":birlesik:paper" in scope:
            # Forward evidence spans actual PAPER entry → final exit timestamps.
            # Missing entry provenance means the 14-day requirement is not proven.
            entries = [int(row.get("entry_ts") or 0) for row in rows]
            exits = [int(row.get("ts") or 0) for row in rows]
            evidence_ok = bool(entries) and all(ts > 0 for ts in entries + exits)
            d["evidence_window_days"] = (
                round((max(exits) - min(entries)) / 86_400_000, 2) if evidence_ok else 0.0
            )
        else:
            timestamps = [int(row.get("ts") or 0) for row in rows if int(row.get("ts") or 0) > 0]
            d["evidence_window_days"] = (
                round((max(timestamps) - min(timestamps)) / 86_400_000, 2)
                if len(timestamps) >= 2 else 0.0
            )
        # Unified PAPER için 100 kapanmış işlem ve istatistiksel kanıt tek başına
        # yeterli değildir: gerçek forward kayıtları en az 14 güne yayılmalıdır.
        # Legacy/backtest sınıfları bu canlı PAPER kapısını taklit edemez.
        if (scope.startswith("strateji:") and ":birlesik:paper" in scope
                and d["karar"] == "terfi_adayi" and d["evidence_window_days"] < 14.0):
            d["karar"] = "kanit_yetersiz"
            d["karar_tr"] = (
                f"100 işlem ve istatistik eşiği sağlandı; ancak Unified PAPER forward kanıtı "
                f"{d['evidence_window_days']:.2f}/14 gün. Gözlem sürmeli."
            )
        return d

    def verdict(self, snap: EffectivenessSnapshot, dsr: dict | None) -> tuple[str, str]:
        n = snap.n_trades
        if n >= int(self.s["kapat_min_islem"]) and snap.ci_high_r is not None and snap.ci_high_r < 0:
            return "kapat_adayi", f"{n} işlemde ortalama R'nin üst güven sınırı bile negatif ({snap.ci_high_r:+.2f}R)"
        if n >= int(self.s["terfi_min_islem"]) and snap.proven_positive and dsr and dsr["dsr"] >= float(self.s["dsr_esik"]):
            return "terfi_adayi", (f"{n} işlem, alt güven sınırı {snap.ci_low_r:+.2f}R > 0 ve deneme sayısına göre "
                                   f"düzeltilmiş olasılık %{dsr['dsr'] * 100:.0f}")
        need = max(0, int(self.s["terfi_min_islem"]) - n)
        why = f"{n} işlem" + (f" (terfi için en az {need} işlem daha)" if need else "")
        if snap.ci_low_r is not None and snap.ci_low_r <= 0:
            why += f"; alt güven sınırı {snap.ci_low_r:+.2f}R (sıfırı içeriyor: şans olabilir)"
        return "kanit_yetersiz", why

    def audit(self, legacy_closed: Iterable[Mapping] = (), unified_closed: Iterable[Mapping] = (),
              now_ms: int | None = None, since_ms: int | None = None) -> dict:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        rows = _legacy_rows(legacy_closed) + _unified_rows(unified_closed)
        if since_ms is not None:
            rows = [r for r in rows if r["ts"] >= since_ms]
        trials = self.trials()
        out: dict[str, Any] = {"ts": now, "kapsamlar": {}, "karsilastirma": {}, "notlar": []}
        if not trials:
            out["notlar"].append("deneme kaydı yok: her strateji 1 deneme sayıldı (düzeltme en gevşek hâlinde)")
        groups: dict[str, list[dict]] = {}

        def add(key, r):
            groups.setdefault(key, []).append(r)

        for r in rows:
            if r["carry"]:
                add("carry", r)
                continue
            mode = r.get("mode") if r["kaynak"] == "birlesik" else ("golge" if r["golge"] else "paper")
            add(f"sistem:{r['kaynak']}:{mode}", r)
            add(f"strateji:{r['strategy']}:{r['kaynak']}:{mode}", r)
            add(f"rejim:{r['rejim']}:{r['kaynak']}:{mode}", r)
            add(f"ufuk:{r['ufuk']}:{r['kaynak']}:{mode}", r)
        for key, rs in sorted(groups.items()):
            strat = key.split(":")[1] if key.startswith("strateji:") else None
            out["kapsamlar"][key] = self.snapshot(key, rs, now, trials.get(strat, 1) if strat else max(1, sum(trials.values()) or 1))
        # karşılaştırmalar: aynı strateji legacy ↔ birleşik, gölge ↔ paper
        strategies = {r["strategy"] for r in rows if not r["carry"]}
        for s in sorted(strategies):
            k = out["kapsamlar"]
            cmp = {}
            for a, b, label in ((f"strateji:{s}:legacy:paper", f"strateji:{s}:birlesik:paper", "legacy_birlesik"),
                                (f"strateji:{s}:legacy:golge", f"strateji:{s}:legacy:paper", "golge_paper")):
                if a in k and b in k:
                    ka, kb = k[a], k[b]
                    cmp[label] = {"a": a, "b": b, "n_a": ka["n_trades"], "n_b": kb["n_trades"],
                                  "avg_r_a": ka["avg_r"], "avg_r_b": kb["avg_r"],
                                  "fark_r": (kb["avg_r"] - ka["avg_r"]) if ka["avg_r"] is not None and kb["avg_r"] is not None else None,
                                  "not": "iki taraf da 30+ işleme ulaşmadan fark yorumlanmaz"
                                  if min(ka["n_trades"], kb["n_trades"]) < 30 else ""}
            if cmp:
                out["karsilastirma"][s] = cmp
        out["ozet"] = {
            "terfi_adayi": sorted(k for k, v in out["kapsamlar"].items() if k.startswith("strateji:") and v["karar"] == "terfi_adayi"),
            "kapat_adayi": sorted(k for k, v in out["kapsamlar"].items() if k.startswith("strateji:") and v["karar"] == "kapat_adayi"),
            "islem_sayisi": len(rows)}
        return out

    def write(self, report: Mapping) -> Path | None:
        if not self.root:
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.root / "etkinlik.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(to_jsonable(report), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(p)
        return p


def race_rows_from_csv(path: str | Path) -> list[dict]:
    """`race_islemler.csv` (radar/race.write_trades_csv) → legacy işlem sözlükleri (yalnız R; dolar yok)."""
    import csv
    out = []
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                r = float(row["r"])
            except (TypeError, ValueError):
                continue
            out.append({"mgr": row["yonetici"], "r": r, "pnl_usd": 0.0, "fee_usd": 0.0, "tf": "1D",
                        "regime": row.get("donem") or "?", "exit_ts": 0, "shadow": False})
    return out


def audit_race(path: str | Path, cfg: Mapping | None = None, root: str | Path | None = None,
               now_ms: int | None = None) -> dict:
    """Geçmiş test (yarış) işlemlerinin etkinlik raporu. `rejim` kapsamı burada DÖNEM'i (egitim/dogrulama) taşır.
    Dolar tutarları yoktur (yalnız R); maliyet yarışta zaten R'ye işlenmiştir."""
    rep = EffectivenessAuditor(cfg, root).audit(race_rows_from_csv(path), now_ms=now_ms)
    rep["kaynak"] = f"yarış: {Path(path).name}"
    rep["notlar"].append("geçmiş test: 'rejim:*' kapsamları dönemi gösterir (egitim / dogrulama)")
    return rep
