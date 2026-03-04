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
- Durum: `Kapatildi`
- Kapanis Tarihi: `2026-02-27`
- Kapanis Commit: `a31a98a`
- Dogrulama: `pytest -q tests/test_schedule_conflicts.py (PASS), pytest -q tests/test_api.py (PASS), manuel smoke PASS`
- Devreden Risk/Not: `TECH-DEBT-IO-LOCK` maddesine tasindi (V1.2 actor/command queue ile kapanacak).
- V1.1'i Bloklar mi?: `Hayir (Kapatildi)`

#### BL-STREAM-BLOCKER-02 - Mesai/ezan restore akisinda policy boslugu

- ID: `BL-STREAM-BLOCKER-02`
- Oncelik: `P0`
- Neden/Risk: Restore akisi mesai/ezan policy onceligini her durumda zorunlu kapatmiyor; sessizlik penceresinde istenmeyen geri donus riski var.
- Kanit: [scheduler.py:327](/Users/berkaybakac/announceflow/scheduler.py:327), [scheduler.py:573](/Users/berkaybakac/announceflow/scheduler.py:573)
- YAGNI Siniri: Yeni policy sistemi kurma yok; mevcut karar akisinin mesai+ezan guard'i sertlestirilir.
- Kabul Kriteri: Mesai/ezan aktifken ses geri donmez, bitiste sadece policy'ye uygun geri donus olur.
- Durum: `Kapatildi`
- Kapanis Tarihi: `2026-02-28`
- Kapanis Commitleri: `a72f29f, 711de6a, d4d945d`
- Dogrulama:
  - `pytest -q tests/test_silence_policy.py (PASS)`
  - `pytest -q tests/test_prayer_cache_horizon.py (PASS)`
  - `pytest -q tests/test_schedule_conflicts.py (PASS)`
  - `pytest -q tests/test_api.py (PASS)`
  - `python3 simulate_smoke.py --dry-run (PASS)`
- Devreden Risk/Not:
  - `unknown => silence` hard-constraint trade-off'u korunuyor (false-positive sessizlik riski).
  - Saha canli smoke (gercek vakit penceresi) operasyonel olarak halen gerekli.
- V1.1'i Bloklar mi?: `Hayir (Kapatildi)`

#### BL-STREAM-BLOCKER-03A - Agent ag cagrilarinda timeout/non-blocking standardizasyon

- ID: `BL-STREAM-BLOCKER-03A`
- Oncelik: `P0`
- Neden/Risk: Bazi isteklerde timeout yok ve login/discovery akisinda UI thread bloklanabilir. Stream butonu eklendiginde kullanici deneyimi bozulur.
- Kanit: [agent.py:318](/Users/berkaybakac/announceflow/agent/agent.py:318), [agent.py:337](/Users/berkaybakac/announceflow/agent/agent.py:337), [agent.py:349](/Users/berkaybakac/announceflow/agent/agent.py:349), [agent.py:359](/Users/berkaybakac/announceflow/agent/agent.py:359), [agent.py:369](/Users/berkaybakac/announceflow/agent/agent.py:369), [agent.py:379](/Users/berkaybakac/announceflow/agent/agent.py:379), [agent.py:394](/Users/berkaybakac/announceflow/agent/agent.py:394), [agent.py:611](/Users/berkaybakac/announceflow/agent/agent.py:611)
- YAGNI Siniri: Yeni UI framework yok; sadece timeout standardizasyonu ve bloklamayan ag cagrisi.
- Kabul Kriteri: Tum ag operasyonlarinda explicit timeout, NetworkWorker ile non-blocking cagri, shutdown pratik bounded-time davranisi.
- Durum: `Kapatildi`
- Kapanis Tarihi: `2026-03-03`
- Kapanis Commitleri: `46b57a7, 381faec`
- Dogrulama:
  - 10+ ag operasyonunda explicit timeout dogrulandi (DEFAULT 2/5s, LOGIN 2/10s, UPLOAD 3/30s)
  - `NetworkWorker.shutdown(wait=False, cancel_futures=True)` kuyruktaki isleri iptal eder; calisan ag istegi (or. upload timeout penceresi) dogal suresinde tamamlanir
  - Session leak fix: except handler'larda `session.close()` eklendi (381faec)
- Devreden Risk/Not: Yok. `login()` icindeki `session = None` init + `if session is not None` guard ile NameError edge-case tamamen kapatildi (381faec).
- V1.1'i Bloklar mi?: `Hayir (Kapatildi)`

