# v8 Test Raporu — 2026-10-27 01:40 UTC

- Dönem: 2021-10-28 → bugün (5 yıl), örneklem içi/dışı ayrımı 2024-10-27
- Evren: 27 coin · Deneme sayısı (Deflated Sharpe için): 20
- Maliyet: taker %0.1, maker %0.04, giriş 2.0 bp, stop 5.0 bp kayma, long funding günde %0.03

| Yönetici | İşlem | Kazanma | Ort. R | Kâr faktörü | Örneklem dışı ort. R | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe | Karar |
|---|---|---|---|---|---|---|---|---|---|---|
| Baş Trader | 587 | 39,2% | 0,147 | 1,33 | 0,161 | 15,8% | -32,1% | 0,73 | 0,000 | zayıf pozitif |
| Turbo | 587 | 39,2% | 0,147 | 1,33 | 0,161 | -6,4% | -52,7% | -0,08 | 0,000 | zayıf pozitif |
| Toplayıcı | 996 | 37,2% | -0,521 | 0,26 | -0,521 | -99,2% | -99,2% | -13,18 | 0,000 | KALDI |
| Kemal | 897 | 32,7% | 0,041 | 1,15 | 0,082 | 5,8% | -23,8% | 0,39 | 0,000 | zayıf pozitif |
| Ayşe | 38 | 50,0% | -0,080 | 0,79 | -0,008 | -0,7% | -9,1% | -0,16 | 0,000 | KALDI |
| Murat | 822 | 40,9% | -0,018 | 0,96 | 0,014 | -4,3% | -48,5% | 0,01 | 0,000 | KALDI |
| Kaan | 568 | 42,8% | 0,156 | 1,35 | 0,179 | 26,2% | -25,9% | 0,92 | 0,000 | zayıf pozitif |
| Selin | 357 | 44,0% | 0,126 | 1,34 | 0,118 | 18,4% | -21,2% | 0,77 | 0,000 | zayıf pozitif |

## Firma (tüm yöneticiler birlikte, firma limitleriyle)

| Katmanlar | Alınan / atlanan işlem | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe |
|---|---|---|---|---|---|
| hepsi_esit · limitler | 2767 / 2085 | 4,4% | -31,4% | 0,33 | 0,000 |
| hepsi_esit · limitler+hava+yigilma | 2807 / 2045 | 7,5% | -24,5% | 0,51 | 0,000 |
| politika · limitler | 1274 / 1722 | 16,4% | -20,2% | 0,96 | 0,000 |
| politika · limitler+hava+yigilma | 1301 / 1695 | 17,0% | -19,2% | 1,04 | 0,000 |

Politika gereği sermaye alanlar: bas, turbo, kemal, kaan, selin (diğerleri gölgede).

## Yıllara göre ortalama R

| Yönetici | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| Baş Trader | -0,37 (9) | 0,21 (69) | 0,33 (103) | 0,11 (128) | -0,33 (125) | 0,45 (153) |
| Turbo | -0,37 (9) | 0,21 (69) | 0,33 (103) | 0,11 (128) | -0,33 (125) | 0,45 (153) |
| Toplayıcı | – (0) | – (0) | – (0) | – (0) | -0,54 (198) | -0,52 (798) |
| Kemal | -0,24 (21) | -0,01 (106) | 0,08 (134) | 0,10 (204) | -0,09 (195) | 0,12 (237) |
| Ayşe | 0,00 (1) | 0,46 (3) | 0,65 (3) | -0,39 (16) | -0,08 (9) | 0,10 (6) |
| Murat | -0,25 (22) | -0,02 (116) | 0,09 (115) | -0,13 (170) | -0,02 (205) | 0,04 (194) |
| Kaan | 0,00 (11) | 0,12 (69) | 0,22 (90) | 0,30 (120) | -0,10 (130) | 0,25 (148) |
| Selin | -0,44 (10) | 0,06 (52) | 0,27 (66) | 0,22 (56) | 0,04 (93) | 0,16 (80) |

## Notlar

- Parametre optimizasyonu yapılmadı; kurallar önceden sabit.
- Evren bugünün en likit coinleri (survivorship): sonuçlar iyimser tarafta olabilir.
- Funding/OI/alıcı-satıcı verisi 5 yıl geriye yok: bu doğrulamalar testte 'tutmadı' sayıldı, risk taban seviyede.
- Elif (funding carry) 5 yıllık funding geçmişi olmadığı için test edilemedi; yayımlanmış istatistikler kullanıldı.
- Deniz yalnızca BTC/ETH 15dk son 1 yılda test edildi.
