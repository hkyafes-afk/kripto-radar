# v9 Faz 1 — 5 yıllık varyant yarışı (2026-10-29 22:38 UTC)

656 coin · 2020-10-28 → bugün · eğitim/doğrulama ayrımı 2025-01-07 · 9 deneme (Deflated Sharpe bütün denemelere göre) · 430 sn

Benimseme: doğrulamada ≥ +0,02R iyi, eğitimde kötü değil, bileşik büyüme düşmüyor, MAR ≥ %90 taban, yeterli işlem.

## kemal

| Varyant | İşlem | Ort. R | Eğitim R | Doğrulama R | Yıllık % | Maks. düşüş % | MAR | DSR |
|---|---|---|---|---|---|---|---|---|
| taban (mevcut) | 373 | 0.275 | 0.285 | 0.254 | 12.3 | -26.37 | 0.47 | 0.954 |
| adx25 | 209 | 0.253 | 0.212 | 0.338 | 8.1 | -18.93 | 0.43 | 0.906 |
| adx15 | 345 | 0.265 | 0.273 | 0.247 | 11.82 | -28.46 | 0.42 | 0.949 |
| adx18 | 310 | 0.257 | 0.272 | 0.223 | 11.04 | -26.62 | 0.41 | 0.942 |
| adx_up | 321 | 0.322 | 0.316 | 0.335 | 11.72 | -26.61 | 0.44 | 0.951 |
| cikis_ma_tampon | 362 | 0.244 | 0.252 | 0.229 | 12.13 | -26.86 | 0.45 | 0.951 |
| cikis_ma_2kapanis | 364 | 0.246 | 0.257 | 0.222 | 11.57 | -25.76 | 0.45 | 0.943 |
| cikis_ma20 | 428 | 0.232 | 0.255 | 0.186 | 13.86 | -16.91 | 0.82 | 0.979 |
| cikis_don20 | 351 | 0.258 | 0.222 | 0.338 | 12.45 | -24.75 | 0.5 | 0.957 |

**Karar:** hiçbir varyant dört şartı birden geçmedi → mevcut kural korunur

## Firma geneli kurallar (taban işlemleriyle, tek havuz)

| Kural | Yıllık % | Maks. düşüş % | MAR | İşlem | Eğitim R | Doğrulama R |
|---|---|---|---|---|---|---|
| taban (ayıda long yarım, sınıf kuralı yok) | 12.3 | -26.37 | 0.47 | 311 | 0.245 | 0.271 |
| ayıda long YASAK | 12.3 | -26.37 | 0.47 | 311 | 0.245 | 0.271 |
| Genel Müdür kanıt sınıfı açık (B yarım, C gölge) | 12.18 | -17.98 | 0.68 | 312 | 0.216 | 0.376 |
| ayıda long yasak + kanıt sınıfı | 12.18 | -17.98 | 0.68 | 312 | 0.216 | 0.376 |
| sadece C gölge (B tam boyut) | 11.9 | -25.75 | 0.46 | 294 | 0.206 | 0.373 |
| B tam, C yarım | 12.11 | -26.07 | 0.46 | 313 | 0.242 | 0.292 |
| ayıda long yasak + sadece C gölge | 11.9 | -25.75 | 0.46 | 294 | 0.206 | 0.373 |

Kanıt sınıfına göre işlemler: A 249 (ort. 0.297R), B 104 (ort. 0.231R), C 20 (ort. 0.236R)

Kaynak: binance-vision {'coin': 656, 'batan': 24, 'ilk_gun': '2020-01-01', 'son_gun': '2026-10-27', 'top_n': 100} · evren gün gün kuruldu (batan coinler dahil, hacme göre ilk N).