#### BL-STREAM-BLOCKER-03B - Agent ag katmani kalan iyilestirmeler

- ID: `BL-STREAM-BLOCKER-03B`
- Oncelik: `P1`
- Neden/Risk: BL-03 kapsaminda kalan iyilestirmeler (POST-01, POST-04 vb.) stash'te bekliyor.
- Kanit: `stash@{0}` ("wip: BL-03/POST-01/POST-04 tum uncommitted work")
- YAGNI Siniri: Stash icerigi dokunulmadan parkta bekletilir; scope degerlendirmesi stream sonrasina birakilir.
- Durum: `Ertelendi (Stash'te)`
- V1.1'i Bloklar mi?: `Hayir (Ertelendi)`

#### BL-STREAM-BLOCKER-04 - Guvenlik taban riski (varsayilan sifre / plaintext)

- ID: `BL-STREAM-BLOCKER-04`
- Oncelik: `P1`
- Neden/Risk: Varsayilan admin sifre ve plaintext saklama/karsilastirma yaklasimi guvenlik acigi olusturur.
- Kanit: [config_service.py:25](/Users/berkaybakac/announceflow/services/config_service.py:25), [web_panel.py:114](/Users/berkaybakac/announceflow/web_panel.py:114), [settings_routes.py:46](/Users/berkaybakac/announceflow/routes/settings_routes.py:46), [credential_manager.py:84](/Users/berkaybakac/announceflow/agent/credential_manager.py:84)
- YAGNI Siniri: Kurumsal IAM/pairing katmani yok; yalnizca temel sifre ve credential sertlestirme.
- Kabul Kriteri: Varsayilan sifre ile canli kullanim kalmaz; plaintext bagimliligi minimize edilir.
- Durum: `Kapatildi`
- Kapanis Tarihi: `2026-03-04`
- Dogrulama:
  - Sifre hash: werkzeug generate_password_hash/check_password_hash ile hash'li saklama ve dogrulama
  - Zorunlu degisiklik: admin123 ile login yapildiginda /change-password'a yonlendirme
  - Legacy uyumluluk: plaintext config'ler hash check'e gecisli desteklenir
  - Settings uzerinden sifre degistirme hash olarak kaydedilir
  - Agent fallback dosyasi chmod 600 ile korunur
  - 14 yeni test PASS (test_password_hash.py)
- Stream'i Bloklar mi?: `Hayir (Kapatildi)`
- Release'i Bloklar mi?: `Hayir (Kapatildi)`

#### BL-STREAM-BLOCKER-05 - Release gate zayifligi (ortama bagli API testi)

- ID: `BL-STREAM-BLOCKER-05`
- Oncelik: `P1`
- Neden/Risk: API testleri ayakta sunucuya bagli oldugu icin CI/sandbox ortamlarda guvenli regresyon sinyali zayif kalabilir.
- Kanit: [test_api.py:60](/Users/berkaybakac/announceflow/tests/test_api.py:60), [test_api.py:67](/Users/berkaybakac/announceflow/tests/test_api.py:67)
- YAGNI Siniri: Tam e2e altyapisi kurma yok; test gate ayrimi (unit/integration) netlestirilir.
- Kabul Kriteri: Ortama bagli testler acik etiketlenir; release gate'de deterministic asama bulunur.
- Durum: `Kapatildi`
- Kapanis Tarihi: `2026-03-03`
- Kapanis Commit: `34c0073`
- Dogrulama:
  - Integration testleri `ANNOUNCEFLOW_RUN_LIVE_API_TESTS=1` env var arkasina alindi
  - `pytest.ini` icinde `integration` marker tanimi eklendi
  - `pytest -q` (env var olmadan) integration testlerini atlar, unit testleri calistirir
- Devreden Risk/Not: CI pipeline otomasyonu V1.2 backlog'unda.
- V1.1'i Bloklar mi?: `Hayir (Kapatildi)`

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

## Faz 6 / Release Oncesi Zorunlu

### BL-FAZ6-SOAK - 72 saat soak/stability testi

- ID: `BL-FAZ6-SOAK`
- Oncelik: `P0`
- Neden/Risk: Uzun sureli calisma altinda bellek sizintisi, process zombie, ses cakismasi gibi sorunlar ancak soak testle ortaya cikar.
- Kabul Kriteri: En az 72 saat kesintisiz canli stream + scheduler yuklu calismada cokme/kilitlenme/ses cakismasi yok.
- Durum: `Acik`
- Release'i Bloklar mi?: `Evet`

