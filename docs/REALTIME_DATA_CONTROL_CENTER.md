# Gerçek Zamanlı Veri ve Kontrol Merkezi — Uygulama Sözleşmesi

Durum: araştırma ve uygulama başlangıç belgesi  
Başlangıç: 2026-10-31  
Hedef dal: feat/realtime-data-control-center

## Değişmez ürün ilkeleri

1. Üretim dashboard'unda örnek, sentetik, tahmini veya uydurulmuş piyasa/portföy/işlem verisi gösterilmez.
2. Her veri alanı kaynak, borsa sembolü, borsa olay zamanı, yerel alım zamanı, son güncelleme zamanı, kalite durumu ve ölçüm yöntemiyle izlenebilir.
3. Veri yoksa UNKNOWN/VERİ YOK; gecikmişse STALE/BAYAT; sıra bütünlüğü bozuksa INVALID/GEÇERSİZ; bağlantı yoksa DISCONNECTED/KOPUK gösterilir. Bilinmeyen değer sıfır değildir.
4. Her kanal kendi gerçek yayın ritminde güncellenir. Olay bazlı kanallara yapay sabit yenileme hızı atfedilmez. "Sıfır gecikme" veya bütün kanallarda 1 ms garanti edilmez.
5. Stratejiler veri gereksinimlerini açıkça tanımlar. Bir strateji yalnız ihtiyaç duyduğu kritik alanlar geçerli değilse bekletilir; diğer bağımsız stratejiler gereksiz yere durdurulmaz. Hesap/pozisyon uzlaştırması, temel fiyat güvenliği ve genel risk kilitleri ortak güvenlik istisnasıdır.
6. UnifiedTrader tek yeni pozisyon otoritesi olmaya devam eder. İlk uygulama PAPER'dır. Gerçek emir/private API yolu eklenmez ve etkinleştirilmez.
7. Mevcut defterler, işlem geçmişleri, arşivler ve legacy/challenger verileri silinmez veya tek portföy gibi birleştirilmez.
8. Dashboard'daki her grafik, KPI, tablo, filtre ve buton gerçek bir veri kaynağına/işleme bağlıdır. Henüz bağlanmayan özellik gizlenir veya açıkça "henüz bağlı değil" der; dekoratif sahte kontrol konmaz.

## 2026-10-31 mevcut repo denetimi — başlangıç bulguları

Repo: hkyafes-afk/kripto-radar  
Başlangıç main commit: 69a6239d4d6c9846db7defd01101a377e71ea170

### Dashboard

- Ana arayüz `radar/templates/birlesik.html` üzerinden `site/index.html` olarak üretiliyor.
- Dashboard statik JSON dosyalarını `get()` ile okuyor; ana `load()` döngüsü 60 saniyede bir çalışıyor.
- Tarayıcı ayrıca OKX public WebSocket'e bağlanıyor; yalnız BTC ve açık pozisyon sembolleri için `tickers` kanalını dinliyor.
- Tarayıcı WebSocket alımında `Date.now()` ile yerel alım zamanı yazılıyor. Bu, borsa kaynaklı olay zamanı ile uçtan uca ağ gecikmesi ölçümü değildir.
- Dashboard'da gerçek tarayıcı tick'i ile statik sunucu snapshot'ı ayrıştırılmalıdır. Bağlantı kurulmuş olması bütün sembollerin/kanalların canlı olduğu anlamına gelmez.
- Mevcut sayfa kendisini 60 saniyede bir yenilenen JSON dosyaları üzerinden açıklıyor; bu mimari bütün paneli milisaniyelik gerçek zamanlı yapmaz.

### Çalıştırma ve yayın

