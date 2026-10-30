# DEVAM NOTU — Birleşik Baş Trader dönüşümü (yarıda kalırsa buradan devam)

> Bu dosya her adımdan sonra güncellenir. Yeni bir oturum (ya da kota yenilenince aynı oturum) **önce bunu okur**,
> sonra "Sıradaki adım"dan devam eder. Kullanıcı onayı: tüm aşamalar için önceden verildi (30.10.2026 03:14 ve 03:18:
> "herşeye onay veriyorum, hiçbir işlemi durdurma"); her küçük adımda tekrar onay istenmez.
> Kullanıcının kotası Pazartesi 2 Kasım 2026 21:00'de (İstanbul) yenilenir; 21:10'a "devam et" mesajı zamanlandı.

## Görev

- Tam talimat: `docs/birlesik/GOREV.md` (kullanıcı dosyası, 966. satırda kesik geliyor).
- Plan: `docs/birlesik/PLAN.md` (PR 2 ayrıntılı tasarımı bölüm 10). Denetim: `docs/birlesik/ARASTIRMA_DENETIMI.md`.
- **main'e merge YOK**, PR'lar kullanıcı incelemesine kalır. PR'ları REST ile aç (GraphQL kapalı, `gh pr create` çalışmaz):
  `gh api -X POST repos/hkyafes-afk/kripto-radar/pulls --input pr.json` (title, head, base, body).

## Değişmez kurallar (özet)

- Gerçek emir, gerçek hesap, API anahtarı YOK. Yürütme modu yalnız `paper` (`flags.ensure_paper`).
- Legacy canlı davranış bozulmaz; yeni sistem `birlesik.karar_modu: legacy` ile kapalı başlar.
- Açık paper pozisyonları ve `kalp` dalındaki defter (ledger.json) bozulmaz.
- Sentetik test verisi test adında/dokümanda "sentetik" diye belirtilir. Her PR'da tam paket: `python3 -m pytest -q tests`.

## Durum

### PR 1 — TAMAM: https://github.com/hkyafes-afk/kripto-radar/pull/2 (dal `feat/unified-trader-foundation`, base `main`)
models, coverage, costs, flags, config `birlesik` bloğu, 46 yeni test (tam paket 180 geçti), denetim ve plan dokümanları.

### PR 2 — stratejiler + UnifiedDecisionEngine (yalnız SHADOW modu)
Dal: `feat/unified-decision-engine` (PR 1 dalından). PR base: `feat/unified-trader-foundation`.
- [x] Dalı aç
- [x] `radar/unified/factors.py` FactorEngine: legacy ctx + günlük df → MarketContext (mum, yapı, hacim, türev, rejim, veri kalitesi, faktörler, kapsam) + testler
- [x] `radar/unified/strategies/base.py` sözleşme + kayıt + "strateji yürütme modülü import etmez" testi
- [x] Stratejiler (carry PR 3'e kaldı): swing_trend (team.a_kemal), compression_breakout (a_selin), controlled_breakout (a_kaan), panic_reversal (a_ayse, shadow), short_trend (yeni, shadow), intraday_trend (shadow), microstructure (shadow, veri yok), carry (harvest) + legacy eşleşme testleri
- [x] `radar/unified/edge.py` brüt avantaj önselleri (Binance EĞİTİM dönemi beklentisi, n/(n+50) büzülme) + testler
- [x] `radar/unified/decision.py` UnifiedDecisionEngine (birleştirme, kapsam kapısı, maliyet kapısı, çelişki çözümü, Türkçe açıklama) + testler
- [ ] `radar/unified/journal.py` karar günlüğü (günlük döndürülen jsonl) + testler
- [ ] Motor kancası: `karar_modu: shadow` iken `engine.scan_live_daily` sonunda birleşik motor çalışır, hata legacy'yi etkilemez + test
- [ ] Tam paket + push + PR

### PR 3–5: plan bölüm 4, 9, 11.

## Sıradaki adım

PR 2 listesinde işaretlenmemiş ilk madde.

## Bağlam (kaybolmasın)

- Araştırma: `data/TEK_AJAN_ARASTIRMA.md`, kod `tools/arastirma/` (README'de tekrar üretme). Veri dalları: `binance-veri`
  (günlük+funding, batanlar dahil, 2020-01→2026-10), `binance-sonuc` (Binance yarışları), `arastirma-sonuc` (gün içi).
- Bu ortamdan GitHub artifact indirilemiyor (blob depolama engelli); ağır işler GitHub Actions'ta çalıştırılıp sonuç küçük
  dosya olarak bir dala yazılıyor (ör. `.github/workflows/arastirma-gun-ici.yml`).
- Açık iş: "Binance metrikleri" iş akışı (run 40963164036) OI/long-short indiriyordu; evreni bugünkü ilk 100 coin
  (survivorship). Sonuç `binance-metrik` dalında olur.
- Brüt avantaj önselleri için Binance EĞİTİM dönemi (2020-10-28 → 2025-01-06) sonuçları (`tools/arastirma/team_test.py`):
  Kemal 252 işlem ort. +0,292R; Kaan 142, +1,139R; Selin 97, +0,708R; Murat 185, +0,236R. Test dönemi önselde KULLANILMAZ.
- Denetim ek testleri: 1 gün gecikme (sızıntı yok), 8 tohumlu plasebo (A hepsinden iyi; test −%22…−%50'ye karşı +%17,7).