### BL-FAZ6-FROZEN-EXE - Frozen Windows EXE gercek ortam dogrulamasi

- ID: `BL-FAZ6-FROZEN-EXE`
- Oncelik: `P0`
- Neden/Risk: CI runner'da Python kurulu; Python'suz temiz Windows ortaminda bundled ffmpeg ile stream sender'in calismasi dogrulanmadi.
- Kabul Kriteri: Python yuklu olmayan Windows makinede `AnnounceFlowAgent.exe` baslatilir, "Yayini Baslat" ile ffmpeg sender process spawn edilir, en az 10 saniye calisir, "Yayini Durdur" ile durur.
- Durum: `Acik`
- Release'i Bloklar mi?: `Evet`

### BL-FAZ6-DEPPIN - Dependency version pinning

- ID: `BL-FAZ6-DEPPIN`
- Oncelik: `P1`
- Neden/Risk: Sabitlenmemis dependency versiyonlari build tekrarlanabilirligini bozabilir.
- Kabul Kriteri: `requirements.txt` veya esdeser dosyada tum production dependency'ler pinlenmis.
- Durum: `Kapatildi`
- Kapanis Tarihi: `2026-03-04`
- Dogrulama:
  - requirements.txt: sadece server (Pi4) paketleri, tum versiyonlar == ile pinli
  - agent/requirements-agent.txt: sadece agent (Windows) paketleri, tum versiyonlar == ile pinli
  - CI workflow: `pip install -r agent/requirements-agent.txt` kullanir (unpinned install kaldirildi)
  - typing-extensions>=4.10.0 -> ==4.10.0, keyring agent dosyasina tasindi ==24.0.0
  - pystray ve Pillow artik agent requirements'ta pinli
- Release'i Bloklar mi?: `Hayir (Kapatildi)`

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

### BL-STREAM-POLICY-01 - Recurring schedule skip DB izi

- ID: `BL-STREAM-POLICY-01`
- Oncelik: `P3`
- Kaynak: Faz 4 audit W3
- Konu: Stream aktifken atlanan recurring schedule'larin DB'de izlenebilir kaydinin olmamasi
- Mevcut davranis: Sadece log yaziliyor, DB'de iz yok
- Icerik:
  - Atlanan recurring schedule icin DB kaydi veya metrik olusturma
  - Dashboard/reporting icin skip sayaci
- Etiket: `V1'i bloklamaz`
- Not: Recurring schedule bir sonraki tetik zamaninda tekrar denenecegi icin fonksiyonel risk dusuk.

### BL-STREAM-POLICY-02 - Singleton StreamService test izolasyonu

- ID: `BL-STREAM-POLICY-02`
- Oncelik: `P2`
- Kaynak: Faz 4 audit W4
- Konu: `get_stream_service()` global singleton'u test suiteleri arasinda state sizmasina neden olabilir
- Mevcut davranis: Testler mock/patch ile calistigi icin su an sorun yok
- Icerik:
  - Test fixture'da singleton reset mekanizmasi ekleme
  - Alternatif: DI (dependency injection) ile test-local instance olusturma
- Etiket: `V1'i bloklamaz`
- Not: Test suite buyudukce onem kazanir; su an fonksiyonel risk yok.

### BL-AGENT-UI-01 - AgentGUI callback helper refactor

- ID: `BL-AGENT-UI-01`
- Oncelik: `P3`
- Kaynak: Faz 5 kapanisi
- Konu: `_submit_network_job + messagebox` tekrar patternini private helper'a toplama
- Mevcut davranis: Her callback (start/stop music, stream start/stop, upload) kendi `_job` + `_on_done` tanimliyor
- Neden simdi yapilmadi: Callback'ler farkli akislara sahip (rollback, dosya secici, basit bool); ortak helper parametreleri sisirir veya if/else dallanmasi yaratir
- Kabul Kriteri: Tekrar eden `_job` + `_on_done` bloklari tek helper uzerinden calisir; mevcut testler PASS, davranis degismez.
- Durum: `Acik`
- Tetik: Faz 6+ yeni callback eklerse pattern netlesir ve refactor hakli olur
- Etiket: `V1'i bloklamaz`
