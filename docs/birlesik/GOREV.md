# Görev tanımı (kullanıcının 30.10.2026 talimatı, olduğu gibi)

> Not: Dosya kullanıcıdan geldiği haliyle 966. satırda ("Swing: ... Günlük kapanış stopu seçeneği") bitiyor; Aşama 8'in devamı ve EffectivenessAuditor / Dashboard aşamalarının ayrıntıları gelen dosyada yok. Bu aşamalar PR listesindeki başlıklara göre planlandı (docs/birlesik/PLAN.md).

---

GitHub deposu:
hkyafes-afk/kripto-radar

ROLÜN

Bu depoda çalışan kıdemli bir quantitative developer, sistem mimarı, algorithmic trading researcher, risk mühendisi ve frontend geliştiricisi olarak hareket et.

Yalnız tavsiye, örnek kod veya plan üretme. Depoya erişebiliyorsan görevi doğrudan uygula:

- Depoyu incele
- Güvenli bir çalışma branch’i oluştur
- Kod değişikliklerini yap
- Testleri yaz
- Testleri çalıştır
- Hataları düzelt
- Mantıksal commitler oluştur
- Branch’i GitHub’a gönder
- İncelenebilir pull request aç

Her küçük aşamada kullanıcıdan onay isteme. Belirsiz ve riskli bir noktada mevcut davranışı koruyan, gerçek işlem açmayan ve paper/shadow modunu tercih eden güvenli varsayımla ilerle.

ANA AMAÇ

Kripto Radar projesini, birbiriyle yarışan ve aynı işlemleri tekrarlayan çok sayıda isimli ajan sisteminden çıkararak tek merkezden karar veren profesyonel bir Baş Trader sistemine dönüştür.

Sistemin temel amacı:

1. İşlem maliyetlerinden sonra sürdürülebilir pozitif beklenti üretmek
2. Sermayeyi kontrollü ve ölçülebilir biçimde büyütmek
3. Dakikalık, saatlik, günlük ve haftalık fırsatları farklı strateji mantıklarıyla değerlendirmek
4. OKX canlı piyasa verilerini kullanarak gerçek zamanlı paper trading yapmak
5. Şartları sağlayan paper işlemleri kullanıcıdan tek tek onay istemeden otomatik açmak
6. Pozisyonları otomatik yönetmek ve kapatmak
7. Botun gerçekten işe yarayıp yaramadığını sürekli ölçmek
8. OKX Demo API erişimi sağlandığında strateji sistemini değiştirmeden yalnız execution adaptörünü değiştirmek
9. Gerçek para ve gerçek borsa emirlerini bu çalışmada kesinlikle kapalı tutmak

DEĞİŞMEZ GÜVENLİK KURALLARI

- Main branch üzerinde doğrudan çalışma
- Main tabanlı yeni feature branch oluştur
- Önerilen branch adı:
  feat/unified-trader-foundation
- Mevcut sistemi tek seferde silme
- Mevcut açık paper pozisyonları ve işlem defterini bozma
- Geriye uyumluluk sağla
- Büyük veri formatı değişikliklerinde migrasyon veya uyumluluk katmanı oluştur
- API key, secret, passphrase, erişim tokenı veya gerçek hesap bilgisi isteme
- Secret veya credential kod içine yazma
- Gerçek OKX emir bağlantısı kurma
- Gerçek işlem modunu etkinleştirme
- Paper dışındaki execution modlarını varsayılan olarak kapalı tut
- Eksik, bayat veya güvenilmez veride yeni paper pozisyon açma
- Aynı işlemin tekrar oluşturulmasına engel ol
- Risk sınırlarını paper modunda da uygula
- Testleri geçmeyen kodu tamamlanmış sayma
- Sentetik test verisini gerçek veri gibi sunma
- Kullanılan sentetik verileri test isimlerinde veya dokümantasyonda belirt
- Main branch’e merge yapma
- Pull request’i kullanıcı incelemesine bırak

ÖNCE İNCELENECEK DOSYA VE BÖLÜMLER

