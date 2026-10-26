# Kripto Radar — Kararlar ve Yol Haritası

> Bu dosya kullanıcıyla birlikte alınan kararların kalıcı kaydıdır. Her yeni çalışma oturumunda **önce bu dosya okunur**.
> Son güncelleme: 26 Ekim 2026 (v8 uygulandı)

## 1. Değişmeyen temel kararlar

- **Sermaye ve veri:** Başlangıç sermayesi **1000 $**. Sadece **ücretsiz** veri kaynakları.
- **Gerçek para bağlı değil.** API anahtarı açılacağı gün: sadece işlem izni, **para çekme kapalı**, IP kilitli. Telegram token'ı yalnızca GitHub Secrets'ta.
- **Risk limitleri:** İşlem **sayısı** sınırı yok; hayatta kalma limitleri var. Firma limitleri: günlük kayıp **%3**, zirveden düşüş **%15** (/devam), açık risk %10, coin başı %2,5, yön başı %6.
- **Borsa:** Demo **OKX global** kurallarıyla çalışır. Gerçek paraya geçişte kullanıcının hesap tipi (OKX TR / global, vadeli açık mı) kontrol edilecek.
- **Çalışma kuralı:** Kullanıcı **"başla" demeden programda değişiklik yapılmaz**; önce konuşulur. ("Konuşalım" planları iptal etmek demek değildir.)
- **Kanıt kuralı:** Her strateji değişikliği önce **5 yıllık veride, sert maliyet modeliyle, ileriye dönük (walk-forward) testten** geçer; sonra canlıya alınır.
- **Gölge kuralı:** Testi geçemeyen ajan kovulmaz; sermayesiz (gölge) işlem yapar, kendini kanıtlarsa geri döner.
- **Hedef:** Sistem para kazanmalı. Kazanç garanti edilemez; amaç masraflardan sonra pozitif beklentiyi kanıtlamak, kaybettireni erken kesmek.

## 2. Devreye alınmış olanlar (26 Ekim 2026)

- **Kesintisiz çalışma:** 7/24 nöbet devri (`kalp` dalı), devir kesintisi ~5 sn. İlk iki gerçek devir sorunsuz.
- **Devre kesici:** Ekip modunda firma limitlerini kullanır (%3 / %15). Eski %2 ile konmuş durdurma bir kez düzeltilir.
- **Kendi kendini onarma:** Planlı halef çağrısı yeniden denenir. Hatayla biten ya da takılan döngü yedeğini çağırır. İş akışında "Çökme kurtarma" adımı var.
- **Veri denetçisi:** Canlı fiyat sapma eşiği oynaklığa göre ayarlanır (en az %8, 5 ATR).
- **OKX funding aboneliği:** Yalnızca SWAP kontratlarında.

## 2b. v8 uygulandı (26 Ekim 2026) — 5 yıllık test sonuçları

Test: Ekim 2021 → Ekim 2026, OKX'in en likit 28 coini, günlük mumlar (Deniz: BTC/ETH 15dk, son 1 yıl).
Sert maliyet: taker %0,10, maker %0,04, girişte 2 bp, stop'ta 5 bp kayma, long funding yıllık ~%11.
Parametre optimizasyonu yok. Ayrıntı: `data/BACKTEST.md`, `data/backtest_v8.json`.

| Yönetici | İşlem | Ort. R | Örneklem dışı | Long / Short | Yıllık | Maks. düşüş | Karar |
|---|---|---|---|---|---|---|---|
| Kaan (günlük kırılım) | 574 | +0,17 | +0,19 | +0,28 / +0,01 | %20,0 | −%25,6 | sermaye |
| Selin (sıkışma patlaması) | 396 | +0,13 | +0,13 | +0,20 / +0,04 | %15,5 | −%23,4 | sermaye |
| Kemal (günlük trend) | 918 | +0,05 | +0,08 | +0,02 / +0,08 | %7,0 | −%33,7 | sermaye (zayıf) |
| Murat (geri çekilme) | 940 | −0,05 | −0,02 | −0,13 / +0,02 | −%3,7 | −%47,8 | gölge |
| Ayşe (kapitülasyon) | 39 | −0,04 | +0,08 | −0,22 / +0,33 | −%0,2 | −%11,6 | gölge |
| Deniz (15dk maker) | 233 | −0,28 | −0,28 | −0,35 / −0,21 | −%52 | −%52 | gölge |

Firma (eşit pay, firma limitleri): 6 yönetici %4,8 yıllık / −%22,4 düşüş; **politika (Kemal+Kaan+Selin) %12,0 / −%12,8**;
politika + hava + yığılma katmanları %10,9 / −%11,6 (Sharpe 0,72).

**Dürüst not:** Deflated Sharpe ≈ 0. 16 deneme hesaba katılınca hiçbir sonuç "şans değil" diye kanıtlanmış sayılmaz.
Kaan ve Selin long tarafının işlem başı t-istatistiği güçlü (3,4 / 2,65), ama kesin kanıt canlı sonuçla gelecek.
Short sinyalleri araştırmadaki gibi sıfıra yakın çıktı.

Testte bulunup düzeltilen hatalar:
- "Yatay piyasada kırılımcıyı yarıya indir" kuralı dayanaksızdı, Kaan'ı %20'den %11,5'e düşürdü → kaldırıldı.
- Firma simülasyonunda risk yanlışlıkla firma sermayesine göre hesaplanıyordu → yönetici payına göre düzeltildi.
- Geniş günlük stoplarda sabit 5x kaldıraç likidasyonu stop'tan önceye getiriyordu → kaldıraç her işlemde
  likidasyon stop'un 1,5 katı ötede kalacak şekilde seçiliyor.
- Gölgedeki yönetici sermayesiz kalınca gölge işlem de açamıyordu → sanal sermaye.

