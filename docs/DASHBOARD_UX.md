# Tek merkezli Baş Trader — ürün ve dashboard sözleşmesi

## Ürün kararı
- Tek karar ve pozisyon yönetim otoritesi: **Baş Trader / UnifiedTrader**.
- Eski BAS, Turbo, Kemal, Ayşe, Murat, Elif, Kaan, Selin ve Polat kodları bu aşamada silinmez. Araştırma, karşılaştırma, veri üretimi ve geçmiş raporlar korunur.
- Eski ajanların hiçbiri yeni sermaye/pozisyon açma yetkisine sahip olmayacak. Yetki değişikliği, her çağrı zinciri ve workflow denetlenip tek tek kapılandıktan sonra yapılır.
- İlk yayın modu PAPER/SHADOW'dur. OKX public market data kullanılabilir; API trade anahtarı veya gerçek emir yolu eklenmez.
- 100 USDT başlangıç sermayesi yalnızca sanal portföy varsayımıdır.

## Bilgi mimarisi
1. **Genel Bakış** — 100 USDT başlangıç, anlık sanal özsermaye, net gerçekleşen/gerçekleşmemiş PnL, günlük kayıp, zirveden düşüş, açık pozisyon, veri/WS sağlığı, son güncelleme.
2. **Fırsatlar** — tüm izlenen semboller; LONG/SHORT/BEKLE; strateji ve rejim; karar puanı; brüt edge ile ücret+spread+slippage+funding tahmini; net edge; reddedilme kodu. Sinyal değil, karar günlüğü olarak gösterilir.
3. **Portföy** — açık sanal pozisyon, yön, giriş/mark, miktar/notional, kaldıraç, stop/hedef, marj, net açık PnL, ücret/funding ve pozisyon yaşı.
4. **Karar Merkezi** — Baş Trader tek otorite olarak; veri kaynakları ve araştırma/challenger bileşenleri yalnızca gözlemci. Her bileşenin son çalışma zamanı, sağlık, hata ve izin seviyesi.
5. **Performans** — yalnız net sonuçlar; ücret, funding, slippage, turnover, profit factor, expectancy, drawdown; long/short, strateji, sembol, rejim ve dönem kırılımı. Örnek sayısı ve belirsizlik aralığı gösterilir.
6. **Deney Laboratuvarı** — ATR eşikleri ve diğer kural varyantları aynı fırsat evreninde; walk-forward ve dokunulmamış holdout; maliyet sonrası kıyas; deney sonucu üretim kuralını otomatik değiştirmez.
7. **Ayarlar** — sanal sermaye, risk limitleri, evren, tarama sıklığı, bildirim, veri sağlayıcı. Riskli ayarlar için değişiklik kaydı ve doğrulama.

## Kullanım akışı
- Kullanıcı dashboard'u açtığında önce sistem modu, veri sağlığı ve portföy durumunu görür.
- Fırsat satırına dokununca kararın nedenleri, reddedilen kapılar, maliyet tahmini ve ilgili piyasa verisi açılır.
- Pozisyon satırına dokununca girişten bugüne tüm yönetim olayları ve ücret/funding dökümü görünür.
- Araştırma ajanları için kullanıcıdan ajan ajan seçim yapması istenmez; bunlar arka planda challenger rolündedir.
- **Durdur** yeni girişleri kapatır; mevcut sanal pozisyonların risk yönetimi ve kapanış kayıtları devam eder. **Acil durdur** yeni girişleri engeller ve tanımlı güvenli çıkış politikasını çalıştırır.
- Canlı işlem anahtarı veya canlı mod dashboard'da bulunmaz. İleride eklenecekse ayrı güvenlik tasarımı, ayrı onay akışı ve kapsamlı paper kanıtı gerektirir.

## Güvenlik ve doğruluk
- Bilinmeyen/eski/bozuk durum dosyası sıfırlanmaz; fail-closed.
- Bayat fiyat, kopuk WS, bozuk order book, aşırı spread, yetersiz likidite veya maliyet sonrası negatif edge yeni pozisyonu engeller.
- Dashboard'da eksik veri sıfır gibi gösterilmez; "bilinmiyor" olarak gösterilir.
- Kârlılık iddiası için en az walk-forward, holdout, fee/funding/slippage, sembol yoğunlaşması, bootstrap güven aralığı ve stres senaryoları gerekir.
- Hiçbir performans ekranı yalnız kazanma oranı veya brüt PnL göstererek yanıltamaz.

## Tamamlanma kriterleri
- Tek dashboard rotası gerçek birleşik durum dosyasını okur; örnek/sahte sayılar üretim görünümünde gösterilmez.
- Eski ajanların hiçbir workflow'u karar/pozisyon açma yetkisiyle çalışmaz; araştırma workflow'ları açıkça etiketlidir.
- Birleşik motor PAPER/SHADOW dışında çalışamaz.
- Testler: veri kesintisi, bozuk state, restart/reconcile, duplicate signal, fee/funding/slippage, long/short, risk halt, stop koruması.
- CI yeşil olmadan PR merge edilmez.


## Uygulama notu — 2026-10-31
- `site/index.html` tek ana giriş olarak birleşik Baş Trader panelini yükler.
- Önceki Radar paneli `site/radar.html` altında arşivlenir; eski bağlantıların bozulmaması için `site/birlesik.html` alias'ı korunur.
- `site/polat.html` korunur; Polat artık yeni pozisyon açmayan challenger modundadır.
- Legacy ve birleşik veriler aynı panelde birleştirilmiş portföy gibi gösterilmez. Shadow kararları ayrı gölge defterinden okunur; legacy baseline ayrı kalır.
- Panel üretim testi ana girişin birleşik şablon olduğunu, legacy sayfanın arşiv rotasında kaldığını ve Polat sayfasının korunduğunu doğrular.


## Tek yetki yayını — 2026-10-31

- birlesik.karar_modu: unified durumunda ana sayfa birlesik/durum.json dosyasını kullanır; önceki shadow durumu birlesik/golge/durum.json ayrı kalır.
- Durum seçimi zaman damgasına göre yapılır; eski shadow portföyü yeni paper portföyü gibi gösterilmez.
- Unified mod etiketi PAPER · TEK OTORİTE olarak görünür. Legacy Radar ve Polat arşiv/management-only durumundadır; portföyleri birleşik 100 USDT bakiyesine eklenmez.


## Dashboard veri kaynağı ve güncellik şeritleri — 2026-11-01

Her ana sekmenin üstünde, o görünümün gerçek kaynak yolu, kaynak zaman damgası ve hesaplama kapsamı gösterilir. Fırsat/Karar günlükleri JSONL kayıt zamanını; Portföy yalnız Unified PAPER durum dosyasını; Performans yayımlanmış karne snapshot'ını; Deney Laboratuvarı ayrı SHADOW sonuçlarını; Veri Sağlığı sunucu BBO tanılamasını; Ayarlar salt-okunur katalog dosyasını kullanır.

Mobilde kaynak şeridi satır sarar; ilk veri beklenirken erişilebilir yükleniyor durumu, uyarılarda aria-live bildirimi bulunur. Tarayıcıdaki public OKX ticker akışı karar motoru kaynağı değildir ve statik snapshot'a gerçek zamanlılık garantisi eklemez.
