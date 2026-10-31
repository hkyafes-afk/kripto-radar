# Analist-Hakem modeli — 2026-10-31 02:15 UTC

1264 test işlemi + 98 canlı/gölge işlem · 29 coin · ridge regresyon (hedef R), zaman sıralı %70/%30

| Grup | İşlem | Doğrulama ilişkisi | Boyutla kazanç (R/işlem) | Söz hakkı | En etkili özellikler |
|---|---|---|---|---|---|
| _hepsi | 1362 | 0.116 | +0.037 | evet | rs_rank +0.325, rs -0.271, dist50 -0.106, range_atr +0.102 |
| kemal | 420 | 0.051 | +0.015 | hayır | rs_rank +0.449, rs -0.347, rsi +0.233, vol_ratio +0.211 |
| murat | 457 | -0.025 | -0.009 | hayır | rs_rank +0.145, vol_ratio -0.144, range_atr +0.094, bbw_rank -0.080 |

Söz hakkı: doğrulamada ilişki ≥ 0,05 ve önerilen boyutla işlem başına ≥ +0,02R. Yoksa model yalnızca tahminini yazar.

**Gözcü kontrolü (göreli güç, trend tarzı, 1342 işlem):** en güçlü %20 → 0.249R (277), orta → 0.128R, en zayıf %40 → -0.049R (426). Kural: **momentum** (momentum = güçlüde büyüt, ters = güçlüde küçült, nötr = göreli güç boyuta dokunmaz).
