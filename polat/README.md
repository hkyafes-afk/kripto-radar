# Polat

Kripto Radar'dan **bağımsız** tek ajan. `radar/` kodunu, defterini ve kurallarını kullanmaz; kendi 100.000$ sanal
sermayesiyle çalışır. Gerçek para yok. **Yatırım tavsiyesi değildir.**

## Veri: geldiği anda

Sabit aralıkla veri sorgulanmaz. Her WebSocket mesajı geldiği an işlenir.

| Bağlantı | Kanallar | Kapsam |
|---|---|---|
| piyasa | tickers, liquidation-orders, funding-rate, open-interest, mark-price, index-tickers | tickers ve likidasyonlar bütün USDT vadeliler için, diğerleri izlenen 30 coin için |
| defter | books (400 kademe, artımlı, checksum doğrulamalı) | izlenen 30 coin |
| tape | trades (her işlem) | izlenen 30 coin |
| mum | 5dk, 15dk, 1S, 4S, 1G (UTC) | izlenen 30 coin |
| mum_genel | 1S | bütün piyasa (hacim ivmesi için) |

REST yalnızca dört iş için kullanılır: açılışta geçmiş mumlar, long/short oranı (OKX 5 dakikalık verir), devir arasındaki 1 dakikalık mumlar ve kontrat bilgileri.
OKX dışından ücretsiz kaynaklar:
- BTC dominansı (CoinGecko, 5 dk)
- stablecoin arzı (DefiLlama, 30 dk)
- ekonomik takvim (ForexFactory)

**Token unlock** için güvenilir, ücretsiz ve anahtarsız bir kaynak yok. Polat bu veriyi kullanmaz.

Ölçülen hız (yerel test): mesaj başına ortalama ~64 µs, 400 kademeli defter mesajında p95 ~190 µs, saniyede ~15.000 mesaj
işleme kapasitesi. Asıl gecikme ağdan gelir. Polat her mesajda borsa zaman damgası ile varış arasındaki farkı ölçer ve `ozet.json` dosyasına yazar.
Gecikmeyi en çok düşürecek adım, Polat'ı OKX'e yakın bir sunucuya taşımak (ör. Oracle Cloud ücretsiz Tokyo).

## Coin seçimi

Polat bütün piyasayı izler. Derin okumayı 30 coine ayırır. Puan, iki sıralamanın eşit ağırlıklı toplamıdır: 24 saatlik hacim sırası ve hacim ivmesi sırası. Hacim ivmesi, son 1 saatin hacminin önceki 24 saatin saatlik ortalamasına oranıdır.
Seçim 5 dakikada bir yenilenir:
- Listeye giren bir coin, sırası 40'ın dışına düşene kadar çıkmaz (histerezis).
- Açık pozisyonu olan coin listeden çıkmaz.
- BTC ve ETH her zaman izlenir.

## Beyin: her okuma oy verir (`karar.py`)

Polat, kendisine verilen her okumayı hesaplar ve her okuma kararda oy kullanır. Oy +1 ile −1 arasındadır: +1 long yönünü, −1 short yönünü gösterir. Testte tek bir coin için 83 ayrı okuma oy verdi.

| Kategori | Okumalar (kaynak) |
|---|---|
| trend (ağırlık 2) | 1G/4S/1S yapı ve yapı bozulması, EMA20/50/200 ve eğimleri, ADX + DI spread, Parabolic SAR, SuperTrend, TRIX, Dow hacim teyidi (kitap, txt) |
| seviye | destek/direnç konumu ve test sayısı, önceki gün/hafta Y-D-K, gün/hafta açılışı, VWAP, POC/VAH/VAL, 4S salınımları, yuvarlak seviyeler, trend çizgileri |
| formasyon | OBO/TOBO, çift tepe/dip, üçgenler, takozlar, kanallar, elmas, bayrak/flama, fincan; Gartley, Yarasa, Yengeç, ABCD, Wolfe; mum formasyonları (seviyede ve trend bağlamında güçlenir) |
| momentum | RSI (yatayda aşırı bölge dönüş, trendde güç), Stokastik, CCI, Williams %R, Ultimate, Momentum, MACD histogramı |
| hacim | hacim oranı, climax, OBV eğimi ve kendi direncini kırması, MFI, PVI, Demand Index |
| uyuşmazlık | fiyat ile RSI, MACD, OBV, MFI ve CCI arasında (1S ve 15dk); CVD ayrışması |
| türev | OI–fiyat ilişkisi, fonlama (kalabalığın tersi), basis, long/short, likidasyon patlaması (tükenme), tahmini likidasyon kümesi (mıknatıs) |
| akış | CVD 5/15 dk, defter dengesi, kalıcı duvarlar, büyük işlemler, tape hızı |
| piyasa | BTC 4S/1S yönü, BTC dominansı değişimi, stablecoin arzı, haber sonrası piyasa tepkisi |

