# Analist-Hakem modeli — 2026-10-27 02:03 UTC

2609 test işlemi + 0 canlı/gölge işlem · 27 coin · ridge regresyon (hedef R), zaman sıralı %70/%30

| Grup | İşlem | Doğrulama ilişkisi | Boyutla kazanç (R/işlem) | Söz hakkı | En etkili özellikler |
|---|---|---|---|---|---|
| _hepsi | 2609 | 0.082 | +0.026 | evet | rs_rank +0.146, rs -0.105, rsi +0.102, dist50 -0.094 |
| kemal | 824 | -0.014 | +0.029 | hayır | dist50 -0.247, rs_rank +0.223, rsi +0.157, dist200 -0.121 |
| kaan | 568 | 0.052 | +0.022 | evet | side +0.186, rs_rank +0.147, rs -0.120, rsi -0.069 |
| selin | 357 | 0.1 | +0.007 | hayır | rsi -0.326, range_atr +0.295, rs_rank +0.223, bbw_rank +0.211 |
| murat | 822 | 0.08 | +0.022 | evet | dist200 -0.115, bbw_rank -0.111, range_atr +0.107, vol_ratio -0.107 |

Söz hakkı: doğrulamada ilişki ≥ 0,05 ve önerilen boyutla işlem başına ≥ +0,02R. Yoksa model yalnızca tahminini yazar.

**Gözcü kontrolü (göreli güç, trend tarzı, 2571 işlem):** en güçlü %20 → 0.09R (697), orta → 0.055R, en zayıf %40 → -0.024R (752). Kural: **momentum** (momentum = güçlüde büyüt, ters = güçlüde küçült, nötr = göreli güç boyuta dokunmaz).
