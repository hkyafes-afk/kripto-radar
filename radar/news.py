"""Haber Masası: ücretsiz kaynaklardan haber toplar, coinlere bağlar, risk uyarısı üretir.

Araştırma (PLAN.md): forum/sosyal duygu fiyatı TAHMİN ETMİYOR (fiyat duyguyu belirliyor); listeleme kazancı duyuru
öncesinde oluşup 1–2 haftada geri veriliyor. Bu yüzden Haber Masası al-sat sinyali VERMEZ; sigortadır:
  kırmızı (hack, açık, borsadan çıkarma, düzenleyici darbe) → o coinde yeni işlem yok (72 saat; çıkarma 30 gün)
  sarı    (büyük borsada yeni listeleme, olağandışı ilgi patlaması) → yükselişin peşine düşülmez (long yok, 7 gün / 48 saat)
  ortam   (Korku ve Açgözlülük Endeksi uçları) → hafif risk ayarı

Kaynaklar: OKX / Bybit / Binance duyuruları, Cointelegraph / CoinDesk / Decrypt / The Block RSS, alternative.me
Korku-Açgözlülük, Reddit r/CryptoCurrency, DefiLlama hack listesi. Her kaynak ayrı ayrı denenir; biri çökerse
diğerleri çalışır. Sınıflandırma kural tabanlıdır (anahtar kelime + coin eşleşmesi).
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

RED_WORDS = ["hack", "hacked", "exploit", "exploited", "drained", "stolen", "breach", "attacker", "vulnerability",
             "rug pull", "rugpull", "depeg", "de-peg", "insolven", "bankrupt", "halts withdrawals", "suspends withdrawals",
             "pauses withdrawals", "sec sues", "sec charges", "charged by", "lawsuit against", "delist", "delisting",
             "will remove", "frozen", "freeze", "manipulation probe", "arrested"]
MAJOR_SAFE = {"BTC", "ETH", "SOL", "BNB", "XRP", "USDT", "USDC"}     # çalınan varlık olarak geçer, zincir riski değildir
MAJOR_RED_WORDS = ["network halt", "chain halt", "51% attack", "halted block", "consensus bug", "mainnet halted"]
AMBIGUOUS = {"ONE", "NOT", "GAS", "BAT", "MEME", "HOT", "TRUMP", "AI", "ME", "IO", "MOVE", "BIO", "S", "T", "U", "W"}
NAMES = {"bitcoin": "BTC", "ethereum": "ETH", "ether": "ETH", "solana": "SOL", "ripple": "XRP", "dogecoin": "DOGE",
         "cardano": "ADA", "avalanche": "AVAX", "chainlink": "LINK", "polkadot": "DOT", "litecoin": "LTC",
         "toncoin": "TON", "aptos": "APT", "arbitrum": "ARB", "optimism": "OP", "injective": "INJ", "celestia": "TIA",
         "uniswap": "UNI", "aave": "AAVE", "hyperliquid": "HYPE", "tron": "TRX", "stellar": "XLM", "hedera": "HBAR",
         "filecoin": "FIL", "cosmos": "ATOM", "near protocol": "NEAR", "ethena": "ENA", "ondo": "ONDO",
         "jupiter": "JUP", "worldcoin": "WLD", "shiba inu": "SHIB", "bonk": "BONK", "pendle": "PENDLE",
         "lido": "LDO", "curve finance": "CRV", "polygon": "POL", "sui network": "SUI", "binance coin": "BNB",
         "pepe": "PEPE", "dogwifhat": "WIF", "render network": "RENDER", "bittensor": "TAO", "kaspa": "KAS",
         "monero": "XMR", "ethereum classic": "ETC", "bitcoin cash": "BCH", "internet computer": "ICP",
         "algorand": "ALGO", "the sandbox": "SAND", "decentraland": "MANA", "axie infinity": "AXS",
         "starknet": "STRK", "zksync": "ZK", "eigenlayer": "EIGEN", "jito": "JTO", "pyth": "PYTH", "raydium": "RAY"}
RSS = [("Cointelegraph", "https://cointelegraph.com/rss"),
       ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
       ("Decrypt", "https://decrypt.co/feed"),
       ("The Block", "https://www.theblock.co/rss.xml")]
RED_H, DELIST_D, LIST_D, ATTN_H = 72, 30, 7, 48


def _now_ms():
    return int(time.time() * 1000)


def mentions(text: str, universe: set[str]) -> set[str]:
    """Başlıkta geçen coinler: büyük harf sembol (kelime sınırıyla), $SEMBOL ya da tam ad."""
    found = set()
    for m in re.finditer(r"\$([A-Z0-9]{2,10})\b", text):
        if m.group(1) in universe:
            found.add(m.group(1))
    for m in re.finditer(r"(?<![A-Za-z0-9$])([A-Z][A-Z0-9]{2,9})(?![A-Za-z0-9])", text):
        s = m.group(1)
        if s in universe and s not in AMBIGUOUS:
            found.add(s)
    low = text.lower()
    for name, sym in NAMES.items():
        if sym in universe and re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", low):
            found.add(sym)
    return found


def classify(title: str, syms: set[str]) -> tuple[str | None, set[str]]:
    """Döner: (seviye, etkilenen coinler). Büyük coinler sadece zincir seviyesinde olaylarda kırmızı olur."""
    low = title.lower()
    if any(w in low for w in MAJOR_RED_WORDS):
        return "kirmizi", syms
    if any(w in low for w in RED_WORDS):
        hit = {s for s in syms if s not in MAJOR_SAFE}
        return ("kirmizi", hit) if hit else (None, set())
    return None, set()


class NewsDesk:
    def __init__(self):
        self.items: list[dict] = []          # son haberler (panel)
        self.flags: dict[str, dict] = {}     # sym → {level, title, src, ts, until}
        self.fng: dict | None = None
        self.status: dict[str, str] = {}
        self.attn_ema: dict[str, float] = {}
        self.attn_n = 0
        self.seen: set[str] = set()
        self.new_alerts: list[dict] = []     # motorun Telegram'a ileteceği yeni kırmızı/sarı uyarılar
        self.delist_okx: set[str] = set()    # OKX'ten çıkarılacak semboller (açık pozisyon kapatılır)

    # ── durum (devirde kaybolmasın: aynı uyarı her döngüde tekrar gönderilmesin) ──
    def to_state(self) -> dict:
        now = _now_ms()
        return {"flags": {k: v for k, v in self.flags.items() if v["until"] > now}, "fng": self.fng,
                "seen": list(self.seen)[-1500:], "attn_ema": self.attn_ema, "attn_n": self.attn_n,
                "items": self.items[:60]}

    def from_state(self, st: dict):
        self.flags = dict(st.get("flags") or {})
        self.fng = st.get("fng")
        self.seen = set(st.get("seen") or [])
        self.attn_ema = dict(st.get("attn_ema") or {})
        self.attn_n = int(st.get("attn_n") or 0)
        self.items = list(st.get("items") or [])

    # ── dış arayüz ──
    def flag(self, sym: str, now_ms: int | None = None) -> dict | None:
        f = self.flags.get(sym)
        if f and f["until"] > (now_ms or _now_ms()):
            return f
        return None

    def fng_mult(self, side: str) -> float:
        v = (self.fng or {}).get("value")
        if v is None:
            return 1.0
        if v >= 80 and side == "long":
            return 0.8
        if v <= 20 and side == "short":
            return 0.8
        return 1.0

    def public(self, now_ms: int | None = None) -> dict:
        now_ms = now_ms or _now_ms()
        act = {s: f for s, f in self.flags.items() if f["until"] > now_ms}
        return {"fng": self.fng, "flags": act, "items": self.items[:40], "status": self.status}

    # ── kayıt ──
    def _add(self, level, syms, title, src, url, ts, hours):
        key = f"{src}|{title}"[:200]
        new = key not in self.seen
        self.seen.add(key)
        until = ts + int(hours * 3600_000)
        for s in syms:
            cur = self.flags.get(s)
            rank = {"kirmizi": 2, "sari": 1}
            if not cur or cur["until"] < _now_ms() or rank[level] > rank[cur["level"]] or until > cur["until"]:
                self.flags[s] = {"level": level, "title": title[:160], "src": src, "url": url, "ts": ts, "until": until}
                if new and until > _now_ms():
                    self.new_alerts.append({"sym": s, **self.flags[s]})

    def _item(self, title, src, url, ts, syms, level):
        self.items.append({"title": title[:180], "src": src, "url": url, "ts": ts, "syms": sorted(syms), "level": level})

    # ── kaynaklar ──
    def refresh(self, universe: set[str], get_json, get_text):
        """get_json(url) → JSON, get_text(url) → str. Her kaynak bağımsız; hata diğerlerini durdurmaz."""
        t_cut = _now_ms() - 7 * 86400_000
        self.items = [x for x in self.items if x["ts"] >= t_cut]
        for name, fn in (("OKX duyuruları", self._okx), ("Bybit duyuruları", self._bybit),
                         ("Binance duyuruları", self._binance), ("Haber akışları", self._rss),
                         ("Korku-Açgözlülük", self._fng), ("Reddit", self._reddit), ("DefiLlama hack", self._llama)):
            try:
                n = fn(universe, get_json if name != "Haber akışları" else get_text)
                self.status[name] = f"tamam ({n})"
            except Exception as e:  # noqa: BLE001
                self.status[name] = f"alınamadı: {type(e).__name__}"
        self.items.sort(key=lambda x: -x["ts"])
        del self.items[120:]
        if len(self.seen) > 4000:
            self.seen = set(list(self.seen)[-2000:])

    def _listing_syms(self, title: str, universe: set[str]) -> set[str]:
        s = mentions(title, universe)
        for m in re.finditer(r"\(([A-Z0-9]{2,10})\)", title):    # "Will List Foo (FOO)"
            if m.group(1) in universe:
                s.add(m.group(1))
        return s

    def _exchange_ann(self, src, rows, universe, okx=False):
        n = 0
        for title, ts, url in rows:
            low = title.lower()
            syms = self._listing_syms(title, universe)
            if not syms:
                continue
            if "delist" in low or "will remove" in low or "removal of" in low:
                self._add("kirmizi", syms, title, src, url, ts, DELIST_D * 24)
                self._item(title, src, url, ts, syms, "kirmizi")
                if okx and ("perpetual" in low or "swap" in low or "delist" in low):
                    self.delist_okx |= syms
                n += 1
            elif any(w in low for w in ("will list", "lists ", "listing", "launches", "new listing", "will add", "adds ")):
                self._add("sari", syms, title, src, url, ts, LIST_D * 24)
                self._item(title, src, url, ts, syms, "sari")
                n += 1
        return n

    def _okx(self, universe, get_json):
        rows = []
        for ann in ("announcements-delistings", "announcements-new-listings"):
            js = get_json(f"https://www.okx.com/api/v5/support/announcements?annType={ann}")
            for blk in (js.get("data") or []):
                for d in blk.get("details", []):
                    rows.append((str(d.get("title", "")), int(float(d.get("pTime") or 0)) or _now_ms(), d.get("url", "")))
        return self._exchange_ann("OKX", rows, universe, okx=True)

    def _bybit(self, universe, get_json):
        rows = []
        for typ in ("delistings", "new_crypto"):
            js = get_json(f"https://api.bybit.com/v5/announcements/index?locale=en-US&type={typ}&limit=20")
            for d in ((js.get("result") or {}).get("list") or []):
                rows.append((str(d.get("title", "")), int(d.get("publishTime") or d.get("dateTimestamp") or _now_ms()),
                             d.get("url", "")))
        return self._exchange_ann("Bybit", rows, universe)

    def _binance(self, universe, get_json):
        rows = []
        for cat in (161, 48):   # 161 = borsadan çıkarma, 48 = yeni listeleme
            js = get_json("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
                          f"?type=1&pageNo=1&pageSize=20&catalogId={cat}")
            for c in ((js.get("data") or {}).get("catalogs") or []):
                for a in c.get("articles", []):
                    rows.append((str(a.get("title", "")), int(a.get("releaseDate") or _now_ms()),
                                 f"https://www.binance.com/en/support/announcement/{a.get('code', '')}"))
        return self._exchange_ann("Binance", rows, universe)

    def _rss(self, universe, get_text):
        n = 0
        for src, url in RSS:
            try:
                root = ET.fromstring(get_text(url))
            except Exception:  # noqa: BLE001
                continue
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                try:
                    ts = int(parsedate_to_datetime(it.findtext("pubDate") or "").timestamp() * 1000)
                except Exception:  # noqa: BLE001
                    ts = _now_ms()
                if ts < _now_ms() - 3 * 86400_000:
                    continue
                syms = mentions(title, universe)
                level, hit = classify(title, syms)
                if level:
                    self._add(level, hit, title, src, link, ts, RED_H)
                    n += 1
                if syms:
                    self._item(title, src, link, ts, hit or syms, level)
        return n

    def _fng(self, universe, get_json):
        js = get_json("https://api.alternative.me/fng/?limit=1")
        d = (js.get("data") or [{}])[0]
        self.fng = {"value": int(d.get("value")), "label": d.get("value_classification"), "ts": _now_ms()}
        return 1

    def _reddit(self, universe, get_json):
        js = get_json("https://www.reddit.com/r/CryptoCurrency/hot.json?limit=100")
        counts: dict[str, int] = {}
        for ch in ((js.get("data") or {}).get("children") or []):
            title = (ch.get("data") or {}).get("title", "")
            for s in mentions(title, universe):
                counts[s] = counts.get(s, 0) + 1
        self.attn_n += 1
        n = 0
        for s in universe:
            c = counts.get(s, 0)
            ema = self.attn_ema.get(s, c)
            if self.attn_n >= 6 and s not in MAJOR_SAFE and c >= 5 and c >= 3 * max(ema, 0.5):
                self._add("sari", {s}, f"Reddit'te ani ilgi patlaması: {c} başlık", "Reddit",
                          "https://www.reddit.com/r/CryptoCurrency/", _now_ms(), ATTN_H)
                n += 1
            self.attn_ema[s] = 0.8 * ema + 0.2 * c
        return n

    def _llama(self, universe, get_json):
        js = get_json("https://api.llama.fi/hacks")
        rows = js if isinstance(js, list) else (js.get("data") or [])
        n = 0
        cut = _now_ms() - 3 * 86400_000
        for h in rows[-200:]:
            ts = int(float(h.get("date") or 0) * 1000)
            if ts < cut:
                continue
            name = str(h.get("name", ""))
            syms = mentions(name, universe) | ({NAMES[name.lower()]} if name.lower() in NAMES and NAMES[name.lower()] in universe else set())
            syms -= MAJOR_SAFE
            if syms:
                amt = h.get("amount")
                title = f"{name} hacklendi" + (f" (~{float(amt) / 1e6:.1f}M$)" if amt else "")
                self._add("kirmizi", syms, title, "DefiLlama", "https://defillama.com/hacks", ts, RED_H)
                self._item(title, "DefiLlama", "https://defillama.com/hacks", ts, syms, "kirmizi")
                n += 1
        return n