Önce bütün depoyu incele. Özellikle:

- README ve proje dokümantasyonu
- config.yaml
- radar/ paketi
- radar/team.py
- radar/director.py
- radar/policy.py
- radar/dashboard.py
- radar/templates/
- ledger, store ve execution ile ilgili modüller
- OKX istemcileri
- WebSocket kodu
- canlı döngü
- sinyal motorları
- strateji ve yönetici sınıfları
- risk hesapları
- Telegram bildirimi
- backtest araçları
- test klasörü
- GitHub Actions workflow dosyaları
- data/TEK_AJAN_ARASTIRMA.md
- data/LAB.md
- data/YARIS.md
- data/YARIS_KARAR.md
- data/BACKTEST.md
- tools/arastirma/
- tools/arastirma/features.py
- tools/arastirma/sim.py
- tools/arastirma/team_test.py
- mevcut paper işlem kayıtları ve veri şemaları

TEK_AJAN_ARASTIRMA.md dosyasındaki bulguları yalnız özetleme. Araştırmayı üreten kodla sonuçların birbirini gerçekten destekleyip desteklemediğini kontrol et.

Özellikle şunları doğrula:

- Saklı test ayrımı
- Sinyal ve execution zamanlaması
- Listeden çıkan coinlerin işlenmesi
- Sıfır hacimli zombi satırların temizlenmesi
- İşlem maliyetleri
- Funding uygulaması
- Günlük ve gün içi strateji farkları
- Kârın az sayıda işleme yoğunlaşması
- Long ve short tarafındaki asimetri
- Kemal, Selin, Kaan ve Murat sonuçları
- Polat ve kısa vadeli strateji sonuçları
- Look-ahead riski
- Survivorship bias riski
- Data leakage riski
- Aynı test verisine tekrar tekrar uyum sağlama riski

KOD DEĞİŞİKLİĞİNDEN ÖNCE

Kısa fakat somut bir uygulama planı hazırla. Daha sonra kullanıcıdan tekrar onay istemeden uygulamaya devam et.

Plan şunları içersin:

- Mevcut sistemde tespit edilen sorunlar
- Korunacak bileşenler
- Kaldırılacak veya shadow moda alınacak bileşenler
- Yeni modüller
- Veri migrasyonu
- Feature flag geçişi
- Test sırası
- Commit sırası
- PR sırası

BÜYÜK DÖNÜŞÜM STRATEJİSİ

Değişiklikleri mümkünse bağımsız ve sıralı pull requestlere böl:

PR 1:
Ortak veri modelleri, factor coverage, CostEngine ve testler

PR 2:
Strateji modülleri ve UnifiedDecisionEngine

PR 3:
RiskGate ve PaperExecutionAdapter güçlendirmesi

PR 4:
EffectivenessAuditor ve araştırma altyapısı

PR 5:
Dashboard payload ve modern arayüz

Tek bir çalışma oturumunda birden fazla PR açılamıyorsa:

- Önce PR 1’i eksiksiz tamamla
- Sonraki PR’lar için ayrıntılı bağımlılık planı oluştur
- Kullanıcı tekrar görevlendirdiğinde kaldığın yerden devam edilebilecek dokümantasyon bırak

Her PR:

- Tek başına çalışabilir olmalı
- Mevcut davranışı bozmamalı
- Testleri geçmeli
- Gerçek işlem etkinleştirmemeli
- Açık ve denetlenebilir olmalı

AŞAMA 1: ORTAK ALAN MODELLERİ

Uygun paket yapısını inceleyerek aşağıdaki veri modellerini ekle:

- MarketContext
- FactorValue
- FactorCoverage
- CandleContext
- MarketStructure
- VolumeContext
- LiquidityContext
- DerivativesContext
- RegimeContext
- DataQualityContext
- Opportunity
- CostEstimate
- DecisionResult
- RiskDecision
- ExecutionIntent
- ExecutionResult
- ExecutionEvent
- EffectivenessSnapshot

Python sürümü ve mevcut bağımlılıklar uygunsa dataclass, enum ve type hints kullan.

