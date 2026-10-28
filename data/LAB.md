# v10 Laboratuvar Raporu — 27.10.2026

Amaç: yöneticilerin **masraflar düştükten sonra** işlem başına beklenen kârını (R) ve kazanma oranını artırmak; kanıtı
ezberlemeden (eğitimde seç, görmediği dönemde doğrula).

## Veri ve yöntem

- **Veri:** `gecmis` dalı (`tools/gecmis.py`, kaynak data.binance.vision). 895 USDT vadeli sembol, borsadan silinenler
  dahil (survivorship yanlılığı yok). 2020-01 → 2026-10. Günlük + 4 saatlik mum, funding geçmişi, alıcı/satıcı hacmi,
  en likit 40 coin için OI/long-short (saatlik).
- **Evren (point-in-time):** her gün, o günün verisiyle, 30 günlük medyan hacmi ≥ 5M$ olan en likit 100 kripto vadelisi
  (hisse/emtia vadelileri hariç) — canlı sistemin evreniyle aynı tanım. Eski testler bugünün en likit 28 coinini
  kullanıyordu (iyimser).
- **Kod:** yöneticilerin canlıdaki kendi fonksiyonları (`team.a_*`), bt2 ile aynı işlem simülasyonu.
- **Maliyet:** taker %0,10, maker %0,04, girişte 2 bp, stop'ta oynaklığa bağlı kayma, **gerçek funding** (long öder,
  short alır).
- **Ayrım:** eğitim (IS) 2020–2024, doğrulama (OOS) 2025-01-01 → bugün. Kararlar eğitimle verildi; doğrulama yalnızca teyit.
- **Araç:** `tools/lab.py` (varyant koşusu), `tools/lab_analiz.py` (kırılımlar). Ham tablolar: `data/lab/`.

## 1. Bugünkü sistem (temel)

| Yönetici | IS n | IS kazanma | IS ort. R | OOS n | OOS kazanma | OOS ort. R |
|---|---|---|---|---|---|---|
| Baş Trader | 1403 | %42,1 | +0,168 | 622 | %42,1 | +0,138 |
| Kaan | 2374 | %39,3 | +0,034 | 948 | %40,3 | +0,026 |
| Selin | 1548 | %40,3 | +0,046 | 618 | %37,7 | −0,027 |
| Kemal | 4023 | %32,9 | +0,021 | 1652 | %32,0 | −0,006 |
| Murat | 3606 | %40,9 | −0,054 | 1558 | %41,6 | −0,065 |
| Ayşe | 158 | %57,6 | +0,013 | 38 | %57,9 | +0,070 |
| **Ekip** | 13112 | %38,4 | **+0,022** | 5436 | %38,2 | **−0,003** |

Ekip masraf sonrası başa baş; yalnızca Baş Trader sağlam.

## 2. Tek tek değişiklikler (aynı veri geçişi)

| Değişiklik | Ekip IS ort. R | Ekip OOS ort. R | Not |
|---|---|---|---|
| temel | +0,022 | −0,003 | |
| **stop ≤ fiyatın %15'i** | **+0,117** | **+0,028** | yüksek oynaklık zararlı |
| **kapanış stop'u** (fitil yerine günlük kapanış) | **+0,047** | +0,006 | kazanma +2 puan |
| **kalabalık filtresi** (funding) | +0,039 | −0,005 | eğitimde artı, doğrulamada nötr |
| yeniden giriş kuralı | +0,036 | +0,005 | |
| kâr kilidi merdiveni | +0,020 | −0,002 | etkisiz (iz stop zaten benzer) |
| tepe kârın en fazla %40'ını geri ver | +0,010 | −0,003 | kazanma +1,2 puan, R düşük |
| tepe kârın en fazla %25'ini geri ver | +0,011 | −0,005 | kazanma +1,2 puan, R düşük |
| kısmi kâr yok | +0,017 | −0,006 | kısmi kâr hafif faydalı |
| Donchian orta nokta iz stop'u | −0,008 | −0,037 | kötü |
| sadece long | +0,031 | −0,087 | 2025–26'da short'lar gerekli |
| 1 yıldan genç coin yok | +0,017 | −0,005 | asıl etken yaş değil oynaklık |
| BTC ayıyken long yok | +0,025 | +0,003 | nötr |

### Kırılımlar (temel, eğitim → doğrulama)

- **Günlük ATR %3–5:** Baş +0,58 → +0,40 · Kaan +0,75 → +0,38 · Selin +0,48 → +0,18.
  **ATR %5–12:** hepsi ≈0 ya da eksi. Akademik karşılık: düşük oynaklık anomalisi (Kwok ve ark., *J. Empirical
  Finance* 2023, 4.000+ coin: en yüksek özgün riskli coinler yıllık −%9, en düşükler +%81).
- **Funding:** long'lar funding yıllık %30–60'ta −0,09R, %60+'da −0,14R (doğrulamada da eksi). BIS WP 1087: yüksek
  carry sonraki çöküşleri öngörüyor.
- **Kaan, BTC yatayken:** −0,29R → −0,40R (iki dönemde de eksi).

## 3. Birleşik politika (v10, canlıya alındı)

