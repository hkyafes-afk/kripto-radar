# Politika denetimi ve ATR deney sözleşmesi

Tarih: 2026-10-31  
Durum: denetim başlangıcı; bu belge bir performans iddiası veya canlıya alma onayı değildir.

## 1. Şu an kodun gerçekten yaptığı

Kaynaklar: `radar/policy.py`, `config.yaml`, `tests/test_policy.py`, `radar/unified/decision.py`.

- Legacy günlük (1D) fikirlerde `radar.policy.gate` uygulanır. 15m/4H fikirler ATR/funding/rejim kapılarının bu fonksiyonundan muaf.
- Günlük ATR/fiyat > %5 ise (Ayşe hariç) fikir reddedilmez; gerçek sermaye yerine gölgeye alınır. Bu, fırsatın taranmasını durdurmaz fakat legacy masada sermaye tahsisini engeller.
- Günlük stop mesafesi > %15 ise gölgeye alınır.
- Funding yıllık > %30 long veya < −%20 short ise gölgeye alınır.
- Kaan'ın BTC yatay kırılımları gölgeye alınır.
- Rejim uyumu açıksa BTC boğada long, ayıda short dışındaki günlük yön fikirleri gölgeye alınır; rejim yoksa bu kapı uygulanmaz.
- Yeniden giriş kuralı: aynı yönetici/coin/yön zararla kapandıysa 5 gün bekleme; kârla kapandıysa yeni en iyi kapanış şartı. Baş ve Turbo hariç.
- Günlük kapanış stop'u + 2R felaket stop'u kullanılır.
- Unified motor aynı politika fonksiyonunu çağırır; yeni strateji fırsatını sözde-fikir olarak dönüştürür. Veri kalitesi/kapsamı ve strateji yetkisi kontrolleri politika öncesinde/yanında çalışır.
- `birlesik.karar_modu: shadow` ise kararlar yalnız gölge kaydıdır; PAPER motorunun karar üretmesi, kendi başına canlı borsa emri gönderdiği anlamına gelmez.

## 2. ATR %5 hakkında karar

%5'i evrensel ve kalıcı gerçek olarak kabul etmiyoruz. Mevcut LAB raporu %3–5 kovasını olumlu, %5–12 kovasını zayıf rapor ediyor; fakat bu sonuç mevcut strateji kümesi, günlük ufuk, evren ve simülasyon maliyetleriyle sınırlı. Tek ajan raporu da ATR ≤ %5 filtresini destekliyor, ancak kendi saklı testinde tek ajan A'nın getirisi üç coinde yoğunlaşmış ve short sistemleri ağır kaybetmiş. Bu nedenle %5 bulgusu değerli bir adaydır, bütün stratejiler için genel yasa değildir.

Deney adayları (ATR günlük yüzde olarak):
- 3%, 5%, 7%, 10%, 12.5%, limitsiz.
- Ayrı ayrı strateji, yön (long/short), BTC rejimi (boğa/yatay/ayı), likidite dilimi ve coin yaşı kırılımları.
- İlk karşılaştırmada diğer tüm kurallar, veri evreni, giriş/çıkış ve maliyet modeli sabit tutulur. Sonraki aşamada yalnız sağlam kalan ATR yaklaşımı için dinamik/percentile eşik değerlendirilir.

Önemli: ATR eşiği yüksek fikirleri deney datasından silmemeli. Her aday fikir bütün ATR varyantları için aynı zaman damgası ve aynı özelliklerle kaydedilmeli; varyantın kararı/pozisyon boyutu ayrı kolonlarda hesaplanmalı. Böylece %5'in hangi fırsatları elediği ve bunların net sonucu ölçülebilir. Gölgeye alınan fikirlerin fiilen hangi fiyattan doldurulabileceği de execution simülatörüyle değerlendirilmeden gerçekleşmiş işlem gibi sayılmaz.

## 3. Önceden sabitlenecek değerlendirme ölçütleri

Bir ATR varyantı yalnızca en yüksek Sharpe'a göre seçilmez. Rapor en az şunları içermeli:
- Net expectancy (R ve bp), ücret + spread + slippage + funding sonrası.
- İşlem sayısı, turnover ve piyasada kalma oranı.
- Maksimum düşüş, günlük/haftalık kuyruk kaybı, ardışık kayıp.
- Long ve short ayrı; BTC rejimi ve yıl/çeyrek ayrı.
- Coin ve dönem yoğunlaşması; en iyi 3 coin çıkarıldığında sonuç.
- Bootstrap güven aralığı ve eşik komşuluğu duyarlılığı.
- Maker/taker ve 1×/2× maliyet stresleri.
- Emir dolum modeli: candle touch tek başına limit dolumu sayılmaz; spread, derinlik, kısmi dolum, sıra, gecikme ve stop gap dikkate alınır.

## 4. Veri bölme ve araştırma disiplini

