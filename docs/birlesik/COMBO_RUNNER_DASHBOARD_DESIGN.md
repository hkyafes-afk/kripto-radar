# COMBO PAPER Runner ve Dashboard Veri Bağlantısı Tasarımı

Durum: TASARIM / UYGULAMA DEĞİL
Hedef dal: feature/combo-paper-v1
PR: #26
Güvenlik: PAPER-only, LONG-only, private API kapalı, gerçek emir gönderimi yok.

## 1. Ayrım ve yetki sınırı

- COMBO kendi ComboConfig, ComboStrategy, ComboPaperAdapter ve combo_paper.json defterini kullanır.
- UnifiedTrader'ın site/birlesik/ dosyaları, 100 USDT PAPER bakiyesi, karar motoru ve emir yetkisi COMBO tarafından okunup yazılmaz.
- COMBO için ayrı 2.500 USDT simüle bakiye ve 10x marjin hesabı korunur.
- Piyasa verisi yalnız OKX public WS ticker akışından gelir; private WS/REST, API anahtarı, gerçek emir adaptörü ve borsa emir endpoint'i yoktur.
- SHORT/hedge yoktur. Her SELL yalnız mevcut LONG lotuna bağlı reduce-only'dir.
- COMBO varsayılan olarak kapalı kalır. UnifiedTrader canli.yml döngüsüne kendiliğinden eklenmez.

## 2. Kontrollü PAPER Runner

### Başlatma politikası

1. Ayrı combo-paper.yml workflow'u yalnız workflow_dispatch ile çalışır; cron, push ve pull_request tetikleyicisi olmaz.
2. Workflow yalnız feature/combo-paper-v1 ref'inde çalışmayı kabul eder; başka ref'te ilk adımda fail-closed kapanır.
3. GitHub Actions concurrency grubu combo-paper-runner, cancel-in-progress: false ile tek aktif oturum sağlar.
4. Varsayılan çalışma süresi sınırlıdır (en çok 5 saat 30 dakika). Süre dolunca feed durdurulur, son snapshot/ledger atomik yazılır, oturum kapalı işaretlenir.
5. Girdiler yalnız süre ve açıkça onaylanmış devam/yeniden başlatma seçeneğiyle sınırlıdır. Sermaye, kaldıraç, sembol, grid, miktar ve risk parametreleri onaylı sabit ComboConfig değerlerinden gelir.
6. Runner başlarken defterin schema sürümü ve config imzası doğrulanır. Bozuk/uyuşmayan defter sıfırlanmaz; oturum fail-closed kapanır.
7. Public OKX instrument metadata ve BBO doğrulanmadan başlatılmaz. ctVal dönüşümü, tickSz, sembol ve miktar birimi doğrulanır.
8. Feed stale, invalid, out-of-order, timestamp'siz veya BBO size'sız ise yeni PAPER fill varsayılmaz; durum STALE/INVALID/UNKNOWN yazılır ve güvenli beklemeye geçilir.
9. Her açılış, emir kabulü, kısmi/tam dolum, iptal, TP, hard-stop ve kurtarma olayı append-only event kaydına yazılır.
10. Runner yalnız sanal defter üzerinde simülasyon yapar; gerçek işlem açmaz.

### Kalıcı durum ve kilit

- Çalışma dosyaları combo_paper.json, combo_events.jsonl ve combo_runner_status.json ayrı COMBO state alanında tutulur.
- Yazma temp dosyaya yapılır, atomik rename ile tamamlanır. Ledger yazımı tamamlanmadan snapshot published işaretlenmez.
- Lease içinde session_id, owner run_id, heartbeat_ts_ms, expiry_ts_ms ve config_hash bulunur.
- Önceki oturumun son snapshot'ı doğrulanmadan yeni session başlatılmaz.
- State branch/artifact deposunda compare-and-swap veya commit-parent doğrulaması gerekir; yarışta otomatik force-push yapılmaz. Çakışmada ikinci oturum fail-closed durur.
- GitHub Actions runner kalıcı sunucu değildir; timeout, iptal, kota ve job sonu nedeniyle 7/24 veya milisaniyelik SLA vermez.

## 3. Dashboard veri sözleşmesi

Dashboard, COMBO için yalnız tek bir read-only snapshot dosyası okur: combo_paper.json. UnifiedTrader'ın birlesik/*.json verilerinden bağımsızdır.

Önerilen üst seviye şema:

    {
      "schema_version": 1,
      "strategy": "BITS GAP_COMBO",
      "session_id": "uuid",
      "generated_at_ms": 0,
      "source": {
        "runner": "github-actions",
        "run_id": "string",
        "branch": "feature/combo-paper-v1",
        "feed": "okx-public-ws",
        "instrument": "BTC-USDT-SWAP",
        "quote_ts_ms": 0,
        "quote_age_ms": 0,
        "quote_state": "LIVE|STALE|INVALID|UNKNOWN"
      },
      "safety": {
        "execution_mode": "PAPER_ONLY",
        "private_api_enabled": false,
        "live_order_submission": false,
        "allow_short": false,
        "allow_hedge": false,
        "runner_enabled": true,
        "halted": false,
        "halt_reason": null
      },
      "account": {
        "capital_usdt": 2500.0,
        "equity_usdt": null,
        "free_margin_usdt": null,
        "used_margin_usdt": null,
        "leverage_simulation": 10.0,
        "fees_paid_usdt": null,
        "realized_net_usdt": null,
        "unrealized_gross_usdt": null
      },
      "position": {
        "side": "LONG|FLAT",
        "qty_btc": null,
        "avg_entry_price": null,
        "mark_price": null,
        "tp_price": null,
        "hard_stop_price": null
      },
      "orders": [],
      "recent_fills": [],
      "recent_events": []
    }

