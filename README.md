# 🛰 Kripto Radar

OKX perpetual piyasasını **canlı** izleyen (15dk / 1S / 4S mum kapanır kapanmaz tarar), sinyalleri
anında **Telegram'a** gönderen, her güçlü sinyalde **20$'lık demo işlem** açıp stop/hedefte kendisi
kapatan ve kâr/zararı **telefondan açılan bir dashboard'da** gösteren karar destek aracı. Bilgisayar gerekmez: her şey GitHub'ın ücretsiz sunucularında çalışır.

> ⚠️ **Otomatik emir vermez. Yatırım tavsiyesi değildir.** "Buna bak" diyen bir radardır.
> Defter en az 30 kapanmış işlemde pozitif beklenti göstermeden hiçbir setup'a para bağlama.

---

## Telefondan kurulum (10 dakika)

### 1) Telegram botunu oluştur
1. Telegram'da **@BotFather**'ı aç → `/newbot` yaz.
2. Bota bir isim ve `_bot` ile biten bir kullanıcı adı ver (ör. `kanka_radar_bot`).
3. BotFather sana uzun bir **token** verir (`123456:ABC-...`). Kopyala.
4. Kendi botunu aç ve **Başlat / `/start`** de. (Bot seni sahibi olarak kaydeder.)

### 2) Token'ı GitHub'a gizli olarak ekle
Repo sayfası → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| İsim | Değer | Zorunlu mu |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather'ın verdiği token | ✅ |
| `TELEGRAM_CHAT_ID` | Senin sohbet ID'n (@userinfobot söyler) | İsteğe bağlı* |
| `CALLMEBOT_PHONE` | WhatsApp numaran, `+905xx...` | İsteğe bağlı |
| `CALLMEBOT_APIKEY` | CallMeBot'un verdiği anahtar | İsteğe bağlı |

\* Eklemezsen bota ilk `/start` yazan sohbet sahip olarak kaydedilir. Eklersen daha güvenli ve
sohbet ID'n herkese açık dosyalarda görünmez.

> Telefonda GitHub ayarları görünmüyorsa tarayıcıda **"Masaüstü sitesi"** seçeneğini aç.

### 3) Canlı döngüyü başlat
**Actions** sekmesi → soldan **Canlı döngü** → **Run workflow** → yeşil buton.
Döngü ~5,5 saat açık kalır; saatlik tetik bir sonrakini sıraya koyar, böylece kesintisiz çalışır.

### 4) Dashboard'u aç
**Settings** → **Pages** → *Source:* **Deploy from a branch** → *Branch:* **gh-pages** / **(root)** → **Save**.
1–2 dakika sonra dashboard şu adreste: `https://KULLANICI_ADIN.github.io/REPO_ADI/`
Telefonda açıp **Ana ekrana ekle** de; uygulama gibi açılır.

### 5) (İsteğe bağlı) WhatsApp
CallMeBot'un numarasını rehbere ekle, ona WhatsApp'tan `I allow callmebot to send me messages` yaz.
Gelen API anahtarını `CALLMEBOT_APIKEY`, numaranı `CALLMEBOT_PHONE` olarak ekle. Ücretsiz bir
üçüncü parti servis olduğu için gecikebilir; ana kanal Telegram.

Bitti. Bundan sonra kendi kendine sürekli çalışır.

---

## Ne yapar?

