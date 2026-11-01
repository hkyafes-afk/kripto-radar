# v8 Test Raporu — 2026-11-01 04:07 UTC

- Dönem: 2021-11-02 → bugün (5 yıl), örneklem içi/dışı ayrımı 2024-11-01
- Evren: 28 coin · Deneme sayısı (Deflated Sharpe için): 18
- Maliyet: taker %0.1, maker %0.04, giriş 2.0 bp, stop 5.0 bp kayma, long funding günde %0.03

| Yönetici | İşlem | Kazanma | Ort. R | Kâr faktörü | Örneklem dışı ort. R | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe | Karar |
|---|---|---|---|---|---|---|---|---|---|---|
| Baş Trader | 199 | 42,7% | 0,267 | 1,52 | 0,296 | 9,9% | -17,3% | 0,75 | 0,625 | zayıf pozitif |
| Turbo | 325 | 43,4% | 0,359 | 1,94 | 0,414 | 20,0% | -16,9% | 1,12 | 0,900 | GEÇTİ |
| Kemal | 213 | 35,7% | 0,114 | 1,38 | 0,175 | 4,5% | -11,8% | 0,51 | 0,408 | zayıf pozitif |
| Ayşe | 13 | 53,8% | 0,076 | 1,16 | 0,466 | 0,1% | -1,3% | 0,11 | 0,136 | yetersiz örnek |
| Murat | 119 | 50,4% | 0,187 | 1,39 | 0,086 | 2,1% | -5,7% | 0,44 | 0,349 | zayıf pozitif |
| Kaan | 148 | 53,4% | 0,410 | 1,87 | 0,476 | 10,9% | -13,8% | 0,93 | 0,787 | zayıf pozitif |
| Selin | 82 | 52,4% | 0,305 | 1,78 | 0,570 | 9,6% | -14,3% | 0,70 | 0,590 | zayıf pozitif |

## Firma (tüm yöneticiler birlikte, firma limitleriyle)

| Katmanlar | Alınan / atlanan işlem | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe |
|---|---|---|---|---|---|
| hepsi_esit · limitler | 1075 / 24 | 9,2% | -7,3% | 1,04 | 0,861 |
| hepsi_esit · limitler+hava+yigilma | 1072 / 27 | 8,5% | -7,3% | 1,02 | 0,847 |
| politika · limitler | 1042 / 44 | 9,9% | -8,5% | 1,03 | 0,846 |
| politika · limitler+hava+yigilma | 1042 / 44 | 9,5% | -8,5% | 1,03 | 0,848 |

Politika gereği sermaye alanlar: bas, turbo, kemal, murat, kaan, selin (diğerleri gölgede).

## Yıllara göre ortalama R

| Yönetici | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| Baş Trader | -1,11 (2) | -0,35 (12) | 0,30 (68) | 0,69 (39) | 0,03 (29) | 0,24 (49) |
| Turbo | -0,85 (2) | -0,36 (25) | 0,43 (95) | 0,72 (57) | 0,09 (59) | 0,46 (87) |
| Kemal | -0,78 (2) | -0,37 (17) | 0,04 (64) | 0,50 (43) | 0,15 (37) | 0,05 (50) |
| Ayşe | -0,51 (1) | 0,30 (2) | -0,01 (2) | -0,48 (3) | 0,77 (2) | 0,27 (3) |
| Murat | 0,27 (1) | -0,43 (8) | 0,31 (47) | 0,36 (16) | 0,20 (17) | 0,06 (30) |
| Kaan | – (0) | -0,54 (6) | 0,41 (49) | 0,69 (32) | 0,05 (21) | 0,52 (40) |
| Selin | – (0) | -1,32 (4) | 0,44 (40) | -0,23 (11) | 0,38 (9) | 0,67 (18) |

## Notlar

- Parametre optimizasyonu yapılmadı; kurallar önceden sabit.
- Evren bugünün en likit coinleri (survivorship): sonuçlar iyimser tarafta olabilir.
- Funding/OI/alıcı-satıcı verisi 5 yıl geriye yok: bu doğrulamalar testte 'tutmadı' sayıldı, risk taban seviyede.
- Elif (funding carry) 5 yıllık funding geçmişi olmadığı için test edilemedi; yayımlanmış istatistikler kullanıldı.
- Deniz yalnızca BTC/ETH 15dk son 1 yılda test edildi.
