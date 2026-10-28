# v8 Test Raporu — 2026-10-27 07:02 UTC

- Dönem: 2021-10-28 → bugün (5 yıl), örneklem içi/dışı ayrımı 2024-10-27
- Evren: 27 coin · Deneme sayısı (Deflated Sharpe için): 18
- Maliyet: taker %0.1, maker %0.04, giriş 2.0 bp, stop 5.0 bp kayma, long funding günde %0.03

| Yönetici | İşlem | Kazanma | Ort. R | Kâr faktörü | Örneklem dışı ort. R | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe | Karar |
|---|---|---|---|---|---|---|---|---|---|---|
| Baş Trader | 210 | 45,2% | 0,349 | 1,78 | 0,441 | 13,1% | -21,1% | 0,96 | 0,810 | zayıf pozitif |
| Turbo | 329 | 41,6% | 0,246 | 1,62 | 0,322 | 10,9% | -17,6% | 0,73 | 0,642 | zayıf pozitif |
| Kemal | 204 | 39,7% | 0,169 | 1,58 | 0,169 | 6,3% | -7,9% | 0,71 | 0,617 | zayıf pozitif |
| Ayşe | 30 | 56,7% | 0,067 | 1,29 | 0,085 | 0,2% | -2,3% | 0,12 | 0,155 | zayıf pozitif |
| Murat | 113 | 50,4% | 0,323 | 1,68 | 0,512 | 3,6% | -6,2% | 0,75 | 0,663 | zayıf pozitif |
| Kaan | 126 | 54,8% | 0,534 | 2,18 | 0,558 | 12,2% | -12,7% | 1,11 | 0,909 | GEÇTİ |
| Selin | 93 | 54,8% | 0,400 | 2,10 | 0,559 | 13,2% | -14,2% | 0,96 | 0,803 | zayıf pozitif |

## Firma (tüm yöneticiler birlikte, firma limitleriyle)

| Katmanlar | Alınan / atlanan işlem | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe |
|---|---|---|---|---|---|
| hepsi_esit · limitler | 1090 / 15 | 10,0% | -8,0% | 1,18 | 0,929 |
| hepsi_esit · limitler+hava+yigilma | 1083 / 22 | 9,3% | -7,4% | 1,16 | 0,920 |
| politika · limitler | 1090 / 15 | 10,0% | -8,0% | 1,18 | 0,929 |
| politika · limitler+hava+yigilma | 1083 / 22 | 9,3% | -7,4% | 1,16 | 0,920 |

Politika gereği sermaye alanlar: bas, turbo, kemal, ayse, murat, kaan, selin (diğerleri gölgede).

## Yıllara göre ortalama R

| Yönetici | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| Baş Trader | -0,92 (2) | 0,15 (9) | 0,42 (70) | 0,28 (44) | 0,05 (32) | 0,59 (53) |
| Turbo | -0,69 (3) | -0,43 (19) | 0,37 (94) | 0,27 (66) | 0,09 (56) | 0,36 (91) |
| Kemal | -0,78 (2) | -0,23 (13) | 0,16 (62) | 0,38 (41) | 0,13 (33) | 0,18 (53) |
| Ayşe | 0,00 (1) | 0,80 (3) | 0,20 (2) | -0,12 (14) | -0,05 (5) | 0,22 (5) |
| Murat | -0,09 (1) | -0,24 (5) | 0,34 (49) | -0,09 (18) | 0,66 (15) | 0,51 (25) |
| Kaan | – (0) | -0,44 (5) | 0,62 (44) | 0,74 (27) | 0,01 (16) | 0,66 (34) |
| Selin | – (0) | -0,86 (6) | 0,53 (39) | 0,16 (13) | 0,69 (8) | 0,52 (27) |

## Notlar

- Parametre optimizasyonu yapılmadı; kurallar önceden sabit.
- Evren bugünün en likit coinleri (survivorship): sonuçlar iyimser tarafta olabilir.
- Funding/OI/alıcı-satıcı verisi 5 yıl geriye yok: bu doğrulamalar testte 'tutmadı' sayıldı, risk taban seviyede.
- Elif (funding carry) 5 yıllık funding geçmişi olmadığı için test edilemedi; yayımlanmış istatistikler kullanıldı.
- Deniz yalnızca BTC/ETH 15dk son 1 yılda test edildi.