- `.github/workflows/canli.yml` GitHub Actions üzerinde uzun koşu/lease devriyle çalışır; GitHub Actions runner kalıcı, düşük gecikmeli WebSocket sunucusu olarak kabul edilmemelidir.
- `.github/workflows/polat.yml` de 6 saatlik koşu/lease zinciriyle yaklaşık sürekli çalışma hedefler. Bu, kesintisiz tek process/kalıcı socket veya milisaniye gecikme SLA'sı değildir; koşular arasında runner değişimi ve devir aralığı olabilir.
- GitHub Pages statik sunum katmanıdır; tarayıcıdan doğrudan public OKX WebSocket alınabilir, ancak UnifiedTrader karar motorunun sürekli çalışması ve güvenilir durum yayını ayrı bir sürekli çalışan servis gerektirir.
- GitHub Pages statik sayfasına GitHub token/PAT veya uygulama özel anahtarı gömülmez. Ayar kaydetme için kimlik doğrulamalı sunucu tarafı yazma aracısı gerekir.

### Mevcut piyasa verisi / Polat

- `polat/market.py` tickers, order book, trades, candles, open interest, funding-rate, mark-price, index-tickers ve liquidation-orders kanallarını işliyor.
- Mevcut gecikme örneklemesi ticker/trade zaman damgalarından alım anına kadar hesaplanıyor; tüm kanallar için ortak uçtan uca ölçüm değildir.
- Mesaj işleme süresi ayrıca `perf_counter` ile ölçülüyor. Bu süre ağ gecikmesinden ayrı tutulmalıdır.
- Order book bütünlüğü, snapshot/delta sırası ve yeniden snapshot akışı ayrıca test edilmelidir.
- Polat'ın yeni pozisyon yetkisi kapalı kalır; yalnızca veri/araştırma/challenger rolü sürer. Polat verisinin UnifiedTrader'a doğrudan yetki devri yapılmaz.

### UnifiedTrader veri aktarımı

- `radar/unified/factors.py` mevcut legacy günlük bağlamdan MarketContext üretiyor; açıklama ve kodda intraday/microstructure bağlamının bu yoldan kurulmadığı belirtiliyor.
- `radar/engine.py` UnifiedTrader'a legacy bağlamları ve mevcut quote'ları iletiyor. Polat'ın bütün ham kanallarının ortak normalize veri katmanından UnifiedTrader'a aktarıldığı henüz doğrulanmış değil.
- `DerivativesContext` içinde funding, OI, basis, mark/index, long-short ve likidasyon alanları bulunması bu alanların canlıda doğru, taze ve tarihsel olarak kullanılabilir olduğu anlamına gelmez.
- `radar/unified/trader.py` çağrısında live bar zamanı için yerel `now` kullanılabildiğinden gerçek borsa event timestamp'inin korunması ve bar kapanış zamanının doğru taşınması denetlenmelidir.

## Hedef mimari

1. Exchange adapters: OKX primary; Binance/Bybit/Bitget bağımsız teyit ve araştırma kaynakları. Erişim/abonelik/ülke/hesap kısıtları doğrulanmadan kaynak "aktif" sayılmaz.
2. Raw event journal: değiştirilmeyen ham mesaj, exchange event timestamp, local wall-clock receive timestamp, monotonic receive timestamp, venue/channel/instrument/connection id.
3. Normalization: ortak sembol/kontrat birimi, decimal hassasiyeti, tick/lot, timestamp, funding interval ve contract multiplier.
4. Quality engine: seqId/prevSeqId, snapshot/delta, duplicate, out-of-order, gap, stale, clock skew, reconnect/resubscribe, crossed/locked book, impossible price/size ve source disagreement kontrolleri.
5. Shared market state: her sembol/kanal için son değer + zaman + kalite + provenance.
6. Feature/factor engine: yalnız geçerli ve uygun tazelikteki girdilerle hesaplanan faktörler; faktörün input kaynak kimlikleri ve zaman aralığı kaydedilir.
7. Strategy requirements: her strateji zorunlu/opsiyonel veri kanallarını ve kanal başına maksimum yaş sınırını bildirir.
8. UnifiedTrader: veri kapısı, karar, risk ve PAPER execution tek otoritede.
9. Realtime API: dashboard'a push (WebSocket/SSE) ile durum, fiyat, veri sağlığı ve karar akışı.
10. Dashboard: gerçek API abonelikleri, bağlantı durumu, kaynak bazlı gecikme/kalite ve veri yok/bayat durumları.
11. Settings API: şema doğrulamalı, rol kontrollü, audit log'lu, versiyonlu, onaylı ve rollback destekli sunucu tarafı ayar değişikliği.

