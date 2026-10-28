# v9 Faz 1 — varyant yarışı kararları (26 Ekim 2026)

Aynı kurallar üç ayrı coin evreninde yarıştı (27, 27 ve 36 coin; evren her seferinde o anki hacme göre seçildiği için
değişiyor). Tek bir coin farkı bile sonuçları oynattı; bu yüzden **en az 2/3 koşuda bütün şartları geçen ve hiçbir
koşuda doğrulama R'si ya da büyümesi tabandan geri düşmeyen** varyant benimsendi.

Şartlar (her koşuda): doğrulamada ≥ +0,02R iyi, eğitimde kötü değil (−0,01R tolerans), 5 yıllık bileşik büyüme düşmüyor,
MAR (büyüme/düşüş) tabanın en az %90'ı, yeterli işlem. Sert maliyet + oynaklığa bağlı stop kayması.

| Yönetici | Varyant | Geçti (3 koşu) | Doğrulama R farkı | Yıllık büyüme farkı (puan) | Karar |
|---|---|---|---|---|---|
| kemal | adx25 | 0/3 | +0.033 / +0.048 / -0.011 | -6.4 / -4.1 / -10.1 | reddedildi |
| kaan | cikis_iz | 0/3 | -0.003 / -0.018 / +0.002 | -5.4 / -4.4 / -1.7 | reddedildi |
| kaan | cikis_iz_tp1 | 0/3 | -0.006 / -0.011 / -0.009 | +1.2 / +0.9 / -0.9 | reddedildi |
| kaan | cikis_hedef | 0/3 | +0.031 / +0.042 / +0.027 | -1.0 / +2.0 / +1.1 | reddedildi |
| kaan | retest | 0/3 | -0.187 / -0.080 / -0.037 | +3.4 / +12.4 / -2.0 | reddedildi |
| kaan | fitil_stop | 0/3 | -0.090 / -0.117 / -0.081 | -16.3 / -12.9 / -12.0 | reddedildi |
| selin | cikis_iz | 0/3 | -0.099 / -0.112 / -0.093 | -2.0 / -3.3 / -3.2 | reddedildi |
| selin | cikis_iz_tp1 | 0/3 | +0.038 / +0.026 / +0.048 | -0.7 / -2.1 / -1.5 | reddedildi |
| selin | cikis_hedef | 0/3 | -0.002 / +0.003 / -0.018 | +6.9 / +6.9 / +7.4 | reddedildi |
| selin | ttm | 2/3 | +0.009 / +0.024 / +0.020 | +7.5 / +10.2 / +7.9 | ✅ benimsendi |
| murat | cikis_iz | 0/3 | +0.007 / +0.018 / +0.006 | -1.2 / -0.2 / -1.6 | reddedildi |
| murat | cikis_iz_tp1 | 2/3 | +0.030 / +0.028 / +0.004 | +3.9 / +3.2 / +0.4 | ✅ benimsendi (Murat gölgede) |
| murat | cikis_hedef | 1/3 | +0.012 / +0.005 / +0.022 | +0.7 / +2.5 / +0.9 | reddedildi |

## Firma geneli kurallar (yıllık büyüme %, 3 koşu)

| Kural | Koşu 1 | Koşu 2 | Koşu 3 | Karar |
|---|---|---|---|---|
| taban (ayıda long yarım, sınıf kuralı yok) | 26.22 | 29.18 | 21.95 |  |
| ayıda long YASAK | 26.8 | 25.06 | 26.16 | reddedildi (tutarsız: +0,6 / −4,1 / +4,2) |
| Genel Müdür kanıt sınıfı açık (B yarım, C gölge) | 18.57 | 23.2 | 20.15 | düzeltildi → B tam boyut, C gölge |
| ayıda long yasak + kanıt sınıfı | 22.19 | 25.93 | 26.39 |  |
| sadece C gölge (B tam boyut) | – | 23.03 | 17.45 |  |
| B tam, C yarım | – | – | 17.5 |  |
| ayıda long yasak + sadece C gölge | – | 25.51 | 21.46 |  |

Kanıt sınıfına göre ortalama R (3 koşu): koşu 1: A 0.053 / B 0.125 / C 0.003 · koşu 2: A 0.067 / B 0.115 / C 0.0 · koşu 3: A 0.031 / B 0.074 / C -0.039

**Sınıf kuralı kararı:** B sınıfı (1–3 yıl) her koşuda en iyi ortalama R'yi verdi → yarım boyut kaldırıldı, tam boyut.
C sınıfı (1 yıldan yeni) ortalama ≈0 ya da eksi R; ayrıca test evreni bugün hâlâ işlem gören coinlerden oluştuğu için
hayatta kalan yanılgısı en çok genç coinleri iyimser gösterir (batan yeni coinler testte yok; 2025 listelemeleri bir yılda
ortalama −%71). C gölgede kalır; büyüme maliyetini sigorta olarak kabul ediyoruz. Bir yönetici C coinlerde gölgede 30+
işlemle +0,10R yaparsa onun için bir basamak büyür.

**Önemli bulgu — 'kârı koştur':** Kaan ve Selin'de stop'u baştan iz sürdürmek (kısmi kâr yok, hedef yok) her üç
koşuda da kaybettirdi (Kaan −2…−5 puan, Selin −0,1R). Kısmi kârı kaldırıp ilk hedeften sonra iz sürmek (cikis_iz_tp1)
ise kısmi kârla neredeyse aynı. Yani kırılım ajanlarında kazananı erken kesmek sorun değil; erken *iz sürmek* sorun.
Kemal (trend) zaten hedefsiz, iz süren stopla çalışıyor ve en iyi ayarı o.

Uyarı: firma havuzunun 5 yıllık en büyük düşüşü %33–40. Gerçek parada risk düşürülmeden kullanılmamalı.
