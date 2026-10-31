# Eski ajanlardan tek Baş Trader'a geçiş planı

## Hedef
Tek pozisyon/karar defteri ve tek karar otoritesi. Legacy sistemler araştırma ve geçmiş veri kaynağı olarak korunur; işlem yetkileri kontrollü şekilde kaldırılır.

## Envanter (başlangıç)
- Legacy Radar: `radar/`, `config.yaml`, `radar live` workflow'u.
- Polat: `polat/`, `.github/workflows/polat.yml` ve teşhis/rapor görevleri.
- Unified: `radar/unified/`; `birlesik.karar_modu` mevcut konfigürasyonda shadow.
- Ek workflow'lar: araştırma, backtest, veri, hakem, yarışma, Binance/OKX probe ve arşiv. Bunlar otomatik olarak "ajan" sayılmaz; veri ve doğrulama işlevleri ayrı tutulur.

## Aşamalar
### 0 — Değişiklik öncesi envanter
Her workflow için tetikleyici, komut, yazdığı dosyalar, secret erişimi, işlem/pozisyon yan etkisi ve veri bağımlılığı kaydedilir. Her ajan için emir açma, pozisyon yönetme, bildirim ve öğrenme yan etkileri çağrı zincirinde izlenir.

### 1 — Yetkiyi ayır
- `research_only` / `decision_authority` / `execution_authority` kavramlarını açıkça tanımla.
- Legacy ajanların sinyalleri karar günlüğüne challenger kaydı olarak yazılır; UnifiedTrader dışında hiçbir motor pozisyon açamaz.
- Legacy portföyler silinmez veya birleştirilmiş portföy gibi gösterilmez.
- Bu aşamada eski workflow'lar kapatılmaz; önce yalnız rapor/araştırma modunda çalıştıkları test edilir.

### 2 — Tek dashboard
- UnifiedTrader durum/karar/pozisyon API veya statik JSON çıktısı tek arayüze bağlanır.
- Eski dashboard'lar geçiş süresince salt okunur arşiv rotalarında kalır.
- Veri bulunmayan alanlar boş/unknown gösterilir; tahmini değer üretilmez.

### 3 — Eski işlem yetkilerini kapat
- Her workflow ve CLI giriş noktasında yetki kapısı zorunlu olur.
- Legacy live workflow'unun otomatik çalışması, UnifiedTrader shadow/paper kabul testleri ve veri devamlılığı doğrulandıktan sonra devre dışı bırakılır.
- Polat ve diğer ajanların otomatik emir/pozisyon açma çağrıları testlerle engellenir.
- Araştırma, veri indirme, arşiv, backtest ve hakem görevleri korunur.

### 4 — Doğrulama ve yayın
- Aynı piyasa olayları legacy challenger ve unified karar motoruna verilir; sonuçlar aynı fırsat kimliğiyle kıyaslanır.
- Ücret, funding, spread ve kayma sonrası net performans ölçülür.
- Walk-forward + dokunulmamış holdout + bootstrap + stres testleri geçmeden strateji kuralı varsayılan yapılmaz.
- CI yeşil olmadan merge yok. Gerçek emir yolu bu PR'ın kapsamı dışındadır.

## Geri dönüş
Her aşama ayrı commit ve config flag ile yapılır. Veri dosyaları ve eski kod silinmez. Sorunda legacy raporlar okunabilir kalır; ancak eski işlem yetkisi otomatik olarak geri açılmaz.


## Güncel uygulama durumu (2026-10-31)
- **Baş Trader ana paneli:** site üretiminde `index.html` artık birleşik paneldir. Eski Radar arayüzü kaybolmaz; `radar.html` arşiv rotasında tutulur. `birlesik.html` geriye dönük bağlantı alias'ıdır. Polat paneli korunur.
- **Polat yetkisi:** `polat/config.yaml → yetki.yeni_pozisyon: false`. Polat piyasa okuması, karar/challenger günlüğü, öğrenme ve mevcut sanal pozisyonların stop/hedef/funding yönetimini sürdürür; yeni sanal pozisyon açamaz. Yetki kodda açık bayrakla denetlenir ve CI testi vardır.
- **Legacy Radar döngüsü:** `canli.yml` kapatılmadı. `birlesik.karar_modu: shadow` aşamasında legacy demo portföyü karşılaştırma taban çizgisi olarak çalışır; UnifiedTrader ayrı gölge kayıt üretir. Bu aşamada legacy emir yolu gerçek borsa emri değildir. Shadow kabul kapısı tamamlanmadan eski döngüyü kapatmak karşılaştırma verisini keser.
- **Araştırma/veri iş akışları:** backtest, race, geçmiş veri, Binance arşiv/metrik, saatlik arşiv, OI/long-short toplayıcı, hakem, öğrenme, teşhis ve OKX public probe görevleri araştırma/veri amaçlı korunur. Bunlar tek başına pozisyon yetkisi vermez.
- **Polat nöbet zinciri:** workflow'u şu an açık kalır; çünkü raporlama/öğrenme ve mevcut sanal pozisyonların güvenli yönetimini sürdürür. Yeni pozisyon yetkisi ayrıca kapatılmıştır. Zinciri kapatmak yerine giriş yetkisini kapatmak veri ve pozisyon sürekliliğini korur.

