# Ücretsiz çalışma modeli (ek sunucu/PC/hosting yok)

Durum: uygulanabilir hedef mimari; kalıcı ve milisaniyelik runtime iddiası değildir.

## Kısıtlar

Bu proje ek VPS, ücretli hosting veya kullanıcının açık tuttuğu kişisel bilgisayar gerektirmeyecek şekilde tasarlanır. Kod, testler ve statik panel GitHub'ın ücretsiz/hesap limitleri içindeki özellikleri kullanabilir. GitHub Actions çalışma dakikaları ve eşzamanlılık kotaları hesaba tabidir; ücretsiz planın limitleri aşılırsa işler bekleyebilir veya çalışmayabilir.

## Ücretsiz düzende neler yapılabilir?

- Kaynak kod, PR, issue, sürüm takibi ve testler GitHub üzerinde yürütülür.
- GitHub Actions; tetiklenen testleri, periyodik araştırma/hesaplama işlerini ve statik dashboard üretimini çalıştırabilir.
- GitHub Pages etkin ve repo ayarları izin veriyorsa dashboard statik olarak yayınlanabilir.
- Dashboard tarayıcıdan erişilebilen halka açık borsa WebSocket/REST uçlarına bağlanabilir. Bu yalnızca açık olan tarayıcı sekmesinin veri almasıdır; karar motorunun sunucu tarafında sürekli çalıştığı anlamına gelmez.
- PAPER/SHADOW hesaplama ve geçmiş raporları workflow çalıştığı sırada üretilebilir. Süreçler arası devamlılık yalnızca açıkça kalıcılaştırılmış artifact/commit verisiyle sağlanır.

## Ücretsiz düzende garanti edilemeyenler

- 7/24 tek process, kesintisiz WebSocket aboneliği veya sıfır kesinti.
- Milisaniyelik veri işleme/yayın garantisi ve gerçek zamanlı push API.
- Actions cron tetiklerinin tam dakikasında çalışması; GitHub zamanlayıcıları gecikebilir.
- Actions runner belleğinde tutulan durumun bir sonraki çalışmaya kendiliğinden taşınması.
- Ücretsiz dakikaların, artifact saklama alanının veya Pages kullanımının sınırsız olması.

## Güvenli işletim kuralları

1. Actions çalışmadığında yeni PAPER girişleri de çalışmıyor kabul edilir; dashboard eski snapshot'ı LIVE diye sunmaz.
2. Her snapshot üretim zamanı, kaynak zamanı ve veri kalite durumu taşır. Yaşı doğrulanamayan veri UNKNOWN, eşik aşan veri STALE olur.
3. Workflow tetiklenmesi veya dashboard sayfasının açılması, borsa bağlantısının aktif olduğuna kanıt değildir.
4. Kullanıcı bilgisayarının veya telefonunun sürekli açık kalması varsayılmaz.
5. Ücretli sunucu veya dış hosting gerektiren işlevler varsayılan olarak devre dışı tutulur. Ücretsiz mimaride karşılığı yoksa özellik "uygulanamıyor/kapalı" gösterilir.
6. Gerçek emir/private API, gerçek para veya canlı yürütme eklenmez. UnifiedTrader tek yeni pozisyon otoritesi olarak PAPER modunda kalır.
7. GitHub Actions lease zinciri tek bir sürekli trader process'i değildir. Devir aralığı, iptal, runner değişimi ve kota nedeniyle oluşabilecek boşluklar ölçülmeden 24/7 çalışma iddia edilmez.

## Kabul tanımı

"Ücretsiz ortamda tamamlandı" demek; ilgili kodun GitHub Actions üzerinde test edilmesi, başarısız/eskimiş veri durumlarının güvenli ele alınması ve ücretli altyapı olmadan çalışabildiğinin sınırlarıyla belgelenmesi demektir. "Sürekli canlı" veya "milisaniyelik" anlamına gelmez.
