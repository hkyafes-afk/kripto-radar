# v9 Faz 1 — 5 yıllık varyant yarışı (2026-10-26 19:20 UTC)

27 coin · 2021-10-27 → bugün · eğitim/doğrulama ayrımı 2025-04-27 · 17 deneme (Deflated Sharpe bütün denemelere göre) · 291 sn

Benimseme: doğrulamada ≥ +0,02R iyi, eğitimde kötü değil, bileşik büyüme düşmüyor, MAR ≥ %90 taban, yeterli işlem.

## kemal

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 843 | 0.105 | 0.101 | 0.11 | 11.74 | -38.84 | 0.3 | 0.451 |
| adx25 | 491 | 0.077 | 0.021 | 0.158 | 7.68 | -38.67 | 0.2 | 0.359 |

**Karar:** hiçbir varyant dört şartı birden geçmedi → mevcut kural korunur

## kaan

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 334 | 0.239 | 0.413 | 0.038 | 10.71 | -33.54 | 0.32 | 0.443 |
| cikis_iz | 380 | 0.059 | 0.089 | 0.02 | 6.27 | -30.7 | 0.2 | 0.317 |
| cikis_iz_tp1 | 334 | 0.255 | 0.453 | 0.027 | 11.66 | -35.43 | 0.33 | 0.445 |
| cikis_hedef | 344 | 0.224 | 0.348 | 0.08 | 12.76 | -31.09 | 0.41 | 0.503 |
| retest | 307 | 0.202 | 0.407 | -0.042 | 23.14 | -31.26 | 0.74 | 0.744 |
| fitil_stop | 384 | 0.076 | 0.206 | -0.079 | -2.23 | -52.8 | -0.04 | 0.128 |

**Karar:** hiçbir varyant dört şartı birden geçmedi → mevcut kural korunur

## selin

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 225 | 0.146 | 0.065 | 0.224 | 0.29 | -24.35 | 0.01 | 0.143 |
| cikis_iz | 230 | 0.05 | -0.015 | 0.112 | -3.01 | -33.99 | -0.09 | 0.065 |
| cikis_iz_tp1 | 225 | 0.17 | 0.086 | 0.25 | -1.86 | -29.1 | -0.06 | 0.098 |
| cikis_hedef | 225 | 0.157 | 0.083 | 0.227 | 7.22 | -23.1 | 0.31 | 0.381 |
| ttm | 198 | 0.172 | 0.11 | 0.248 | 10.52 | -26.18 | 0.4 | 0.496 |

**Karar:** ttm: doğrulama +0.248R (taban +0.224R), yıllık %10.52 (taban %0.29), MAR 0.4 (taban 0.01)

## murat

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 855 | -0.034 | -0.072 | 0.02 | -7.67 | -54.56 | -0.14 | 0.039 |
| cikis_iz | 882 | -0.021 | -0.061 | 0.038 | -7.92 | -51.15 | -0.15 | 0.058 |
| cikis_iz_tp1 | 844 | -0.014 | -0.057 | 0.048 | -4.44 | -47.02 | -0.09 | 0.095 |
| cikis_hedef | 909 | -0.025 | -0.061 | 0.025 | -5.14 | -55.14 | -0.09 | 0.065 |

**Karar:** cikis_iz_tp1: doğrulama +0.048R (taban +0.020R), yıllık %-4.44 (taban %-7.67), MAR -0.09 (taban -0.14)

## Firma geneli kurallar (taban işlemleriyle, tek havuz)

| Kural | Yıllık % | Maks. düşüş % | MAR | İşlem | Eğitim R | Doğrulama R |
|---|---|---|---|---|---|---|
| taban (ayıda long yarım, sınıf kuralı yok) | 29.18 | -36.29 | 0.8 | 636 | 0.102 | 0.214 |
| ayıda long YASAK | 25.06 | -33.15 | 0.76 | 591 | 0.138 | 0.193 |
| Genel Müdür kanıt sınıfı açık (B yarım, C gölge) | 23.2 | -31.44 | 0.74 | 838 | 0.1 | 0.162 |
| ayıda long yasak + kanıt sınıfı | 25.93 | -37.02 | 0.7 | 773 | 0.127 | 0.191 |
| sadece C gölge (B tam boyut) | 23.03 | -34.69 | 0.66 | 647 | 0.084 | 0.172 |
| ayıda long yasak + sadece C gölge | 25.51 | -33.17 | 0.77 | 592 | 0.131 | 0.165 |

Kanıt sınıfına göre işlemler: A 1334 (ort. 0.067R), B 720 (ort. 0.115R), C 203 (ort. 0.0R)

Uyarı: coin evreni bugün hâlâ işlem gören coinler (batanlar yok) → sonuçlar iyimser tarafta.
