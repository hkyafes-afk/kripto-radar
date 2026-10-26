# Analist-Hakem modeli — 2026-10-26 19:36 UTC

2675 test işlemi + 0 canlı/gölge işlem · 27 coin · ridge regresyon (hedef R), zaman sıralı %70/%30

| Grup | İşlem | Doğrulama ilişkisi | Boyutla kazanç (R/işlem) | Söz hakkı | En etkili özellikler |
|---|---|---|---|---|---|
| _hepsi | 2675 | 0.092 | +0.031 | evet | rs_rank +0.139, rs -0.104, rsi +0.101, dist50 -0.091 |
| kemal | 843 | -0.011 | +0.044 | hayır | dist50 -0.257, rs_rank +0.209, rsi +0.162, dist200 -0.112 |
| kaan | 585 | 0.03 | +0.029 | hayır | side +0.189, rs_rank +0.141, rs -0.122, rsi -0.060 |
| selin | 364 | 0.093 | +0.004 | hayır | rsi -0.321, range_atr +0.282, rs_rank +0.222, bbw_rank +0.215 |
| murat | 844 | 0.064 | +0.016 | hayır | range_atr +0.119, vol_ratio -0.118, dist200 -0.117, bbw_rank -0.111 |

Söz hakkı: doğrulamada ilişki ≥ 0,05 ve önerilen boyutla işlem başına ≥ +0,02R. Yoksa model yalnızca tahminini yazar.

**Gözcü kontrolü (göreli güç, trend tarzı, 2636 işlem):** en güçlü %20 → 0.064R (695), orta → 0.062R, en zayıf %40 → -0.022R (784). Kural: **momentum** (momentum = güçlüde büyüt, ters = güçlüde küçült, nötr = göreli güç boyuta dokunmaz).