Kurallar:
- null = veri henüz yok / hesaplanamıyor. Sıfır yalnız gerçek sıfırsa yazılır.
- generated_at_ms ile quote_ts_ms ayrı tutulur. Snapshot'ın yeni olması fiyatın da yeni olduğu anlamına gelmez.
- LIVE rozeti yalnız quote timestamp'i yaş sınırındaysa ve runner/feed sağlık durumu sağlamsa gösterilir.
- Runner durmuşsa son snapshot SESSION STOPPED / LAST UPDATE etiketiyle gösterilir. Eski veri canlı gibi renklendirilmez.
- orders kayıtları order_id, side, role, price, qty, filled_qty, remaining_qty, reduce_only, lot_id, parent_order_id, status, created_ts_ms ve updated_ts_ms alanlarını taşır.
- recent_fills kayıtları order_id, side, role, qty, price, fee_usdt, ts_ms ve lot_id içerir.
- recent_events yalnız gerçek ledger event'lerinden üretilir; sentetik örnek satır yoktur.
- Snapshot, config hash ve session id olmadan eski/yeni defterler birleştirilmez.

## 4. Dashboard sunumu

Ayrı bir COMBO PAPER sekmesi/paneli:
- 2.500 USDT sanal sermaye, özkaynak, serbest/kullanılan teminat ve 10x simülasyon kaldıraç;
- LONG miktarı, ağırlıklı ortalama giriş, son doğrulanmış mark, gerçekleşmemiş K/Z, ödenen ücret;
- ortalama giriş +%1,50 dinamik TP ve hard-stop seviyesi;
- açık DCA BUY ve lot destekli reduce-only GRID SELL emirleri; kısmi dolum ve kalan miktar;
- son dolumlar: zaman, yön, miktar, fiyat, ücret;
- feed yaşı, runner session, son snapshot zamanı, halt sebebi ve PAPER-only güvenlik bayrakları.

COMBO değerleri UnifiedTrader KPI'larına eklenmez, toplam bakiyeye katılmaz ve ortak performans grafiği üretmez. COMBO için veri yoksa panel Veri yok der; UnifiedTrader bakiyesiyle doldurulmaz.

## 5. Taşıma / yenileme sınırı

Mevcut birlesik.html dashboard'u statik dosya okur ve mevcut kodunda 60 saniyelik yenileme döngüsü vardır. GitHub Pages'e yazılan snapshot, Runner içindeki her tick'i tarayıcıya anında iletmez. İlk kontrollü sürümde ayrı snapshot dosyası 30–60 saniyelik aralıkla yayımlanabilir; gerçek milisaniye/saniye düzeyinde izleme gerekiyorsa kalıcı bir servis + SSE/WebSocket/API gerekir. GitHub Actions + Pages ile gerçek zamanlı ekran vaat edilmez.

Yayınlama için iki seçenek:
- Kontrollü ilk aşama: Runner'ın son snapshot'ı ayrı COMBO state ref'ine atomik kaydetmesi; Pages yayınlayıcısının yalnız doğrulanmış snapshot'ı read-only kopyalaması.
- İleri aşama: kalıcı host üzerinde read-only snapshot endpoint'i / SSE; statik GitHub Pages yalnız yedek/son bilinen durum.

İlk sürümde UnifiedTrader'ın canli.yml veya scripts/site.sh akışına COMBO auto-start / defter yazımı eklenmez.

## 6. Kabul testleri

- Başka branch'ten dispatch reddedilir.
- Aynı anda iki runner açılmaz; lease çakışması fail-closed.
- Runner durdurma/timeout sonrası state tekrar yüklenebilir; config hash uyuşmazlığı sıfırlama yapmaz.
- Public feed kesilince yeni fill yok; eski quote ile fill yok.
- Her SELL reduce-only ve açık lot miktarını aşamaz; SHORT pozisyon oluşamaz.
- Snapshot yalnız COMBO ledger'ından türetilir; UnifiedTrader state dosyalarına yazma yapılmaz.
- Dashboard null, stale, missing, stopped durumlarını ayırır.
- Snapshot checksum/schema/session doğrulaması başarısızsa Dashboard Veri doğrulanamadı gösterir.
- Runner açılış/kapama ve PAPER işlem event'leri audit kaydına eklenir.
- main merge edilmez; PR taslak/açık kalır.

## 7. Uygulama sırası

1. Bu sözleşmeye uygun snapshot serializer + schema doğrulama testleri.
2. Ayrı manual-dispatch workflow ve runner lifecycle/lease testleri.
3. Dashboard'da ayrı COMBO sekmesi; yalnız read-only snapshot fetch.
4. Runner, feed, ledger ve Dashboard uçtan uca entegrasyon testleri.
5. feature/combo-paper-v1 PR CI tamamlanır; kullanıcı onayı olmadan merge veya auto-start yapılmaz.
