# v8 Test Raporu — 2026-10-26 14:45 UTC

- Dönem: 2021-10-27 → bugün (5 yıl), örneklem içi/dışı ayrımı 2024-10-26
- Evren: 28 coin · Deneme sayısı (Deflated Sharpe için): 15
- Maliyet: taker %0.1, maker %0.04, giriş 2.0 bp, stop 5.0 bp kayma, long funding günde %0.03

| Yönetici | İşlem | Kazanma | Ort. R | Kâr faktörü | Örneklem dışı ort. R | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe | Karar |
|---|---|---|---|---|---|---|---|---|---|---|
| Kemal | 918 | 33,0% | 0,045 | 1,17 | 0,084 | 7,3% | -34,2% | 0,44 | 0,000 | zayıf pozitif |
| Deniz | 233 | 46,4% | -0,278 | 0,58 | -0,278 | -52,3% | -52,4% | -3,07 | 0,000 | KALDI |
| Ayşe | 39 | 51,3% | -0,037 | 0,90 | 0,076 | -0,2% | -11,6% | -0,02 | 0,000 | KALDI |
| Murat | 940 | 49,5% | -0,046 | 0,90 | -0,018 | -3,7% | -47,8% | -0,02 | 0,000 | KALDI |
| Kaan | 574 | 42,9% | 0,167 | 1,38 | 0,194 | 20,0% | -25,6% | 0,75 | 0,001 | zayıf pozitif |
| Selin | 396 | 43,2% | 0,132 | 1,34 | 0,130 | 15,5% | -23,4% | 0,70 | 0,001 | zayıf pozitif |

## Firma (tüm yöneticiler birlikte, firma limitleriyle)

| Katmanlar | Alınan / atlanan işlem | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe |
|---|---|---|---|---|---|
| limitler | 599 / 2501 | -7,8% | -46,3% | -0,35 | 0,000 |
| limitler+hava | 693 / 2407 | -5,1% | -38,3% | -0,19 | 0,000 |
| limitler+hava+yigilma | 669 / 2431 | -1,8% | -28,4% | -0,01 | 0,000 |

## Yıllara göre ortalama R

| Yönetici | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| Kemal | -0,18 (22) | -0,00 (106) | 0,09 (134) | 0,09 (210) | -0,09 (194) | 0,12 (252) |
| Deniz | – (0) | – (0) | – (0) | – (0) | -0,23 (51) | -0,29 (182) |
| Ayşe | 0,00 (1) | 0,46 (3) | 0,65 (3) | -0,38 (16) | -0,07 (9) | 0,27 (7) |
| Murat | -0,00 (25) | -0,04 (126) | 0,03 (129) | -0,16 (194) | -0,00 (235) | -0,05 (231) |
| Kaan | -0,08 (12) | 0,13 (69) | 0,23 (90) | 0,34 (119) | -0,11 (129) | 0,26 (155) |
| Selin | -0,52 (8) | 0,23 (60) | 0,16 (63) | 0,24 (62) | 0,02 (109) | 0,16 (94) |

## Notlar

- Parametre optimizasyonu yapılmadı; kurallar önceden sabit.
- Evren bugünün en likit coinleri (survivorship): sonuçlar iyimser tarafta olabilir.
- Funding/OI/alıcı-satıcı verisi 5 yıl geriye yok: bu doğrulamalar testte 'tutmadı' sayıldı, risk taban seviyede.
- Elif (funding carry) 5 yıllık funding geçmişi olmadığı için test edilemedi; yayımlanmış istatistikler kullanıldı.
- Deniz yalnızca BTC/ETH 15dk son 1 yılda test edildi.
