# Veri adaptörü, kanal sağlığı, ayar değişikliği ve kanıt kapıları

## Ortak normalize piyasa sözleşmesi

`radar/unified/market_data.py` OKX ve diğer public feed adaptörlerinin aynı BBO sözleşmesine normalize ettiği ortak çekirdektir. Sonuç; venue, symbol, last, bid, ask, borsa event timestamp'i, valid ve reason alanlarını taşır. Eksik timestamp hiçbir yerde yerel saatle doldurulmaz. UnifiedTrader fiyatı yalnız kalite motoru LIVE dediğinde deftere, izlemeye veya limit dolum kontrolüne geçirir.

Polat ve UnifiedTrader aynı normalizasyon sözleşmesini kullanır; fakat ayrı process/runner ve ayrı defterlerdir. Bu modül onları ortak bir canlı socket veya ortak bellek üzerinden bağlamaz. Cross-process gerçek zamanlı fan-out bu altyapıda yoktur.

## Kanal kalite kapsamı

Polat kalite telemetrisi şu kanalları izler: ticker, L2 books (sequence), trades, open interest, funding, mark, index, liquidation ve 1m/5m/15m/1H/4H/1D candles. Kanal bazlı yaş eşikleri tanımlıdır; `stats()` çağrısı sırasında zaman geçişi nedeniyle bayatlayan son gözlemler de STALE olarak sayılır. İlgili WebSocket bağlantısı koparsa yalnız o bağlantının sahip olduğu kanallar DISCONNECTED olur. Geçersiz/bayat ticker, trade, metric veya candle strateji state'ine işlenmez; bozuk orderbook snapshot/update sonrası yeni snapshot beklenir.

Eşikler veri akışının beklenen yayın sıklığına göre muhafazakâr operasyonel sınırdır; her strateji daha sıkı tazelik eşiği uygulayabilir. Likidasyon kanalı olay tabanlıdır; bu kanalın sessizliği tek başına fiyat feed'inin kesildiği anlamına gelmez.

## Güvenli ayar değişikliği

Statik GitHub Pages dashboard içinde secret veya PAT saklayan bir yazma API'si yoktur. Bunun yerine kimliği GitHub tarafından doğrulanan `workflow_dispatch` akışı yalnızca sabit allowlist içindeki yedi sayısal parametre için çalışır. Her parametrenin aralığı kodda sabittir; YAML parser ile tip doğrulanır, YAML scalar token'ı değiştirilir (yorumlar korunur), dosya atomik replace edilir ve değişiklik `main` üzerine otomatik uygulanmak yerine taslak PR açar. PR testleri ve insan incelemesi gerekir.

Akış `.github/workflows/settings-change-request.yml` dosyasıdır. Yalnız workflow `main` ref'inden çağrıldığında iş yapar. Dashboard bağlantısı, PR main'e alındıktan sonra etkinleşir. PAPER modu, Unified tek-otorite modu, API anahtarları, borsa kimlik bilgileri, private endpoint ve canlı işlem modu allowlist'te değildir.

## Strateji araştırma doğrulaması

`radar/unified/research.py` kronolojik, expanding-window purged walk-forward ve ayrı son holdout üretir. Holdout fold eğitiminde veya fold validation'da kullanılmaz. Araştırma uygunluğu için en az 100 karşılaştırılabilir kapanmış işlem, en az iki geçerli WF fold, her validation fold'da pozitif net R, untouched holdout'ta pozitif net R ve %95 normal alt sınırın sıfırdan büyük olması, DSR ≥ 0.95, maliyetlerin ve veri provenance'ının eksiksiz olması ve en az 20 holdout işlemi gerekir. Denenen varyant sayısı DSR'ye girer.

Bu rapor otomatik strateji terfisi yapmaz. Ayrıca Unified PAPER strateji terfi adayı, EffectivenessAuditor içinde 100 kapanmış işlem/istatistik koşullarına ek olarak en az 14 günlük gerçek forward PAPER işlem zaman aralığına sahip olmalıdır.

## Zamanla tamamlanabilecek kanıt

14 günlük forward gözlem ve 100 karşılaştırılabilir kapanmış işlem kodla üretilebilen anlık bir sonuç değildir. Kod bu şartları zorunlu kılar ve yetersiz veriyi `kanit_yetersiz` gösterir; eksik süre veya işlem sayısı sentetik kayıtla doldurulmaz. GitHub Actions kesintisiz sunucu olmadığından 7/24 ve milisaniyelik SLA iddiası yoktur.