| Bölüm | Açıklama |
|---|---|
| **Canlı veri** | OKX ve Bitget'e WebSocket ile sürekli bağlı; her fiyat değişiminde (~100 ms) stop/hedef kontrolü. Koparsa kendiliğinden yeniden bağlanır, bu arada REST ile 5 sn'de bir fiyat alınır |
| **Sinyal hızı** | 15dk, 1S ve 4S mumlar kapanır kapanmaz tarama (mum kesinleşene kadar 1-2 sn aralıkla tekrar); tarama ayrı thread'de, stop takibini bekletmez |
| **Telegram** | Long polling ile komutlara anında cevap, gönderim kuyruğu, hız sınırında bekleyip tekrar deneme, işlem mesajında ❌ Kapat butonu, bot kapalı kaldıysa haber |
| **Tarama** | 24s hacme göre en likit 100 OKX USDT perpetual (sadece kripto; hisse ve emtia kontratları hariç) |
| **Risk yönetimi** | İşlem başı bakiyenin %0,5'i risk (stop mesafesine göre büyüklük), toplam açık risk ≤ %3, aynı yönde ≤ 5 işlem ve ≤ %2 risk, günlük %2 kayıpta o gün dur, zirveden %10 düşüşte botu durdur (/devam), 4 kayıp serisinde riski yarıya indir, 30 işlemden sonra negatif beklentili setup'ı kapat |
| **İşlem yönetimi** | +1R'de yarısını limit emirle kapat, stop'u başabaş + ücretlere çek, kalanı 2 ATR iz süren stop ile takip et |
| **Canlı K/Z** | Dashboard telefondan doğrudan OKX'e bağlanır, net K/Z'yi (ücret ve funding dahil) saniyede 3 kez günceller; Telegram'da sabit canlı panel 15 sn'de bir yenilenir |
| **Metrikler** | Beklenti (R), kâr faktörü, kazanç/kayıp oranı, isabet, maksimum düşüş, Sharpe, Sortino, seriler |
| **Demo hesap** | 1.000$ sanal bakiye, risk bazlı büyüklük (istenirse sabit 20$), coin başına tek pozisyon. Gerçek kurallar: giriş kitaptaki ask/bid'den, OKX kontrat ve lot yuvarlaması, OKX ücretleri (piyasa %0,05 / limit %0,02), hedef limit emir (fiyat içinden geçmeli), stop piyasa emri (kötü dolum dahil), funding gerçek kesim saatinde gerçek oranla, kaldıraçta likidasyon |
| **Çin borsaları** | Bitget canlı akış; Bitget, Gate, MEXC, HTX funding oranları; borsalar arası funding arbitraj listesi ve OKX↔Bitget fiyat farkı |
| **Sinyaller** | 4 setup, her biri 0–100 puan: Trend Long/Short, Dönüş Long/Short |
| **Rejim filtresi** | BTC günlük EMA200 altında long'lar %50, boğada short'lar kırpılır (ham puan ayrıca saklanır) |
| **Funding harvest** | Spot+perp delta-nötr carry adayları: 30g ortalama, istikrar, başabaş gün, diğer borsalarla karşılaştırma |
| **Karne** | Setup, periyot ve gösterge bazında isabet, ortalama R ve dolar K/Z |
| **Bildirim** | Yeni sinyal + açılan demo işlem, kapanan işlem (K/Z ile), günlük 09:00 özeti, art arda hata uyarısı |
| **Backtest** | Her pazar walk-forward test (parametre ilk %70'te seçilir, son %30'da ölçülür) |

### Telegram komutları
`/durum` · `/sinyaller` · `/demo` · `/pozisyonlar` · `/kapat SOL` · `/risk` · `/devam` · `/borsalar` · `/saglik` · `/coin SOL` · `/funding` · `/karne` · `/tara` · `/sessiz 8` · `/ac`
(Döngü açıkken cevaplar birkaç saniyede gelir.)

---

## Yatırım ekibi (v7)

1.000$ başlangıç sermayesi 7 yöneticiye eşit dağıtılır. Her yönetici kendi tarzıyla fikir üretir; risk müdürü onaylar.

| Yönetici | Tarz | Periyot | İşlem başı risk |
|---|---|---|---|
| 🧭 Kemal | Swing: trend + günlük trend, geri çekilmede limit emir | 4S | %1,5–2,5 |
| ⚡ Deniz | Gün içi: trend içi RSI geri çekilmesinden dönüş, en fazla 8 saat | 15dk | %1,5–2,5 |
| 🔄 Ayşe | Kontrarian: funding uçta + aşırı uzama → ortalamaya dönüş | 1S | %1,5–2,5 |
| 📐 Murat | Teknik: son dalganın %50–70,5 Fibonacci bölgesinde dönüş onayı | 1S, 4S | %1,5–2,5 |
| ⚖️ Elif | Piyasa-nötr: spot al + vadeli short, istikrarlı funding toplar | 8 saatlik funding | yön riski yok |
| 🚀 Kaan | Kırılım (agresif): hacimli aralık kırılımı + yeni para girişi | 1S, 4S | %3–5 |
| 🌪 Selin | Squeeze (agresif): en dar volatiliteden patlama + sıkışan kalabalık | 15dk, 1S | %3–5 |

- **Güven = tutan doğrulama oranı.** Risk tabandan tavana kanıtla büyür; az kanıtla büyük oynanamaz.
- **Risk müdürü:** işlem sayısı sınırı yok; sınırlar risk cinsinden: firma toplam riski %10, aynı coinde %2,5, aynı yönde %6,
  günlük kayıp %3 (o gün durur), zirveden %15 düşüş (durur, /devam).
- **Yedek kulübesi:** zirvesinden %20 (agresiflerde %30) düşen yönetici gölgede sermayesiz işlem yapar; 10 işlemde +0,1R
  ortalama yaparsa geri döner. **Haftalık sermaye dağıtımı:** son 30 günün ortalama R'sine göre.
- **Maliyetler kâra dahil:** OKX vadeli piyasa %0,05 / limit %0,02, spot %0,10, gerçek bid/ask ve derinlik (VWAP), funding
  gerçek kesim anında, stop'ta kötü dolum.
- **Karar günlüğü:** her açılış, red, iptal ve sermaye kararı gerekçesiyle kaydedilir (dashboard → Ekip).