Modeller mümkün olduğunca immutable veya kontrollü olmalı.

Her Faktör Değeri mümkün olduğunda şunları taşımalı:

- name
- value
- source
- timestamp
- age_seconds
- timeframe
- validity
- confidence
- missing_reason
- stale
- required
- metadata

FactorCoverage şunları hesaplamalı:

- Toplam beklenen faktör
- Mevcut faktör
- Zorunlu faktörlerin durumu
- Kapsama oranı
- Bayat faktörler
- Eksik faktörler
- Geçersiz faktörler
- Paper işlem açmaya yeterli olup olmadığı

Zorunlu veri eksik, geçersiz veya bayatsa sistem:

- Gerçek paper risk kullanmamalı
- Opportunity kaydını silmemeli
- SHADOW kararı üretmeli
- Eksik faktörleri reason_codes içinde göstermeli
- Dashboard’a veri kapsamı göndermeli

AŞAMA 2: FAKTÖR MOTORU

Ortak bir FactorEngine tasarla.

Mevcut göstergeleri mümkün olduğunca yeniden kullan. Aynı hesapları farklı ajanlarda tekrar tekrar yapma.

FAKTÖR GRUPLARI

1. Mum ve fiyat hareketi

Hesaplanabilecek faktörler:

- Open
- High
- Low
- Close
- Volume
- Mum tamamlanma durumu
- Gövde büyüklüğü
- Gövde / toplam aralık
- Üst fitil
- Alt fitil
- Fitil oranları
- Kapanışın mum aralığındaki konumu
- Mum aralığı / ATR
- Önceki muma göre konum
- İç mum
- Dış mum
- Engulfing
- Doji benzeri kararsızlık
- Pin bar ve reddedilme
- Art arda sıkışan mumlar
- Genişleyen mumlar
- Başarısız kırılım
- Seviyenin geri kazanılması
- Çoklu mum devam yapısı
- Çoklu mum dönüş yapısı
- Olağan dışı sıçrama

Mum isimleri tek başına işlem nedeni olmasın.

Mumun:

- Ana trend
- Yapısal seviye
- Hacim
- Likidite
- Volatilite
- Rejim

bağlamı zorunlu değerlendirilsin.

2. Çoklu zaman dilimi

Desteklenecek zaman dilimleri:

- 1m
- 5m
- 15m
- 1H
- 4H
- 1D
- Gerekirse 1W

Her Opportunity içinde:

- Üst zaman dilimi yönü
- Karar zaman dilimi kurulumu
- Alt zaman dilimi giriş kalitesi

ayrı tutulmalı.

Tamamlanmamış mum normal swing ve gün içi stratejilerde kesin sinyal olarak kullanılmamalı.

Mikro yapı stratejisi mum kapanışından bağımsız çalışıyorsa bu durum açıkça belirtilmeli.

3. Trend ve piyasa yapısı

- EMA seviyeleri ve eğimleri
- Fiyatın EMA’lara göre konumu
- ADX
- Supertrend
- Donchian kanalları
- Yükselen tepe ve dip
- Düşen tepe ve dip
- Break of structure
- Character change
- Destek ve direnç
- Önceki gün tepe ve dip
- Önceki hafta tepe ve dip
- Önceki ay tepe ve dip
- Konsolidasyon
- Birikim
- Sıkışma
- Genişleme
- Kırılım
- Kırılım sonrası kabul
- Başarısız kırılım
- Likidite süpürmesi
- Trend yaşı
- Trend kalitesi
- Trendin aşırı uzaması
- Fiyatın denge bölgesine uzaklığı

4. Momentum ve dönüş

- RSI
- ROC
- Stochastic veya mevcut eşdeğer
- Kısa dönem momentum
- Orta dönem momentum
- Uzun dönem momentum
- Volatiliteye normalize momentum
- Divergence
- Son 1 günlük getiri
- Son 3 günlük getiri
- Son 7 günlük getiri
- Son 14 günlük getiri
- Son 28 günlük getiri
- Pompa kovalamama ölçümü
- Aşırı uzama
- Ortalama dönüş uygunluğu

