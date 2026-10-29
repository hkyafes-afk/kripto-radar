# v9 Faz 1 — 5 yıllık varyant yarışı (2026-10-29 21:00 UTC)

27 coin · 2021-10-30 → bugün · eğitim/doğrulama ayrımı 2025-04-30 · 20 deneme (Deflated Sharpe bütün denemelere göre) · 286 sn

Benimseme: doğrulamada ≥ +0,02R iyi, eğitimde kötü değil, bileşik büyüme düşmüyor, MAR ≥ %90 taban, yeterli işlem.

## kemal

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 166 | 0.289 | 0.321 | 0.229 | 8.48 | -12.7 | 0.67 | 0.638 |
| adx25 | 99 | 0.107 | 0.042 | 0.213 | 2.05 | -14.83 | 0.14 | 0.252 |
| adx15 | 157 | 0.264 | 0.307 | 0.189 | 7.15 | -12.74 | 0.56 | 0.56 |
| adx18 | 146 | 0.2 | 0.235 | 0.135 | 5.32 | -12.77 | 0.42 | 0.452 |
| adx_up | 146 | 0.327 | 0.339 | 0.303 | 8.19 | -13.41 | 0.61 | 0.637 |

**Karar:** hiçbir varyant dört şartı birden geçmedi → mevcut kural korunur

## kaan

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 95 | 0.741 | 0.933 | 0.477 | 12.28 | -8.54 | 1.44 | 0.969 |
| cikis_iz | 99 | 0.47 | 0.535 | 0.378 | 7.97 | -7.76 | 1.03 | 0.895 |
| cikis_iz_tp1 | 95 | 0.845 | 1.071 | 0.535 | 14.19 | -9.09 | 1.56 | 0.978 |
| cikis_hedef | 99 | 0.681 | 0.839 | 0.458 | 11.93 | -8.5 | 1.4 | 0.972 |
| retest | 69 | 0.409 | 0.668 | 0.005 | 4.74 | -11.02 | 0.43 | 0.535 |
| fitil_stop | 104 | 0.748 | 0.968 | 0.459 | 12.67 | -9.73 | 1.3 | 0.929 |

**Karar:** cikis_iz_tp1: doğrulama +0.535R (taban +0.477R), yıllık %14.19 (taban %12.28), MAR 1.56 (taban 1.44)

## selin

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 61 | 0.59 | 0.524 | 0.673 | 11.65 | -14.35 | 0.81 | 0.845 |
| cikis_iz | 64 | 0.496 | 0.505 | 0.486 | 8.46 | -11.43 | 0.74 | 0.788 |
| cikis_iz_tp1 | 62 | 0.771 | 0.676 | 0.893 | 14.89 | -14.44 | 1.03 | 0.887 |
| cikis_hedef | 63 | 0.562 | 0.53 | 0.605 | 12.08 | -14.26 | 0.85 | 0.863 |
| ttm | 53 | 0.483 | 0.325 | 0.723 | 9.03 | -14.35 | 0.63 | 0.701 |

**Karar:** cikis_iz_tp1: doğrulama +0.893R (taban +0.673R), yıllık %14.89 (taban %11.65), MAR 1.03 (taban 0.81)

## murat

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 102 | 0.156 | 0.138 | 0.201 | 1.49 | -4.59 | 0.32 | 0.413 |
| cikis_iz | 113 | 0.399 | 0.372 | 0.461 | 4.37 | -5.69 | 0.77 | 0.758 |
| cikis_iz_tp1 | 104 | 0.312 | 0.275 | 0.394 | 3.06 | -6.63 | 0.46 | 0.581 |
| cikis_hedef | 101 | 0.178 | 0.158 | 0.233 | 1.68 | -4.46 | 0.38 | 0.514 |

**Karar:** cikis_iz: doğrulama +0.461R (taban +0.201R), yıllık %4.37 (taban %1.49), MAR 0.77 (taban 0.32)

## Firma geneli kurallar (taban işlemleriyle, tek havuz)

| Kural | Yıllık % | Maks. düşüş % | MAR | İşlem | Eğitim R | Doğrulama R |
|---|---|---|---|---|---|---|
| taban (ayıda long yarım, sınıf kuralı yok) | 15.55 | -20.5 | 0.76 | 349 | 0.349 | 0.213 |
| ayıda long YASAK | 15.55 | -20.5 | 0.76 | 349 | 0.349 | 0.213 |
| Genel Müdür kanıt sınıfı açık (B yarım, C gölge) | 13.92 | -22.54 | 0.62 | 353 | 0.357 | 0.224 |
| ayıda long yasak + kanıt sınıfı | 13.92 | -22.54 | 0.62 | 353 | 0.357 | 0.224 |
| sadece C gölge (B tam boyut) | 15.26 | -21.47 | 0.71 | 344 | 0.333 | 0.22 |
| B tam, C yarım | 15.33 | -20.81 | 0.74 | 350 | 0.35 | 0.213 |
| ayıda long yasak + sadece C gölge | 15.26 | -21.47 | 0.71 | 344 | 0.333 | 0.22 |

Kanıt sınıfına göre işlemler: A 347 (ort. 0.355R), B 69 (ort. 0.61R), C 8 (ort. 0.632R)

Uyarı: coin evreni bugün hâlâ işlem gören coinler (batanlar yok) → sonuçlar iyimser tarafta.