## Kanal envanteri ve zorunlu ölçümler

Her exchange + instrument + channel kombinasyonu için aşağıdaki ölçümler tutulur:

- event_ts_ms (borsa zamanı, varsa)
- recv_wall_ms (yerel UTC duvar saati)
- recv_mono_ns (monotonik saat)
- process_start_ns / process_end_ns
- publish_ts_ms / dashboard_receive_ts_ms (varsa)
- event_to_receive_ms (yalnız karşılaştırılabilir saatler ve clock uncertainty ile)
- local_queue_ms, process_us, publish_to_client_ms
- p50/p95/p99/max ve örnek sayısı
- last_event_age_ms, message_rate, reconnect_count, dropped/duplicate/out_of_order/gap counters
- snapshot age, sequence continuity, book validity
- source id, channel id, symbol, connection id, schema version

Kanal listesi: instruments, tickers/BBO, L2 order book, trades/tape, candles (open/closed ayrımı), mark price, index price, funding current/history/next settlement, open interest, long-short ratios (kaynak ve kapsam belirtilerek), liquidation events, account/balance/positions (PAPER durumuyla kesin ayrım), exchange status/maintenance.

## Veri kalite ve strateji kapıları

- Bozuk L2 defteri: yalnız L2 gerektiren stratejiye geçersiz; snapshot yeniden kurulana kadar L2 kullanılmaz.
- Funding bayat/eksik: funding stratejisi ve funding maliyet tahmini güvenilmez; başka bağımsız strateji otomatik olarak durmaz.
- OI/long-short eksik: bu alanlara dayalı faktörler UNKNOWN; 0 olarak doldurulmaz.
- Candle incomplete: kapanış bazlı stratejiler tamamlanmış mum olmadan sinyal üretmez.
- Quote stale veya crossed: ilgili sembolde yeni giriş kapısı kapanır.
- Hesap/pozisyon durumu uzlaştırılamıyor veya global risk kilidi aktif: yeni girişler ortak güvenlik gereği durur.
- Kaynaklar arası fiyat farkı: otomatik olarak bozuk veri denmez; farklı venue basis/spread olasılığı ayrıştırılır.
- Kalite kapısı reddi her zaman neden kodu ve etkilenen strateji ile kaydedilir.

## Dashboard doğruluk sözleşmesi

- Her sayı için kaynağa, güncelleme zamanına ve hesaplama formülüne ulaşılabilir detay.
- Live exchange data, server snapshot, PAPER simulation, historical/backtest, shadow/challenger ve unavailable data görsel olarak ayrı.
- Gerçek borsa emri ile PAPER fill hiçbir koşulda karıştırılmaz.
- Gerçekleşmemiş PnL mark/quote kaynağı ve hesaplama zamanı ile gösterilir; statik kayıtla karıştırılmaz.
- Funding/fees/slippage gerçekleşmiş ve tahmini değerler ayrı etiketlenir.
- Grafik yalnız gerçek zaman serisi mevcutsa çizilir; yeterli örnek yoksa "veri birikiyor/veri yok".
- Tüm butonlar gerçek backend eylemine bağlıdır; bağlanmayan buton yayınlanmaz.
- Ekran yenileme, veri yayın hızı değildir. UI animasyonu yeni veri üretmez.
- Browser WebSocket public data yalnız görüntüleme için kullanılabilir; karar motorunun kendi doğrulanmış sunucu verisinin yerine geçmez.

## Ayarlar sözleşmesi

