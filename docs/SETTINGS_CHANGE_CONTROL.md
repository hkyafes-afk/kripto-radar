# Güvenli config değişikliği, audit ve geri alma

Dashboard ayar kataloğu salt okunurdur. Değişiklik talebi yalnızca `.github/workflows/settings-change-request.yml` üzerinden ve `main` ref'inde açılan manuel workflow ile yapılır. Her sonuç taslak PR'dır; config otomatik olarak çalışan motora uygulanmaz.

## Değer değiştirme talebi

1. GitHub Actions → **Secure settings change request** → **Run workflow**.
2. `change_mode=apply`, izinli parametre ve yeni sayısal değeri seç.
3. Workflow config SHA-256 değerini okuyup aynı sürümü beklediğini bildirir. Script dosyayı tekrar okuyarak eşzamanlı değişikliği reddeder.
4. Yalnız izin verilen path/tip/aralık kabul edilir. YAML yorumları korunur.
5. `.settings-change-audit.jsonl` içine eski/yeni değer, önce/sonra SHA-256, aktör, workflow run ve zaman damgası yazılır.
6. Workflow yalnız `config.yaml` ve audit kaydını içeren taslak PR açar. CI ve insan incelemesi gerekir.

İzin listesi `scripts/settings_change.py` içindeki `ALLOWLIST` sabitidir. `birlesik.*`, borsa erişimi, ekip yetkileri, demo/canlı modu, sırlar ve API kimlik bilgileri bu mekanizmanın dışındadır.

## Geri alma talebi

Geri alma, önceki değişikliği doğrudan main üzerinde uygulamaz; yine taslak PR oluşturur.

1. Önceki apply audit satırındaki `event_id` değerini bul.
2. Workflow'u `change_mode=rollback` ve bu `rollback_id` ile çalıştır.
3. Script config'in SHA-256 değerinin uygulama kaydındaki `after_sha256` ile birebir aynı olmasını şart koşar.
4. Config sonradan değişmişse geri alma reddedilir; başka değişikliklerin üstüne yazılmaz.
5. Kabul edilen geri alma yeni audit satırını `rollback_of` alanıyla ilişkilendirir.

## İşlem garantileri ve sınırlar

- Tek parametre değişikliği, sabit allowlist ve sınır kontrolünden geçer.
- Config yazımı geçici dosya + fsync + atomic replace ile yapılır.
- Audit dosyasına kayıt yazılamazsa config mümkün olduğu ölçüde önceki içeriğine döndürülür; hata sıfırdan başarılı olarak raporlanmaz.
- SHA-256 kontrolü eşzamanlı düzenlemeyi saptar; GitHub Actions concurrency grubu talepleri sıraya alır.
- Audit dosyası PR ile birlikte sürüm kontrolüne girer. Audit geçmişi Git geçmişiyle birlikte korunmalıdır.
- Bu mekanizma config'i çalışan sisteme canlı olarak enjekte etmez. PR birleştirildikten sonra motorun bir sonraki normal config yükleme davranışı geçerlidir; yeniden başlatma gereksinimi tüm parametreler için henüz otomatik sınıflandırılmış değildir.
