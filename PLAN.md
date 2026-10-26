# Kripto Radar — Kararlar ve Yol Haritası

> Bu dosya kullanıcıyla birlikte alınan kararların kalıcı kaydıdır. Her yeni çalışma oturumunda **önce bu dosya okunur**.
> Son güncelleme: 26 Ekim 2026

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

## 6. Uygulama sırası

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
