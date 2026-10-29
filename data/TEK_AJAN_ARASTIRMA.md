# Tek ajan araştırması — 30 Ekim 2026

Soru: Tek bir ajan kursak, hangi teknik ve kurallarla kurulmalı? Bir coini ne yükseltip ne düşürüyor?

## Veri ve yöntem

- **Binance Vision**, USDT-M vadeli, günlük mum + gerçek funding, 2020-01 → 2026-10-27. 741 kripto coin, **163'ü batmış**
  (listeden çıkmış). Hisse/emtia/endeks vadelileri ve stabil coinler ayıklandı.
- **Bulunan veri hatası:** Arşiv, listeden çıkan coinler için çıkıştan sonra da fiyatı donmuş, hacmi sıfır satır üretiyor
  (ör. FTT 2022-11'den bugüne "işlem görüyor"). Hacmi sıfır gün = işlem yok sayıldı. Repo okuyucusu düzeltildi
  (`radar/binance_data.py`); önceki Binance yarış sonuçları bu düzeltmeden önceydi.
- **Evren her gün yeniden kurulur:** o gün işlem gören, en az 60 günlük, son 30 günün hacmine göre ilk 100 coin.
- **Eğitim:** 2020-10-28 → 2025-01-06. **Saklı test:** 2025-01-07 → 2026-10-27 (22 ay). Bütün tasarım kararları yalnız
  eğitimle alındı; saklı test bir kez açıldı.
- Sinyal gün kapanışında, işlem ertesi gün açılışında. Maliyet: ücret 5 bp + likiditeye göre kayma 3–15 bp; gerçek funding.

## 1. Bir coini ne hareket ettiriyor? (eğitim dönemi)

| Etken | 28 günlük etki | Tutarlılık (yıl) | Anlamı |
|---|---|---|---|
| **Oynaklık (ATR %)** | en oynak %20 evrenden −2,4 puan; en sakin %20 +2,7 | 6 yılın 6'sında aynı yön | En güçlü bulgu. Çok oynak coin uzun vadede kaybettiriyor. |
| **Coin yaşı** | en genç %20 −2,4 puan | 6'da 5 | Yeni listelenen coinler eriyor. |
| **Funding (kalabalık long)** | funding'i en yüksek %20 −2,1 puan | 6'da 6 | Herkesin long olduğu coin geride kalıyor. |
| **20 günlük zirve kırılımı** | zirvede olanlar +0,9; zirveden uzak olanlar −1,9 | 6'da 6 | Kırılım haftalar ölçeğinde zayıf ama tutarlı. |
| **Son 1–7 gün yükselişi** | sonraki günlerde geri veriyor (ters) | 6'da 5–6 | Pompayı kovalamak zarar. |
| **BTC rejimi** | evren ortalaması: BTC boğada **+%5,2**, yatayda −%7,7, ayıda −%5,8 | — | Yönü belirleyen en büyük etken. |
| **Coinin kendi MA50 trendi** | boğada üstü/altı fark yok (+3,5 / +3,2); yatay/ayıda MA50 üstü daha kötü | — | Tek başına bir sonraki ayı tahmin etmiyor. |

Günlük ufukta (1 gün) aynı etkiler var ama küçük: en iyi dilim günde 10–20 bp fark ederken tipik günlük hareket ~300 bp,
işlem maliyeti 10–40 bp. Günlük al-sat yapan bir kural maliyeti zor karşılar.

## 2. Denenen tek ajanlar (yeni, sıfırdan)

Bileşik puan = düşük oynaklık + yaş + düşük funding + 20g zirveye yakınlık (eşit ağırlık, optimize edilmedi).

| Ajan | Eğitim yıllık / maks. düşüş | **Saklı test** yıllık / maks. düşüş / Sharpe |
|---|---|---|
| A: yalnız BTC boğadayken en iyi 10 coin long, haftalık | +%115 / −%52 | **+%17,7 / −%32 / 0,64** (2× maliyetle +%14) |
| A, her gün dengeleme | +%87 / −%51 | +%7,9 / −%33 / 0,39 |
| B: en iyi 10 long + en kötü 10 short (piyasa nötr) | −%4 / −%56 | **−%80** / −%98 |
| C: boğada long, değilken short | +%86 / −%73 | **−%68** / −%93 |
| BTC al-tut (vadeli, funding dahil) | +%41 / −%79 | −%11,7 / −%54 |
| Altcoinler eşit ağırlık (boğada) | +%80 / −%74 | −%26,3 / −%63 |

- A testte BTC'yi ve altcoinleri geçti ama **kârın büyük kısmı 3 coinden** (ENJ'nin Nisan 2026'daki %340'lık yükselişi
  tek başına ~18 puan); onlar hariç ~sıfır. Zamanın %63'ünde nakitte.