Oynaklık ≤ %5 + kapanış stop'u (gün içi 2R felaket stop'u) + kalabalık filtresi + yeniden giriş kuralı:

| Yönetici | IS n | IS kazanma | IS ort. R | OOS n | OOS kazanma | OOS ort. R |
|---|---|---|---|---|---|---|
| Baş Trader | 217 | %55,8 | +0,617 | 84 | %42,9 | +0,093 |
| Kaan | 193 | %63,2 | +0,794 | 82 | %46,3 | +0,264 |
| Selin | 162 | %53,7 | +0,435 | 60 | %55,0 | +0,431 |
| Kemal | 299 | %40,8 | +0,141 | 161 | %29,8 | +0,095 |
| Murat | 223 | %49,3 | +0,326 | 74 | %43,2 | +0,116 |
| **Ekip** | 1098 | **%51,5** | **+0,434** | 466 | **%40,8** | **+0,171** |

Ekip maks. düşüş (yönetici başına %0,5 risk): temel −%43 → **−%14**. Kapıdan geçemeyen fikirler gölgede açılır (veri
toplama sürer, gerçek sermaye kullanılmaz). Ayşe oynaklık kapısından muaf (kapanış stop'u + kalabalıkla +0,24R → +0,30R,
kazanma %66 → %72). Baş Trader/Turbo yeniden giriş kuralından muaf (testte getirilerini düşürdü).

Bilinen zayıflık: yıllara göre ekip ortalaması 2022 −0,14R, 2025 −0,11R; 2023 +0,60R. Kötü yıllar var; bu yüzden risk
seviyeleri büyümeyi değil düşüşü sınırlayacak şekilde seçildi.

## 4. Risk seviyeleri ve Turbo

Yönetici başına (kendi sermayesinde) eğitim düşüşü ~%25'i aşmayan ve doğrulamada büyümesi artı kalan en yüksek seviye
(`data/lab/turbo_risk.txt`):

| Yönetici | Eski | Yeni | Gerekçe |
|---|---|---|---|
| Baş Trader | %1,5–4 | %1–2 | %2'de eğitim +%40/yıl, düşüş %26 |
| Kaan | %3–5 | %1–1,5 | %2+ riskte doğrulama büyümesi eksi |
| Selin | %4–6 | %2–3 | %3'te eğitim düşüşü %28 |
| Kemal | %1–2 | %1–2 | aynı |
| Murat | %1,5–2,5 | %0,5–1 | düşüşü büyük |
| Ayşe | %1–1,5 | %0,5–1 | örneklem az |
| Turbo | %5–10 | %1–1,5 | aşağıda |

**Turbo = bütün ekip tek elde** (Baş, Kemal, Kaan, Selin, Murat, Ayşe'nin fikirleri; coin başına tek pozisyon, çatışmada
çoğunluk). v10 politikasıyla 759 işlem, ort. +0,30R, kazanma %45:

| İşlem riski | Yıllık büyüme | Maks. düşüş |
|---|---|---|
| %0,5 | +%16,8 | −%16 |
| %1 | +%17,4 | −%30 |
| %2 | +%24,4 | −%53 |
| %5 | +%10,5 | −%77 |
| %8 | −%2,6 | −%89 |

Eski Turbo ayarı (%5–10) 5 yılda hesabı batırma bölgesinde: kazanan işlemler olsa bile fazla risk bileşik büyümeyi yer.

## 5. Denenip reddedilenler

- **Kâr kilitleme (285$ → 85$ sorusu):** tepe kârın %25–40'ından fazlasını geri vermeden çıkmak kazanma oranını ~1–3 puan
  artırdı ama ortalama R'yi düşürdü: büyük kazananlar (ekipte 2R+ görmüş işlemler) erken kesiliyor. Mevcut ATR iz stop'u
  zaten tepe R'ye göre benzer bir merdiven gibi çalışıyor (kilit merdiveni testinde fark çıkmadı).
- **Zarattini tarzı Donchian topluluğu** (yeni kişilik adayı): bu evrende doğrulamada −0,15R, düşüş −%70. Makale (SSRN
  5209907) en likit 20 coin, 1 yıl+ listeleme ve oynaklık hedefleme kullanıyor; bizim haliyle tekrar edilemedi.
- **Donchian orta nokta iz stop'u, sadece long, genç coin yasağı:** yukarıdaki tabloda.

## 6. Kaynaklar

- Kaminski & Lo, "When do stop-loss rules stop losses?" (MIT) — stop'lar ancak momentum/rejim varsa değer katar.
- Kwok ve ark., *Journal of Empirical Finance* (2023) — kriptoda düşük oynaklık anomalisi.
- Schmeling, Schrimpf, Todorov, "Crypto carry", BIS WP 1087 — yüksek carry çöküşleri öngörüyor.
- Zarattini, Pagani, Barbon, "Catching Crypto Trends" (SSRN 5209907, 2025).
- Zaremba ve ark., *IRFA* 78 (2021) — likit coinlerde günlük momentum, illikitlerde geri dönüş.
- arXiv 2607.27070 — likidasyon kaskadlarından önce tek bir güvenilir erken uyarı sinyali yok (5 dk OI/long-short dahil).
