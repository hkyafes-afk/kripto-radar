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
