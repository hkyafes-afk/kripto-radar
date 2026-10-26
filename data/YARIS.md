# v9 Faz 1 — 5 yıllık varyant yarışı (2026-10-26 19:28 UTC)

36 coin · 2021-10-27 → bugün · eğitim/doğrulama ayrımı 2025-04-27 · 17 deneme (Deflated Sharpe bütün denemelere göre) · 403 sn

Benimseme: doğrulamada ≥ +0,02R iyi, eğitimde kötü değil, bileşik büyüme düşmüyor, MAR ≥ %90 taban, yeterli işlem.

## kemal

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 1114 | 0.056 | 0.042 | 0.076 | 8.09 | -45.25 | 0.18 | 0.382 |
| adx25 | 638 | 0.0 | -0.047 | 0.065 | -2.05 | -44.32 | -0.05 | 0.137 |

**Karar:** hiçbir varyant dört şartı birden geçmedi → mevcut kural korunur

## kaan

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 432 | 0.155 | 0.293 | -0.002 | 7.42 | -39.16 | 0.19 | 0.387 |
| cikis_iz | 491 | 0.027 | 0.049 | 0.0 | 5.77 | -34.32 | 0.17 | 0.34 |
| cikis_iz_tp1 | 432 | 0.164 | 0.319 | -0.011 | 6.56 | -40.22 | 0.16 | 0.353 |
| cikis_hedef | 444 | 0.147 | 0.256 | 0.025 | 8.5 | -32.48 | 0.26 | 0.414 |
| retest | 405 | 0.142 | 0.3 | -0.039 | 5.43 | -43.9 | 0.12 | 0.321 |
| fitil_stop | 498 | 0.016 | 0.101 | -0.083 | -4.55 | -55.0 | -0.08 | 0.127 |

**Karar:** hiçbir varyant dört şartı birden geçmedi → mevcut kural korunur

## selin

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 296 | 0.174 | 0.104 | 0.243 | 2.14 | -21.15 | 0.1 | 0.231 |
| cikis_iz | 304 | 0.077 | 0.003 | 0.15 | -1.03 | -26.55 | -0.04 | 0.131 |
| cikis_iz_tp1 | 296 | 0.203 | 0.113 | 0.291 | 0.62 | -22.03 | 0.03 | 0.184 |
| cikis_hedef | 296 | 0.172 | 0.119 | 0.225 | 9.51 | -21.17 | 0.45 | 0.51 |
| ttm | 261 | 0.175 | 0.099 | 0.263 | 10.06 | -24.31 | 0.41 | 0.525 |

**Karar:** ttm: doğrulama +0.263R (taban +0.243R), yıllık %10.06 (taban %2.14), MAR 0.41 (taban 0.1)

## murat

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 1111 | -0.064 | -0.104 | -0.007 | -11.59 | -63.18 | -0.18 | 0.028 |
| cikis_iz | 1153 | -0.055 | -0.091 | -0.001 | -13.2 | -62.27 | -0.21 | 0.039 |
| cikis_iz_tp1 | 1099 | -0.068 | -0.114 | -0.003 | -11.18 | -60.81 | -0.18 | 0.053 |
| cikis_hedef | 1182 | -0.045 | -0.088 | 0.015 | -10.69 | -65.57 | -0.16 | 0.035 |

**Karar:** cikis_hedef: doğrulama +0.015R (taban -0.007R), yıllık %-10.69 (taban %-11.59), MAR -0.16 (taban -0.18)

## Firma geneli kurallar (taban işlemleriyle, tek havuz)

| Kural | Yıllık % | Maks. düşüş % | MAR | İşlem | Eğitim R | Doğrulama R |
|---|---|---|---|---|---|---|
| taban (ayıda long yarım, sınıf kuralı yok) | 21.95 | -40.37 | 0.54 | 689 | 0.105 | 0.117 |
| ayıda long YASAK | 26.16 | -35.13 | 0.74 | 628 | 0.133 | 0.164 |
| Genel Müdür kanıt sınıfı açık (B yarım, C gölge) | 20.15 | -35.85 | 0.56 | 861 | 0.095 | 0.132 |
| ayıda long yasak + kanıt sınıfı | 26.39 | -35.6 | 0.74 | 806 | 0.107 | 0.192 |
| sadece C gölge (B tam boyut) | 17.45 | -38.12 | 0.46 | 673 | 0.071 | 0.154 |
| B tam, C yarım | 17.5 | -38.89 | 0.45 | 764 | 0.097 | 0.034 |
| ayıda long yasak + sadece C gölge | 21.46 | -34.97 | 0.61 | 615 | 0.096 | 0.196 |

Kanıt sınıfına göre işlemler: A 1787 (ort. 0.031R), B 897 (ort. 0.074R), C 269 (ort. -0.039R)

Uyarı: coin evreni bugün hâlâ işlem gören coinler (batanlar yok) → sonuçlar iyimser tarafta.