## Yetki sınırları
| Bileşen | Karar/araştırma | Yeni pozisyon | Mevcut pozisyon yönetimi |
|---|---|---|---|
| UnifiedTrader (shadow) | Evet | Yalnız ayrı gölge defter | Gölge defter |
| Legacy Radar | Baseline/challenger | Legacy sanal defter, shadow döneminde | Evet |
| Polat | Challenger/öğrenme | **Kapalı** | Mevcut sanal pozisyonları yönetir |
| Veri/backtest/probe workflow'ları | Evet | Yok | Yok |

Not: Legacy Radar'ın sanal işlem üretmeye devam etmesi shadow testinin karşılaştırma taban çizgisidir; birleşik karar modu `unified` yapılmadıkça tek pozisyon otoritesi geçişi tamamlanmış sayılmaz. Bu PR'da canlı borsa emri açılmaz.


## Tek yetkili paper cutover — 2026-10-31

- config.yaml / birlesik.karar_modu: unified: yeni pozisyon açma yetkisi yalnız UnifiedTrader'dadır; yürütme modu hâlâ PAPER'dır.
- Legacy Radar'ın open_positions, yönetici process_ideas, bekleyen limit emirleri ve Elif'in yeni carry girişleri tek yetki kapısıyla engellenir. Bekleyen eski giriş emirleri iptal edilir; mevcut legacy pozisyonların çıkış/risk yönetimi ve arşiv defteri korunur.
- Eski yönetici fikirleri yeni pozisyon değildir; challenger günlüğüne yazılır. Bunlar UnifiedTrader portföyüne otomatik aktarılmış gibi gösterilmez.
- Polat yetki.yeni_pozisyon: false olarak kalır; mevcut sanal pozisyonları yönetir, araştırma/öğrenme kayıtlarını üretir.
- canli.yml ve polat.yml veri akışı ile açık sanal pozisyonların yönetimi için çalışmaya devam eder. Workflow'un çalışıyor olması bağımsız yeni pozisyon yetkisi anlamına gelmez.
- Eski 100.000 USDT shadow state, yalnızca paper emir/pozisyon/kapanmış işlem geçmişi yoksa 100 USDT'ye güvenli biçimde yeniden bazlanır. İşlem geçmişi varsa başlangıç sermayesi değişimi fail-closed olur; otomatik silme yapılmaz.
- Ana dashboard yalnız UnifiedTrader'ın 100 USDT PAPER durumunu (birlesik/durum.json) gösterir. Eski Shadow (birlesik/golge/durum.json) ayrı arşivdir; aktif hesap gibi gösterilmez ve iki portföy birleştirilmez.

## Açık güvenlik/strateji sınırları — 2026-10-31

- Unified modu erken PAPER yayınıdır. Önceki planın 2 hafta ve en az 100 karar karşılaştırma kabul kapısı henüz tamamlanmış sayılmaz; bu performans kârlılık kanıtı değildir.
- IntradayTrend, Microstructure ve PanicReversal gibi SHADOW_ONLY stratejiler, veri/örneklem koşulları sağlanmadan paper yetkisine yükseltilmez.
- Elif CarryStrategy henüz UnifiedTrader strateji kayıtçısına ve iki bacaklı atomik spot+perp PAPER yürütmesine entegre değildir. Eski Elif yeni carry girişleri bu nedenle engellenmiştir; açık eski carry pozisyonları kendi legacy yönetiminde kapanır.
- Polat, önceki negatif kanıt nedeniyle UnifiedTrader'a otomatik strateji olarak aktarılmamıştır; yeni pozisyon yetkisi kapalı challenger/öğrenme kaynağıdır.