## 7/24 çalışma ve sağlık

- Her döngü ~5,5 saat sürer; bitmeden 6 dk önce halefini başlatır, halef ısınıp bekler ve nöbeti **birkaç saniyede** devralır.
  Durum her dakika `kalp` dalına yedeklenir. Döngü çökerse 15 dakikalık bekçi devralır.
- **Sağlık denetçisi (30 sn):** donmuş/kopuk akışı yeniden bağlar, ölen thread'leri yeniden başlatır, gecikmiş taramayı,
  muhasebe tutarlılığını (yönetici bakiyeleri toplamı = firma bakiyesi) ve geçersiz pozisyonları kontrol eder; kritik sorunu Telegram'a bildirir.
- **Veri sağlığı:** eksik/gecikmiş mum, canlı fiyatla tutarsızlık ya da Bitget ile %3+ fark olan coinde işlem açılmaz.
- Telegram: `/ekip`, `/yonetici kaan`, `/emirler`, `/saglik`, `/risk`.

## Orijinal prompt'a göre neler değişti ve neden?

| Orijinal (Mac versiyonu) | Bu versiyon | Neden |
|---|---|---|
| Binance API | **OKX API** | GitHub sunucuları ABD'de; Binance ABD IP'lerine 451 hatası veriyor |
| launchd + localhost:8765 | **GitHub Actions + GitHub Pages** | Bilgisayarsız, bedava, telefondan erişim |
| macOS bildirimi | **Telegram (+WhatsApp)** | Telefona anında, komut verilebilir |
| 7 gösterge → tek net skor (±6) | **4 ayrı setup, 0–100 puan** | Trend ve ortalamaya dönüş göstergeleri zıt şeyler söyleyip birbirini götürüyordu; orijinal backtest Sharpe −0.7 |
| Elle Excel/Notion defteri | **Otomatik defter + karne** | Hangi setup'ın ve hangi göstergenin işe yaradığını veri gösterir |
| %55 isabet kuralı | **Ortalama R / beklenti** | 2R hedefte başabaş isabet ≈ %33; isabet tek başına yanıltıcı |
| Tek dönem backtest | **Walk-forward backtest** | Aynı veride optimize edip test etmek kendini kandırmaktır |
| Simetrik rejim | **Short'lara ek ceza** | Araştırma: kriptoda momentum boğa piyasasında yoğunlaşıyor, short tarafı zayıf |
| Funding harvest | **+ başabaş gün, soğuma uyarısı, çoklu borsa** | Carry getirisi 2024'ten beri ciddi daraldı |

---

## Ayarlar
Tüm eşikler `config.yaml` dosyasında, Türkçe açıklamalı. GitHub'da dosyayı açıp kalem ikonuyla
telefondan düzenleyebilirsin; bir sonraki taramada geçerli olur. Bozarsan bot varsayılanlarla çalışmaya devam eder.

Ayar değiştirmeden önce: **backtest'in OOS (görülmemiş veri) sonucuna ve defterin 30+ işlemlik karnesine bak.**
Tek bir iyi haftaya göre ayar değiştirmek en klasik hatadır.

## Dosyalar
```
radar/            Python paketi (göstergeler, sinyal motoru, demo hesap, canlı motor, WebSocket, Telegram, dashboard)
config.yaml       Ayarlar
.github/workflows Canlı döngü (sürekli), Backtest (haftalık)
scripts/site.sh   Dashboard + defter dosyalarını gh-pages dalında tutar (repo şişmez)
tests/            Çevrimdışı testler (sahte WebSocket sunucusu dahil): python tests/test_engine_live.py
data/KARNE.md     Haftalık karne (otomatik)
```

## Sık sorunlar
- **Dashboard 404:** Pages ayarında dal olarak `gh-pages` seçili mi? İlk tarama yeşil bitti mi?
- **Telegram mesajı gelmiyor:** Bota `/start` dedin mi? Secret adı tam olarak `TELEGRAM_BOT_TOKEN` mı?
- **"Veri X saattir güncellenmedi":** Actions sekmesinde kırmızı çarpı var mı, logu aç. 60 gün hiç
  aktivite olmayan public repolarda GitHub zamanlanmış işleri durdurur; haftalık karne commit'i bunu önler.
- **Actions'ta çok sayıda "iptal edildi" görmek normal:** döngü çalışırken gelen saatlik tetikler sıraya girer, fazlası iptal edilir.
- **Uzun süreli döngü hakkında:** GitHub Actions asıl olarak derleme/test için tasarlandı. Sürekli açık döngü public
  repoda ücretsizdir ama GitHub bir gün kısıtlarsa aynı kod küçük bir sunucuda `python -m radar live` ile çalışır.
- **Maliyet:** Public repoda GitHub Actions ücretsizdir. Repo private olursa aylık dakika limiti dolar ve Pages ücretli plana kalır.
