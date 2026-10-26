# 🛰 Kripto Radar

OKX perpetual piyasasını **her saat** tarayan, sinyalleri **Telegram'a** gönderen, sonuçları
**telefondan açılan bir dashboard'da** gösteren ve her sinyalin karnesini **otomatik kağıt üstü
defterde** tutan karar destek aracı. Bilgisayar gerekmez: her şey GitHub'ın ücretsiz sunucularında çalışır.

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

### 3) İlk taramayı başlat
**Actions** sekmesi → (sorarsa *I understand… enable* de) → soldan **Tarama** → **Run workflow** → yeşil buton.
3–4 dakika sürer. Bitince yeşil tik görürsün.

### 4) Dashboard'u aç
**Settings** → **Pages** → *Source:* **Deploy from a branch** → *Branch:* **gh-pages** / **(root)** → **Save**.
1–2 dakika sonra dashboard şu adreste: `https://KULLANICI_ADIN.github.io/REPO_ADI/`
Telefonda açıp **Ana ekrana ekle** de; uygulama gibi açılır.

### 5) (İsteğe bağlı) WhatsApp
CallMeBot'un numarasını rehbere ekle, ona WhatsApp'tan `I allow callmebot to send me messages` yaz.
Gelen API anahtarını `CALLMEBOT_APIKEY`, numaranı `CALLMEBOT_PHONE` olarak ekle. Ücretsiz bir
üçüncü parti servis olduğu için gecikebilir; ana kanal Telegram.

Bitti. Bundan sonra her saat başı (~:07) kendiliğinden tarar.

---

## Ne yapar?

| Bölüm | Açıklama |
|---|---|
| **Tarama** | 24s hacme göre en likit 100 OKX USDT perpetual, 4 saatlik kapanmış mumlar |
| **Sinyaller** | 4 setup, her biri 0–100 puan: Trend Long/Short, Dönüş Long/Short |
| **Rejim filtresi** | BTC günlük EMA200 altında long'lar %50, boğada short'lar kırpılır (ham puan ayrıca saklanır) |
| **Funding harvest** | Spot+perp delta-nötr carry adayları: 30g ortalama, istikrar, başabaş gün, diğer borsalarla karşılaştırma |
| **Kağıt üstü defter** | Her güçlü sinyal sanal pozisyon açar; stop/hedef/süre kurallarıyla otomatik kapanır, karne çıkar |
| **Bildirim** | Yeni güçlü sinyal, kapanan işlem, günlük 09:00 özeti, art arda 2 hata uyarısı |
| **Backtest** | Her pazar walk-forward test (parametre ilk %70'te seçilir, son %30'da ölçülür) |

### Telegram komutları
`/durum` · `/sinyaller` · `/coin SOL` · `/funding` · `/karne` · `/tara` · `/sessiz 8` · `/ac`
(Cevaplar en geç ~10 dk içinde gelir; GitHub zamanlayıcısı böyle çalışıyor.)

---

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
radar/            Python paketi (göstergeler, sinyal motoru, defter, dashboard, Telegram)
config.yaml       Ayarlar
.github/workflows Tarama (saatlik), Telegram komutları (10 dk), Backtest (haftalık)
scripts/site.sh   Dashboard + defter dosyalarını gh-pages dalında tutar (repo şişmez)
tests/            Çevrimdışı testler: python tests/test_offline.py
data/KARNE.md     Haftalık karne (otomatik)
```

## Sık sorunlar
- **Dashboard 404:** Pages ayarında dal olarak `gh-pages` seçili mi? İlk tarama yeşil bitti mi?
- **Telegram mesajı gelmiyor:** Bota `/start` dedin mi? Secret adı tam olarak `TELEGRAM_BOT_TOKEN` mı?
- **"Veri X saattir güncellenmedi":** Actions sekmesinde kırmızı çarpı var mı, logu aç. 60 gün hiç
  aktivite olmayan public repolarda GitHub zamanlanmış işleri durdurur; haftalık karne commit'i bunu önler.
- **Maliyet:** Public repoda GitHub Actions ücretsizdir. Repo private olursa aylık dakika limiti dolar ve Pages ücretli plana kalır.