- UI şemadan türetilir; kod/config içindeki düzenlenebilir bütün parametreler kategorili şekilde keşfedilir.
- Her alan: tip, açıklama, varsayılan, geçerli aralık, birim, etki kapsamı, yeniden başlatma ihtiyacı ve güvenlik seviyesi.
- ATR, RSI/ADX/EMA, stop/hedef, strateji eşikleri, risk yüzdeleri, kaldıraç tavanı, evren, hacim/spread, veri tazelik eşikleri, ücret/kayma varsayımları gibi alanlar şemada tanımlanır.
- UI'dan doğrudan secret, API key, trade enable veya PAPER dışına geçiş ayarı sunulmaz.
- Değişiklik önce doğrulanır, farkı gösterilir, kullanıcı onayı alınır; ardından server-side GitHub App ile branch/commit/PR veya kontrollü config sürümü oluşturulur.
- Browser'a PAT, GitHub token veya App private key gönderilmez.
- Her değişiklik audit log, author, timestamp, old/new value, config hash ve rollback ref ile saklanır.
- Uygulama atomik olmalı: bozuk config aktif ayarları kısmen değiştiremez; son geçerli config korunur.

## Dashboard ve servis altyapısı için kabul testleri

- Her kanal için event/receive/process/publish timestamp tutarlılığı.
- OKX seqId/prevSeqId gap, duplicate, out-of-order, reconnect ve snapshot recovery.
- Stale data, missing channel, malformed payload, wrong contract multiplier, crossed book.
- Clock offset/uncertainty ve monotonic timer testleri.
- Source divergence vs actual cross-venue basis ayrımı.
- Dashboard API disconnect, websocket reconnect, slow consumer, browser background tab.
- Static snapshot vs live stream display consistency.
- Settings invalid range, conflicting params, unauthorized write, audit, rollback, restart.
- PAPER-only invariant, no private trade credentials, no real-order endpoints.
- Full existing test suite + yeni regression tests.
- Gerçek uçtan uca latency ölçümleri ancak gerçek çalışan sunucu ve gerçek exchange bağlantısı üzerinde "measured" olarak raporlanır; yerel test latency'si network latency diye sunulmaz.

## Uygulama aşamaları

### A — Envanter ve ölçüm (ilk)
- Tüm veri akışlarının kaynağı, çağrı zinciri, frekansı, timestamp semantiği, kayıp davranışı.
- Mevcut dashboard JSON ve browser WebSocket alanlarının envanteri.
- Mevcut workflow'ların yazdığı dosyalar ve çalışma periyotları.
- Güncel OKX API changelog ve kanal erişim koşulları.

### B — Ortak veri kalite katmanı
- Provenance ve timestamp taşıyan typed event envelope.
- Kanal bazlı quality/latency metrics.
- L2 book sequence integrity ve recovery.
- Strateji bazlı data requirements ve quality gating.

### C — Canlı servis
- Sürekli çalışan, kalıcı process ve uygun bölgede ölçülmüş host.
- WebSocket market feed ve ortak market state.
- UnifiedTrader loop, PAPER execution, health endpoint.
- Dashboard'a push API.
- GitHub Actions araştırma, CI, backtest, deploy iş akışları için kalır; düşük gecikmeli runtime varsayılmaz.

### D — Dashboard
- Piyasa, orderbook, tape, funding/OI, liquidations, portfolio, decisions, performance, data quality, agent health ve research lab.
- Her kart gerçek kaynak bağlı; eksik olan "bağlı değil" diye işaretlenir.
- Masaüstü ve mobil responsive; grafikler pan/zoom/timeframe, tooltip, source/time details.

### E — Settings
- Config schema ve registry.
- Server-side authenticated settings API.
- Diff/validation/approval/versioning/rollback.
- GitHub repo ile izlenebilir sync.

