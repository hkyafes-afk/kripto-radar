# v8 Test Raporu — 2026-10-26 22:54 UTC

- Dönem: 2021-10-27 → bugün (5 yıl), örneklem içi/dışı ayrımı 2024-10-26
- Evren: 27 coin · Deneme sayısı (Deflated Sharpe için): 18
- Maliyet: taker %0.1, maker %0.04, giriş 2.0 bp, stop 5.0 bp kayma, long funding günde %0.03

| Yönetici | İşlem | Kazanma | Ort. R | Kâr faktörü | Örneklem dışı ort. R | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe | Karar |
|---|---|---|---|---|---|---|---|---|---|---|
| Baş Trader | 626 | 39,3% | 0,152 | 1,35 | 0,194 | 8,2% | -24,2% | 0,59 | 0,000 | zayıf pozitif |
| Kemal | 958 | 32,7% | 0,036 | 1,13 | 0,087 | 7,5% | -35,8% | 0,44 | 0,000 | zayıf pozitif |
| Deniz | 597 | 48,4% | -0,613 | 0,39 | -0,613 | -93,6% | -93,7% | -8,34 | 0,000 | KALDI |
| Ayşe | 40 | 50,0% | -0,074 | 0,81 | -0,008 | -0,7% | -10,1% | -0,14 | 0,000 | KALDI |
| Murat | 880 | 40,3% | -0,031 | 0,94 | 0,016 | -4,1% | -47,6% | 0,03 | 0,000 | KALDI |
| Kaan | 608 | 41,9% | 0,137 | 1,30 | 0,164 | 25,0% | -28,0% | 0,87 | 0,000 | zayıf pozitif |
| Selin | 377 | 43,8% | 0,128 | 1,34 | 0,111 | 22,3% | -20,6% | 0,90 | 0,000 | zayıf pozitif |

## Firma (tüm yöneticiler birlikte, firma limitleriyle)

| Katmanlar | Alınan / atlanan işlem | Yıllık getiri | Maks. düşüş | Sharpe | Deflated Sharpe |
|---|---|---|---|---|---|
| hepsi_esit · limitler | 3196 / 890 | -1,0% | -32,0% | 0,03 | 0,000 |
| hepsi_esit · limitler+hava+yigilma | 3159 / 927 | 1,6% | -25,4% | 0,18 | 0,000 |
| politika · limitler | 1576 / 993 | 13,7% | -12,8% | 0,86 | 0,000 |
| politika · limitler+hava+yigilma | 1610 / 959 | 14,3% | -11,5% | 0,92 | 0,000 |

Politika gereği sermaye alanlar: bas, kemal, kaan, selin (diğerleri gölgede).

## Yıllara göre ortalama R

| Yönetici | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| Baş Trader | -0,35 (10) | 0,14 (79) | 0,30 (109) | 0,11 (136) | -0,29 (130) | 0,48 (162) |
| Kemal | -0,17 (24) | -0,02 (118) | 0,06 (150) | 0,07 (222) | -0,08 (204) | 0,14 (240) |
| Deniz | – (0) | – (0) | – (0) | – (0) | -0,52 (111) | -0,64 (486) |
| Ayşe | 0,00 (1) | 0,63 (4) | 0,65 (3) | -0,43 (17) | -0,08 (9) | 0,10 (6) |
| Murat | -0,17 (25) | -0,06 (129) | 0,05 (130) | -0,15 (183) | -0,01 (217) | 0,03 (196) |
| Kaan | -0,06 (13) | 0,09 (77) | 0,21 (99) | 0,27 (128) | -0,11 (139) | 0,25 (152) |
| Selin | -0,44 (10) | 0,05 (55) | 0,28 (71) | 0,22 (59) | 0,02 (98) | 0,18 (84) |

## Notlar

- Parametre optimizasyonu yapılmadı; kurallar önceden sabit.
- Evren bugünün en likit coinleri (survivorship): sonuçlar iyimser tarafta olabilir.
- Funding/OI/alıcı-satıcı verisi 5 yıl geriye yok: bu doğrulamalar testte 'tutmadı' sayıldı, risk taban seviyede.
- Elif (funding carry) 5 yıllık funding geçmişi olmadığı için test edilemedi; yayımlanmış istatistikler kullanıldı.
- Deniz yalnızca BTC/ETH 15dk son 1 yılda test edildi.