- Eğitim dönemi yalnız varyant/parametre seçimi içindir.
- Walk-forward doğrulama; zaman sıralı, purged/embargo ayrımı ve point-in-time evren kullanılır.
- Son holdout, varyantlar ve kurallar dondurulana kadar açılmaz.
- Denenen varyant/parametre sayısı kaydedilir; çoklu deneme yanlılığı raporlanır.
- Yeni kural her işlemden sonra kendini değiştirmez. Champion/challenger, sabit değerlendirme penceresi ve rollback kullanılır.

## 5. Şu anki denetim boşlukları

- Eski politika ile unified motorun tüm kuralları tek bir versiyonlu kayıt altında henüz birleştirilmiş değil.
- Legacy policy'nin ATR kapısı günlükte gölgeye alıyor; 4H/15m stratejilerinde aynı kapı uygulanmıyor. Bu fark bilinçli araştırma tercihi olarak belgelenmeli veya testle gerekçelendirilmeli.
- RiskGate küme limiti kodda var; gerçek cluster metadata'nın her karar yolundan geldiği doğrulanmadan etkin koruma gibi kabul edilmemeli.
- LAB raporundaki özet metrikler ham veri ve deney komutlarıyla bağımsız yeniden üretilmeden doğrulanmış performans garantisi sayılmaz.
- Unified motor konfigürasyonu shadow modda; gerçek para ve canlı emir yetkisi yok.

## 6. Kabul kapısı

Hiçbir ATR varyantı veya politika değişikliği canlıya yalnızca backtest getirisi yüksek diye alınmaz. Önce:
1. Aynı fırsat kümesi üzerinde varyant karşılaştırması,
2. Maliyet ve dolum stres testleri,
3. Walk-forward ve dokunulmamış holdout,
4. En az 100 gölge işlem ve pozitif net beklenti için güven aralığı,
5. risk/operasyonel fail-safe testleri,
6. insan incelemesi ve açık onay.

Başlangıç sermayesi hedefi 100 USDT olarak ele alınacak; testlerde minimum emir/lot, ücret ve gerçekçi kayma küçük bakiyede ayrıca ölçülecek. Bu değer canlı işlem izni değildir.


## 7. ATR eşik deneyi — otomatik yeniden üretim

`tools/lab.py --set atr` aynı günlük fikir üretimini ve V10'un diğer filtrelerini koruyarak yalnız ATR üst sınırını değiştirir:
- 3%, 5%, 7%, 10%, 12.5% ve limitsiz.
- Point-in-time evren: günlük 30 günlük medyan hacim ≥ 5M USDT ve her tarihte en likit 100 coin.
- Funding geçmişi mevcutsa günlük gerçek funding; yoksa kodun tanımlı long funding varsayımı uygulanır. Eksik funding verisi ayrıca raporda görünmelidir.
- Giriş/çıkış simülatörü taker/maker ücretleri, giriş kayması ve ATR'a bağlı stop kayması kullanır.
- Bu mum tabanlı model emir defteri derinliğini, sıra önceliğini ve gerçek kısmi dolumu modellemez; sonuç canlı dolum garantisi değildir.
- Çalışma `.github/workflows/atr-research.yml` ile `gecmis` veri dalındaki 1D ve funding verisini alıp rapor ve işlem defterini CI artifact olarak üretir.
- Her varyant için IS/OOS işlem sayısı, ortalama R, PF ve drawdown karşılaştırılmalıdır. Eşik tek metrikle seçilmez.
- 2025-01-01 sonrası OOS dönemi önceki LAB araştırmalarında görüldüğü için dokunulmamış holdout değildir. Sonuç tekrar üretim ve aday eleme içindir; nihai onay için yeni ileri dönem gölge/holdout gerekir.


## 8. 31.10.2026 ATR deneyinin ilk bulgusu
Eşleştirilmiş altı varyant tamamlandı. Özet ve sağlamlık uyarıları `data/ATR_DENeyi.md` içinde; ham işlem defteri Actions artifact'inde.

- OOS %5: 466 işlem, +0,171R ortalama, PF 1,41, max DD −%14,2.
- OOS %3: 78 işlem, +0,187R, PF 1,44, max DD −%3,9; yalnız 8 sembol nedeniyle örneklem çok küçük.
- OOS %7: 1.547 işlem, +0,005R, PF 1,01, max DD −%41,3.
- OOS %10 / %12,5 / sınırsız: ortalama net R yaklaşık sıfır veya negatif; düşüşler yaklaşık −%38 ila −%43.
- %5 yön ayrımında long +0,307R (261), short −0,002R (205). Geniş ATR eşiklerinde short tarafı görece daha iyi, long tarafı belirgin biçimde kötüleşiyor.
- %5 2025'te −0,112R (162), 2026'da +0,321R (304); dönem kararlılığı yok.
- Coin-küme bootstrap %95 aralığı %5 için [+0,031; +0,335]R; ancak coin kümeleri ortak piyasa/zaman korelasyonunu çözmez.

**Karar:** %5 en makul araştırma adayı, fakat üretim onayı değil. %3 küçük örneklem nedeniyle seçilemez. ATR eşiği config/production üzerinde değiştirilmedi. Yön ve rejim bazlı analiz, zaman-blok bootstrap, maliyet stresleri ve yeni ileri dönem gölge/holdout olmadan kuralı güncellemiyoruz.
