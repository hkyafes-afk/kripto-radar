"""v10 politika: 5 yıllık laboratuvarın (tools/lab.py) eğitimde bulup görmediği dönemde doğruladığı kurallar.

Kanıt (data/LAB.md; evren: her gün o günün en likit 100 kripto vadelisi, silinen coinler dahil, 2020–2026;
eğitim < 2025-01-01 ≤ doğrulama; masraf + gerçek funding sonrası):
  temel ekip            eğitim +0,02R  doğrulama −0,00R  maks. düşüş −%43
  bu politikayla        eğitim +0,43R  doğrulama +0,17R  maks. düşüş −%14   (kazanma %51 / %41)

Kurallar (canlı masa, bt2 ve laboratuvar AYNI fonksiyonları kullanır):
  1. Oynaklık kapısı: günlük ATR fiyatın %5'inden büyükse fikir gerçek işlem olmaz, gölgede ölçülür.
     ATR %3–5 kovası bütün yöneticilerde artı, %5–12 kovası sıfır/eksi (iki dönemde de). Akademik karşılığı
     "düşük oynaklık anomalisi": yüksek riskli coinler düşük riskliden kötü getiri (Kwok ve ark., J. Empirical
     Finance 2023, 4.000+ coin, 2015–2022).
  2. Kalabalık kapısı: funding yıllık %30'un üstündeyken long, −%20'nin altındayken short gölgede.
     Long'lar funding %30–60'ta −0,09R, %60+'da −0,14R (eğitim); doğrulamada da eksi. BIS (WP 1087): yüksek carry
     sonraki çöküşleri öngörüyor.
  3. Kapanış stop'u: ilk stop gün içi fitille değil GÜNLÜK KAPANIŞLA tetiklenir (çıkış hemen); gün içinde yalnızca
     2R'lik felaket stop'u durur. Stop başabaşa/iz seviyesine taşındıktan sonra normal (gün içi) stop çalışır.
     Kripto fitilleri stop avlar: kapanış stop'u kazanma oranını ~2–3 puan, ortalama R'yi yükseltti.
  4. Yeniden giriş: aynı yönetici aynı coinde aynı yönde ZARARLA çıktıysa 5 gün o yöne girmez; KÂRLA çıktıysa
     ancak fiyat çıkıştan beri görülen en iyi kapanışı aşarsa (yeni zirve/dip) yeniden girer.
     (Baş Trader ve Turbo hariç: kendi puan çıkışları ve piramitleri var; testte bu kural onların getirisini düşürdü.)
  5. Kaan (kırılım): BTC havası yatayken kırılımlar iki dönemde de eksi (−0,29R / −0,40R) → gölgede.
  6. Ayşe oynaklık kapısından muaf: kapitülasyon günleri tanım gereği oynak; kapanış stop'u + kalabalık kapısıyla
     eğitim +0,24R / doğrulama +0,30R, kazanma %66 / %72 (130 + 32 işlem).
  7. v10.2 rejim kapısı: sermaye yalnızca BTC havasıyla uyumlu fikre gider (boğada long, ayıda short; yatayda hiçbiri).
     Uyumsuz fikir REDDEDİLMEZ, gölgede açılıp ölçülür. Kanıt (tools/lab.py --set rejim, 28.10.2026): v10 kurallarıyla
     5 yılda ekibin kârının %96'sı bu iki kovadan geldi; uyumsuz işlemler eğitimde +0,20R, doğrulamada −0,13R.
     Üç coin evreninde (en likit 100 / en likit 30 / en az 1 yaşında) doğrulama ortalaması +0,17→+0,29R,
     +0,23→+0,28R, +0,10→+0,24R; doğrulama maks. düşüşü üçünde de azaldı. Eğitimde toplam R neredeyse aynı: kural yeni
     kâr üretmez, sıfıra yakın getirili işlemleri sermayeden çıkarır. Elif (carry) ve Ortak Masa kapsam dışı.
Kâr kilitleme merdiveni / tepe kârın %25–40'ını geri vermeden çıkma test edildi: kazanma oranı +3 puan ama ortalama
R düştü (büyük kazananları erken kesiyor) → uygulanmadı; mevcut iz süren stop kaldı. v10.2'de yeniden sınandı
(+1,5R'de stop +0,5R'ye): eğitim +0,434→+0,376R → reddedildi. Stop mesafesini ×1,33/×1,67 açmak da iki dönemde
düşürdü; boğada short yasağı etkisiz (günlük ajanlar 5 yılda boğada 4 short açtı).
"""
from __future__ import annotations

