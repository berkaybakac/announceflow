# Backlog

Bu dosya V1 disi ama sonraki fazlarda degerli teknik isleri toplar.

## Backlog politikasi

- V1 scope disi her yeni teknik istek bu dosyaya eklenir.
- V1 sprint icinde backlog item'lari implement edilmez.
- V1.1 scope disi talepler bu dosyaya eklenir.
- V1.1 disi her talep once `V1_1_PROGRESS.md` dosyasina notlanir, sonra backlog'a tasinir.

## Kod Denetim Bulgulari (2026-02-27)

### V1.1 ONCESI ZORUNLU (Blocker)

#### BL-STREAM-BLOCKER-01 - Playback state sahipligi daginik

- ID: `BL-STREAM-BLOCKER-01`
- Oncelik: `P0`
- Neden/Risk: Playlist ve playback state birden fazla katmanda dogrudan degistiriliyor. Stream eklendiginde state cakisma/regresyon riski artar.
- Kanit: [main.py:166](/Users/berkaybakac/announceflow/main.py:166), [scheduler.py:224](/Users/berkaybakac/announceflow/scheduler.py:224), [scheduler.py:305](/Users/berkaybakac/announceflow/scheduler.py:305), [scheduler.py:586](/Users/berkaybakac/announceflow/scheduler.py:586), [player_routes.py:171](/Users/berkaybakac/announceflow/routes/player_routes.py:171), [player.py:624](/Users/berkaybakac/announceflow/player.py:624)
- YAGNI Siniri: Buyuk refactor yok; sadece state orkestrasyon sahipligi tek noktaya cekilir.
- Kabul Kriteri: Stream olmayan mevcut akislarda davranis degismeden, state gecisleri tek orkestrasyon noktasi uzerinden ilerler.
- V1.1'i Bloklar mi?: `Evet`

#### BL-STREAM-BLOCKER-02 - Mesai/ezan restore akisinda policy boslugu

- ID: `BL-STREAM-BLOCKER-02`
- Oncelik: `P0`
- Neden/Risk: Restore akisi mesai/ezan policy onceligini her durumda zorunlu kapatmiyor; sessizlik penceresinde istenmeyen geri donus riski var.
- Kanit: [scheduler.py:327](/Users/berkaybakac/announceflow/scheduler.py:327), [scheduler.py:573](/Users/berkaybakac/announceflow/scheduler.py:573)
- YAGNI Siniri: Yeni policy sistemi kurma yok; mevcut karar akisinin mesai+ezan guard'i sertlestirilir.
- Kabul Kriteri: Mesai/ezan aktifken ses geri donmez, bitiste sadece policy'ye uygun geri donus olur.
- V1.1'i Bloklar mi?: `Evet`

#### BL-STREAM-BLOCKER-03 - Agent ag cagrilarinda timeout/UI blok riski

- ID: `BL-STREAM-BLOCKER-03`
- Oncelik: `P0`
- Neden/Risk: Bazi isteklerde timeout yok ve login/discovery akisinda UI thread bloklanabilir. Stream butonu eklendiginde kullanici deneyimi bozulur.
- Kanit: [agent.py:318](/Users/berkaybakac/announceflow/agent/agent.py:318), [agent.py:337](/Users/berkaybakac/announceflow/agent/agent.py:337), [agent.py:349](/Users/berkaybakac/announceflow/agent/agent.py:349), [agent.py:359](/Users/berkaybakac/announceflow/agent/agent.py:359), [agent.py:369](/Users/berkaybakac/announceflow/agent/agent.py:369), [agent.py:379](/Users/berkaybakac/announceflow/agent/agent.py:379), [agent.py:394](/Users/berkaybakac/announceflow/agent/agent.py:394), [agent.py:611](/Users/berkaybakac/announceflow/agent/agent.py:611)
- YAGNI Siniri: Yeni UI framework yok; sadece timeout standardizasyonu ve bloklamayan ag cagrisi.
- Kabul Kriteri: Ag hatasinda UI donmaz, istekler belirli surede timeout olur.
- V1.1'i Bloklar mi?: `Evet`

#### BL-STREAM-BLOCKER-04 - Guvenlik taban riski (varsayilan sifre / plaintext)

- ID: `BL-STREAM-BLOCKER-04`
- Oncelik: `P1`
- Neden/Risk: Varsayilan admin sifre ve plaintext saklama/karsilastirma yaklasimi guvenlik acigi olusturur.
- Kanit: [config_service.py:25](/Users/berkaybakac/announceflow/services/config_service.py:25), [web_panel.py:114](/Users/berkaybakac/announceflow/web_panel.py:114), [settings_routes.py:46](/Users/berkaybakac/announceflow/routes/settings_routes.py:46), [credential_manager.py:84](/Users/berkaybakac/announceflow/agent/credential_manager.py:84)
- YAGNI Siniri: Kurumsal IAM/pairing katmani yok; yalnizca temel sifre ve credential sertlestirme.
- Kabul Kriteri: Varsayilan sifre ile canli kullanim kalmaz; plaintext bagimliligi minimize edilir.
- V1.1'i Bloklar mi?: `Evet`

#### BL-STREAM-BLOCKER-05 - Release gate zayifligi (ortama bagli API testi)

