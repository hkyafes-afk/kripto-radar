# DEVAM NOTU — Birleşik Baş Trader dönüşümü (yarıda kalırsa buradan devam)

> Bu dosya her adımdan sonra güncellenir. Yeni bir oturum (ya da kota yenilenince aynı oturum) **önce bunu okur**,
> sonra "Sıradaki adım"dan devam eder. Kullanıcı onayı: tüm aşamalar için önceden verildi (30.10.2026 03:14);
> her küçük adımda tekrar onay istenmez. Kullanıcının kotası Pazartesi 2 Kasım 2026 21:00'de (İstanbul) yenilenir.

## Görev

- Tam talimat: `docs/birlesik/GOREV.md` (kullanıcı dosyası, 966. satırda kesik geliyor).
- Plan: `docs/birlesik/PLAN.md`. Araştırma denetimi: `docs/birlesik/ARASTIRMA_DENETIMI.md`.
- Çalışma dalı: `feat/unified-trader-foundation` (main'den). **main'e merge YOK**, PR kullanıcı incelemesine kalır.

## Değişmez kurallar (özet)

- Gerçek emir, gerçek hesap, API anahtarı YOK. Varsayılan yürütme modu `paper`; başka mod istense bile reddedilir.
- Mevcut canlı davranış (legacy) bozulmaz; yeni sistem feature flag ile (`birlesik.karar_modu: legacy`) kapalı başlar.
- Açık paper pozisyonları ve `kalp` dalındaki defter (ledger.json) bozulmaz.
- Sentetik test verisi test adında/dokümanda "sentetik" diye belirtilir.

## Durum (işaretli = bitti, push edildi)

PR 1 — ortak modeller, veri kapsamı, CostEngine, testler (dal: feat/unified-trader-foundation)
- [x] Dal açıldı, görev ve devam notu yüklendi
- [x] Araştırma denetimi dokümanı (docs/birlesik/ARASTIRMA_DENETIMI.md)
- [x] Uygulama planı (docs/birlesik/PLAN.md)
- [x] radar/unified/models.py — enum + dataclass'lar (MarketContext … EffectivenessSnapshot), eski fikir → Opportunity adaptörü
- [x] radar/unified/coverage.py — FactorValue/FactorSpec/FactorCoverage (zorunlu/bayat/eksik/geçersiz, paper'a yeterlilik)
- [x] radar/unified/costs.py — CostEngine (ledger.fees_cfg, vwap_fill, stop_slip_bp yeniden kullanılır)
- [x] radar/unified/flags.py — karar_modu (legacy|shadow|unified), yürütme modu yalnız paper
- [x] config.yaml'a belgeli `birlesik:` bloğu (mevcut değerler değişmez)
- [x] Testler: tests/test_unified_*.py (180 geçti: 134 eski + 46 yeni) (hepsi + eski testler geçmeli: `python3 -m pytest -q tests`)
- [ ] Push + PR (REST ile: `gh api repos/hkyafes-afk/kripto-radar/pulls -f title=... -f head=feat/unified-trader-foundation -f base=main -f body=...`;
      GraphQL bu ortamda kapalı, `gh pr create` çalışmaz)

PR 2–5: plan `docs/birlesik/PLAN.md` içinde (stratejiler + UnifiedDecisionEngine, RiskGate + PaperExecutionAdapter,
EffectivenessAuditor, dashboard). PR 2 dalı PR 1 dalının üstüne açılır: `feat/unified-decision-engine`.

## Sıradaki adım

1. `radar/unified/` paketini yaz (models → coverage → costs → flags), her modülden sonra test + commit + push.
2. Denetim ve plan dokümanlarını yaz, push.
3. PR'ı REST ile aç, numarasını buraya yaz.

## Bağlam (kaybolmasın)

- Araştırma: `data/TEK_AJAN_ARASTIRMA.md`, kod `tools/arastirma/`. Veri dalları: `binance-veri` (günlük+funding, batanlar
  dahil, 2020-01→2026-10), `binance-sonuc` (Binance yarışları), `arastirma-sonuc` (gün içi sonuçlar).
- Bu ortamdan GitHub artifact indirilemiyor (blob depolama engelli); ağır işler GitHub Actions'ta çalıştırılıp sonuç
  küçük dosya olarak bir dala yazılıyor (ör. `.github/workflows/arastirma-gun-ici.yml`).
- Açık iş: "Binance metrikleri" iş akışı (run 40963164036) OI/long-short indiriyordu; evreni bugünkü ilk 100 coin
  (hayatta kalma yanlılığı var). Sonucu `binance-metrik` dalında olur.
- Denetimden şimdiye kadar (tools/arastirma/sim.py, ajan A, saklı test 2025-01-07→2026-10-27):
  - Sinyal 1 gün geciktirilince sonuç bozulmadı (eğitim +%114, test +%20/yıl, düşüş −%20) → ertesi gün sızıntısı izi yok.
  - Rastgele puan (plasebo, aynı evren ve rejim, tek tohum): eğitim +%94/yıl, test −%35/yıl → eğitimdeki getirinin çoğu
    2020–21 boğasındaki rejim etkisinden; testte seçimin katkısı büyük ama tek tohum, birden çok tohumla tekrarlanmalı.
