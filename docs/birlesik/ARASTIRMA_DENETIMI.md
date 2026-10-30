# Araştırma denetimi — TEK_AJAN_ARASTIRMA.md bulguları kodla tutarlı mı?

Tarih: 30.10.2026. Denetlenen: `data/TEK_AJAN_ARASTIRMA.md`, `tools/arastirma/{panel,features,ic_train,combo_train,sim,team_test,gun_ici}.py`,
`radar/binance_data.py`, `radar/bt2.py`, `radar/race.py`. Ek kontroller bu denetim için çalıştırıldı (aşağıda "Ek test").

Özet hüküm: **Bulgular kodla destekleniyor**, ancak dört önemli çekince var: (1) saklı test dönemi artık temiz değil
(defalarca bakıldı), (2) araştırma maliyet modeli canlı paper defterinden hafif, (3) yeni ajanın test kârı az sayıda
coinde yoğun, (4) OKX tabanlı eski yarışlarda hayatta kalma yanlılığı var. Ayrıntılar aşağıda.

## 1. Saklı test ayrımı

- `features.py`: `TRAIN_START = 2020-10-28`, `SPLIT = 2025-01-07`. `ic_train.py` ve `combo_train.py` yalnız
  `TRAIN_START ≤ t < SPLIT` maskesini kullanıyor; `sim.py` test sonuçlarını yalnız `--test` bayrağıyla yazdırıyor. ✔
- Bileşik puanın ağırlıkları eşit, yalnız işaretler eğitim IC'lerinden; optimize edilmiş parametre yok. ✔
- Adaylar (A, B, C) eğitimde seçildi, test bir kez açıldı; testten sonra A'nın tanımı değişmedi. ✔
- **Çekince — test dönemi tekrar kullanıldı:** 2025-01-07 → 2026-10-27 penceresi şu işlerde de görüldü: Binance yarışında
  Kemal varyantları (doğrulama), mevcut ekip karşılaştırması (`team_test.py`), A'nın testi, günlük dengeleme varyantı,
  aşağıdaki gecikme ve plasebo kontrolleri. Hiçbirinde kural değiştirilmedi ama pencere artık "hiç görülmemiş" değil.
  **Bundan sonraki tek temiz sınav ileriye dönük canlı gölge (paper) ölçümüdür.**

## 2. Sinyal ve yürütme zamanlaması (look-ahead)

- Özellikler yalnız t kapanışına kadar: kayan pencereler geriye dönük (`rolling`, `shift`), 20 günlük zirve `high.shift(1)`
  ile (bugün hariç), evren son 30 günün hacmiyle t'de kuruluyor, rejim BTC'nin t kapanışından. ✔
- İşlem t+1 açılışında: `features.py` ileri getiri `open.shift(-1)` girişli; `sim.py` hedefi t'de hesaplayıp `O[t+1]`'de
  işlem yapıyor. ✔ Gün içi araştırmada (`gun_ici.py`) günlük evren/filtreler `shift(1)` (önceki gün), giriş bir sonraki
  saatin açılışı. ✔
- **Ek test — 1 gün gecikme:** Hedefler bir gün eski sinyalle kurulduğunda (LAG=1) eğitim +%113,8/yıl (önce +%114,7),
  test +%20,0/yıl, düşüş −%19,6 (önce +%17,7, −%31,7). Sonuç ertesi güne bağımlı değil → sızıntı izi yok.

## 3. Listeden çıkan coinler ve zombi satırlar

- Binance arşivi listeden çıkan coinler için çıkıştan sonra da **hacmi sıfır, fiyatı donmuş** satır üretiyor (ör. FTT 2022-11
  sonrası). `panel.py` hacmi ya da işlem sayısı sıfır günleri NaN yapıyor (60.165 hücre). Son işlem günleri doğrulandı:
  FTT 2022-11-14, LUNA 2022-05-12. Batan coin sayısı 163 (önceki manifest yanlışlıkla 31 gösteriyordu). ✔
- `radar/binance_data.py` (yarış okuyucusu) aynı düzeltmeyi aldı; düzeltmeden önceki Binance yarış sonuçları (Kemal 30/100
  coin) hafif farklıydı, `team_test.py` sonuçları düzeltmeden sonra. ✔
- Tutulan coin listeden çıkarsa `sim.py` son kapanıştan kapatıyor. **Çekince:** gerçek çıkış fiyatı son kapanıştan kötü
  olabilir (duyuru sonrası düşüş günlük mumlara yansır, ama son anlık takas fiyatı bilinmiyor) → hafif iyimser.

## 4. Maliyetler

- `sim.py`: işlem başına ücret 5 bp (OKX liste taker) + likiditeye göre kayma 3/8/15 bp; `--stres` ile ×2.
- **Çekince:** Canlı paper defteri (`ledger.py`, `config.yaml → ucret`) liste ücretinin **2 katını** (10 bp) ve oynaklığa bağlı
  stop kayması kullanıyor; araştırma modeli bundan hafif. ×2 stres yaklaşık defter sertliğine denk: A'nın testi +%14,0/yıl.
  Yeni `CostEngine` defterle aynı ücret tablosunu kullanıyor (tek kaynak).
- Gün içi araştırma gidiş-dönüş ~14–20 bp; 2× maliyetle hiçbir kural eğitimi bile geçmedi. ✔

## 5. Funding

- Gerçek Binance funding'i günlük toplanıyor (`panel.py`); `sim.py` her gün `oran × adet × kapanış` ile long'dan düşüyor,
  short'a ekliyor. ✔ Yaklaşım: kesim anı yerine gün sonu pozisyonu (kesim saatlerinde pozisyon değişiyorsa küçük sapma).