**Tetikler (zamanlama):**
- kırılım ve anlık kırılım (canlı 1 dk mumda seviye geçişi, 2 kat hacim, CVD aynı yönde)
- geri test ve rol değişimi
- seviyede ret mumu
- süpürme
- sıkışmadan çıkış (kitap: sıkışmada yön tayini)
- 15 dk yapı bozulması, trend çizgisi kırılımı
- formasyonun tamamlanması
- uyuşmazlık + dönüş mumu
- SAR ve SuperTrend dönüşü, OBV kırılımı
- MACD ve Stokastik kesişimi
- likidasyon tükenmesi

**Giriş şartları (hepsi birden):**
1. Toplam puan eşiğin üstünde. Gece ve hafta sonu eşik yükselir.
2. Büyük zaman dilimi trendi ters değil.
3. Kitaptaki şablon kuralı: trend, hacim ve momentum ailelerinin üçü de aynı yönü gösteriyor.
4. En az bir tetik aynı yönde.
5. Sert kapıların hiçbiri engellemiyor: veri tazeliği, defter doğrulaması, spread, stop genişliği, takvim, aşırı fonlama, 1R'den yakın karşı duvar, risk motoru, derinlik.

**Stop:** Yapıya göre konur: girişin aleyhindeki en yakın tepe, dip, stop kümesi ya da seviyenin 0,2 ATR ötesine.

**Ne zaman bakıyor:** Her 1 dakikalık kapanışta tam değerlendirme yapılır. Fiyat önemli bir seviyeyi geçtiği an, mum kapanışı beklenmeden ayrıca bakılır (en sık 10 saniyede bir).

Ölçülen süre: önbellekli bir değerlendirme ~3 ms, 1 dk göstergeleri ~4 ms. Bu ölçüm v2.1 içindir; ayrıntı aşağıdaki "kesinleşenler" tablosunda.

## Öğrenme ve dürüst ölçüm (`learn.py`)

- **Gölge sinyaller:** Eşiğe yakın ve tetikli her sinyal, işlem açılmasa bile aynı plana göre takip edilir.
- **Maliyetler:** Gölge sinyallerin sonucundan giriş ve çıkış taker ücreti, spread ve kayma tahmini ve fonlama düşülür.
- **Kıyaslamalar:** Her sinyalin yanına aynı anda, aynı stop ve aynı maliyetle iki giriş konur: biri **rastgele yönlü**, diğeri **sadece trend yönünde**. Bunlara ek olarak **BTC al-tut** ile kıyaslanır. Polat bunları geçemiyorsa rapor bunu açıkça yazar.
- **Walk-forward:** Örneklerin ilk %70'i eğitim, son %30'u doğrulama için kullanılır. Bir okumanın katsayısı ancak iki dönemde de etkisi aynı yöndeyse ve yeterli örnek varsa değişir (eğitimde ≥30, doğrulamada ≥10). Değişim, örnek sayısına göre küçültülerek uygulanır. Doğrulamada tutmayan okumalar raporda "reddedilen" olarak sayılır.
- **Kanıt eşiği:** 100 sonuçlanmış sinyale kadar raporda "kanıt yetersiz" yazar.
- **Sınır:** Bütün okumalarıyla geriye dönük bir test yapılamaz, çünkü geçmiş emir defteri, tape ve likidasyon verisi ücretsiz olarak yok. Polat'ın gerçek örneklem dışı testi, canlı veriyle yaptığı sanal işlemlerdir.

## Bu sürümde kesinleşenler (v2.1)

