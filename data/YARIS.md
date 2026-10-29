# v9 Faz 1 — 5 yıllık varyant yarışı (2026-10-29 20:38 UTC)

30 coin · 2021-10-30 → bugün · eğitim/doğrulama ayrımı 2025-04-30 · 20 deneme (Deflated Sharpe bütün denemelere göre) · 471 sn

Benimseme: doğrulamada ≥ +0,02R iyi, eğitimde kötü değil, bileşik büyüme düşmüyor, MAR ≥ %90 taban, yeterli işlem.

## kemal

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 189 | 0.249 | 0.279 | 0.194 | 8.44 | -13.67 | 0.62 | 0.576 |
| adx25 | 108 | 0.077 | -0.018 | 0.234 | 1.65 | -16.48 | 0.1 | 0.205 |
| adx15 | 178 | 0.211 | 0.246 | 0.149 | 6.53 | -13.91 | 0.47 | 0.465 |
| adx18 | 163 | 0.144 | 0.167 | 0.102 | 4.27 | -14.29 | 0.3 | 0.338 |
| adx_up | 165 | 0.277 | 0.286 | 0.26 | 7.69 | -13.87 | 0.55 | 0.539 |

**Karar:** hiçbir varyant dört şartı birden geçmedi → mevcut kural korunur

## kaan

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 106 | 0.697 | 0.883 | 0.445 | 12.59 | -10.3 | 1.22 | 0.941 |
| cikis_iz | 110 | 0.419 | 0.48 | 0.335 | 7.35 | -7.27 | 1.01 | 0.818 |
| cikis_iz_tp1 | 106 | 0.786 | 1.005 | 0.489 | 14.43 | -10.65 | 1.35 | 0.955 |
| cikis_hedef | 110 | 0.654 | 0.804 | 0.446 | 12.37 | -9.63 | 1.28 | 0.949 |
| retest | 80 | 0.433 | 0.661 | 0.093 | 5.87 | -12.91 | 0.45 | 0.575 |
| fitil_stop | 116 | 0.664 | 0.865 | 0.408 | 12.59 | -13.05 | 0.96 | 0.878 |

**Karar:** cikis_iz_tp1: doğrulama +0.489R (taban +0.445R), yıllık %14.43 (taban %12.59), MAR 1.35 (taban 1.22)

## selin

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 71 | 0.578 | 0.52 | 0.653 | 10.18 | -14.04 | 0.73 | 0.751 |
| cikis_iz | 74 | 0.511 | 0.502 | 0.522 | 10.05 | -15.65 | 0.64 | 0.81 |
| cikis_iz_tp1 | 72 | 0.754 | 0.668 | 0.867 | 14.99 | -14.35 | 1.04 | 0.854 |
| cikis_hedef | 74 | 0.536 | 0.51 | 0.572 | 14.04 | -13.89 | 1.01 | 0.895 |
| ttm | 64 | 0.505 | 0.387 | 0.691 | 10.51 | -14.04 | 0.75 | 0.753 |

**Karar:** cikis_iz_tp1: doğrulama +0.867R (taban +0.653R), yıllık %14.99 (taban %10.18), MAR 1.04 (taban 0.73)

## murat

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 115 | 0.126 | 0.081 | 0.241 | 1.31 | -4.77 | 0.27 | 0.335 |
| cikis_iz | 125 | 0.342 | 0.293 | 0.453 | 4.11 | -5.99 | 0.69 | 0.687 |
| cikis_iz_tp1 | 117 | 0.252 | 0.193 | 0.391 | 2.74 | -6.66 | 0.41 | 0.482 |
| cikis_hedef | 114 | 0.141 | 0.098 | 0.261 | 1.47 | -4.66 | 0.32 | 0.408 |

**Karar:** cikis_iz: doğrulama +0.453R (taban +0.241R), yıllık %4.11 (taban %1.31), MAR 0.69 (taban 0.27)

## Firma geneli kurallar (taban işlemleriyle, tek havuz)

| Kural | Yıllık % | Maks. düşüş % | MAR | İşlem | Eğitim R | Doğrulama R |
|---|---|---|---|---|---|---|
| taban (ayıda long yarım, sınıf kuralı yok) | 12.58 | -23.34 | 0.54 | 380 | 0.273 | 0.2 |
| ayıda long YASAK | 12.58 | -23.34 | 0.54 | 380 | 0.273 | 0.2 |
| Genel Müdür kanıt sınıfı açık (B yarım, C gölge) | 11.22 | -23.12 | 0.49 | 386 | 0.283 | 0.216 |
| ayıda long yasak + kanıt sınıfı | 11.22 | -23.12 | 0.49 | 386 | 0.283 | 0.216 |
| sadece C gölge (B tam boyut) | 12.17 | -22.4 | 0.54 | 375 | 0.254 | 0.207 |
| B tam, C yarım | 12.41 | -23.26 | 0.53 | 381 | 0.269 | 0.201 |
| ayıda long yasak + sadece C gölge | 12.17 | -22.4 | 0.54 | 375 | 0.254 | 0.207 |

Kanıt sınıfına göre işlemler: A 403 (ort. 0.316R), B 70 (ort. 0.629R), C 8 (ort. 0.632R)

Uyarı: coin evreni bugün hâlâ işlem gören coinler (batanlar yok) → sonuçlar iyimser tarafta.
