# Birleşik dashboard — gerçek veri kaynakları

Bu belge, dashboard bölümlerinin hangi yayımlanmış dosyalardan beslendiğini ve hangi sınırları taşıdığını kaydeder. Panelde eksik veri sıfır veya sentetik değerle doldurulmaz.

| Bölüm | Kaynak | Gösterilen kapsam | Sınır |
|---|---|---|---|
| Overview | `data.json`, `birlesik/durum.json` | Yayınlanmış sistem özeti, Unified PAPER bakiye/pozisyon özeti, karar sayacı | Statik snapshot; sayfa açıkken görülen tarayıcı fiyat akışı karar motorunun girdisi değildir |
| Opportunities | `birlesik/kararlar-YYYY-MM-DD.jsonl` | Son 24 saatteki OPEN, REDUCE_SIZE ve SHADOW karar kayıtları | Tahmin veya yeni sinyal üretmez; yalnız karar günlüğünü filtreler |
| Portfolio | `birlesik/durum.json` | Yalnız `mod=unified` defterinin açık ve kapanmış PAPER işlemleri | Legacy ve bağımsız shadow defterleri aktif portföye eklenmez |
| Decision Center | `birlesik/kararlar-YYYY-MM-DD.jsonl`, `birlesik/hazirlik.json` | Son kararlar ve yayınlanmış giriş engelleri | Kayıt dosyası yoksa veri yok gösterilir |
| Performance | `data.json` içindeki `karne` | Yayınlanmış yeni/legacy karne istatistikleri | Legacy sonuçları yeni UnifiedTrader performansı gibi sunulmaz |
| Experiment Lab | `data.json` içindeki `karne.yeni_golge` ve `yeni_kaynaklar` | Ayrı Unified shadow kapanış sonuçları | Örneklem büyüklüğü ve süre yeterli olmadan strateji başarısı/terfisi kanıtı değildir |
| Settings | `birlesik/ayarlar.json` | config.yaml keşif kataloğu, tip ve doğrulanmış şema bilgisi | Salt okunur; kimlik doğrulamalı server-side API olmadığı için yazma kapalıdır |
| Data Health | `birlesik/durum.json` içindeki `tanilama` | UnifiedTrader BBO/OKX feed kalite gözlemleri | Yalnız gözlenen BBO kapsamı; bütün piyasa kanallarının sağlığı anlamına gelmez |

## Güncellik

- Ana snapshot ve portföy durumu kendi yayın zaman damgalarına göre güncellik kontrolünden geçer.
- Karar günlüğü son 24 saatlik kayıt penceresini kullanır.
- Data Health tanılaması kendi üretim zamanını gösterir.
- Kaynak zaman damgası veya veri yoksa UNKNOWN / Veri yok; eşik aşılmışsa Bayat gösterilir.
- GitHub Actions/Pages ücretsiz ortamı sürekli process, anlık push veya milisaniyelik SLA sağlamaz.

## Güvenlik sınırları

- UnifiedTrader yeni pozisyonlar için tek otoritedir ve PAPER modundadır.
- Gerçek borsa emri/private API yoktur.
- Polat raw event journal ve legacy/challenger defterleri UnifiedTrader portföyüne karıştırılmaz.
- Settings ekranına GitHub PAT, token veya private key konmaz; güvenli yazma altyapısı kurulana kadar ayar değiştirme kapalıdır.


## Sonraki doğrulanmış bağlantılar

- Polat kalite telemetrisi artık ticker, L2 books, trades, open-interest, funding-rate, mark-price, index-tickers, liquidation-orders ve candle zaman dilimlerini kapsar. Her kanal için freshness, sequence/timestamp, DISCONNECTED ve STALE durumu raporlanır.
- Polat latency paneli ölçülen örnek sayısı ile p50/p95/p99/max exchange-event-to-receive ve processing latency değerlerini gösterir. Örnek yoksa alan boş kalır.
- UnifiedTrader ve Polat aynı `NormalizedBBO` sözleşmesini `radar/unified/market_data.py` üzerinden kullanır. Bu ortak parser/şema, iki runtime'ı ortak socket veya process hâline getirmez.
- Settings için dashboard PAT saklamaz. GitHub kimlik doğrulamalı workflow yalnız yedi allowlisted sayısal parametreye taslak PR açar; aralık dışı değerler reddedilir ve hiçbir değişiklik otomatik uygulanmaz.
- Research Lab altyapısı `scripts/unified_research.py` ile CSV üzerinden purged walk-forward, untouched holdout, DSR/multiple-testing ve maliyet/provenance attestation raporu üretir. Otomatik terfi yoktur.
- Unified PAPER terfi adaylığına 100 kapanmış işlem ve istatistik eşiklerine ek olarak 14 günlük forward kapanış kanıtı şartı eklendi. Bu zaman ve örneklem, kod tarafından sentetik şekilde tamamlanmaz.


## Sekme üstü kaynak açıklaması — 2026-11-01

Dashboard her sekmede gerçek dosya yolunu, kaynağın son zaman damgasını ve görünümün kapsam/yöntem notunu gösterir. Zaman damgası yoksa "Kaynak zaman damgası yok" görünür; eksik veri sıfıra çevrilmez. Mobil kaynak şeridi dar ekranlarda satır sarar. Yükleniyor ve uyarı bölgeleri erişilebilir canlı bildirim olarak işaretlidir.