| Konu | Nasıl çözüldü |
|---|---|
| VWAP ilk dakikalar | Günlük VWAP 1 dk mumlardan, ilk dakikada oluşur. Gün içi hacim 24 saatin %5'ine ulaşana ya da 30 dk geçene kadar kayan 24 saatlik VWAP kullanılır. Haftalık VWAP ve 4S dip/tepeden başlayan VWAP da var. |
| Demand Index | Sibbet'in formülü (MetaStock dönüşümü, wabbit 2006), −100…+100 |
| Likidasyon kümeleri | Tahmindir (kimin nerede likide olacağını yalnızca borsa bilir). Fiyatın sonradan ulaştığı kümeler silinir. Kaldıraç dağılımı OKX'in gerçek likidasyon akışıyla kalibre edilir; isabet oranı panoda görünür. |
| Çift sayım | Aynı şeyi ölçen okumalar önce alt grupta ortalanır (trend göstergeleri, osilatörler, hacim akışı, uyuşmazlıklar, ortalama fiyatlar, mumlar, CVD, BTC). |
| Gevşek formasyonlar | Üç dokunuşun çizgiye oturması (≤0,5 ATR), ≥2 ATR boy, taze kırılım (son 3 mum, ≥0,25 ATR) şartı. Eski kırılım oy vermez. Rastgele veride Wolfe %32 → %2. |
| İyimser dolum | Stop-market: tetikten sonraki 250 ms'nin en kötü fiyatı ile defter dolumunun kötüsü. Hedef: yalnızca 1 tick ötesinde işlem görünce dolar. |
| Gölge sinyaller | Gerçek işlem gibi yönetilir: 1R başabaş, 2R yarısı, %50 geri verme, yapısal iz stop, süre. Maliyetler düşülür. |
| Her sonuçtan ders | Her kapanan işlem ve gölge sinyal okumaların katsayısını hemen günceller. Yol defteri: zarar ettiren yoldan dönülür, kâr ettiren yol benimsenir (en az 5 sonuç, küçültülmüş ortalama). Hata sınıfları sınırlı ayar üretir (en dar stop, başabaş seviyesi, coin bazlı kayma cezası). Hatasız işlemler ayarı yavaşça başlangıca çeker. Hepsi `gunluk/dersler.jsonl` dosyasına yazılır. |
| Öğrenme koruması | A/B: öğrenilmiş puanla seçilenler, öğrenilmemiş puanla seçilenlerden 0,10R'den fazla kötüyse çevrimiçi dersler geri alınır. Toplu katsayılar walk-forward ile ayrıca doğrulanır. |
| Açılış | Kalıcı HTTPS bağlantı havuzu. Uç nokta başına OKX hız kovası (sınırın %80'i). 429/50011'de kademeli tekrar. En hızlı sunucu ölçülüp öne alınır. Önce açık pozisyonlu coinler ve BTC/ETH yüklenir. Halef ısınırken yükler, devralmayı beklemez. |
| Gecikme | Değerlendirme zaman dilimi ve formasyon parçalarına bölünür, aralarda olay döngüsüne söz verilir. EMA/Wilder C hızında (scipy), salınım noktaları vektörel. Günlük yazımı ayrı iş parçacığında. Ölçüm (30 coin aynı anda): en uzun bloklanma ~10 ms, zamanın %99'unda < 0,05 ms. |
| Devir | Nöbet (lease) + sıcak halef (`polat/lease.py`). Halef 15 dk önce çağrılır (başarısızsa her dakika yeniden; HTTP sonucu günlüğe ve Telegram'a), ısınır, selef bıraktığı an devralır. Bitişte halef koşusu görünmüyorsa döngü 10 dk'ya kadar uzar. Nöbet atomik alınır: aynı anda iki aday varsa yalnızca biri kazanır. Nabzı gönderilemeyen nöbetçi, başkası devralabilmeden önce işlem açmayı keser; nöbetini kaybettiğini görünce hiçbir şey göndermeden durur. Nöbeti yalnızca sahibi bırakır. Bekçi her 10 dk'da bir bakar, nöbet sağlıklıysa Python kurulmadan ~10 sn'de çıkar. Aradaki stop/hedefler 1 dk mumlarla kontrol edilir. |
| Kesinti dersleri (28.10) | İş akışı elle devre dışı bırakılmıştı → halef başlatılamadı, zincir koptu. `kararlar.jsonl` 100 MB'ı geçti → gönderimler durdu, bekçi devraldı, iki Polat 3 saat aynı anda işlem yaptı. Selef bırakınca halef ve bekçi aynı anda devraldı. Hepsi `tests/test_polat_lease.py`'de test edilir. |
| Telegram | `POLAT_TELEGRAM_CHAT_ID` secret'ı tanımlanırsa Polat mesajları ayrı sohbete gider. |
| Pano | Ana sitede `polat.html` (ana panodaki "🎯 Polat" düğmesi). Veri `polat-veri` dalındaki `ozet.json` dosyasından gelir, 30 sn'de bir yenilenir. Polat her dakika yayınlar. |

## Kurallar (senin ilkelerin)

| İlke | Kural |
|---|---|
| Sabit risk, küçük adım, kartopu | Her işlemde özsermayenin %0,5'i |
| Kaldıraç dengesi | Büyüklük stop mesafesinden hesaplanır. Kaldıraç yalnızca marjı belirler ve likidasyon fiyatı stop'tan en az 2 stop mesafesi ötede kalacak şekilde seçilir (tavan 10x) |
| %12,5 kuralı | Hedef, %12,5'lik fiyat hareketi |
| Kazanmak için kaybetmeye razı olma sınırı | 1R'de stop başabaşa çekilir. 1R'den sonra kâr, tepeden en fazla %50 geri verilir. 2R'de pozisyonun yarısı satılır (Minervini). Stop ayrıca 15dk yapısına göre iz sürer |
| Durmayı bilmek | Gün içinde −%2'de o gün, hafta içinde −%5'te o hafta yeni işlem açılmaz. Üst üste 3 zarardan sonra 12 saat ara verilir. Zirveden −%10'da risk yarıya iner, −%15'te Polat durur. Durduktan sonra yalnızca senin onayınla devam eder (`devam` girdisi) |
| Overtrade | Günde en çok 8 işlem. Aynı anda en çok 5 pozisyon, aynı yönde en çok 3. Toplam açık risk en çok %2,5 |
| İntikam işlemi yok | Stop olunan coinde aynı yöne 4 saat giriş yapılmaz |
| Plan yoksa işlem yok | Giriş, stop, hedef, gerekçe ve teyitler yazılmadan emir gitmez |
| Overwatch yok | Pozisyon yalnızca kuralla yönetilir: stop yalnızca ileri taşınır, keyfi müdahale yok |

## Yürütme (OKX gibi)

- Piyasa emri, gerçek emir defterinde kademe kademe dolar. Kayma gerçekçidir; derinlik yetmezse emir reddedilir.
- Ücretler OKX'in taker (%0,05) ve maker (%0,02) oranlarıdır.
- Stop, borsa tarafında stop-market ve reduce-only gibi çalışır: tetiklendiği andaki defterden dolar.
- Hedef, reduce-only limit emirdir. Fiyat hedefin içinden geçince dolar.
- Kontrat yuvarlaması OKX kurallarına göre yapılır. İzole marj, likidasyon ve gerçek funding kesimi uygulanır.
- OKX demo adaptörü (`OkxBroker`) hazır ama anahtar verilene kadar kapalı. OKX'e karşı canlı test edilmedi. Anahtar geldiğinde ilk iş küçük bir demo emirle uçtan uca doğrulama olacak. Gerçek para modu kod tarafından reddedilir.

## Günlük

`polat_veri/gunluk/` klasöründe üç dosya tutulur:

- `kararlar.jsonl`: zorunlu koşulları tutan **her** kurulum (son 2 UTC günü). Girildi mi, girilmediyse neden, oylar ve plan. Ham okumalar buraya yazılmaz.
- `arsiv/okuma-GG.jsonl.gz`: ham okumalar, yalnızca girilen kurulumların ve her 100 kurulumdan birinin (örnek). Günde yaklaşık 25 MB sıkıştırılmış olduğu için hepsi tutulmaz.
- `islemler.jsonl`: açılış, kısmi satış, stop taşıma, funding ve kapanış. Kapanışta R, MFE/MAE, ücret, kayma, süre, giriş ve çıkış okumaları ve hata sınıfları kaydedilir. Hata sınıfları: ilk 15 dakikada stop, büyük kayma, 1R+ kârdayken zararla kapanış, likidasyon.
- `olaylar.jsonl`: başlama ve durma, seçici değişiklikleri, risk durdurmaları, hatalar.

`RAPOR.md` dosyası son 1, 50 ve 500 günü özetler. Kırılımlar: kurulum, çıkış nedeni, seans, teyit sayısı ve BTC rejimi. Girilmeyen kurulumların hangi engelde kaldığı ve hata sınıfları da raporda yer alır.

## Çalıştırma

GitHub'da `.github/workflows/polat.yml`: Her döngü yaklaşık 340 dakika sürer. Zamanlanmış tetik kuyruğa girer, önceki döngü biter bitmez yenisi başlar (arada yaklaşık 1 dakika). Aradaki stop ve hedefler 1 dakikalık mumlarla kontrol edilir; aynı mumda ikisi birden varsa stop sayılır. Durum ve günlük `polat-veri` dalında tutulur.

```
python -m polat calis --kok polat_veri          # sürekli
python -m polat rapor --kok polat_veri
python -m polat devam --kok polat_veri          # −%15 durdurmasından sonra, yalnızca kullanıcı onayıyla
```

## Dürüst notlar

- Eşik, kategori ağırlıkları, risk ve çıkış sayıları başlangıç değerleri (`config.yaml`, `karar.py`). Geçmiş veride kanıtlanmış değiller ve kullanıcı onayına açıklar. Hangi okumanın işe yaradığını gölge sinyaller ve kıyaslamalar gösterecek.
- Demand Index basitleştirilmiş bir hesap. Likidasyon kümeleri OI ve fiyattan çıkarılmış bir tahmin; gerçek bir likidasyon haritası değil.
- Bu ortamdan OKX'e ağ erişimi yok. Akış kodu, yerel sahte bir OKX sunucusuna karşı test edildi: abonelik, veri, kopma ve yeniden bağlanma. Gerçek OKX'e ilk bağlantı GitHub'da olacak.
