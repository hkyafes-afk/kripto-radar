> **GÜNCEL DURUM / ÖNCELİKLİ KAYIT — 2026-10-31**
> Bu dosyanın aşağıdaki eski PR sıralaması ve cutover notları tarihsel kalmıştır; yeni çalışma için bunları talimat olarak izleme.
> Güncel üretim yapılandırması `config.yaml → birlesik.karar_modu: unified`, yürütme `paper`; tek yeni pozisyon yetkilisi UnifiedTrader'dır.
> Eski PR #2–#6 sıralamasını tekrar merge etmeye çalışma. Güncel çalışma PR #23'tür:
> https://github.com/hkyafes-afk/kripto-radar/pull/23
> Kalan tüm işler ve kabul kapıları: https://github.com/hkyafes-afk/kripto-radar/issues/24
>
> PR #23 dalı: `feat/realtime-data-control-center`. Son kod head'i `193aeb2f7d21af673b4b4a9410ae2b9acdf4b5ca` için GitHub Actions: UnifiedTrader `187 passed, 3 skipped`; tam depo `338 passed, 10 skipped` (ikisi de başarılı).
> Ardından yalnız ücretsiz çalışma modeli dokümantasyonu eklendi; bu doküman commit'leri için CI yeniden çalıştırılacak.
> Ek ücretli sunucu/hosting/kişisel PC varsayımı yoktur. Ücretsiz çalışma sınırları `docs/FREE_ONLY_OPERATIONAL_MODEL.md` içinde kayıtlıdır.
> PR açık ve taslak; main'e merge edilmedi. Main tabanı `69a6239d4d6c9846db7defd01101a377e71ea170`.
>
> Polat, UnifiedTrader değildir ve ana portföyü yönetmez. Polat'ın `yetki.yeni_pozisyon` değeri false; kodda eksik yetki de fail-closed'dur.
> Polat'ın eski sanal pozisyon yönetimi/araştırma kaydı ayrı kalır. Polat verisi UnifiedTrader'a ortak feed olarak bağlıymış gibi gösterilmez.
> Gerçek OKX emir/private API kapalı; PAPER dışı yürütme yok. Eski defter ve portföyler ayrı korunur.
>
> **Sıradaki adım:** Issue #24'teki ilk açık kabul maddesinden ilerle. Her kod diliminden sonra hem UnifiedTrader hem tam depo testlerini çalıştır; son commit için sonuçlar yeşil değilse tamamlandı deme. Tüm kabul maddeleri kanıtlanmadan PR #23 merge edilmez.
>
> ---
>
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
- [x] `radar/unified/journal.py` karar günlüğü (günlük döndürülen jsonl, değişmeyen karar tekrar yazılmaz, 14 gün budama) + `radar/unified/shadow.py` ShadowRunner (5 dk aralık) + testler (tests/test_unified_journal.py)
- [x] Motor kancası: `Engine._unified_shadow` (`scan_live_daily` sonunda; yalnız shadow modda; site/birlesik/ altına yazar; hata yutulur) + test
- [x] Tam paket (216 test geçti) + push + PR #3 (https://github.com/hkyafes-afk/kripto-radar/pull/3, taban: feat/unified-trader-foundation)

### PR 3 — `feat/unified-risk-execution` (PR 2 dalından)

- [x] `radar/unified/risk.py` RiskGate (devre kesiciler, coin/tekrar, toplam/yön/küme sınırları, nominal tavanı, en düşük
      yeterli kaldıraç, likidasyon tamponu, lot yuvarlama, kayıp serisi) + `tests/test_unified_risk.py`
- [x] `radar/unified/execution/` base (Quote, sözleşme), paper (durum makinesi, reduce-only, idempotency, olay günlüğü,
      kalıcılık, uzlaştırma, funding), okx_demo (stub → ExecutionModeError) + `tests/test_unified_paper.py`
- [x] `radar/unified/exits.py` (stop, kısmi kâr, başabaş, kâr kilidi, iz stop, hedef, kural, zaman, rejim, veri sorunu)
- [x] `radar/unified/trader.py` UnifiedTrader + motor kancası `unified` modu (AYRI paper defteri `site/birlesik/yurutme.json`;
      legacy defter/yöneticiler etkilenmez) + `tests/test_unified_trader.py`
- [x] `DecisionResult.plan` (inst, giriş tipi, çıkış kuralı, ATR) — decision.py doldurur
- [x] config `birlesik.risk/cikis/paper` yeni anahtarlar (belgeli; mevcut değer değişmedi)
- [x] Tam paket (254 test geçti) + push + PR #4 (https://github.com/hkyafes-afk/kripto-radar/pull/4, taban: feat/unified-decision-engine)
- Ertelendi (bilinçli): carry stratejisinin birleşik sürümü (legacy harvest çalışmaya devam eder); legacy deftere yeni
  alan migrasyonu gerekmedi çünkü birleşik pozisyonlar ayrı defterde.