### F — Araştırma ve kural optimizasyonu
- Futures öncelikli; spot yalnız index/basis/carry/hedge referansı gerektiğinde.
- LONG ve SHORT ayrı değerlendirilir; bull/bear/range/high-vol/squeeze rejimlerinde ayrı test.
- Trend/momentum, cross-sectional ranking, breakout/compression, mean reversion, funding forecast/crowding, basis, OI-price-funding interactions, liquidation flow, order flow, spread/depth/market impact.
- Fee, funding, spread, slippage, market impact, lot rounding ve liquidation risk dahil.
- Walk-forward, purged/embargoed split, untouched holdout, multiple-testing/PBO kontrolü ve forward PAPER.
- Hiçbir araştırma parametresi otomatik olarak üretim ayarını değiştirmez.

## Yayın / güvenlik kapısı

Bu dalda ilk commit dokümantasyon ve denetim bulgularını içerir. Çalışan bot, config, GitHub Actions, mevcut defterler ve canlı emir yetkileri değiştirilmez. Kod değişiklikleri küçük, testli, ayrı commit/PR'lar olarak yapılır. CI ve PAPER kabul testleri geçmeden main'e merge edilmez.

Gerçek canlı işlem, bu çalışmanın kapsamı değildir.


---

## Uygulama ilerlemesi — 2026-10-31

Bu bölüm PR #23 dalındaki gerçek kod değişikliklerini ve doğrulama durumunu kaydeder.

### Tamamlanan ilk uygulama dilimi
- `polat/okx.py`: OKX bağlantı döngüsü gerçekten kurulmuş bir WebSocket bağlantısı koptuğunda ilgili bağlantı grubunu callback ile bildirir; başarısız ilk bağlantı denemeleri sahte kopma olayı üretmez.
- `polat/agent.py` bu callback'i `Market.disconnect_channels` ile bağlar. Piyasa, defter ve tape bağlantıları yalnızca kendi kanal gözlemlerini DISCONNECTED yapar; diğer bağlantı grupları etkilenmez.
- `tests/test_market_quality_integration.py` bağlantı grubuna özgü kopma durumunu ve Feed → Market callback yolunu sınar. Test fake feed'i de yeni opsiyonel callback imzasıyla uyumlu tutuldu.
- Bu kopma sinyali Polat'ın kanal telemetrisi içindir; UnifiedTrader strateji bazlı kalite kapısı henüz Polat'ın tüm normalize verileriyle birleştirilmiş değildir.

- `radar/unified/settings.py`: `config.yaml` içindeki iç içe parametreleri deterministik biçimde keşfeden ve kategoriye ayıran katalog eklendi.
- Güvenlik/işlem yetkisi alanları (`birlesik.karar_modu`, `birlesik.yurutme_modu`) sıradan ayar olarak işaretlenmez.
- Token/secret/private-key/parola benzeri alanların değerleri katalog çıktısında maskelenir.
- Katalog şu an bilinçli olarak salt okunurdur. Tip, aralık, etki kapsamı, yeniden başlatma gereksinimi, yetkilendirme, audit, atomik uygulama ve rollback doğrulanmadan yazma açılmaz.
- `radar/dashboard.py` site üretiminde `site/birlesik/ayarlar.json` üretir.
- `radar/templates/birlesik.html` içinde Ayarlar sekmesi eklendi; katalog gerçek config dosyasından gelen değerleri gösterir ve yazma kapalı durumunu açıkça belirtir.
- Ayar kataloğu şeması v2: her config leaf için doğrulanmış Python tipi, dizi öğe tipleri, koruma seviyesi, kapsam ve doğrulanmamış alanların (min/max/enum/birim/restart) açık `unknown`/null durumu yayımlanır. İşlevsel açıklama henüz doğrulanmadıysa bunu da açıkça söyler; tahmini sınır üretmez.
- `radar/templates/birlesik.html` Ayarlar sekmesinde tür, değer, açıklama doğrulama durumu, birim/sınır/restart belirsizliklerini gösterir; yazma kapalı kalır.
- `tests/test_unified_settings_catalog.py` ve dashboard regresyon testleri eklendi/güncellendi.
- `polat/okx.py` ve `polat/agent.py` gerçek WebSocket bağlantı kopmalarını kanal grubuna göre Market kalite motoruna iletir; `piyasa`, `defter`, `tape` kanalları birbirinden bağımsız DISCONNECTED olur. Bağlantı ilk kez hiç kurulmadıysa sahte kopma olayı üretilmez.
- `tests/test_market_quality_integration.py` kopma kapsamı ve Feed → Market callback yolunu; `tests/test_polat.py` fake feed imzası uyumluluğunu sınar.