- BTC al-tut kıyası da vadeli + funding ile; 2021'de funding getiriyi belirgin düşürüyor (fiyat +%59, funding sonrası +%17). ✔

## 6. Günlük ve gün içi farkı

- Günlük: haftalık dengelemeli A testte +%17,7/yıl; günlük dengelemede +%7,9 (maliyet). ✔
- Gün içi (saatlik, 9 kural): eğitimi geçen tek kural (saatlik dip al) testte −81 bp/işlem. ✔
- **Ölçüm notu:** `gun_ici.py`'deki günlük eşit ağırlıklı özsermaye, az işlemli günlere fazla ağırlık veriyor (S2P'de işlem
  başına net eksi iken özsermaye artı görünmesi bundan). Karar ölçütü işlem başına net ortalama ve t; doğru olan o.

## 7. Kârın yoğunlaşması

- A'nın testinde toplam katkı ~32,8 puan; ENJ tek başına ~18,1, en iyi 3 coin ~28,5, onlar hariç ~+4,4. ✔ (raporda yazılı)
- **Ek test — plasebo (aynı evren + aynı BTC rejimi, rastgele seçim, 8 tohum):** eğitim +%43…+%105/yıl (düşüş −%73…−%78),
  **test −%22…−%50/yıl** (düşüş −%62…−%84). A: eğitim +%115 (−%52), test +%17,7 (−%32). A, 8 rastgele seçimin hepsinden
  iki dönemde de iyi. Testteki farkın büyük kısmı, ENJ'den bağımsız olarak, oynak/genç/kalabalık coinlerden uzak durmaktan
  geliyor. Yorum: eğitimdeki yüksek getirinin çoğu 2020–21 boğasında rejim etkisi; seçimin katkısı testte belirgin.

## 8. Long/short asimetrisi

- Naif short (B: en kötü 10'u açığa sat, C: boğa değilken short) testte −%80 ve −%68; zayıf coinler sert pompalanıyor. ✔
- Kemal'in stoplu trend short'ları OKX yarışında artı (short_r +0,094) — sorun short'un kendisi değil, stopsuz/sürekli
  sepet short'u. `ShortTrendStrategy` bu yüzden ayrı ve daha sıkı tasarlanacak (PLAN.md, PR 2).

## 9. Kemal, Selin, Kaan, Murat

- `team_test.py`: yarışın kendi kuralları (`bt2.run_manager`), Binance verisi (zombi düzeltmeli, hacme göre ilk 100, batanlar
  dahil), yönetici risk ayarları; eğitim/test ayrı özsermaye. Rapordaki tablo bu çıktıyla aynı. ✔
- Sıralama dönemden döneme değişiyor (eğitimde Kaan, testte Kemal en iyi); Murat iki dönemde de en zayıf. ✔
- **Çekince:** `race.py` Kaan ve Selin'i özsermayede yalnız long ile ölçüyor (`LONG_ONLY`); short'ları canlıda gölgede
  olduğu için tutarlı ama raporda belirtilmeli.

## 10. Polat ve kısa vade

- Polat'ın −0,27R'si `polat-veri` dalındaki `RAPOR.md`'den (1.549 sinyal, maliyet sonrası). Gün içi araştırma ile aynı yön. ✔
- Deniz (15 dk) zaten durdurulmuş (config: testte −%94). Yeni gün içi strateji yalnız SHADOW_ONLY başlayacak.

## 11. Hayatta kalma (survivorship) yanlılığı

- Binance günlük panel: batanlar dahil, evren gün gün. ✔ Saatlik arşiv: geçmişte bir gün ilk 30'a girmiş 429 coin (batanlar
  dahil). ✔
- **Çekince:** OI/long-short metrik indirmesi bugünkü ilk 100 coinle yapıldı → o veriyle yapılacak OI testi yanlı olur.
- **Çekince:** Eski OKX yarışları (`data/YARIS.md`, `data/LAB.md` kısmen) bugünkü OKX evreniyle; `bt2.py` bunu kendisi not
  ediyor. Binance sonuçlarıyla karşılaştırırken bu fark hatırlanmalı.
- Yalnız Binance'te listelenmiş coinler var; Binance'e hiç gelmemiş coinler kapsam dışı (küçük etki).

## 12. Veri sızıntısı ve tekrar tekrar uyum

- Faktör işaretleri yalnız eğitimden; bileşik puan sabit ağırlıklı; test görüldükten sonra parametre değişmedi. ✔
- Gün içi 9 kural veriye bakmadan sabitlendi, seçim yalnız eğitimle. ✔
- **Çekince:** Aynı Binance verisi üzerinde toplam deneme sayısı arttı (yarış varyantları + 3 ajan + 9 gün içi kural + kontroller).
  Tek tek sonuçlar Deflated Sharpe ile düzeltilmedi; yeni kural benimsenirken deneme sayısı kaydedilmeli (EffectivenessAuditor, PR 4).

## Sonuç

`TEK_AJAN_ARASTIRMA.md`'deki ana bulgular (oynaklık/yaş/kalabalık/BTC rejimi etkileri, naif short ve gün içi al-satın
başarısızlığı, mevcut ekibin yeni ajandan iyi olması) kodla ve ek kontrollerle destekleniyor. Birleşik sistemin tasarımı bu
bulgulara dayanacak, ama hiçbir kural yalnız bu geçmiş testle gerçek riske terfi etmeyecek: terfi ölçütü canlı paper/gölgede
yeterli örnek ve güven aralığının sıfırın üstünde olması (EffectivenessSnapshot.proven_positive + örnek sayısı).
