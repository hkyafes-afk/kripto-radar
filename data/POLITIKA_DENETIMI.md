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