### Doğrulama

- Önceki PR head `586277a18034f8ef91a731fcd5e7ebbb62752ec4` için Unified Trader test workflow'u başarılıydı.
- Yeni ayar kataloğu testleri için Unified Trader workflow'u `322c25c670df9fe3a6c4f20663283117cc91aef2` commit'inde başarılı raporlandı.
- Son dashboard değişiklikleri head `93f0507e4ac521149e1b1d4febada4f260ae6b8f` üzerine işlendi. Bu head için tam repo ve unified workflow sonuçları henüz kesinleşmeden PR merge edilmemelidir.
- PR #23 draft ve açık kalır; `main` dalına otomatik merge yapılmaz.

### Sıradaki uygulama işleri

1. Son head için tam test paketinin sonucunu doğrula; başarısızsa düzelt.
2. Her ayar için işlevsel açıklama, doğrulanmış min/max veya enum, birim, etki kapsamı, restart gereksinimi ve güvenlik sınıfını kaynak kod/config üzerinden tek tek doğrula.
3. Salt-okunur keşif kataloğunu doğrulanmış path-specific şemayla eşleştir; şeması/limitleri belirsiz parametreler yazılamaz kalsın.
4. Ayar değişikliği için kimlik doğrulamalı server-side API; tarayıcıya GitHub token/PAT/private key koyma.
5. Değişiklik önizleme, onay, sürümleme, audit log, atomik config doğrulama ve rollback.
6. Sürekli veri runtime'ı, ortak event journal, timestamp/provenance, kalite/latency motoru.
7. UnifiedTrader'a kanal bazlı doğrulanmış market state adaptörü.
8. Dashboard'un gerçek API/push akışına taşınması; eski statik kaynaklar ve PAPER/legacy ayrımı korunur.
9. Kapsamlı futures testleri, PAPER/SHADOW ileri gözlem ve kabul kriterleri.

### Değişmez güvenlik ilkeleri

- PAPER dışı yürütme modu etkinleştirilmez.
- Gerçek OKX emirleri/private API yolu eklenmez.
- UnifiedTrader tek yeni-pozisyon otoritesi olarak kalır.
- Ayar kataloğu veya dashboard, güvenlik kilitlerini devre dışı bırakamaz.
- Gerçek olmayan veri üretilmez; eksik veri UNKNOWN/VERİ YOK olarak kalır.
- Testte sentetik fixture kullanılırsa test kapsamı olarak açıkça etiketlenir.


### Güncel takip ve doğrulama — 2026-10-31 (PR #23)

- Tüm kalan proje planları tek kabul listesinde toplandı: https://github.com/hkyafes-afk/kripto-radar/issues/24
- PR #23 güncel dalı `feat/realtime-data-control-center`; son doğrulanan test head'i `3691dd7f86fe21e6ba5dfb9f9df9adaa63e1d0a4`.
- Bu head için UnifiedTrader CI: **184 passed, 3 skipped**.
- Aynı head için tam depo CI: **335 passed, 10 skipped**.
- Son test hatası, Polat yetki fonksiyonunun fail-closed varsayılanına geçişinden sonra eski bir ortak yetki testinin varsayılan-açık davranış beklemesiydi. Test, fail-closed sözleşmesine göre güncellendi; iki CI da son head'de başarılı oldu.
- PR #23 açık ve taslaktır; main'e merge edilmemiştir. Main tabanı `69a6239d4d6c9846db7defd01101a377e71ea170` olarak korunur.
- `docs/birlesik/DEVAM.md` içindeki eski PR #2–#6 sıralaması tarihsel olarak işaretlendi; güncel devam noktası Issue #24'tür.
- Bu test başarısı yalnız mevcut head'i doğrular; Issue #24'teki kalan işler tamamlandı anlamına gelmez.