Uygulanan politika (config `ekip.politika`): Kemal, Kaan, Selin eşit pay + Elif %15 sabit. Deniz, Murat (kendi işlemleri),
Ayşe gölgede; 10 gölge işlemde ort. +0,1R yapan geri döner. Murat'ın baş analist karnesi her fikre uygulanıyor.

## 2c. v8.1 – v8.2 (26 Ekim 2026 akşamı, kullanıcı kararları)

- **Kesintisiz karar (v8.1):** Günlük yöneticiler gece yarısını beklemez. Her dakika "kapanmış günler + bugünün canlı mumu"
  (canlı fiyat, kayan 24 saatlik hacim) ile değerlendirilir; kırılım/trend oluştuğu an işlem açılır, kural çıkışları da anlıktır.
  5 yıllık test (4 saatlik adımla, canlıdan yavaş = kötümser): kesintisiz + Kaan/Selin sadece long → **yıllık %14,4, maks. düşüş
  −%14,2, Sharpe 0,96**. Gün sonu modu %11,5 / −%11,6 / 0,76 idi. (`data/BACKTEST_KESINTISIZ.md`)
- **Kaan ve Selin short'ları gölgede:** Kesintisiz testte Kaan long +0,31R / short −0,05R, Selin long +0,18R / short ±0R.
  Öğrenme 30 gölge işlemde kanıt görürse gerçeğe alır.
- **Gölge işlemler ayrı sekmede:** İşlemler sayfası, /pozisyonlar ve Telegram yalnızca gerçek işlemleri gösterir.
- **/devam evet:** Kullanıcı onayıyla günde 1 kez %1,5 ek günlük kayıp hakkı (durdurmalar açıkken geçerli).
- **v8.2 bütçe:** Her yöneticiye ayrı **10.000 $** demo bütçe (8 masa, toplam 80.000 $). 1.000 $'lık ilk dönem arşivde.
- **Gerçek API'ye kadar kapalı:** Günlük kayıp/zirveden düşüş durdurması, firma risk/coin/yön/yığılma/nominal sınırları,
  yedek kulübesi. Gerçek paraya geçişte config `ekip.firma` içinde `durdurma_aktif`, `sinirlar_aktif` ve `yedek_kulubesi`
  **true** yapılacak. Her zaman geçerli olanlar: her işlemin stop'u, riske göre boyut, "olmayan parayla işlem yok".
