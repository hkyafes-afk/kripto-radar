# v9 Faz 1 — 5 yıllık varyant yarışı (2026-10-26 19:06 UTC)

27 coin · 2021-10-27 → bugün · eğitim/doğrulama ayrımı 2025-04-27 · 17 deneme (Deflated Sharpe bütün denemelere göre) · 194 sn

Benimseme: doğrulamada ≥ +0,02R iyi, eğitimde kötü değil, bileşik büyüme düşmüyor, MAR ≥ %90 taban, yeterli işlem.

## kemal

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 887 | 0.09 | 0.074 | 0.113 | 10.78 | -39.95 | 0.27 | 0.403 |
| adx25 | 515 | 0.06 | 0.004 | 0.146 | 4.39 | -41.08 | 0.11 | 0.243 |

**Karar:** hiçbir varyant dört şartı birden geçmedi → mevcut kural korunur

## kaan

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 344 | 0.242 | 0.378 | 0.069 | 14.75 | -27.59 | 0.53 | 0.561 |
| cikis_iz | 390 | 0.076 | 0.082 | 0.066 | 9.34 | -32.31 | 0.29 | 0.402 |
| cikis_iz_tp1 | 344 | 0.259 | 0.412 | 0.063 | 15.92 | -28.25 | 0.56 | 0.56 |
| cikis_hedef | 354 | 0.226 | 0.324 | 0.1 | 13.74 | -24.8 | 0.55 | 0.522 |
| retest | 316 | 0.165 | 0.379 | -0.118 | 18.13 | -35.89 | 0.51 | 0.611 |
| fitil_stop | 396 | 0.092 | 0.178 | -0.021 | -1.54 | -47.42 | -0.03 | 0.129 |

**Karar:** hiçbir varyant dört şartı birden geçmedi → mevcut kural korunur

## selin

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 242 | 0.183 | 0.105 | 0.265 | 1.09 | -20.28 | 0.05 | 0.155 |
| cikis_iz | 247 | 0.075 | -0.013 | 0.166 | -0.96 | -28.8 | -0.03 | 0.1 |
| cikis_iz_tp1 | 242 | 0.207 | 0.114 | 0.303 | 0.36 | -26.11 | 0.01 | 0.138 |
| cikis_hedef | 242 | 0.195 | 0.13 | 0.263 | 8.02 | -18.81 | 0.43 | 0.393 |
| ttm | 208 | 0.192 | 0.128 | 0.274 | 8.59 | -26.98 | 0.32 | 0.41 |

**Karar:** hiçbir varyant dört şartı birden geçmedi → mevcut kural korunur

## murat

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 901 | -0.049 | -0.089 | 0.014 | -9.11 | -60.27 | -0.15 | 0.028 |
| cikis_iz | 935 | -0.037 | -0.074 | 0.021 | -10.3 | -53.63 | -0.19 | 0.039 |
| cikis_iz_tp1 | 890 | -0.032 | -0.081 | 0.044 | -5.18 | -48.13 | -0.11 | 0.083 |
| cikis_hedef | 955 | -0.034 | -0.074 | 0.026 | -8.44 | -61.07 | -0.14 | 0.033 |

**Karar:** cikis_iz_tp1: doğrulama +0.044R (taban +0.014R), yıllık %-5.18 (taban %-9.11), MAR -0.11 (taban -0.15)

## Firma geneli kurallar (taban işlemleriyle, tek havuz)

| Kural | Yıllık % | Maks. düşüş % | MAR | İşlem | Eğitim R | Doğrulama R |
|---|---|---|---|---|---|---|
| taban (ayıda long yarım, sınıf kuralı yok) | 26.22 | -33.08 | 0.79 | 656 | 0.12 | 0.133 |
| ayıda long YASAK | 26.8 | -33.76 | 0.79 | 599 | 0.116 | 0.227 |
| Genel Müdür kanıt sınıfı açık (B yarım, C gölge) | 18.57 | -35.76 | 0.52 | 824 | 0.078 | 0.166 |
| ayıda long yasak + kanıt sınıfı | 22.19 | -34.95 | 0.63 | 774 | 0.112 | 0.154 |

Kanıt sınıfına göre işlemler: A 1504 (ort. 0.053R), B 674 (ort. 0.125R), C 196 (ort. 0.003R)

Uyarı: coin evreni bugün hâlâ işlem gören coinler (batanlar yok) → sonuçlar iyimser tarafta.