### Append-only olay günlüğü altyapısı — 2026-10-31

- `radar/unified/event_journal.py` eklendi: JSONL append-only kayıt, sıra numarası, önceki kayıt SHA-256 bağlantısı, kayıt hash'i, event/receive/monotonic zaman alanları, provenance alanı, yeniden başlatmada zincir doğrulaması ve bozuk/kesik kayıt reddi.
- `tests/test_unified_event_journal.py`: kayıt ekleme, restart recovery, hash zinciri, tamper, truncated tail ve JSON/timestamp doğrulama testleri.
- Yazıcı sözleşmesi şu anda **tek süreç/tek yazıcı** ile sınırlıdır. Bu primitive canlı Polat/OKX feed'ine veya UnifiedTrader'a henüz bağlanmamıştır; bu yüzden gerçek event coverage veya sıfır kayıp garantisi değildir.
- UnifiedTrader CI bu değişiklikte başarılıdır; tam depo CI aynı head için tamamlanmayı bekliyor.


## Sonraki uygulama — Polat OKX ham olay günlüğü

- `QueuedEventJournal` artık Polat'ın OKX public Feed callback'ine bağlandı.
- Ham WS mesajları sınırlı kuyruk üzerinden arka plan yazıcısına aktarılır; feed callback'i disk yazımını beklemez.
- Her kayıtta borsa event zamanı (tek timestamp'li batch ise), yerel receive zamanı, monotonic receive zamanı, kaynak/kanal/enstrüman ve SHA-256 hash zinciri tutulur.
- Secret benzeri alanlar kayıttan önce maskelenir.
- Kuyruk taşması, dosya boyutu/record limiti veya disk hatası varsa `complete=false`, dropped/error/limit sayaçları görünür; sessizce tam kayıt iddiası yapılmaz.
- Varsayılan çalışma dosyası `/tmp/polat-market-events-<pid>.jsonl` altındadır. GitHub Actions runner ephemeral olduğu için workflow/runner değişiminde kalıcı arşiv garantisi yoktur; dosya GitHub Pages'e yayınlanmaz ve Git'e commit edilmez.
- Varsayılan limitler: 4.096 kuyruk girdisi, 64 MiB ve 100.000 kayıt. Bu sınırlar dolarsa günlüğün eksiksiz olmadığı açıkça raporlanır.
- Bu entegrasyon Polat'ın OKX feed'ini UnifiedTrader'a bağlamaz; Polat'ın yeni pozisyon yetkisi kapalı kalır.


## UnifiedTrader BBO kalite kapısı — canlı karar yolu

- `Engine._on_tick` artık OKX/Bitget olay timestamp'lerini yerel receive zamanından ayrı tutar; OKX WS ve REST ticker verileri ayrı kalite kanallarında değerlendirilir.
- UnifiedTrader'a giden BBO yalnızca 15 saniye içinde, pozitif ve çapraz olmayan bid/ask ile geçerli borsa event timestamp'i varsa kabul edilir. Eksik timestamp UNKNOWN; bayat STALE; future clock skew veya crossed/locked quote INVALID olur.
- OKX WS timestamp'i eksikse adapter artık yerel zamanı borsa timestamp'iymiş gibi uydurmaz; `None` geçirir. Pozisyon izleme içi yerel fallback ayrı tutulur, karar BBO'suna yetki vermez.
- `_trusted_bbo` hem Unified hem Shadow karar yollarında kullanılır; doğrulanmamış eski WS/REST defteri quote üretmez. Data Health örneklerinde kalite state ve reason code gösterilir.
- Regresyonlar `tests/test_unified_bbo_quality.py` içinde eksik timestamp, stale, future clock skew, crossed quote ve doğrulanmamış REST snapshot senaryolarını kapsar.
