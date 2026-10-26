# v8 Test Raporu — 2026-10-26 15:13 UTC

- Dönem: 2021-10-27 → bugün (5 yıl), örneklem içi/dışı ayrımı 2024-10-26
- Evren: 28 coin · Deneme sayısı (Deflated Sharpe için): 16
- Maliyet: taker %0.1, maker %0.04, giriş 2.0 bp, stop 5.0 bp kayma, long funding günde %0.03

| Yönetici | İşlem | Kazanma | Ort. R | Kâr faktörü | Örneklem dışı ort. R | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe | Karar |
|---|---|---|---|---|---|---|---|---|---|---|
| Kemal | 917 | 33,2% | 0,046 | 1,17 | 0,084 | 6,9% | -33,8% | 0,42 | 0,000 | zayıf pozitif |
| Deniz | 233 | 46,4% | -0,278 | 0,58 | -0,278 | -45,3% | -45,3% | -3,31 | 0,000 | KALDI |
| Ayşe | 39 | 51,3% | -0,037 | 0,90 | 0,076 | -0,3% | -8,8% | -0,08 | 0,000 | KALDI |
| Murat | 849 | 51,2% | -0,024 | 0,95 | 0,006 | -3,9% | -48,5% | -0,04 | 0,000 | KALDI |
| Kaan | 574 | 42,9% | 0,167 | 1,38 | 0,194 | 20,0% | -25,6% | 0,75 | 0,000 | zayıf pozitif |
| Selin | 396 | 43,2% | 0,132 | 1,34 | 0,130 | 15,5% | -23,4% | 0,70 | 0,000 | zayıf pozitif |

## Firma (tüm yöneticiler birlikte, firma limitleriyle)

| Katmanlar | Alınan / atlanan işlem | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe |
|---|---|---|---|---|---|
| hepsi_esit · limitler | 2274 / 734 | 6,7% | -18,2% | 0,48 | 0,000 |
| hepsi_esit · limitler+hava+yigilma | 2248 / 760 | 7,6% | -17,5% | 0,55 | 0,000 |
| politika · limitler | 960 / 927 | 12,4% | -12,7% | 0,78 | 0,000 |
| politika · limitler+hava+yigilma | 990 / 897 | 10,9% | -11,6% | 0,72 | 0,000 |

Politika gereği sermaye alanlar: kemal, kaan, selin (diğerleri gölgede).

## Yıllara göre ortalama R

| Yönetici | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| Kemal | -0,18 (22) | -0,00 (106) | 0,09 (134) | 0,09 (210) | -0,09 (194) | 0,13 (251) |
| Deniz | – (0) | – (0) | – (0) | – (0) | -0,23 (51) | -0,29 (182) |
| Ayşe | 0,00 (1) | 0,46 (3) | 0,65 (3) | -0,38 (16) | -0,07 (9) | 0,27 (7) |
| Murat | 0,12 (22) | -0,01 (121) | 0,03 (114) | -0,13 (175) | 0,01 (213) | -0,02 (204) |
| Kaan | -0,08 (12) | 0,13 (69) | 0,23 (90) | 0,34 (119) | -0,11 (129) | 0,26 (155) |
| Selin | -0,52 (8) | 0,23 (60) | 0,16 (63) | 0,24 (62) | 0,02 (109) | 0,16 (94) |

## Notlar

- Parametre optimizasyonu yapılmadı; kurallar önceden sabit.
- Evren bugünün en likit coinleri (survivorship): sonuçlar iyimser tarafta olabilir.
- Funding/OI/alıcı-satıcı verisi 5 yıl geriye yok: bu doğrulamalar testte 'tutmadı' sayıldı, risk taban seviyede.
- Elif (funding carry) 5 yıllık funding geçmişi olmadığı için test edilemedi; yayımlanmış istatistikler kullanıldı.
- Deniz yalnızca BTC/ETH 15dk son 1 yılda test edildi.
