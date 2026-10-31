# ATR üst sınırı — eşleştirilmiş deney

Tarih: 2026-10-31  
Çalışma: [GitHub Actions #41204106254](https://github.com/hkyafes-afk/kripto-radar/actions/runs/41204106254)  
Ham işlem defteri: `atr-research-41204106254` workflow artifact (30 gün saklama).  
Kapsam: aynı günlük fırsat üretimi ve V10 kuralları; yalnız ATR üst sınırı değişti. Evren point-in-time günlük 30 günlük medyan hacim ≥ 5M USDT ve her tarihte ilk 100 sembol. 2025-01-01 sonrası OOS.

## Ana sonuç

| ATR üst sınırı | OOS işlem | Ortalama net R | PF | OOS maks. düşüş |
|---|---:|---:|---:|---:|
| %3 | 78 | +0,187 | 1,44 | −%3,9 |
| %5 | 466 | +0,171 | 1,41 | −%14,2 |
| %7 | 1.547 | +0,005 | 1,01 | −%41,3 |
| %10 | 2.933 | −0,015 | 0,97 | −%38,2 |
| %12,5 | 3.583 | −0,003 | 0,99 | −%43,4 |
| Sınırsız | 4.209 | +0,003 | 1,01 | −%43,0 |

## Yön ayrımı (OOS)

| ATR | Long n / ort. R / PF | Short n / ort. R / PF |
|---|---|---|
| %3 | 53 / +0,287 / 1,79 | 25 / −0,023 / 0,96 |
| %5 | 261 / +0,307 / 1,83 | 205 / −0,002 / 1,00 |
| %7 | 914 / −0,017 / 0,96 | 633 / +0,036 / 1,08 |
| %10 | 1.646 / −0,111 / 0,78 | 1.287 / +0,109 / 1,31 |
| %12,5 | 1.945 / −0,122 / 0,75 | 1.638 / +0,138 / 1,42 |
| Sınırsız | 2.212 / −0,102 / 0,79 | 1.997 / +0,120 / 1,39 |

**Yorum:** geniş ATR eşikleri altında short performansı görece iyileşirken long performansı bozuluyor. Bu, tek bir ATR eşiğinin iki yöne aynı şekilde uygulanmasının sakıncalı olabileceğini gösteriyor; fakat yön bazlı yeni politika bu deneyden doğrudan çıkarılamaz.

## Sağlamlık kontrolleri

Basit işlem bootstrap'ı (10.000 tekrar, %95 aralık; işlemler bağımsız varsayılır):
- %3: [−0,134; +0,540] R
- %5: [+0,054; +0,289] R
- %7: [−0,054; +0,063] R
- %10: [−0,054; +0,026] R
- %12,5: [−0,038; +0,032] R
- Sınırsız: [−0,029; +0,036] R

Coin-küme bootstrap'ı (5.000 tekrar; aynı coin'in işlemleri birlikte örneklenir, %95 aralık):
- %3: [−0,071; +0,878] R (yalnız 8 coin — çok seyrek)
- %5: [+0,031; +0,335] R (46 coin)
- %7: [−0,070; +0,082] R
- %10: [−0,064; +0,037] R
- %12,5: [−0,048; +0,043] R
- Sınırsız: [−0,036; +0,046] R

Coin bootstrap'ı da %5 lehine sinyal veriyor; ancak bu yöntem zaman içinde ortak piyasa şoklarını, aynı anda açık işlemlerin örtüşmesini ve strateji/coin bağımlılığını tam modellemez. Güven aralıkları nihai istatistiksel kanıt değildir.

## Zaman ve yoğunlaşma uyarıları

- %5 OOS ortalaması 2025'te 162 işlemde −0,112R; 2026'da 304 işlemde +0,321R. Sonuç dönemlere göre kararlı değil.
- %5 OOS 46 sembole yayılmış; en yüksek toplam R üreten üç sembol ETH, ADA ve TON'dur. Üç sembolün toplamı tüm OOS R toplamının yaklaşık %44,5'idir.
- %3 yalnız 78 işlem ve 8 sembol içeriyor; en iyi görünen ortalama R örneklem küçüklüğü ve coin yoğunlaşması nedeniyle güvenilir eşik seçimi sayılmaz.
- Önceki LAB çalışmalarında 2025+ verisi kullanıldığı için bu dönem dokunulmamış holdout değildir.
- Deney günlük zaman dilimi ve mevcut yönetici stratejileriyle sınırlıdır; 15m/4H için sonuç çıkarılamaz.

## Maliyet ve model sınırları

Simülatör maker/taker ücretlerini, giriş kaymasını, ATR tabanlı stop kaymasını ve mevcut tarihsel funding'i kullanır. Funding geçmişi eksikse kodun varsayılan long funding maliyeti devreye girer; eksikliği raporlamak ayrıca gerekir. Mum tabanlı simülasyon emir defteri derinliğini, sıra önceliğini, gecikmeyi ve gerçek kısmi dolumu modellemez. Gerçek hesap ücret kademesi de modellenmemiştir.

## Karar

- %7, %10, %12,5 ve limitsiz eşikler mevcut deneyde zayıf/negatif; bunları üretime genişletmek için kanıt yok.
- %3 umut verici ama örneklem yetersiz.
- %5 en makul araştırma adayı; bu sonuç **%5'i otomatik olarak onaylamaz**.
- Üretim eşiği değiştirilmeyecek. Ayrı long/short ve rejim kırılımları, blok bootstrap, maliyet stresleri, 100+ gerçek gölge işlem ve yeni ileri dönem holdout gerekir.