- ID: `BL-STREAM-BLOCKER-05`
- Oncelik: `P1`
- Neden/Risk: API testleri ayakta sunucuya bagli oldugu icin CI/sandbox ortamlarda guvenli regresyon sinyali zayif kalabilir.
- Kanit: [test_api.py:60](/Users/berkaybakac/announceflow/tests/test_api.py:60), [test_api.py:67](/Users/berkaybakac/announceflow/tests/test_api.py:67)
- YAGNI Siniri: Tam e2e altyapisi kurma yok; test gate ayrimi (unit/integration) netlestirilir.
- Kabul Kriteri: Ortama bagli testler acik etiketlenir; release gate'de deterministic asama bulunur.
- V1.1'i Bloklar mi?: `Evet`

### V1.1 SONRASI (Non-Blocker / Iyilestirme)

#### BL-STREAM-POST-01 - Prayer API cache miss'te gecikme riski

- ID: `BL-STREAM-POST-01`
- Oncelik: `P1`
- Neden/Risk: Cache miss aninda dis API timeout bekleme suresi scheduler dongusunu uzatabilir.
- Kanit: [prayer_times.py:291](/Users/berkaybakac/announceflow/prayer_times.py:291), [prayer_times.py:394](/Users/berkaybakac/announceflow/prayer_times.py:394)
- YAGNI Siniri: Yeni servis altyapisi yok; timeout/backoff/caching davranisi ince ayar.
- Kabul Kriteri: Cache miss'te scheduler etkisi olculur ve kabul limiti altina indirilir.
- V1.1'i Bloklar mi?: `Hayir`

#### BL-STREAM-POST-02 - SQLite WAL/busy_timeout tuning eksikligi

- ID: `BL-STREAM-POST-02`
- Oncelik: `P2`
- Neden/Risk: Varsayilan sqlite baglanti ayarlari yuk altinda lock/latency davranisini kotulestirebilir.
- Kanit: [base_repository.py:24](/Users/berkaybakac/announceflow/database/base_repository.py:24)
- YAGNI Siniri: Veritabani degisimi/migration yok; sqlite pragma tuning seviyesiyle sinirli.
- Kabul Kriteri: WAL/busy_timeout etkisi benchmark ile dogrulanir ve lock olaylari azalir.
- V1.1'i Bloklar mi?: `Hayir`

#### BL-STREAM-POST-03 - Library sayfasinda dosya stat maliyeti

- ID: `BL-STREAM-POST-03`
- Oncelik: `P2`
- Neden/Risk: Her sayfa yuklemede toplu `stat` maliyeti buyuk kutuphanelerde UI gecikmesine neden olabilir.
- Kanit: [web_panel.py:306](/Users/berkaybakac/announceflow/web_panel.py:306)
- YAGNI Siniri: Buyuk cache katmani yok; olcum + hafif optimizasyon.
- Kabul Kriteri: Library acilis suresi buyuk kutuphanelerde kabul edilen sureye cekilir.
- V1.1'i Bloklar mi?: `Hayir`

#### TECH-DEBT-IO-LOCK - Player lock altinda audio I/O liveness riski

- ID: `TECH-DEBT-IO-LOCK`
- Oncelik: `P1`
- Neden/Risk: `player.play()` icinde `self._lock` altinda backend baslatma yapiliyor; `subprocess.Popen`, `time.sleep` ve backend cagrilari lock bekleme zinciri olusturup API/scheduler liveness davranisini bozabilir.
- Kanit: [player.py:450](/Users/berkaybakac/announceflow/player.py:450), [player.py:491](/Users/berkaybakac/announceflow/player.py:491), [player.py:527](/Users/berkaybakac/announceflow/player.py:527)
- YAGNI Siniri: V1.2'de lock kapsami daraltma + actor/command queue; buyuk framework degisimi yok.
- Kabul Kriteri:
  - Lock sadece in-memory state commit icin kullanilir (I/O-free critical section).
  - Audio backend I/O timeout/abort guard ile lock disinda calisir.
  - Eszamanli scheduler/UI komutlarinda deterministic state gecisi ve liveness korunur.
- Not: Kullanicı arayuzunden art arda play/next basilmasi durumunda olusacak I/O spam riski de V1.2 Command Queue ile cozulecek.
- Eventual consistency notu: V1.1'de DB uyuşmazlığı riski urun dogasi geregi kabul edilir; actor model ile V1.2'de kapanir.
- V1.1'i Bloklar mi?: `Hayir`

## Stream V1 sonrasi backlog

### BL-STREAM-NET-01 - Ag dayaniklilik hardening

- Konu: Wi-Fi jitter/loss etkilerini azaltma
- Icerik:
  - retry/backoff tuning
  - jitter/loss hardening ayarlari
  - gecikme ve kopma metriklerinin gozlenmesi
  - QoS ve kablolu ag operasyon notlari
- Etiket: `V1'i bloklamaz`
- Not: Bu madde V1 sonrasi ele alinacaktir.

### BL-STREAM-MOD-01 - Protocol adapter genelleme

- Konu: Receiver/sender protokol adaptasyonunu genisletilebilir hale getirme
- Icerik:
  - `receiver_backend` secim mekanizmasini soyutlama
  - protokol bazli adapter contract tanimi
  - mevcut V1 davranisini bozmadan yeni backend ekleme senaryosu
- Etiket: `V1'i bloklamaz`

### BL-STREAM-MOD-02 - OS-specific sender abstraction genisletme

- Konu: Agent sender katmanini OS bagimsiz genisletme
- Icerik:
  - sender komut uretimini OS bazli alt katmanlara ayirma
  - Windows disi denemeler icin test harness tanimi
  - UI katmanindan platform detaylarini tamamen ayirma
- Etiket: `V1'i bloklamaz`
