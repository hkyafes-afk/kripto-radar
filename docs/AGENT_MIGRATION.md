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
