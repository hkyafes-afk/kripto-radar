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

## Beyin: notundaki her başlığın karşılığı

| Not | Polat'taki fonksiyon |
|---|---|
| Yapı: HH/HL, LH/LL, yapı bozulması, range | `brain.structure` (salınımlar k=3 mum sonra kesinleşir, geleceği görmez) |
| Mum: gövde, iğne, kapanış konumu, formasyon | `brain.candle` (pin, güçlü, yutan, doji) |
| Hacim: kırılım onayı, climax, hacim profili (POC/değer alanı) | `brain.volume`, `brain.volume_profile` |
| Seviyeler: önceki gün/hafta Y-D-K, gün/hafta açılışı, VWAP, test sayısı | `brain.levels`, `brain.vwap` |
| Göstergeler: EMA 20/50/200, RSI, MACD, Bollinger + sıkışma yüzdeliği, ATR, Fibonacci | `series.Series`, `brain.fib_zone` |
| Türev: OI–fiyat dörtlüsü, fonlama, likidasyon patlaması, long/short, basis | `brain.derivatives` |
| Akış: derinlik, defter dengesi, kalıcı duvar (yerinde kalma süresi), büyük işlemler, delta/CVD, ayrışma | `brain.flow`, `Market._walls` |
| Seans ve saat | `brain.session` (günlüğe yazılır, raporda seansa göre kırılır) |
| BTC fiyatı/rejimi, dominans, stablecoin, takvim | BTC okuması, `extras` |
| Okuma sırası: büyük zaman dilimi → seviye → hacim → türev → akış | `brain.bias` → `brain.setups` → `brain.confirmations` |

Göstergeler tek başına sinyal sayılmaz. Polat yalnızca tanıdığı üç kurulumda işlem açar. Her kurulumun zorunlu koşulları yapı, seviye ve mumdan oluşur:

1. **Kırılım:** Önemli bir seviye, güçlü bir 15 dakikalık kapanışla ve en az 1,5 kat hacimle kırılır. Hacim patlaması (climax) varsa işlem yok.
2. **Geri çekilme:** Trend içinde destek bölgesinde bir ret mumu oluşur. Destek bölgesi şunlardan biri olabilir: 4S EMA20, Fibonacci 0,5–0,618, POC, VWAP, dünün dibi. Ayrıca 1S RSI'ın aşırı bölgede olmaması gerekir.
3. **Süpürme:** Stop kümesinin altı (ya da üstü) iğneyle alınır, mum seviyenin içinde kapanır. Bunu bir likidasyon patlaması ya da hacim desteklemelidir.

Yön, 1G ve 4S yapısından gelir. BTC'nin 4S yapısı terse dönmüşse altcoinlerde o yönde işlem açılmaz.
Zorunlu koşullar sağlandıktan sonra en az 3 bağımsız teyit gerekir. Teyitler:
- hacim
- CVD
- defter dengesi
- arkadaki kalıcı duvar
- OI–fiyat ilişkisi
- karşı tarafın kalabalık olması
- MACD
- EMA dizilimi
- Bollinger sıkışmasından çıkış
- BTC'nin aynı yönde olması
- seviyenin daha önce test edilmiş olması

Vetolar, teyit sayısından bağımsız olarak işlemi engeller:
- karşı tarafta, 1R'den yakın kalıcı bir duvar
- CVD ayrışması
- kalabalık fonlama
- hacim patlaması
- yakında yüksek etkili ABD verisi

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

- `kararlar.jsonl`: zorunlu koşulları tutan **her** kurulum. Girildi mi, girilmediyse neden, o anki bütün okumalar.
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

- Kurulum parametreleri (1,5 kat hacim, 3 teyit, %50 geri verme vb.) mantıklı başlangıç değerleri. Geçmiş veride kanıtlanmış değiller. Polat bir deney: hangi okumanın para kazandırdığını günlük gösterecek.
- Bu ortamdan OKX'e ağ erişimi yok. Akış kodu, yerel sahte bir OKX sunucusuna karşı test edildi: abonelik, veri, kopma ve yeniden bağlanma. Gerçek OKX'e ilk bağlantı GitHub'da olacak.