VERSION = "v10.2"                  # her yeni işleme yazılır (ledger "politika"): canlı sonuç sürüme göre ayrılabilsin
DAY_MS = 86_400_000
NO_REENTRY_RULE = ("bas", "turbo")
REJIM_TEXT = {"boga": "boğa", "ayi": "ayı", "yatay": "yatay"}

DEFAULTS = {
    "aktif": True,
    "max_atr_yuzde": 5.0,          # günlük ATR / fiyat (%) üstü → gölge
    "max_stop_yuzde": 15.0,        # giriş–stop uzaklığı fiyatın bu %'sinden fazlaysa → gölge (v10.1: geniş stop zararlı)
    "kalabalik_long": 30.0,        # funding yıllık % üstünde long → gölge
    "kalabalik_short": -20.0,      # funding yıllık % altında short → gölge
    "kapanis_stop": True,          # 1G işlemlerde ilk stop günlük kapanışla
    "felaket_r": 2.0,              # kapanış stop'unun gün içi güvenlik ağı (R)
    "zarar_sonrasi_bekle_gun": 5,
    "kar_sonrasi_yeni_zirve": True,
    "kaan_yatay_golge": True,
    "atr_muaf": ["ayse"],          # aşırılık avcısı: tanım gereği oynak günlerde çalışır (testte kapı olmadan da artı)
    "rejim_uyumu": True,           # v10.2: long yalnız BTC boğayken, short yalnız ayıyken gerçek; gerisi gölge
}


def cfg_of(cfg: dict | None) -> dict:
    p = dict(DEFAULTS)
    p.update((((cfg or {}).get("ekip") or {}).get("kurallar")) or {})
    return p


def funding_ann(idea: dict, ctx: dict | None = None) -> float | None:
    fa = (ctx or {}).get("funding_ann")
    if isinstance(fa, (int, float)) and fa == fa:
        return float(fa)
    fa = idea.get("funding_ann")
    if isinstance(fa, (int, float)) and fa == fa:
        return float(fa)
    r, iv = idea.get("funding_rate"), idea.get("interval_h") or 8
    if isinstance(r, (int, float)) and r == r and r != 0:
        return float(r) * (8760 / iv) * 100
    return None


def atr_pct(idea: dict) -> float | None:
    a, px = idea.get("atr"), idea.get("entry") or idea.get("market_px")
    if a and px and a == a and px > 0:
        return a / px * 100
    return None