5. Volatilite

- ATR
- ATR yüzdesi
- Gerçekleşen volatilite
- Volatilite yüzdeliği
- Bollinger genişliği
- Keltner genişliği
- Sıkışma
- Genişleme
- Gap ve sıçrama
- Normal dışı hareket
- Panik volatilitesi
- Trend volatilitesi
- Düzensiz yönsüz volatilite

6. Hacim

- Güncel hacim
- Ortalama hacim
- Göreli hacim
- Hacim patlaması
- Kırılım hacmi
- Geri çekilmede hacim daralması
- Hacim yükselirken fiyat ilerlememesi
- Fiyat-hacim uyumsuzluğu
- İşlem sayısı bulunuyorsa işlem yoğunluğu
- Alıcı ve satıcı hacmi bulunuyorsa taraf dağılımı

7. Likidite ve mikro yapı

Mevcut OKX veri sağlayıcıları destekliyorsa:

- Bid
- Ask
- Spread
- Spread bp
- En iyi bid derinliği
- En iyi ask derinliği
- Çok seviyeli order-book derinliği
- Order-book imbalance
- Agresif alış hacmi
- Agresif satış hacmi
- Trade imbalance
- İşlem geliş hızı
- Likiditenin geri çekilmesi
- Emilim
- Tahmini kayma
- Emir büyüklüğüne göre kapasite
- Mark, index ve last fiyat ayrışması
- Bayat order-book kontrolü
- Sequence gap kontrolü

Veri mevcut değilse sentetik veya uydurma veri oluşturma.

Mikro stratejiyi SHADOW_ONLY olarak işaretle.

8. Türev verileri

- Funding oranı
- Yıllıklandırılmış funding
- Funding değişim hızı
- Funding kalabalığı
- Funding ödeme zamanı
- Açık pozisyon
- Açık pozisyon değişimi
- Fiyat ve OI ortak durumu
- Basis
- Mark-index farkı
- Tasfiye akışı mevcutsa yoğunluk
- Long squeeze riski
- Short squeeze riski

OI veya liquidation verisi yoksa bunun FactorCoverage içinde açıkça görünmesini sağla.

9. Coin kalitesi

- Coin yaşı
- Listeleme tarihi
- Veri geçmişi
- Günlük hacim
- Ortalama hacim
- Spread kalitesi
- Order-book kapasitesi
- Delist şüphesi
- İşlem durdurma
- Sıfır hacim
- Stabil coin filtresi
- Kripto olmayan enstrüman filtresi
- A, B ve C kalite sınıfları

Mevcut coin sınıfı davranışını bozma. Ancak sınıf kararını ortak modele taşı.

10. Piyasa rejimi

Rejim sınıfları:

- BULL
- RECOVERY
- RANGE
- DETERIORATION
- BEAR
- CRISIS

Rejim girdileri:

- BTC uzun trendi
- BTC orta trendi
- BTC kısa trendi
- BTC volatilitesi
- ETH davranışı
- Piyasa genişliği
- Altcoin katılımı
- Coinin BTC’ye göre göreli gücü
- Korelasyon kümesi
- Funding dağılımı
- Likidite durumu

Rejim tek başına skor ekleyen gösterge olmasın.

Rejim:

- Strateji yetkisi
- Pozisyon boyutu
- Long/short izni
- Çıkış davranışı

üzerinde etkili olsun.

11. Veri kalitesi

Kontrol et:

- Eksik mum
- Tamamlanmamış mum
- Tekrarlanan timestamp
- Zaman sırası bozukluğu
- Anormal fiyat
- Sıfır veya negatif fiyat
- Sıfır hacim
- Bayat ticker
- Bayat order book
- WebSocket kopukluğu
- REST ile WebSocket uyuşmazlığı
- Enstrüman metadata eksikliği
- Saat senkronizasyonu
- Sequence gap
- Yetersiz geçmiş
- Kapsam oranı

Kritik veri kalitesi hatasında fail-closed çalış.

