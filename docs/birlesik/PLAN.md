# Birleşik Baş Trader — uygulama planı

Kaynak görev: `docs/birlesik/GOREV.md`. Kanıt: `data/TEK_AJAN_ARASTIRMA.md` ve denetimi `docs/birlesik/ARASTIRMA_DENETIMI.md`.
Yarıda kalırsa: `docs/birlesik/DEVAM.md`. Bu plan PR 1 ile birlikte yazıldı (30.10.2026).

## 1. Mevcut sistemde tespit edilen sorunlar

1. **Kopya bahisler:** 28.10.2026'da 108 açık pozisyonun 63'ü başka ajanların kopyasıydı (Turbo 35, Ortak 4; config notu).
   Aynı risk birden çok kez sayılıyor; 5 yıllık testte aynı bahse 4+ ajan girmesi sonucu iyileştirmedi.
2. **Parçalı sermaye ve yetki:** her yönetici ayrı 10.000$ demo bütçe ve bağımsız açma yetkisi; firma limitleri kapalı
   (`ekip.firma.durdurma_aktif/sinirlar_aktif: false`).
3. **Üç ayrı maliyet modeli:** `ledger.py` (liste ücretinin 2 katı + ATR'li stop kayması), `bt2.COST` (taker %0,10, sabit kayma,
   long funding %0,03/gün), araştırma `sim.py` (5 bp + kademeli kayma). Tek kaynak yok → PR 1'de `CostEngine` defterin
   tablosunu kullanır.
4. **Veri kalitesi faktör bazında değil:** `engine._data_issue`, `ws.healthy`, `team._live` ayrı ayrı; eksik OI/taker verisi
   "doğrulama tutmadı" sayılıyor (eksik ile olumsuz karışıyor) → PR 1'de `FactorCoverage`.
5. **Karar dağınık:** `desk.evaluate` + `director` + `policy` + `experts` + `hakem`; açıklamalar var ama tek karar kaydı yok.
6. **Kısa vade negatif:** Deniz (15 dk) testte −%94 → durduruldu; Polat −0,27R (1.549 sinyal); araştırmadaki 9 gün içi kural
   saklı testte kazandırmadı.