### PR 4 — `feat/unified-effectiveness` (PR 3 dalından — DEVAM/config çakışmasın diye yığıldı; plan bölüm 11)

- [ ] `radar/unified/effectiveness.py` EffectivenessAuditor: strateji/rejim/ufuk bazında EffectivenessSnapshot (bootstrap
      GA, yoğunlaşma, maliyet payı), legacy–birleşik ve gölge–paper karşılaştırması, deneme sayısı kaydı, terfi ölçütü
      (n ≥ 100 ve GA alt sınırı > 0) + testler  ✅ (commit edildi)
- [x] Motor kancası: shadow/unified modda saatte bir `site/birlesik/etkinlik.json` + test
- [x] Yarış: işlem başına çıktı (`race_islemler.csv`) + `python -m radar etkinlik` komutu (yarış/defter → etkinlik raporu)
- [x] Tam paket (262 test geçti) + push + PR #5 (https://github.com/hkyafes-afk/kripto-radar/pull/5, taban: feat/unified-risk-execution)
- [ ] Karne düzeltmesi KULLANICI ONAYI bekliyor (gölge işlemleri karneye ekleme) — onaysız yapma

### PR 5 — `feat/unified-dashboard` (PR 4 dalından)

- [x] `radar/templates/birlesik.html` (kararlar + filtre, Türkçe açıklama, maliyet dökümü, eksik veri, açık pozisyonlar,
      risk kapısı, etkinlik tablosu, kapananlar; telefonda yatay kayma yok — Playwright ile 390 px kontrol edildi)
- [x] `dashboard.write_site` paneli kopyalar; ana sayfada "👑 Baş Trader" bağlantısı; gölge modu `ozet.json` yazar + test
- [x] Tam paket (265 test geçti) + push + PR #6 (taban: feat/unified-effectiveness)

### Hepsi bittikten sonra (kullanıcıya bırakılanlar)

- PR'ları sırayla incele/merge et: #2 → #3 → #4 → #5 → #6. Merge sonrası `karar_modu: shadow` ile en az 2 hafta gölge,
  sonra kullanıcı onayıyla `unified` (ayrı paper defteri; legacy etkilenmez).
- Karne düzeltmesi kullanıcı onayı bekliyor.

## Sıradaki adım

İşaretlenmemiş ilk madde (yukarıdan aşağı).

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


## Güncel cutover notu — 2026-10-31

PR #19 sonrası main üzerinde kalan uygulama işi: tek yeni-pozisyon yetkisini kod seviyesinde zorunlu kılmak, eski 100k shadow portföy durumunu 100 USDT'ye güvenli taşımak ve ana dashboard'un unified durum dosyasını okuması. Bu işler feat/unified-capital-cutover dalında uygulanıyor.

Kabul koşulları:
- [ ] Sermaye migrasyonu: geçmişsiz 100k state 100 USDT'ye rebase; emir/pozisyon/kapanmış işlem varsa fail-closed.
- [ ] Legacy Radar market, yönetici ve carry giriş yolları Unified modda engellenmiş; mevcut pozisyon yönetimi korunmuş.
- [ ] Unified dashboard birlesik/durum.json dosyasını shadow dosyasından ayırıyor.
- [ ] Unified CI ve tam test paketi yeşil.
- [ ] Yeni main süreci eski nöbetçi döngüden kontrollü devraldı; yayınlanan durum baslangic: 100, mod: unified, yurutme: paper olarak doğrulandı.
- [ ] Gerçek OKX emir/private API kapalı kaldı.

## Performans ve strateji kapsamı — 2026-10-31

- [ ] 2 hafta ve en az 100 karar karşılaştırması tamamlanmadan PAPER performansı kanıtlanmış sayılmaz; dashboard bu dönemi deneysel olarak etiketler.
- [ ] Elif CarryStrategy: iki bacaklı spot+perp paper yürütme, atomik açılış/kapanış, ayrı muhasebe ve uzlaştırma testleri tamamlanana kadar yeni giriş kapalı.
- [ ] SHADOW_ONLY intraday/microstructure/panic stratejileri, veri kapsamı ve örneklem şartları sağlanmadan PAPER'a yükseltilmez.
- [ ] Canlı döngü eski commit'ten yeni main'e kontrollü devraldıktan sonra gh-pages üzerinde 100 USDT / unified / paper durumunu doğrula.