AŞAMA 3: COST ENGINE

Ortak CostEngine oluştur.

Hesaplayacağı maliyetler:

- Giriş komisyonu
- Çıkış komisyonu
- Maker veya taker
- Spread
- Giriş kayması
- Çıkış kayması
- Stop kayması
- Funding
- Beklenen tutma süresine göre funding tahmini
- Kısmi dolum etkisi
- IOC emrinde dolmayan miktar
- Emir kaçırma güvenlik tamponu
- Carry işlemlerindeki bütün bacaklar
- Spot ve swap ücret farkları

Temel formül:

expected_net_edge
=
expected_gross_edge
- entry_fee
- exit_fee
- spread_cost
- slippage_cost
- funding_cost
- execution_uncertainty_buffer

CostEstimate şunları taşımalı:

- Bütün maliyet kalemleri
- Brüt avantaj
- Net avantaj
- Güvenlik tamponu
- Hesaplama güveni
- Eksik maliyet verileri
- İşlem yapılabilirlik kararı

Net avantaj:

- Sıfır veya negatifse REJECT
- Pozitif fakat belirsizlik tamponundan küçükse SHADOW
- Yeterli ve güvenilir ise RiskGate’e aktarılmalı

Mikro ve gün içi stratejilerde swing stratejilerine göre daha sıkı net avantaj eşiği olmalı.

AŞAMA 4: STRATEJİ MODÜLLERİ

Eski ajanları bağımsız trader ve bağımsız sermaye sahibi olmaktan çıkaracak altyapıyı kur.

Ancak ilk PR’da eski sistemi silme.

Feature flag ile legacy ve unified modlarını birlikte destekle.

Yeni strateji sözleşmesi oluştur.

Her strateji:

- MarketContext almalı
- Sadece Opportunity üretmeli
- Emir gönderememeli
- Risk miktarı belirleyememeli
- Kaldıraç belirleyememeli
- Aynı veri modelini kullanmalı
- Shared factor hesaplarını tekrar etmemeli

Stratejiler:

1. SwingTrendStrategy

Kaynak:
Kemal’in trend çekirdeği ve mevcut araştırmadaki trend bulguları.

Zaman dilimi:
4H ve 1D

Özellikler:

- Ana trend
- Yapısal trend
- Donchian kırılımı
- Kontrollü geri çekilme
- Volatilite filtresi
- Funding filtresi
- Coin kalitesi
- Rejim uyumu
- Günler veya haftalar süren pozisyon

2. CompressionBreakoutStrategy

Kaynak:
Selin’in sıkışma çekirdeği.

Özellikler:

- Bollinger/Keltner sıkışması
- TTM benzeri squeeze
- Yapısal birikim
- Hacim teyidi
- Kırılım sonrası kabul
- Aşırı uzamış giriş reddi
- Başarısız kırılım kontrolü

3. ControlledBreakoutStrategy

Kaynak:
Kaan’ın kırılım çekirdeği.

Özellikler:

- 20 günlük ve yapısal seviyeler
- Kırılım öncesi sıkışma
- Hacim
- Rejim
- Funding kalabalığı
- Fiyatın kaçmış olup olmaması
- Kırılım sonrası kabul

Tek başına ana sermaye motoru olmasın. Trend veya sıkışma fırsatını güçlendiren kanıt olarak da kullanılabilsin.

4. IntradayTrendStrategy

Zaman dilimi:
15m, 1H ve 4H bağlamı

Özellikler:

- 4H ana yön
- 15m veya 1H kontrollü geri çekilme
- Geri çekilmede hacim daralması
- Yeniden hızlanma
- Yapısal stop
- Maksimum tutma süresi
- Çok sıkı maliyet filtresi

Mevcut olumsuz Deniz/Polat sistemini yeniden etkinleştirme.

Yeni stratejiyi başlangıçta SHADOW_ONLY çalıştır.

5. MicrostructureStrategy

Zaman dilimi:
1m–15m

Özellikler:

- Order-book imbalance
- Trade imbalance
- Likidite süpürmesi
- Emilim
- Sahte kırılım
- Baskı devamı
- Spread
- Derinlik
- Kayma
- Gecikme
- Kapasite

Gerekli tick ve order-book geçmişi yoksa gerçek paper risk kullanma.

Varsayılan SHADOW_ONLY.

6. PanicReversalStrategy

Kaynak:
Ayşe’nin seçici aşırılık yaklaşımı.

Basit “çok düştü, al” kuralı kullanma.

Zorunlu kanıtlar:

- Volatiliteye göre olağan dışı düşüş
- Zorunlu satış veya tasfiye sinyali
- Satış emilimi
- Yeni dip üretilememesi
- Seviyenin geri kazanılması
- Spread normalleşmesi
- BTC çöküşüyle çelişmeme
- Net avantaj

Başlangıçta SHADOW veya düşük risk.

7. ShortTrendStrategy

Long kurallarını ters çevirmekle yetinme.

Zorunlu koşullar:

- Ayı veya bozulma rejimi
- Coinin kendi aşağı trendi
- Aşağı yapısal kırılım
- Likidite
- Funding short kalabalığı kontrolü
- Short squeeze riski
- Net stop
- Long işlemlerden daha düşük risk tavanı

Naif piyasa-nötr short sepeti oluşturma.

8. CarryStrategy

Kaynak:
Elif’in funding yaklaşımı.

Yönlü işlemden ayrı muhasebe tut.

Destekle:

- Spot long + swap short
- Funding
- Basis
- İki bacaklı hedge
- Dört bacak maliyeti
- Hedge sapması
- Margin riski
- Liquidation buffer
- Funding tersine dönüşü
- Koordineli kapanış

ESKİ AJAN DÖNÜŞÜMÜ

- Kemal → SwingTrendStrategy
- Selin → CompressionBreakoutStrategy
- Kaan → ControlledBreakoutStrategy
- Ayşe → PanicReversalStrategy
- Elif → CarryStrategy
- Murat → Legacy shadow karşılaştırma
- Deniz → Devre dışı legacy ve mikro araştırma referansı
- Turbo → Bağımsız işlem açma yetkisini kaldır
- Ortak Masa → Bağımsız işlem açma yetkisini kaldır
- Baş Trader → UnifiedDecisionEngine

Eski ajan kayıtlarını veya geçmiş performans verilerini silme.

AŞAMA 5: UNIFIED DECISION ENGINE

UnifiedDecisionEngine oluştur.

Görevleri:

- Bütün strateji modüllerinden Opportunity toplamak
- Aynı coin, yön, setup ve zaman ufkundaki fırsatları birleştirmek
- Tekrarları kaldırmak
- Kanıtları ortaklaştırmak
- Aynı riskin iki defa sayılmasını engellemek
- Çelişkili fırsatları çözmek
- Risk motoruna tek karar göndermek

Kararlar:

- OPEN
- REDUCE_SIZE
- SHADOW
- REJECT
- HOLD
- EXIT
- REDUCE_POSITION

DecisionResult şunları taşımalı:

- decision
- symbol
- side
- strategy
- timeframe
- confidence
- gross_edge
- costs
- net_edge
- entry
- stop
- targets
- expected_holding
- risk_proposal
- evidence_for
- evidence_against
- missing_factors
- stale_factors
- reason_codes
- Turkish explanation
- timestamp
- idempotency key

Aynı coin ve yönde birden fazla strateji aynı fırsatı bulduğunda:

- Ayrı pozisyonlar açma
- Tek fırsat oluştur
- Kanıt yoğunluğunu kaydet
- Tek risk bütçesi kullan

Çelişkili long ve short fırsatlarda değerlendir:

- Rejim
- Net avantaj
- Veri kapsamı
- Likidite
- Stratejinin paper performansı
- Maliyet
- Mevcut portföy

Karar açıklaması Türkçe olmalı ve örneğin şunları içermeli:

- Neden işlem açıldı?
- Neden boyut küçültüldü?
- Hangi kanıtlar destekledi?
- Hangi kanıtlar karşı çıktı?
- Maliyet sonrası ne kaldı?
- Hangi veriler eksikti?
- Hangi exit planı kullanılacak?

