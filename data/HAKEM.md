# Analist-Hakem modeli — 2026-10-29 02:15 UTC

474 test işlemi + 22 canlı/gölge işlem · 26 coin · ridge regresyon (hedef R), zaman sıralı %70/%30

| Grup | İşlem | Doğrulama ilişkisi | Boyutla kazanç (R/işlem) | Söz hakkı | En etkili özellikler |
|---|---|---|---|---|---|
| _hepsi | 496 | 0.073 | +0.059 | evet | rs_rank +0.431, dist50 -0.261, rs -0.207, age_cls +0.204 |

Söz hakkı: doğrulamada ilişki ≥ 0,05 ve önerilen boyutla işlem başına ≥ +0,02R. Yoksa model yalnızca tahminini yazar.

**Gözcü kontrolü (göreli güç, trend tarzı, 485 işlem):** en güçlü %20 → 0.434R (70), orta → 0.446R, en zayıf %40 → 0.011R (177). Kural: **momentum** (momentum = güçlüde büyüt, ters = güçlüde küçült, nötr = göreli güç boyuta dokunmaz).