- Açığa satış (B, C) yıkıcı: zayıf coinler sert pompalanıyor.
- Günlük dengeleme maliyetten dolayı getiriyi yarıya indiriyor.

## 3. Mevcut ekip, aynı veri ve aynı dönemler

| Yönetici | Eğitim yıllık / düşüş / Sharpe | **Saklı test** yıllık / düşüş / Sharpe | Test işlem, ort. R |
|---|---|---|---|
| Kemal (trend) | +%11,0 / −%26 / 0,72 | **+%18,1 / −%10 / 1,16** | 107, +0,32R |
| Selin (sıkışma) | +%14,5 / −%14 / 1,16 | +%13,3 / −%11 / 0,99 | 56, +0,54R |
| Kaan (kırılım) | +%22,3 / −%11 / 1,67 | +%8,0 / −%15 / 0,62 | 76, +0,44R |
| Murat (geri çekilme) | +%4,9 / −%10 / 0,65 | +%2,1 / −%4 / 0,59 | 49, +0,17R |
| **Ekip birlikte** | +%21,3 / −%22 / 0,98 | **+%16,2 / −%17 / 0,81** | 288, +0,37R |

- Yeni ajan A, testte mevcut ekipten **iyi değil**: Kemal aynı getiriyi üçte bir düşüşle yaptı.
- Sıralama dönemden döneme değişiyor: eğitimde en iyi Kaan, testte en iyi Kemal. **Tek bir tekniği seçmek kırılgan;**
  teknikleri birlikte kullanmak her iki dönemde de makul kaldı.
- Murat iki dönemde de en zayıf.

## 4. Kendi işlemlerimizden

- Polat (gün içi, emir defteri ve dakikalık veri): 1.553 sinyalde maliyet sonrası **−0,27R**. Gün içi teknik sinyaller
  maliyetten sonra para kaybettirdi; araştırmadaki "günlük al-sat maliyeti karşılamaz" bulgusuyla tutarlı.
- Kripto Radar canlı/gölge: 3 günlük örnek, karar için yetersiz. Kapanan işlemlerde zirvedeki kârın küçük kısmı cebe giriyor.

## Sonuç: tek ajan nasıl olmalı

1. **Sıfırdan yeni teknik değil, mevcut tekniklerin tek elde birleşimi** (Turbo fikri). Giriş tipleri: Kemal trend, Selin
   sıkışma, Kaan kırılım (düşük ağırlık). **Murat çıkarılır.**
2. **Araştırmanın doğruladığı filtreler zorunlu:** BTC rejimi (boğa değilken yeni long yok ya da küçük), ATR % ≤ 5
   (mevcut oynaklık kapısı doğrulandı), genç coinlerde gerçek para yok (mevcut C sınıfı kuralı doğrulandı).
   **Yeni adaylar (önce test edilecek):** funding kalabalıksa giriş yok (şu an Kemal'de yalnız kayıt), son 3 günde sert
   yükselmiş coine giriş yok (pompa kovalamama).
3. **Açığa satış yalnız Kemal'in stoplu trend short'ları** (OKX yarışında artı); sürekli/naif short yok.
4. **Risk:** işlem başına sermayenin %0,5–1'i, stoplu. A gibi tam maruziyet düşüşü −%30'lara çıkarıyor.
5. **Günlük al-sat değil;** pozisyonlar günler/haftalar tutulur, ama çıkışlar her gün kontrol edilir.

## Sınırlar

- Saklı test 22 ay ve büyük ölçüde altcoinlerin düştüğü bir dönem. Ekip bileşenlerinin test sonuçları artık görüldüğü
  için, birleşik ajanın gerçek sınavı **canlı gölge** olmalı (100+ işlem, güven aralığı sıfırın üstünde).
- Binance fiyatları OKX'e yakın ama aynı değil; küçük coinlerde gerçek kayma modelden kötü olabilir.
- OI ve gün içi (saatlik) veri bu çalışmada yok; ikisi de indiriliyor (gün içi araştırma ayrı yapılacak).

Kod: `tools/arastirma/` (README'de tekrar üretme adımları).