AŞAMA 6: RISK GATE

RiskGate stratejilerden bağımsız olmalı.

Baş Trader işlem açmak istese bile son karar RiskGate’te olmalı.

Kontroller:

- İşlem başına dolar riski
- İşlem başına özsermaye yüzdesi
- Stop mesafesi
- Maksimum nominal
- Minimum ve maksimum pozisyon
- Coin riski
- Aynı yön riski
- Toplam açık risk
- Korelasyon kümesi
- Strateji yoğunlaşması
- Tek coin yoğunlaşması
- Günlük kayıp
- Zirveden düşüş
- Kayıp serisi
- Likidite
- Spread
- Tahmini kayma
- Veri kapsamı
- Veri yaşı
- WebSocket sağlığı
- Funding
- Coin yaşı
- Rejim
- Liquidation buffer
- Kullanılabilir cash ve margin

Kaldıraç:

- Strateji skoru yükseldi diye kaldıraç yükseltme
- Önce dolar riski hesapla
- Sonra stop mesafesinden pozisyon nominalini hesapla
- Sonra gerekli teminat için en düşük yeterli kaldıracı seç
- Kaldıraç, izin verilen zararı artırmasın
- Yüksek volatilite ve düşük likiditede kaldıraç düşsün
- Stop ile liquidation arasında sert güvenlik tamponu olsun

Başlangıç paper varsayımları:

- İşlem başına risk yaklaşık %0,5
- Sert toplam açık risk sınırı
- Günlük kayıp devre kesicisi
- Maksimum düşüş devre kesicisi
- Veri sorunu devre kesicisi
- Aynı emir için idempotency

Mevcut config değerlerini açıklamasız silme veya değiştirme.

Yeni ayarları config’e belgeli biçimde ekle.

AŞAMA 7: PAPER EXECUTION

ExchangeAdapter arayüzü oluştur.

İlk uygulama:

PaperExecutionAdapter

Gelecek uygulama:

OKXDemoAdapter

OKXDemoAdapter bu görevde gerçek credential veya bağlantı gerektirmeden yalnız sözleşme veya güvenli stub olarak bırakılabilir.

Paper motor desteklesin:

- Market order
- Limit order
- IOC benzeri limit
- Pending
- Accepted
- Partially filled
- Filled
- Cancelled
- Expired
- Rejected
- Unknown
- Reduce-only
- Stop
- Take-profit
- Trailing stop
- Kısmi kâr
- Başabaş
- Zaman çıkışı
- Rejim çıkışı
- Veri sorunu çıkışı
- Yeniden başlatma kurtarması
- Deterministik client order id
- Idempotency
- Event log
- Komisyon
- Funding
- Spread
- Kayma
- Kısmi dolum
- Kapasite
- Reconciliation

Şartları geçen PAPER işlemler kullanıcıdan tek tek onay istemeden otomatik açılmalı.

Paper motor:

- Pozisyonu otomatik yönetmeli
- Stop ve hedefi otomatik uygulamalı
- Çıkış kararlarını otomatik işlemeli
- Dashboard’u güncellemeli
- Telegram’a açıklama gönderebilmeli

Fakat:

- Gerçek borsa emri gönderme
- Gerçek hesap bağlantısı kurma
- API credential isteme
- Withdraw izni
- Transfer izni
- Gerçek trading modu

kesinlikle kapalı kalmalı.

AŞAMA 8: ÇIKIŞ MOTORU

Zaman ufkuna göre ayrı exit policy oluştur.

Mikro:

- Kısa zaman aşımı
- Order flow tersine dönüşü
- Spread genişlemesi
- Likidite kaybı
- Hızlı stop
- Küçük hedef

Gün içi:

- Yapısal stop
- Momentum kaybı
- Kısmi kâr
- Seans veya zaman çıkışı
- Beklenen sürede hareket başlamaması

Swing:

- Yapısal stop
- Günlük kapanış stopu seçeneği
