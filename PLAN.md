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
