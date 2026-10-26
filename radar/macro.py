"""Olay takvimi: yüksek etkili ABD verileri ve Fed kararları çevresinde yeni işlem açılmaz.

Kaynaklar (ücretsiz):
  - Fed (FOMC) karar günleri: Federal Reserve'in yayımladığı takvim (2026 kesin, 2027 geçici). Karar saati 14:00 New York.
  - Haftalık ekonomik takvim JSON (ForexFactory/faireconomy): 'USD' ve 'High' etkili olaylar (TÜFE, istihdam, GSYH...).
Takvim alınamazsa yalnızca Fed tarihleri kullanılır (sistem durmaz).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
FOMC_DAYS = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28",
             "2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15",
             "2027-10-27", "2027-12-08"]
FF_URLS = ["https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json",
           "https://nfs.faireconomy.media/ff_calendar_thisweek.json"]


def fomc_events() -> list[dict]:
    out = []
    for d in FOMC_DAYS:
        y, m, dd = (int(x) for x in d.split("-"))
        ts = datetime(y, m, dd, 14, 0, tzinfo=NY).astimezone(timezone.utc)
        out.append({"ts": int(ts.timestamp() * 1000), "title": "Fed faiz kararı (FOMC)", "src": "fed"})
    return out


def parse_ff(rows) -> list[dict]:
    out = []
    for r in rows or []:
        try:
            if str(r.get("country", "")).upper() != "USD" or str(r.get("impact", "")).lower() != "high":
                continue
            dt = datetime.fromisoformat(str(r["date"]).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            out.append({"ts": int(dt.timestamp() * 1000), "title": str(r.get("title", ""))[:80], "src": "takvim"})
        except Exception:  # noqa: BLE001
            continue
    return out


class Calendar:
    def __init__(self, before_min: int = 60, after_min: int = 60):
        self.before, self.after = before_min * 60_000, after_min * 60_000
        self.events = fomc_events()
        self.fetched = 0.0
        self.status = "yalnızca Fed takvimi"

    def refresh(self, http_get=None):
        """http_get(url) → JSON listesi. 6 saatte bir çağrılır."""
        if http_get is None:
            import requests

            def http_get(u):
                r = requests.get(u, timeout=15, headers={"User-Agent": "Mozilla/5.0 kripto-radar"})
                r.raise_for_status()
                return r.json()
        self.fetched = time.time()
        for u in FF_URLS:
            try:
                ev = parse_ff(http_get(u))
                keep = [e for e in self.events if e["src"] == "fed"]
                seen = {(e["ts"] // 60000) for e in keep}
                self.events = keep + [e for e in ev if (e["ts"] // 60000) not in seen]
                self.status = f"takvim alındı ({len(ev)} yüksek etkili ABD olayı bu hafta)"
                return True
            except Exception as e:  # noqa: BLE001
                self.status = f"takvim alınamadı ({type(e).__name__}); yalnızca Fed takvimi"
        return False

    def block(self, now_ms: int | None = None) -> str | None:
        now_ms = now_ms or int(time.time() * 1000)
        for e in self.events:
            if e["ts"] - self.before <= now_ms <= e["ts"] + self.after:
                when = datetime.fromtimestamp(e["ts"] / 1000, tz=ZoneInfo("Europe/Istanbul")).strftime("%H:%M")
                return f"olay takvimi: {e['title']} ({when} TR) ±{self.before // 60000} dk"
        return None

    def upcoming(self, now_ms: int | None = None, hours: int = 48) -> list[dict]:
        now_ms = now_ms or int(time.time() * 1000)
        return sorted([e for e in self.events if now_ms - self.after <= e["ts"] <= now_ms + hours * 3600_000],
                      key=lambda e: e["ts"])[:8]


def weekend_mult(now_ms: int | None = None, tf: str = "1D") -> float:
    """Hafta sonu düşük likidite: günlük olmayan işlemlerde risk %75."""
    if tf == "1D":
        return 1.0
    d = datetime.fromtimestamp((now_ms or time.time() * 1000) / 1000, tz=timezone.utc)
    return 0.75 if d.weekday() >= 5 else 1.0