def gate(idea: dict, ctx: dict | None, cfg: dict | None) -> str | None:
    """Gerçek işlem yerine gölgeye alma sebebi (ya da None). Yalnızca günlük (1D) yön fikirlerine uygulanır."""
    P = cfg_of(cfg)
    if not P["aktif"] or idea.get("tf") != "1D" or idea.get("kind") in ("carry", "consensus"):
        return None
    ap = atr_pct(idea)
    if (P.get("max_atr_yuzde") and ap is not None and ap > P["max_atr_yuzde"]
            and idea.get("mgr") not in (P.get("atr_muaf") or [])):
        return (f"oynaklık kapısı: günlük ATR fiyatın %{ap:.1f}'i (> %{P['max_atr_yuzde']:g}); 5 yıllık testte bu kovada "
                "kazanç yok → gölgede ölçülüyor")
    entry = idea.get("entry") or idea.get("market_px")
    if P.get("max_stop_yuzde") and entry and idea.get("sl") and entry > 0:
        sp = abs(entry - idea["sl"]) / entry * 100
        if sp > P["max_stop_yuzde"]:
            return (f"stop kapısı: giriş–stop uzaklığı fiyatın %{sp:.1f}'i (> %{P['max_stop_yuzde']:g}); "
                    "geniş stop'lu işlemler 5 yıllık testte kazanmadı → gölgede ölçülüyor")
    fa = funding_ann(idea, ctx)
    if fa is not None:
        if idea["side"] == "long" and P.get("kalabalik_long") is not None and fa > P["kalabalik_long"]:
            return f"kalabalık kapısı: funding yıllık %{fa:.0f} (long tarafı kalabalık) → gölgede"
        if idea["side"] == "short" and P.get("kalabalik_short") is not None and fa < P["kalabalik_short"]:
            return f"kalabalık kapısı: funding yıllık %{fa:.0f} (short tarafı kalabalık) → gölgede"
    w = (ctx or {}).get("w_trend")
    if P.get("kaan_yatay_golge") and idea.get("mgr") == "kaan" and w == "yatay":
        return "Kaan kuralı: BTC havası yatay; kırılımlar yatay piyasada 5 yılda iki dönemde de eksi → gölgede"
    if P.get("rejim_uyumu") and w in REJIM_TEXT:     # hava bilinmiyorsa (veri yok) kapı uygulanmaz
        if not ((idea["side"] == "long" and w == "boga") or (idea["side"] == "short" and w == "ayi")):
            return (f"rejim kapısı: BTC havası {REJIM_TEXT[w]}, fikir {idea['side']}; 5 yıllık testte kârın %96'sı boğada "
                    "long ve ayıda short işlemlerinden geldi → gölgede ölçülüyor")
    return None


def reentry_block(closed: list[dict], idea: dict, now_ms: int, cfg: dict | None,
                  recent_closes: list | None = None) -> str | None:
    """Aynı yönetici + coin + yön için yeniden giriş kuralı. recent_closes: [(ts_ms, kapanış), ...] (kapanmış günler)."""
    P = cfg_of(cfg)
    if not P["aktif"] or idea.get("mgr") in NO_REENTRY_RULE or idea.get("tf") != "1D":
        return None
    prev = [t for t in closed if t.get("mgr") == idea["mgr"] and t.get("inst") == idea["inst"] and t.get("exit_ts")]
    if not prev:
        return None
    last = max(prev, key=lambda t: t["exit_ts"])
    if last.get("side") != idea["side"]:
        return None
    won = (last.get("r") or last.get("pnl_usd") or 0) > 0
    if not won:
        wait = float(P.get("zarar_sonrasi_bekle_gun") or 0) * DAY_MS
        if now_ms - last["exit_ts"] < wait:
            left = (wait - (now_ms - last["exit_ts"])) / DAY_MS
            return f"yeniden giriş kuralı: bu coinde bu yönde zararla çıkıldı; {left:.1f} gün daha beklenir"
        return None
    if P.get("kar_sonrasi_yeni_zirve") and recent_closes:
        since = [c for ts, c in recent_closes if ts >= last["exit_ts"] - DAY_MS]
        px = idea.get("entry") or idea.get("market_px")
        if since and px:
            best = max(since) if idea["side"] == "long" else min(since)
            if (idea["side"] == "long" and px <= best) or (idea["side"] == "short" and px >= best):
                return ("yeniden giriş kuralı: kârla çıkılan coinde yeni giriş için fiyatın çıkıştan beri görülen en iyi "
                        f"kapanışı ({best:.6g}) aşması gerekir")
    return None


def close_stop_levels(entry: float, sl: float, side: str, cfg: dict | None) -> tuple[float, float] | None:
    """Kapanış stop'u: döner (gün içi felaket stop'u, kapanış stop seviyesi) ya da None (kapalı)."""
    P = cfg_of(cfg)
    if not (P["aktif"] and P.get("kapanis_stop")):
        return None
    sgn = 1 if side == "long" else -1
    r = abs(entry - sl)
    dz = float(P.get("felaket_r") or 2.0)
    disaster = sl - sgn * (dz - 1.0) * r
    if sgn > 0 and disaster <= 0:
        disaster = sl * 0.5
    return disaster, sl
