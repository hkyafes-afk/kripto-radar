# v9 Faz 1 — 5 yıllık varyant yarışı (2026-10-29 22:34 UTC)

656 coin · 2020-10-28 → bugün · eğitim/doğrulama ayrımı 2025-01-07 · 9 deneme (Deflated Sharpe bütün denemelere göre) · 241 sn

Benimseme: doğrulamada ≥ +0,02R iyi, eğitimde kötü değil, bileşik büyüme düşmüyor, MAR ≥ %90 taban, yeterli işlem.

## kemal

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 183 | 0.127 | 0.199 | 0.002 | 3.55 | -17.79 | 0.2 | 0.744 |
| adx25 | 107 | 0.103 | 0.144 | 0.035 | 1.61 | -13.09 | 0.12 | 0.636 |
| adx15 | 175 | 0.11 | 0.2 | -0.042 | 2.68 | -18.14 | 0.15 | 0.689 |
| adx18 | 156 | 0.102 | 0.165 | -0.018 | 2.33 | -17.64 | 0.13 | 0.668 |
| adx_up | 162 | 0.111 | 0.176 | -0.016 | 2.55 | -18.28 | 0.14 | 0.681 |
| cikis_ma_tampon | 180 | 0.084 | 0.151 | -0.031 | 2.12 | -21.06 | 0.1 | 0.643 |
| cikis_ma_2kapanis | 181 | 0.084 | 0.154 | -0.04 | 2.16 | -20.74 | 0.1 | 0.646 |
| cikis_ma20 | 207 | 0.115 | 0.138 | 0.074 | 3.56 | -15.8 | 0.23 | 0.77 |
| cikis_don20 | 177 | 0.107 | 0.092 | 0.135 | 3.21 | -21.16 | 0.15 | 0.72 |

**Karar:** hiçbir varyant dört şartı birden geçmedi → mevcut kural korunur

## Firma geneli kurallar (taban işlemleriyle, tek havuz)

| Kural | Yıllık % | Maks. düşüş % | MAR | İşlem | Eğitim R | Doğrulama R |
|---|---|---|---|---|---|---|
| taban (ayıda long yarım, sınıf kuralı yok) | 3.55 | -17.79 | 0.2 | 182 | 0.211 | 0.002 |
| ayıda long YASAK | 3.55 | -17.79 | 0.2 | 182 | 0.211 | 0.002 |
| Genel Müdür kanıt sınıfı açık (B yarım, C gölge) | 3.45 | -12.68 | 0.27 | 172 | 0.139 | 0.059 |
| ayıda long yasak + kanıt sınıfı | 3.45 | -12.68 | 0.27 | 172 | 0.139 | 0.059 |
| sadece C gölge (B tam boyut) | 2.59 | -17.1 | 0.15 | 172 | 0.139 | 0.059 |
| B tam, C yarım | 3.07 | -17.46 | 0.18 | 182 | 0.211 | 0.002 |
| ayıda long yasak + sadece C gölge | 2.59 | -17.1 | 0.15 | 172 | 0.139 | 0.059 |

Kanıt sınıfına göre işlemler: A 142 (ort. 0.192R), B 31 (ort. -0.301R), C 10 (ort. 0.535R)

Kaynak: binance-vision {'coin': 656, 'batan': 24, 'ilk_gun': '2020-01-01', 'son_gun': '2026-10-27', 'top_n': 30} · evren gün gün kuruldu (batan coinler dahil, hacme göre ilk N).
