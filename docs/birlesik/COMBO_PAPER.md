# Bitsgap COMBO — izole PAPER sürümü

## Onaylı sabitler

- Enstrüman: OKX public perpetual BTC-USDT-SWAP (BTCUSDT perp).
- Grid bandı: **72,426.7–83,608.5 USDT**.
- Grid: **24 eşit bileşik yüzde aralığı / 25 fiyat çizgisi**; adım yaklaşık **%0,60**.
- BUY/DCA ve SELL lot miktarı: **0.013 BTC**.
- Ana TP: güncel açık lotların ağırlıklı ortalama maliyetinin **%1,50 üzeri**.
- Hard stop: alt bandın **%1 altı**.
- Ayrı sanal sermaye: **2,500 USDT**; **10x yalnızca teminat/maruziyet simülasyonu**.
- İlk sürüm LONG-only. SHORT, hedge, private API ve gerçek emir gönderimi reddedilir.

## Çalışma akışı

1. İlk geçerli ve yeterli BBO ask miktarı geldiğinde, fiyat bandın içindeyse 0.013 BTC market Base Order PAPER defterinde açılır.
2. Fiyatın altında kalan grid çizgilerine 0.013 BTC BUY DCA limit emirleri yerleştirilir. Her emir için 10x simüle teminatı izole cüzdanda rezerve edilir; teminat veya 25,000 USDT brüt nominal sınırı yetmiyorsa emir açılmaz.
3. Her dolan BUY lotu için bir üst grid çizgisine, yalnız o lotun miktarını kapatabilen reduce-only SELL emri açılır. Her SELL fill'de bir alt çizgide 0.013 BTC BUY yeniden kurulur (aynı seviyede açık BUY varsa mükerrer emir eklenmez).
4. SELL miktarı emir kabulünde ve dolum anında lot bakiyesiyle tekrar sınanır. Lot yoksa veya miktar aşılırsa satış reddedilir/iptal edilir; pozisyon SHORT'a döndürülemez.
5. Her BUY/SELL dolumundan sonra açık pozisyonun ortalama maliyeti ve global TP miktarı güncellenir. TP fiyatı yukarı tick yuvarlamasıyla avg_entry × 1.015 olarak hesaplanır.
6. TP tetiklenince bütün grid/DCA emirleri iptal edilir ve kalan LONG miktarı reduce-only limit kapanışla azaltılır. Hard stop'ta emirler iptal edilir ve kalan LONG miktarı taker varsayımı + konfigüre stop kaymasıyla kapatılmaya çalışılır. Kapanış BBO miktarı yetersizse kalan miktar PAPER defterinde açık kalır; SHORT açılmaz.

## Dolum ve veri dürüstlüğü

- radar.ws.OKXFeed ticker akışına ek bir observer kanalı sağlar; mevcut on_tick callback sözleşmesi değişmez. COMBO listener var olan Feed'e enjekte edilebilir veya ayrı bir public-only OKX feed'i açabilir.
- Listener, kimlik doğrulaması gerektirmeyen OKX `GET /api/v5/public/instruments` uç noktasından enstrüman metadata'sını alır. Enstrüman `linear`, `BTC` değer para birimli ve `live` değilse feed kabul edilmez; gerçek `tickSz` ayarıyla eşleşmelidir.
- SWAP ticker BBO miktarı kontrat adedidir; PAPER motorunun defteri BTC tuttuğundan miktar `contract_count × ctVal` ile BTC'ye normalize edilir. Metadata alınamazsa veya birim etiketi doğrulanmazsa dolum yapılmaz. Aynı quote içindeki emirler, ilgili BBO miktarını paylaşır; yetersiz miktar kısmi dolum bırakır.
- Emir defterindeki PAPER miktarları BTC cinsindendir. OKX'in gerçek vadeli emirlerinde miktar kontrat ve `lotSz/minSz` kurallarına tabidir; `btc_to_contracts()` dönüşüm kontrolü sağlar, ancak ilk sürüm hiçbir miktarı borsaya göndermez veya canlı emir uygunluğu iddia etmez.
- Bu PAPER fill modeli tam order-book kuyruğunu, derinliğin sonraki seviyelerini veya gerçek borsadaki emir önceliğini modellemez. Bu yüzden sonuçlar gerçek işlem/performans kanıtı değildir.
- Varsayılan ücretler proje radar.ledger.fees_cfg varsayılanlarıyla uyumlu: maker %0.02, taker %0.05. Gerçek hesap ücreti farklıysa ayrıca ayarlanmalıdır.
- Quote bayatsa, sembol farklıysa, fiyat/BBO geçersizse veya WebSocket tick zamanı tekrar/geriye gidiyorsa işlem/dolum yapılmaz.
- Durum combo_paper.json dosyasına atomik yazılır. Bozuk dosya veya yapılandırma imzası uyuşmazlığında sessiz sıfırlama yapılmaz.

## Çalıştırma / entegrasyon sınırı

Bu modül şu an **izole kütüphane bileşeni** olarak eklenmiştir; UnifiedTrader, Polat, Elif veya ana radar live döngüsüne otomatik bağlanmaz ve GitHub Actions tarafından kendiliğinden başlatılmaz. Ayrı bir PAPER runner kontrollü biçimde şu nesneleri oluşturmalıdır:

```python
from radar.unified.combo import ComboConfig, ComboStrategy, ComboWebSocketListener

strategy = ComboStrategy(ComboConfig(), root="site/birlesik/combo_paper")
listener = ComboWebSocketListener(strategy)
listener.start()
# Kontrollü runner yaşam döngüsü:
# listener.stop()
```

Bu örnekte özel anahtar, private API veya gerçek emir yolu yoktur. 2,500 USDT COMBO defteri, UnifiedTrader'ın 100 USDT defterinden ayrı konumda tutulur. TP veya hard stop sonrasında yeni cycle otomatik başlatılmaz; durdurma durumu korunur ve yeni çalışma için açık bir operatör kararı gerekir.