- **Kaldıraç yetkisi:** Her yönetici kaldıracını kendi seçer: güven × tavan (normal 10x, agresif ve Deniz 20x, Ortak Masa 10x,
  Elif 1x), OKX azami kaldıracı ve likidasyon güvenliği (likidasyon stop'un en az 1,5 katı ötede). Kaldıraç riski
  değiştirmez (risk = stop × miktar), teminatı küçültür.
- **🤝 Ortak Masa:** En az 2 yönetici aynı coinde aynı yönde pozisyondaysa ayrı bütçeyle ortak işlem açar.
  Stop, onların stop mesafelerinin ortancasıdır; 2R'de %30 alır, kalanı iz süren stopla taşır; uzlaşı dağılınca çıkar.
  Yöneticilerin kendi işlemleri etkilenmez.
- **İşlem açıklaması:** Panelde işleme tıklayınca canlı açıklama açılır: ne oldu, neden açtı, para dökümü (brüt, komisyon,
  funding, net, risk, MFE/MAE), kaldıraç kararı, sırada ne var. Kapanan işlemde ajanın dersi de yazar.
- **Öğrenme (`radar/learn.py`):**
  1. Her kapanışta ders yazılır.
  2. İşlem türü (yönetici|yön) canlı kanıta göre gerçek/gölge arasında taşınır (30 işlem; +0,10R ve PF 1,2 / −0,10R ya da PF < 0,8).
  3. Haftalık ayar öğrenmesi (pazar, `Öğrenme` iş akışı): stop ve iz stop katsayısı {0,8 / 1,0 / 1,25} ızgarasında, 5 yılın
     ilk %70'inde eğitilir. Sadece son %30'da (görülmemiş) da +0,02R iyileşirse benimsenir. Sınırlar 0,7–1,5.
     Canlı motor `data/learned.json`'u saatte bir okur.
- **Düzeltilen hata:** Kısmi kâr alma kontratı tam sayıya yuvarlıyordu; 0,01/0,1 lotlu küçük pozisyonlarda kısmi kâr hiç
  alınmıyordu. Artık OKX lot büyüklüğüne göre yuvarlıyor.

## 2d. Bekleyen kararlar — 26 Ekim 2026 konuşması (ONAY BEKLİYOR, uygulanmadı)

Kullanıcı kuralı: "başla" denmeden hiçbiri uygulanmaz.

**Ana hedef:** Parayı katlamak = batmadan en hızlı bileşik büyüme. Kaldıraç abartılmaz; aşırı risk bileşik büyümeyi öldürür.

1. **Kârı koştur, kazananı kesme.** Trend ajanlarında (Kemal, Kaan, Selin) erken kâr alma büyük kazancı kesiyor.
   Tepki ajanları (Ayşe, Murat, Deniz) kısa hareket avladığı için hedefte çıkmak doğru olabilir. Tek kural yok, her ajana test karar verir.
   - Test: Kaan, Selin, Murat, Ortak için 5 yılda üç çıkış yarışı → (a) şimdiki kısmi kâr + iz stop, (b) kısmi kâr yok, sadece iz stop, (c) hedefte tam çıkış.
   - Kazanan çıkış, öğrenme modülünün ayarlayabildiği bir parametre olur.
2. **Öğrenmenin ölçüsü değişir:** "işlem başına ortalama R" yerine "bileşik büyüme + sınırlı düşüş".
3. **Ortak Masa güven kuralı:**
   - Oylar ajanın kanıtlanmış karnesine göre ağırlıklı: kanıtlanmış = tam oy, kanıtsız/eksi = yarım oy, gölgedeki segment = 0.
   - En az iki *farklı tarzdan* onay (iki trendcinin anlaşması yetmez).
   - Ağırlıklı oy eşiği; masa kötü giderse eşik otomatik yükselir.
4. **Ortak Masa %5 katkı modeli:** Anlaşan her ajan kendi bütçesinin %5'ini koyar (10k → 500$). Kâr/zarar katkı payına göre ajanlara döner ve güven puanını besler.
   Stop genişse kaldıraç düşürülür, risk sınırı aşılmaz. Kurallar dolar değil yüzde olarak yazılır, 100$ gerçek hesaba taşınabilsin diye.
5. **Kâr artırma önceliği (sıra: a → b → d → c):**
   - a. Testte eksi çıkan ajanlar (Murat, Ayşe, Deniz) yeniden tasarlanıp kanıtlayana kadar gölgede; bütçe kazananlara.
   - b. Meta-labeling: her sinyale "benzer koşulda kaç kez tuttu" güven yüzdesi; yüksek → büyük, orta → küçük, düşük → girme.
   - c. Göreli güç: BTC'den güçlü giden ilk ~5 coinin kırılımları.
   - d. Kalabalık verisi (OKX ücretsiz): açık pozisyon değişimi, fonlama, long/short oranı, likidasyon akışı.
   - e. Kanıta göre boyut: çeyrek Kelly, segment istatistiklerinden.
   - f. Zarar otopsisi: her zarara sebep etiketi (sahte kırılım, BTC çekti, haber, dar stop).
   - Hepsi önce 5 yıl + walk-forward testten geçer; geçmeyen canlıya girmez.
6. **Gerçek API (sonra):** Kullanıcı ~100$ ile başlamayı düşünüyor. OKX asgari işlem miktarları yüzünden 8 ajan ayrı ayrı çalışamaz; kanıtlanmış 1–2 ajan + Ortak Masa ile başlanır. Ciddiye binince ayrıca konuşulacak.
7. **Öğrenme asla yavaşlamaz (kullanıcı ilkesi):** "Öğrenme" ile "para koyma" ayrılır.
   Her sinyal, filtreden elense bile gölgede tam takip edilir ve ders/segment istatistiğine eklenir. Filtreler ve güven kuralları sadece gerçek bütçeyi etkiler.
   Gölgeye alınan ajanlar (ör. Murat) işlem açmaya devam eder; sadece parası risk altında değildir.
8. **Dış işlem verisi (öğrenme kaynağı):**
   - Önce kendi 5 yıllık test işlemlerimiz → "bu sinyal tutar mı" (meta-labeling) eğitimi.
   - Sonra Hyperliquid herkese açık trader verisi (işlem, kâr/zarar, kaldıraç, likidasyon) → hata dersleri + "akıllı para / kalabalık nerede" sinyali.
   - OKX kopya trade liderleri (public API olup olmadığı doğrulanacak).
   - Uyarılar: lider tablolarında hayatta kalan yanılgısı var; kopyalamak değil, ders ve sinyal çıkarmak amaç.
   - **Hız kuralı (kullanıcı):** Hiçbiri canlı ajanların hızını etkilemez. Veri çekme ve eğitim ayrı workflow'da yapılır; canlı döngü sadece hazır özet dosyayı okur. Kaynak çökerse ajanlar beklemez, son veriyle devam eder.

## 3. Araştırma özeti (kaynaklar aşağıda)

- **Aşırı uydurma:** Geçmiş testteki Sharpe canlıyı yalnızca **%1–2** açıklıyor; oynaklık (R² 0,67) ve maks. düşüş (0,34) tekrarlanıyor (Quantopian, 888 strateji). Çok denemeyle seçilen strateji canlıda çöker (Bailey ve López de Prado). → Az ayar, deneme sayısına göre indirimli Sharpe, sermaye riske göre dağıtılır.
- **Günlük trend takibi:** 5 yıllık veride de ayakta.
  - BTC'de 50 günlük ortalama: Sharpe 1,9, elde tutmak 1,3 (Grayscale, 2012–2023).
  - 10–50 günlük zirve kırılımı 2022–2024 canlı testinde çalıştı; "dipte al" çöktü (Quantpedia).
  - **Gün içi trend kârsız.**
- **Kesitsel momentum:** Masraf öncesi haftada +%0,57, gerçekçi maliyet %0,40/hafta, **net sıfır** (Binance vadeli, 2020–2026). → Ayrı momentum ajanı **eklenmiyor**.
- **Funding geliri:**
  - BTC yıllık ortalamaları: 2020 %18,8 · 2021 %35,9 · 2022 %4,3 · 2023 %8,2 · 2024 %12,7 · 2025 %5,3.
  - Bireysel maliyetle Sharpe 1,8, fırsat yılda ~%11 daralıyor.
  - → Sadece likit coinlerde ve funding yüksekken.
- **Kırılım:** Kanıt günlük grafikte ve long tarafta; short sinyalleri tarihsel olarak zayıf.
- **Fibonacci:** Rastgele seviyelerden istatistiksel olarak farksız.
- **Sosyal medya ve forum:** Reddit duygusu fiyatı **tahmin etmiyor**, fiyat duyguyu belirliyor.
  - Korku ve Açgözlülük Endeksi'nin katkısı küçük.
  - Listeleme kazancı duyuru öncesinde gerçekleşiyor ve 1–2 haftada geri veriliyor.
  - X verisi ücretli, kullanılmıyor.
- **Piyasa havasına göre strateji değiştirme:** Hisselerde canlı testte Sharpe 0,46'dan 2,0'a çıktı (kısa dönem, kripto değil). → Basit kural ile test edilecek.

## 4. Programa eklenecekler

1. **Test altyapısı:** 5 yıllık OKX verisi, walk-forward, deneme sayısını hesaba katan ölçüm (Deflated Sharpe), survivorship kontrolü.
2. **Sert maliyet modeli:**
   - Komisyon 2 kat: taker %0,10, maker %0,04.
   - Stop'ta 5 bp, girişte 2 bp ek kayma.
   - Gerçek hesap oranı öğrenilince ona göre güncellenir.
3. **Limit emirle giriş:** Acil olmayan girişlerde maker emir.
4. **Piyasa havası:** BTC'nin 50 günlük ortalamaya göre konumu ve oynaklık rejimi. Hangi tarzın öne çıkacağını ve risk büyüklüğünü belirler.
5. **Yığılma kontrolü:** Aynı coin, aynı yön ve BTC ile korelasyon. Aynı bahsi oynayan pozisyonların toplam riski sınırlanır.
6. **Olay takvimi:** FOMC ve TÜFE saatlerinde yeni işlem yok; hafta sonu düşük likiditede temkin.
7. **Haber Masası (yeni ajan):**
   - **Kaynaklar (ücretsiz):** Borsa listeleme ve çıkarma duyuruları (OKX, Binance, Bybit), haber RSS akışları, Korku ve Açgözlülük Endeksi, Reddit ilgi patlamaları, DefiLlama hack verisi.
   - **Kırmızı alarm** (hack, açık, borsadan çıkarma, düzenleyici darbe): o coinde yeni işlem yok, açık pozisyon gözden geçirilir.
   - **Sarı uyarı** (listeleme, ilgi patlaması): yükselişin peşine düşülmez.
   - Kural tabanlı sınıflandırma. Al-sat sinyali **vermez**; sigorta ve risk sensörüdür.

## 5. Ajanlarda değişecekler

| Ajan | Şimdi | Yeni hali |
|---|---|---|
| **Murat** | Fibonacci | **Baş analist:** trend (çoklu zaman dilimi), fiyat bölgeleri (hacim profili; Fibonacci bunun parçası), vadeli piyasa (funding, açık pozisyon, likidasyon, alıcı/satıcı baskısı), likidite, BTC bağımlılığı, Haber Masası uyarıları, arz tarafı (kilit açılımları; ücretsiz kaynak doğrulanacak). Herkese **coin karnesi** verir. Az sayıda ve tek tek test edilmiş ölçü kullanır. Kendi işlemini sadece bağımsız açılar aynı yönü gösterince açar. |
| **Kemal** | 4H trend | Günlük grafikte kanıtlı trend kuralları (50 günlük ortalama / 20–100 kesişimi). Oynaklığa göre pozisyon. Ekibin çekirdeği. |
| **Kaan, Selin** | 15m / 1H kırılım | Günlük 10–50 günlük zirve kırılımı. Ağırlıklı long; short sadece BTC düşüş trendinde. Agresif risk payı korunur. Listeleme şişkinliğine girmez. |
| **Elif** | Funding carry (sığ coinlerde bile) | Sadece likit coinler. Funding masraf ve risk eşiğini aşınca açılır. Diğer borsaların funding verisiyle en iyi fırsatı seçer. |
| **Deniz** | 15m piyasa emri | BTC/ETH'de limit emirle likidite sağlayan, sadece yoğun saatlerde çalışan yapı. |
| **Ayşe** | Dipte al / funding aşırılığı | Sadece gerçek aşırılıklarda (funding + açık pozisyon şişmesi + zincirleme likidasyon birlikte), küçük pozisyonla. |
| **Haber Masası** | Yok | Yeni ajan (madde 4.7). |

## 6. Uygulama sırası (v8'de 1–4 tamamlandı)

1. Test altyapısı + sert maliyet modeli (madde 4.1–4.2)
2. Ajanların yeni yöntemleri ve tek tek testleri
3. Limit giriş, piyasa havası, yığılma kontrolü, olay takvimi (madde 4.3–4.6)
4. Haber Masası (madde 4.7)

## 7. Sonraya kalanlar

- **Ayar güncellemesi:** Yeterli işlem birikince (ajan başına 30–100) ajanların kendi verisinden stop ve hedef ayarı.
- **Gerçek paraya geçiş:** Hesap tipi kontrolü, gerçek emir modülü, borsa ile defter eşleştirme, acil durdurma, küçük parayla pilot dönemi.

## 8. Açık sorular

- Kullanıcının OKX hesabı: global mi, TR mi? Vadeli açık mı? Ücret seviyesi ne?

## 9. v9 "Fabrika" planı — her yönetici bir fon yöneticisi + 5 kişilik masası (ONAY BEKLİYOR)

Kullanıcı isteği (26 Ekim 20:49): planların hepsi gerçek araştırmayla taransın; her ana ajanın altında 5 uzman ajan olsun; ana ajanlar Buffett / borsa balinası gibi donanımlı olsun; hiçbir şey indirgenmesin.

### 9.1 İlkeler (Buffett + balina → bizim kurallara çeviri)
1. **Önce batma (Buffett kural 1):** Kasayı koru; tek coin, tek ajan, tek olay firmayı yakamaz.
2. **Yetkinlik çemberi:** Kanıtımız olmayan coinde büyük oynanmaz. Coin kanıt sınıfı:
   - **A** = 5 yıllık testteki oturmuş coinler, tam boyut.
   - **B** = en az 1 yıllık geçmiş, testle doğrulanana kadar yarım boyut.
   - **C** = 180 günden yeni listeleme, sadece gölge ya da en küçük boyut.
   - Kanıt: Messari, 2025'te Binance'te listelenen 92 coini eşit ağırlıkla izledi; Mart 2026'da portföy ortalama %71 eksideydi. BitMEX'in 2025 Ç1 raporuna göre OKX'teki vadeli listelemelerin %71'i zirvesini ilk gün gördü; listeleme sonrası medyan zirve kazancı %0.
3. **Güvenlik marjı:** Beklenen kazanç toplam maliyetin (komisyon + kayma + fonlama) en az 3 katı değilse girilmez.
4. **En iyi fikre yoğunlaş:** Boyut = çeyrek Kelly × Analist güven yüzdesi. Kanıtlanan büyür, kötüleşen küçülür.
5. **Sabır:** İşlem sayısı hedef değildir (Kaan modeli). Ama öğrenme yavaşlamaz; her sinyal gölgede izlenir (madde 2d-7).
6. **Balina gözü:** Likidite ve kalabalık nerede? Açık pozisyon (OI), fonlama, long/short oranı, likidasyon kümeleri, emir defteri derinliği, akıllı para cüzdanları.
7. **Kârı koştur, zararı kes:** Çıkışı her ajan için test seçer (madde 2d-1).

### 9.2 Firma yapısı
- **Yatırım Komitesi (firma geneli, Faz 0):**
  - Coin başına tek defter. Bir coinde bir ajan varken diğerleri ters yönde giremez (şu an ENA ve ASTER'de Kemal long / Murat short; OKX tek hesapta bunları birbirinden düşer).
  - Coin başına toplam risk tavanı.
  - Ortak Masa kuralları: güven ağırlıklı oy, en az iki farklı tarzdan onay, %5 katkı. Ajanlar zaten girmişse masa yeni pozisyon açmaz, onların payını büyütür.
- **Araştırma Departmanı (canlı döngü dışında, ayrı workflow'lar):** 5 yıllık testler, Analist modelinin eğitimi, dış veri toplama (Hyperliquid, OKX), haftalık öğrenme. Canlı döngü sadece hazır özet dosyalarını okur (hız kuralı, 2d-8).
- **8 yönetici × 5 uzman = 40 uzman.** Uzmanlar ortak araçları paylaşır (aynı Bloomberg terminalini kullanan analistler gibi). Her uzmanın ayarı, hafızası ve karnesi kendi yöneticisine özeldir.

### 9.3 Her yöneticinin 5 uzmanı (roller)
1. **Gözcü:** Yöneticinin kurulumunu bütün coinlerde her 60 sn'de tarar. Göreli güç sıralaması tutar (BTC'ye göre en güçlü/zayıf coinler). Coin kanıt sınıfını bilir.
2. **Balina Gözü (piyasa yapısı):** OI değişimi (fiyat↑ + OI↑ = sağlam, fiyat↑ + OI↓ = short sıkışması ya da zayıf), fonlama uç değerleri, long/short kalabalığı, likidasyon dalgaları, emir defteri derinliği ve dengesizliği, Hyperliquid akıllı para yönü.
3. **Analist-Hakem ("tutar mı?" + otopsi):**
   - Meta-labeling ile her sinyale güven yüzdesi verir: benzer koşullarda geçmişte kaç kez tuttu.
   - Etiket tipi üçlü bariyer (hedef / stop / süre).
   - İşlem kapanınca otopsi yapar ve sebep etiketi koyar: sahte kırılım, BTC çekti, haber, dar stop, kalabalık tuzağı.
   - Model haftalık yeniden eğitilir.
4. **Risk Sorumlusu:** Stop, boyut (çeyrek Kelly × güven), kaldıraç (likidasyon her zaman stop'un ötesinde), coin/küme/firma riski, ters pozisyon kontrolü, olay takvimi, haber bayrakları.
5. **Trader (icra + pozisyon yönetimi):** Maker/taker kararı, kayma, giriş zamanlaması. Açık pozisyonu yönetir: testin seçtiği çıkış, iz stop, kârı koşturma. Pozisyon büyütme (pyramiding) yalnızca testte kanıtlanırsa.

### 9.4 Yöneticiye özel uzmanlık (her rolün o yöneticideki işi)
| Yönetici | Gözcü | Balina Gözü | Analist-Hakem | Risk | Trader |
|---|---|---|---|---|---|
| Kemal (trend) | Göreli güç ilk %20, A/B sınıfı | OI trendi teyit ediyor mu | Trend yaşı / aşırı uzama güveni | Çeyrek Kelly, coin tavanı | Kârı koştur, iz stop (öğrenilmiş 0.8 / 1.25) |
| Kaan (kırılım) | 20 gün kırılımı, en güçlü 5 coin | Kırılımda OI↑ + fonlama aşırı değil | Sahte kırılım olasılığı | Kalabalık aşırıysa yarım boyut | Kısmi kâr kalkıyor mu: çıkış yarışı |
| Selin (sıkışma) | Bant daralması + rejim | Sıkışmada OI birikimi (patlama yakıtı) | Kırılım yönü güveni | Oynaklık rejimine göre boyut | Çıkış yarışı sonucu |
| Murat (baş analist) | HVN / hacim profili geri çekilmeleri | Destek bölgesinde likidasyon kümeleri | İkinci görüş: diğer ajanların sinyallerine itiraz ya da onay | Gölgede, kanıtlayana kadar | Yeniden tasarım adayı (Faz 6) |
| Ayşe (kapitülasyon) | Sert düşüş + hacim patlaması | Gerçek kapitülasyon = likidasyon dalgası + fonlama eksi + OI çöküşü | Sekme olasılığı | Gölgede | Hedefte çık (tepki kısa sürer) |
| Deniz (15m maker) | BTC/ETH kısa vade | Emir defteri dengesizliği, fon akışı | Mikro güven | Gölgede | Sadece maker; kuyruk sırası gerçekçi |
| Elif (carry) | Yalnız A sınıfı likit coinler | Fonlama tahmini, spot-vadeli farkı (basis) | Fonlamanın süreceği güveni | Düşük kaldıraç, delta nötr | Fonlama dönmeden çık |
| Ortak Masa | Ajan anlaşmaları | Kalabalık ters mi | Güven ağırlıklı oy | %5 katkı, coin tavanı | Ajanlar içerdeyse pay büyütür |

### 9.5 Veriler (hepsi ücretsiz)
- **OKX:** OI, fonlama, long/short oranı, likidasyon akışı (public API ve WebSocket). Herkese açık kopya trade lider sıralaması ve pozisyonları.
- **Hyperliquid:** Herkese açık info API'den cüzdan işlemleri (userFills, cüzdan başına son 2.000 işlem), pozisyonlar ve likidasyonlar. Lider tablosu kaynağı doğrulanacak. GitHub Actions'ta internet açık.
- **Önemli:** OI ve long/short geçmişi borsalarda kısa tutuluyor (çoğu yerde günler ya da haftalar). Bu yüzden toplayıcı workflow **en başta** kurulmalı ki geçmiş birikmeye başlasın.
- **Kendi 5 yıllık testimiz:** Binlerce simüle işlem ve her birinin giriş koşulları. Analist-Hakem'in ana eğitim verisi.

### 9.6 Fazlar (her faz kendi testini geçmeden canlıya girmez)
- **Faz 0 — Güvenlik:** Coin başına defter, ters pozisyon yasağı, coin risk tavanı, coin kanıt sınıfı; Murat, Ayşe ve Deniz gölgeye. Aynı anda veri toplayıcı workflow başlar.
- **Faz 1 — Çıkış yarışı + ölçü değişimi:** 2d-1 ve 2d-2.
- **Faz 2 — Balina Gözü:** OKX verileri canlıda boyut ayarı olarak kullanılır (gir/girme kapısı değil). Testi, toplanan veri biriktikçe yapılır.
- **Faz 3 — Analist-Hakem:** 5 yıllık test işlemleriyle meta-labeling; walk-forward. Doğrulama döneminde bileşik büyümeyi artırırsa kabul edilir. Otopsi etiketleri de bu fazda gelir.
- **Faz 4 — Yatırım Komitesi:** Ortak Masa güven kuralı ve %5 katkı.
- **Faz 5 — Dış trader verisi:** Hyperliquid akıllı para ve OKX liderleri; ders ve kalabalık sinyali.
- **Faz 6 — Yeniden tasarım:** Murat, Ayşe ve Deniz yeni verilerle yeniden kurulur; kanıtlarlarsa gerçek bütçeye döner.

### 9.8 Uzman ajan anayasası (kullanıcı: "tek göreve indirgenmesin")
Her uzman tam bir ajandır. Görevi tek bir kontrol değil; şu beş özelliğin hepsine sahiptir:
1. **Birden çok fonksiyon:** Kendi alanındaki bütün araçları kullanır (örnek aşağıda).
2. **Kendi görüşü:** Her sinyale yön, güç ve gerekçe içeren bir görüş yazar; yöneticiye "gir / küçült / büyüt / bekle / çık" önerisi verir. Açık pozisyonda da sürekli görüş günceller (örneğin "kalabalık döndü, iz stop'u sıkılaştır").
3. **Kendi hafızası ve karnesi:** Her görüşü kaydedilir ve sonuçla karşılaştırılır. "Bu uzman haklı çıktığında kâr ne kadar arttı?" ölçülür.
4. **Kendi öğrenmesi:** Haftalık eğitimde kendi ayarlarını ve kurallarını günceller (walk-forward, doğrulama şartı).
5. **Etki ağırlığı:** Yöneticinin kararındaki ağırlığı karnesine göre artar ya da azalır. Kâra katkısı kanıtlanan uzmanın sözü büyür. Katkı sağlamayan uzmanın ağırlığı sıfıra iner ve yeniden tasarım listesine girer.

Yönetici kararı = uzman görüşlerinin karne ağırlıklı birleşimi + yöneticinin kendi kurulumu → daha tutarlı ve kâr odaklı.

**Örnek — Kaan'ın Balina Gözü'nün fonksiyonları**
- Kırılım anında OI değişimi: yeni para mı giriyor, yoksa short sıkışması mı?
- Fonlama uç değeri ve yönü: kalabalık zaten long'a yığılmış mı?
- Long/short oranı ve büyük hesapların oranı (OKX top trader).
- Likidasyon haritası: yukarıda kümelenmiş short likidasyonları var mı (yakıt), aşağıda long likidasyon tuzağı var mı?
- Emir defteri: kırılım seviyesinin üstünde satış duvarı var mı, alış tarafı derin mi?
- Hyperliquid akıllı para: kârlı cüzdanlar bu coinde hangi yönde?
- Açık pozisyonda: OI düşüp fonlama aşırılaşırsa "kârı koru" uyarısı verir.

Diğer 39 uzman da bu formatta yazılacak: fonksiyon listesi + görüş formatı + karne ölçüsü + öğrenme ayarları. Uygulama Faz 2–4 içinde yapılacak.

### 9.9 Öğrenme hızı (kullanıcı: "haftalık test aşaması için uzun")
Üç katmanlı öğrenme:
- **Anlık (her kapanan işlemde, gerçek ve gölge):**
  - Ders yazılır.
  - Uzman karneleri ve etki ağırlıkları küçük adımlarla güncellenir (Bayes büzülmesi: tek işlem ağırlığı az oynatır, çok işlem çok).
  - Segment istatistikleri güncellenir.
- **Günlük (her gece, ayrı workflow):**
  - Analist-Hakem modeli 5 yıllık test işlemleri + birikmiş canlı ve gölge işlemlerle yeniden eğitilir.
  - Doğrulamada daha iyiyse benimsenir, değilse eski model kalır.
- **Haftalık:** Yapısal ayarlar (stop, iz stop, çıkış türü) değişir; daha fazla veri ister, sık değişirse gürültü kovalanır.

Not: Öğrenmenin hızını takvim değil veri belirler. Ayarları az veriyle her gün değiştirmek şansı öğrenmek olur; bu yüzden her değişiklik doğrulama şartına bağlı.

### 9.10 Ek notlar (26 Ekim 21:00)
- **Döngü süresi sayacı:** Her 60 sn'lik döngünün süresi ölçülür; 10 sn'yi aşarsa Telegram uyarısı gönderilir ve hangi uzmanın yavaşlattığı gösterilir (hız kuralı).
- **Canlıda görülen hatalar (Faz 0'ın gerekçesi, 26 Ekim 20:58 verisi):**
  - Ters pozisyonlar: ENA (Kemal long / Murat short), ASTER (Kemal long / Murat short), RLS (Kemal long / Ayşe short).
  - Aynı coine yığılma: LIGHT (Kemal, Selin, Ortak), CAP (Kemal, Selin, Ortak), GRASS ve BABY (Kemal, Ortak).
  - Ortak Masa yeni ve hype'lı coinlere 150–200$ riskle girdi (LIGHT −16.6$).
  - Elif tarz dışına çıktı: PONS'ta short (küçük coin).
  - Eski sürüm işlemleri eski hedeflerle duruyor (ör. INJ tp1 2.87R); riskleri 3–6$, yani 10 bin dolarlık bütçeden önce açılmışlar.
  - Kemal birkaç saatte 13 pozisyon açtı (birikmiş fırsatlar tek seferde açıldı).

### 9.11 Genel Müdür (kullanıcı önerisi, 26 Ekim 21:02)
Model: çok stratejili fon ("pod" yapısı). Yöneticiler ayrı departmanlar olarak kalır, çünkü farklı tarzlar birbirinden bağımsız kazanç akışı = çeşitlendirme. Hepsinin üstünde tek defter ve tek beyin olarak **Genel Müdür** bulunur. Yatırım Komitesi (9.2) Genel Müdür'ün masası olur.

Genel Müdür'ün 5 uzmanı (anayasa 9.8 geçerli):
1. **Portföy Uzmanı:** Firmanın toplam resmini tutar: coin başına net pozisyon, toplam long/short dengesi, toplam kaldıraç, BTC'ye bağımlılık.
2. **Çatışma Hakemi:** Aynı coinde iki ajan ters düşerse kimin gireceğine karar verir: o coinde ve o rejimde karnesi daha iyi olan girer. Aynı yöne yığılmada coin risk tavanını yöneticiler arasında paylaştırır.
3. **Sermaye Dağıtıcı:** Bütçeyi karneye göre yöneticiler arasında kaydırır (çeyrek Kelly, en az 30 işlem kanıtı); kanıtlanmayanı gölgede tutar.
4. **Rejim/Makro Uzmanı:** BTC havası, oynaklık rejimi, olay takvimi, haber bayrakları. Firma genelinde risk iştahını ayarlar: hücum, normal ya da savunma.
5. **Denetçi:** Disiplin kontrolü yapar: tarz dışı işlem (ör. Elif PONS), kural ihlali, eski sürüm işlemleri, döngü hızı. Haftalık müdür raporu yazar (panel + Telegram).

Karar zinciri: Yönetici (kendi 5 uzmanıyla) öneri hazırlar → Genel Müdür onaylar, küçültür ya da reddeder. Reddedilen öneri gölgede izlenir (öğrenme yavaşlamaz). Müdürün kendi kararları da karneye girer: "reddettiklerim kazansaydı ne olurdu?"

Gerçek API: kullanıcı kararı — uygulama tam hazır olunca; acele yok.

### 9.12 Kullanıcının ajan planı → nihai uygulama ve test listesi (26 Ekim 21:16)
Sıra: Faz 0 (güvenlik + Genel Müdür'ün ilk hali) her zaman önce gelir.

**Doğrudan uygulanacaklar** (küçük bir akıl sağlığı testiyle; tam 5 yıllık test şartı yok):
- **Elif delta-nötr carry:** Spot alım ve vadeli short aynı anda açılır, fiyat riski sıfır.
  - Pozisyon, toplanan fonlama toplam maliyeti (4 bacak komisyon + kayma) geçip hedef net kâra ulaşana kadar günlerce tutulur.
  - Fonlama eksiye dönerse ya da beklenen getiri maliyetin altına düşerse çıkılır.
  - Sadece A sınıfı likit coinler. Spot bacağı tam ödenir, bu yüzden sermaye iki bacağa bölünür.
  - Akıl sağlığı testi: OKX fonlama geçmişiyle "hangi eşikte net kâr" hesabı.
- **Deniz dinamik likidite:** Saat kuralı silinir. Son 15 dakikanın hacmi 24 saatlik ortalamayı aşınca çalışır. Spread şartı BTC/ETH'de ayırt edici olmadığı için ikincil. Deniz zaten gölgede çalışacak.
- **Çift katmanlı icra:**
  - Kemal, Kaan, Selin ve Ortak Masa (günlük): 10 dk maker limit; dolmazsa piyasa emri yerine IOC limit (aktif fiyat ±%0,1).
  - Ayşe ve Deniz (hızlı): doğrudan IOC limit.
  - Girişlerde çıplak piyasa emri kullanılmaz.
  - **Stop çıkışları istisnadır (düzeltme, kullanıcı itirazı 21:18):** Stop'ta öncelik kesin çıkıştır, fiyat ikinci planda.
    - Gerçek API'de stop, **borsanın kendi sunucusunda** bekleyen stop-market emri olarak durur (OKX koşullu emir, reduce-only). Bizim bot (GitHub Actions) çökse ya da gecikse bile stop çalışır.
    - Ne IOC ne de bantlı stop-limit kullanılır: fiyat bandın dışına boşluk yaparsa stop-limit dolmaz ve pozisyon açık kalır.
    - Demo simülasyonu: stop kayması sabit 5 bp yerine oynaklığa bağlı (sert hareket ve ince likiditede daha büyük) modellenecek.

**5 yıllık veride yarıştırılacaklar** (Faz 1 çıkış yarışıyla birlikte):
- **Kemal:** Mevcut kural vs + ADX > 25 (gecikmenin büyük trendleri ne kadar budadığı).
- **Kaan giriş:** Direkt kırılım vs ilk geri çekilmeyi (retest) bekleme.
- **Kaan stop:** 10 günlük kanal çıkışı vs kırılım mumu fitili.
- **Selin:** Mevcut bant genişliği vs TTM Squeeze (Bollinger, Keltner içinde).
- **Ayşe:** Günlük vs 15 dk (RSI < 20 + hacim patlaması, %1–2 hedef). Çöküş anı kayması sert modellenir; 15 dk OKX geçmiş verisi indirilecek.
- **BTC kuralı:** BTC < SMA50 iken long yarım boyut (mevcut) vs long tamamen yasak.
- **Çıkış yarışı (2d-1):** Kısmi kâr + iz stop vs sadece iz stop vs tam hedef (Kaan, Selin, Murat, Ortak).

**Reddedilenler (gerekçeli):**
- Elif'in sadece fonlama saatini atlatıp kapatması: 4 bacak ~%0,5 maliyet, tek fonlama ~%0,01–0,1.
- %80 korelasyonda tek işlem yasağı: kriptoda düşüşte her şey korele olur; korele gruba toplam %4 risk kuralı kalır, korelasyon penceresi 30 gün.
- Makro veriden önce stop'u başabaşa çekmek: veri anı iğneleri stop'ları patlatır.
- Stop çıkışında IOC ya da dar stop-limit: çöküşte dolmama riski kaymadan çok daha tehlikeli (borsa tarafı stop-market seçildi).

### 9.7 Dürüst uyarılar
- 40 uzman demek çok ayar demek, bu da aşırı uyum riski getirir. Bu yüzden uzmanlar ortak araçları kullanır; her parça kendi testini ve Deflated Sharpe kontrolünü geçer.
- Meta-labeling sihirli değnek değil; zayıf bir ana sinyali kurtarmaz, iyi sinyali seçici yapar.
- Lider tablolarında hayatta kalan yanılgısı var. Başkasını kopyalamak değil, ders çıkarmak amaç.
- Beklenti: daha az kötü işlem ve daha doğru boyut. Mucize değil.

Kaynaklar (ek):
- [Messari: Binance 2025 listelemeleri ortalama −%71 (AMBCrypto)](https://ambcrypto.com/messari-data-shows-binances-2025-listings-down-71-on-average/)
- [BitMEX 2025 Ç1: vadeli listelemeler zirve sinyali mi?](https://blog.bitmex.com/2025q1-derivatives-report)
- [Hyperliquid userFills (QuickNode)](https://www.quicknode.com/docs/hyperliquid/info-endpoints/userFills)
- [OKX public lead traders endpoint](https://qveris.ai/tools/okx_api_v5.copytrading.public-lead-traders.list.v5.2bfcea0c)
- [Meta-Labeling (Wikipedia)](https://en.wikipedia.org/wiki/Meta-Labeling)
- [Why meta-labeling is not a silver bullet (QuantConnect)](https://www.quantconnect.com/forum/discussion/14706/why-meta-labeling-is-not-a-silver-bullet/)
- [Reconciling Open Interest with Traded Volume in Perpetual Swaps](https://arxiv.org/html/2310.14973v2)

## Kaynaklar

- [Quantopian 888 strateji çalışması (CXO Advisory)](https://www.cxoadvisory.com/big-ideas/in-sample-vs-out-of-sample-performance-of-888-trading-strategies)
- [The Probability of Backtest Overfitting](https://carmamaths.org/jon/backtest2.pdf) · [The Deflated Sharpe Ratio](https://papers.ssrn.com/abstract=2460551)
- [The Trend is Your Friend (Grayscale)](https://research.grayscale.com/reports/the-trend-is-your-friend-managing-bitcoins-volatility-with-momentum-signals)
- [Revisiting Trend-following and Mean-reversion in Bitcoin (Quantpedia)](https://quantpedia.com/revisiting-trend-following-and-mean-reversion-strategies-in-bitcoin/)
- [A Decade of Evidence of Trend Following in Cryptocurrencies](https://ar5iv.labs.arxiv.org/html/2009.12155)
- [Cross-Sectional Momentum: Net-of-Costs Replication on Binance Perpetuals 2020–2026](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7404139)
- [Fundamentals of Perpetual Futures](https://ar5iv.labs.arxiv.org/html/2212.06888) · [Perpetual Futures (Elm Wealth)](https://elmwealth.com/perpetual-futures/)
- [Funding rate arbitrage: when it pays and when it does not](https://athenum.mataroa.blog/blog/funding-rate-arbitrage-how-the-perp-spot-basis-pays-and-when-it-does-not/)
- [Technical analysis in crypto: transaction costs and bubbles (Springer)](https://link.springer.com/article/10.1007/s10479-019-03357-1)
- [Automatic identification and evaluation of Fibonacci retracements](https://www.datalearner.com/academic/journal-papers/0957-4174/volumes-and-issues/391/paper-detail/17703)
- [Can Reddit Sentiment Predict Bitcoin Returns? (CEU)](https://www.etd.ceu.edu/2023/zhumagaziyev_sh.pdf)
- [How Good Is The Bitcoin Fear & Greed Index](https://bmpro.substack.com/p/how-good-is-the-bitcoin-fear-and)
- [What Does an Exchange Listing Actually Deliver in 2026? (The TIE)](https://www.thetie.io/insights/what-does-an-exchange-listing-actually-deliver-in-2026)
- [Bitcoin intraday time series momentum](https://research.birmingham.ac.uk/en/publications/bitcoin-intraday-time-series-momentum/)
- [Market regime detection with HMM (MDPI)](https://www.mdpi.com/1911-8074/13/12/311/htm)
- [OKX ücretleri (BitDegree, Ağustos 2026)](https://www.bitdegree.org/crypto/tutorials/okx-fees)