7. **Karne bozuk:** `KARNE.md` ve `/karne` eski setup'lara bakıyor (0 işlem).
8. **Yarış eşikleri:** sabit +0,02R, güven aralığı yok; test penceresi tekrar kullanıldı.
9. **Veri:** Binance okuyucusunda zombi satır hatası (main'de düzeltildi, `2102ba5`); OKX yarış evreninde survivorship.
10. **Dosya büyüklüğü:** Polat'ın `kararlar.jsonl`'ü 100 MB'ı geçip gönderimleri durdurmuştu → birleşik karar kayıtları döndürülmeli.

## 2. Korunacak bileşenler

- `radar/ledger.py` paper muhasebesi (VWAP dolum, kontrat yuvarlama, stop kayması, funding, kısmi kâr, iz stop)
- `radar/policy.py` kapıları (oynaklık, kalabalık, rejim, kapanış stop'u, yeniden giriş) — araştırma doğruladı
- `radar/director.coin_class` (A/B/C), `radar/lease.py` + `canli.yml` 7/24 zinciri, `okx.py`, `ws.py`, `notify.py`, dashboard yayını
- `radar/team.py` analiz fonksiyonları: yeni stratejilerin çekirdeği olarak SARMALANIR (kopyalanmaz)
- `radar/bt2.py`, `race.py`, `binance_data.py`, `tools/arastirma/` araştırma altyapısı
- Bütün eski işlem kayıtları ve performans geçmişi (`kalp` dalı `ledger.json`, `data/`)

## 3. Shadow'a alınacak / devre dışı (kayıtlar silinmez)

| Legacy | Birleşik karşılık | Yetki |
|---|---|---|
| Kemal | SwingTrendStrategy | paper |
| Selin | CompressionBreakoutStrategy | paper |
| Kaan | ControlledBreakoutStrategy (ana sermaye motoru değil, kanıt) | paper |
| Ayşe | PanicReversalStrategy | shadow / düşük risk |
| Elif | CarryStrategy (ayrı muhasebe) | paper |
| Murat | legacy gölge karşılaştırma | shadow |
| Deniz | kapalı (mikro araştırma referansı) | disabled |
| Turbo, Ortak Masa | bağımsız açma yetkisi kalkar | shadow |
| Baş Trader | UnifiedDecisionEngine | — |

Eşleme kodda: `radar/unified/models.LEGACY_STRATEGY`.

## 4. Yeni modüller

| PR | Modüller |
|---|---|
| 1 (bu PR) | `radar/unified/models.py`, `coverage.py`, `costs.py`, `flags.py`; `config.yaml → birlesik`; testler; dokümanlar |
| 2 | `radar/unified/factors.py` (FactorEngine), `radar/unified/strategies/*.py`, `radar/unified/decision.py` (UnifiedDecisionEngine), motor kancası (yalnız SHADOW modu) |
| 3 | `radar/unified/risk.py` (RiskGate), `radar/unified/execution/{base,paper,okx_demo}.py`, `radar/unified/exits.py`, defter alanları + migrasyon, UNIFIED modu (yalnız paper) |
| 4 | `radar/unified/audit.py` (EffectivenessAuditor), yarışta işlem başına R + bootstrap güven aralığı + coin sayısına göre ayrı çıktı, yeni karne (strateji/gölge/filtre karnesi), birleşik stratejilerin Binance verisinde eğitim/test koşusu |
| 5 | dashboard payload v2 (kararlar, kapsam, maliyet, risk, etkinlik), `radar/templates/` modern arayüz, Telegram Türkçe açıklamalar |

## 5. Veri migrasyonu

- PR 1–2: defter şeması DEĞİŞMEZ. Birleşik kararlar ayrı dosyada: `kalp` dalında `birlesik/kararlar-YYYY-MM-DD.jsonl`
  (günlük döndürme, 7 günden eskisi gzip arşiv; Polat'taki 100 MB sorunu tekrarlanmasın).
- PR 3: defter işlemine İSTEĞE BAĞLI yeni alanlar (`strategy`, `decision_key`, `client_order_id`, `exec_events`, `costs`);
  `ledger.migrate()` eski işlemlere dokunmadan okurken `LEGACY_STRATEGY` ile strateji adı türetir. Geri dönüş: alanlar yok
  sayılınca legacy aynen çalışır.
- Açık paper pozisyonları: UNIFIED moduna geçişte legacy pozisyonlar kendi kurallarıyla kapanana kadar legacy yönetiminde kalır
  (çifte yönetim yok: her pozisyonun `manager` alanı tek sahip).

## 6. Feature flag geçişi

1. `karar_modu: legacy` (varsayılan, bu PR) — birleşik kod canlıda çalışmaz.
2. `karar_modu: shadow` (PR 2 sonrası) — birleşik motor her taramada karar üretir ve kaydeder; legacy işlem açar. En az 2 hafta
   ve 100+ karar boyunca legacy ile karşılaştırılır (PR 4 denetçisi).
3. `karar_modu: unified` (PR 3 sonrası, kullanıcı onayıyla) — yeni paper işlemleri birleşik motor + RiskGate açar; legacy
   yöneticiler yalnız gölge.
4. `yurutme_modu: okx_demo` — ileride, kullanıcı OKX demo anahtarını GitHub Secrets'a koyup açıkça onaylarsa; strateji kodu
   değişmez, yalnız adaptör. `okx_live` bu çalışmanın kapsamı dışında ve kilitli (`flags.ensure_paper`).

## 7. Test sırası

1. Birim: modeller, kapsam, maliyet, bayraklar (PR 1 — 46 test).
2. Strateji birimleri: her strateji sentetik `MarketContext` ile; stratejilerin yürütme/defter modülü import etmediği kontrolü.
3. Karar motoru: birleştirme (aynı coin+yön+kurulum+ufuk tek fırsat), kanıt yoğunluğu, çelişki çözümü, Türkçe açıklama.
4. RiskGate: dolar risk → nominal → en düşük yeterli kaldıraç, likidasyon tamponu, toplam/yön/coin/küme sınırları, devre kesiciler.
5. Paper adaptörü durum makinesi: kısmi dolum, IOC, iptal, süre dolumu, idempotency (aynı anahtar → tek emir), yeniden başlatma.
6. Entegrasyon: `tests/test_mocked_api.py` tarzı sahte API ile motor SHADOW modu.
7. Geçmiş test eşleşmesi: birleşik stratejiler Binance verisinde (eğitim/test), legacy ile aynı sinyal sayıları.
8. Tam paket: `python3 -m pytest -q tests` (her PR'da).

## 8. Commit sırası (PR 1)

1. `docs(birlesik)`: görev tanımı + devam notu
2. `birlesik`: ortak alan modelleri + legacy köprüsü + testler
3. `birlesik`: faktör kapsamı + testler
4. `birlesik`: CostEngine + bayraklar + config bloğu + testler
5. `docs(birlesik)`: araştırma denetimi; araştırma kontrolleri tekrar üretilebilir
6. `docs(birlesik)`: bu plan

## 9. PR sırası ve bağımlılıklar

```
PR1 (feat/unified-trader-foundation, main'den)
 └─ PR2 (feat/unified-decision-engine, PR1 dalından)
     └─ PR3 (feat/unified-risk-execution, PR2 dalından)
 └─ PR4 (feat/unified-effectiveness, PR1 dalından; PR3 ile paralel olabilir, karne/yarış kısmı bağımsız)
PR5 (feat/unified-dashboard) ← PR2 + PR3 + PR4 payload'ları
```
Her PR kendi başına legacy davranışı bozmadan çalışır; hiçbiri main'e kullanıcı onayı olmadan merge edilmez.

## 10. PR 2 ayrıntılı tasarım (sıradaki iş)

**Strateji sözleşmesi** (`radar/unified/strategies/base.py`):
```python
class Strategy(Protocol):
    name: str                      # "swing_trend" …
    horizon: Horizon
    timeframes: tuple[str, ...]    # karar + üst + alt zaman dilimleri
    authority: StrategyAuthority   # varsayılan; config ile daraltılabilir, genişletilemez
    required_factors: tuple[str, ...]
    def evaluate(self, ctx: MarketContext) -> list[Opportunity]: ...
```
Kurallar: yalnız `MarketContext` okur, emir/risk/kaldıraç belirlemez; `ledger`, `desk`, `okx`, `execution` import etmez
(test bunu kontrol eder). Tamamlanmamış mumla (CandleContext.complete=False) swing/gün içi kesin sinyal yok.

**Stratejiler** (ilk sürüm legacy çekirdeği sarmalar, davranış eşleşmesi testle doğrulanır):
- `SwingTrendStrategy` ← `team.a_kemal` (+ Donchian kırılımı ve kontrollü geri çekilme kanıt olarak; funding/volatilite/kalite/rejim filtreleri)
- `CompressionBreakoutStrategy` ← `team.a_selin` (TTM varyantı açık; aşırı uzamış giriş reddi, başarısız kırılım kontrolü)
- `ControlledBreakoutStrategy` ← `team.a_kaan` (tek başına ana motor değil: aynı coinde trend/sıkışma fırsatına kanıt da olur)
- `IntradayTrendStrategy` — yeni, **SHADOW_ONLY** (4H yön + 15m/1H geri çekilme; çok sıkı maliyet eşiği)
- `MicrostructureStrategy` — **SHADOW_ONLY**; tick/order-book geçmişi yok → paper risk yok (MICRO_SPECS zorunluları eksik)
- `PanicReversalStrategy` ← `team.a_ayse` (zorunlu kanıtlar: olağan dışı düşüş, tasfiye/zorunlu satış, emilim, yeni dip yok, seviye geri kazanımı, spread normal, BTC çöküşü yok) — shadow/düşük risk
- `ShortTrendStrategy` — yalnız ayı/bozulma rejimi, kendi aşağı trendi, aşağı yapı kırılımı, likidite, short kalabalığı ve squeeze riski kontrolü, net stop, long'dan düşük risk tavanı; naif short sepeti YOK
- `CarryStrategy` ← `harvest.py` + `desk.carry_plan` (ayrı muhasebe, 4 bacak `CostEngine.estimate_carry`)

**Brüt avantaj tahmini:** strateji × rejim × ufuk için geçmiş beklentiden (`EffectivenessSnapshot.avg_r` × stop bp),
örnek sayısına göre sıfıra büzülür (n / (n + 50)); veri yoksa `None` → CostEngine SHADOW. Başlangıç önsel değerleri
Binance eğitim döneminden (`team_test.py` eğitim çıktısı), test döneminden DEĞİL.

**FactorEngine** (`factors.py`): mevcut göstergeleri tek yerde hesaplar (`quant.features`, `team.extra_features`,
`indicators`), `FactorValue` + `FactorCoverage` üretir; mum geometrisi `CandleContext`; rejim `RegimeContext`
(BULL/RECOVERY/RANGE/DETERIORATION/BEAR/CRISIS; girdiler BTC uzun/orta/kısa trend, BTC oynaklığı, genişlik, funding dağılımı).

**UnifiedDecisionEngine** (`decision.py`):
1. Bütün stratejilerden fırsatları topla; `dedup_key` ile birleştir (tek fırsat, kanıt listesi ve strateji listesi birleşir, tek risk bütçesi).
2. Her birleşik fırsat için `FactorCoverage` → yetersizse SHADOW (`ZORUNLU_*` kodları).
3. `CostEngine.estimate` → REJECT / SHADOW / PASS.
4. Aynı coinde long ve short çelişkisi: sıra → rejim izni, net avantaj, veri kapsamı, likidite, stratejinin paper performansı,
   maliyet, mevcut portföy; kaybeden REJECT (sebep kodlu).
5. `DecisionResult` (Türkçe açıklama: neden açıldı/küçültüldü, destekleyen/karşı kanıt, maliyet sonrası kalan, eksik veri, çıkış planı).
6. PR 2'de çıktı yalnız kaydedilir (SHADOW modu); işlem açma PR 3'te RiskGate üzerinden.

**Motor kancası:** `engine.scan_live_daily` sonunda `flags.decision_mode(cfg) is SHADOW` ise birleşik motor çalışır, hata olursa
yakalanır ve legacy'yi etkilemez (try/except + Telegram'a değil yalnız log'a uyarı).

## 11. PR 3–5 kabul ölçütleri (özet)

- **PR 3 RiskGate:** işlem başına risk ~%0,5; sert toplam açık risk; günlük kayıp ve maksimum düşüş devre kesicileri; veri sorunu
  devre kesicisi; kaldıraç = gerekli teminatı karşılayan en düşük değer, stop ile likidasyon arasında sert tampon; yüksek
  oynaklık/düşük likiditede kaldıraç düşer. **PaperExecutionAdapter:** market/limit/IOC, pending→accepted→partially_filled→
  filled/cancelled/expired/rejected/unknown, reduce-only, stop/TP/iz stop/kısmi kâr/başabaş/zaman/rejim/veri sorunu çıkışları,
  belirleyici client order id, olay günlüğü, yeniden başlatma kurtarması, uzlaştırma. Şartları geçen paper işlem otomatik açılır.
  **OKXDemoAdapter:** yalnız sözleşme/stub; çağrılırsa `ExecutionModeError`.
- **PR 4 EffectivenessAuditor:** strateji/rejim/ufuk bazında `EffectivenessSnapshot` (bootstrap güven aralığı, yoğunlaşma,
  maliyet payı), legacy–birleşik ve gölge–paper karşılaştırması, deneme sayısı kaydı (Deflated Sharpe), terfi ölçütü
  (örnek ≥ 100 ve güven aralığı alt sınırı > 0).
- **PR 5 Dashboard:** kararlar, veri kapsamı, maliyet dökümü, risk durumu, etkinlik; telefonda okunur.

## 12. Açık sorular (çalışmayı durdurmaz; varsayılan güvenli seçenekle ilerlenir)

1. Görev dosyası 966. satırda kesik (Aşama 8 "Swing" çıkışlarının devamı, EffectivenessAuditor ve Dashboard ayrıntıları yok).
   Varsayım: PR listesindeki başlıklar ve bu plandaki ölçütler.
2. OKX demo API: kullanıcı ileride GitHub Secrets'a ekleyene kadar adaptör stub kalır.
3. Polat birleşik sisteme dahil edilmez (ayrı, negatif sonuçlu, referans olarak korunur).
