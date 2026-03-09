# Stream Faz Karari

Durum: `aktif`
Tarih: `2026-03-09`

## Nihai Karar

- Stream capture Windows agent icinde kalacak.
- Web panelden stream kontrolu eklenecek, ama bu bir `uyumluluk cozumu` degil, `UX iyilestirmesi` olacak.
- Kullanici ileride stream'i panelden baslatip durdurabilecek.
- Buna ragmen Windows agent yine gerekli olacak; kurulum ihtiyaci tamamen kalkmayacak.

## Neden

- Ses kaynagi Windows PC ise, sistem sesi yakalama isi yine Windows runtime tarafinda yapilmak zorunda.
- Web panel sadece kontrol yuzeyini degistirir.
- Windows/Pi/ag/ses cihazi farklari web panel eklense de teknik olarak devam eder.

## Faz Sirasi

### Faz 1 - Stabilizasyon ✅ TAMAMLANDI (2026-03-07)

Ilk oncelik mevcut EXE tabanli akisi sertlestirmek.

- [x] Bazi Windows cihazlarda bozuk ses / bip / cizirti
  - Block size 735 frame (1470 byte < MTU), sender-side rate fallback + resample
  - scipy.signal.resample ile kalite arttirildi (np.interp yerine)
- [x] Bazi Windows cihazlarda son surumun kurulamamasi (`v1.0` kurulurken yeninin kurulamamasi)
- [x] Hoparlor gittiginde sesin otomatik geri donmemesi
- [x] Log analizi (5 Mart 2026, 20:30 sonrasi kayitlar; once toplu inceleme, sonra son satirlardan ilerle)
  - Diagnostic script, structured telemetry, ALSA xrun sayimi eklendi
- [x] Kalan buglarin kok neden bazli kapatilmasi
  - Async stop, start retry guard, bootstrap guard, consecutive failure counter
- [x] Receiver kapanis kararliligi (stop lifecycle)
  - FFmpeg graceful stop (`q + stdin close` + SIGTERM), stop/start handoff guard, force-kill fallback
- [x] Stop reason telemetry
  - `stream_receiver_stop_reason`: `graceful`, `force_kill`, `already_stopping`, `already_stopped`
- [x] Device ownership gate (Faz 1 koruma → Faz 2 LWW'ye yukseltildi)
  - Ayni `device_id` retry: idempotent success
  - Farkli `device_id` start: son basan kazanir (LWW takeover, bkz. Faz 2)
- [x] ~3 saniyelik gecikme sorunu
  - ffmpeg'e `-probesize 32 -analyzeduration 0` eklendi
  - Sender block size 735 frame (~16ms) olarak dusuruldu
- [x] Platformlar arasi ses kalitesi farki (MacBook vs Windows ayni IP uzerinden)
  - Karsilastirmali loglama mekanizmasi eklendi; saha testi onaylandi
- [x] Farkli Windows cihazlarda ses kalitesi farki (birinde temiz, digerinde bozuk)
  - Sender-side rate fallback (44100 -> 48000) + scipy resample; saha testi onaylandi

Basari kriteri:

- [x] Stream farkli Windows 10/11 cihazlarda kabul edilebilir kaliteyle calisiyor
- [x] Son surum kurulum blocker'i kalmiyor
- [x] Hoparlor geri geldiginde sistem toparlaniyor
- [x] Cizirti/bip yok, ses kalitesi kabul edilebilir
- [x] Log analizinde kritik hata kalmadi

---

### Faz 2 - Web Panelden Kontrol (AKTIF)

Stabilizasyon sonrasi panelden kontrol eklenecek.

Hedef:

- [x] LWW takeover (son basan kazanir) — Faz 1'den one cekildi
  - Farkli `device_id` start: aktif sahiplik devredilir, onceki sender stale sayilir
  - Stop/ack alinmasa bile onceki sender akisina stale muamelesi yapilir; sahibi olmayan akisin etkisi engellenir
  - `device_id` bazli ownership takibi, `owner_device_id` API'de expose edildi
  - Bu politika sender sayisindan bagimsizdir; 2, 3 veya 4 bilgisayar olsa da ayni anda yalnizca bir sender aktif olabilir
  - Best-practice referans: LWW state machine + lease/epoch, agent revoke kanali (polling), data-plane izolasyon (session bazli UDP port veya token/epoch)
  - Detay tasarim/backlog: `BL-STREAM-CTRL-01` (`docs/backlog.md`)
- [x] Ghost session koruması (heartbeat monitoring) — Faz 1'den one cekildi
  - Pi her stream baslattiginda 15 saniye icinde heartbeat bekliyor
  - Heartbeat gelmezse stream otomatik kapatiliyor
  - `/api/stream/heartbeat` endpoint eklendi
- [x] Sender-side takeover polling — Faz 1'den one cekildi
  - Windows agent her 2 saniyede `owner_device_id` kontrol ediyor
  - Sahiplik devredilmisse kendi yayinini durdurup kullaniciya uyari veriyor
- [x] `/api/playlist/set` stream guard
  - Aktif yayinda playlist degistirilemez
- [ ] Agent arka planda calisacak (sistem tepsisine kuculmeli, pencere kapansa da calismali) — es gecildi
- [x] Panelden `stream start / stop` komutu gidecek (`74ec031`)
- [x] Isleyis ayni kalacak; sadece kontrol noktasi panel olacak (EXE kontrolu de calismaya devam edecek) (`0d99138`)
- [x] Stream durumu panelde goruncek (aktif/pasif/hata, aktif cihaz adi) (`74ec031`, `063d2a5`)
- [x] Panel komut arbitraji: stream ve muzik/playlist komutlarinda cakisma — stream baslarken playlist durur, biter biter geri gelir (`stream_service.py`)

Teknik not:

- Mevcut backend API sadece Pi receiver tarafini yonetiyor.
- Windows sender bugun agent icinde lokal olarak basliyor.
- Bu nedenle panel kontrolu icin agent ile panel arasinda ek bir komut/koordinasyon katmani gerekecek.
- Yani bu faz dusuk-orta maliyetli bir UX isidir; sadece `index.html` butonu eklemekten ibaret degildir.

Basari kriteri:

- [x] Panelden stream baslatilip durdurulabiliyor
- [x] Stream durumu panelde gorunuyor (aktif/pasif/hata)
- [x] EXE UI'den kontrol de calismaya devam ediyor

---

### Faz 3 - Genel Urun Iyilestirmeleri

Stream stabilizasyonu ve panel kontrolu tamamlandiktan sonra siradaki urun iyilestirmeleri.

#### Shuffle (Karistirma) Modu

- [ ] Zamanli calma listelerinde dosya sirasini rastgele karistirma
- [ ] Kullanici panelden shuffle acip kapatabilecek
- Detay: `BL-SHUFFLE-01` (`docs/backlog.md`)

#### Chunked Upload (Parcali Yukleme)

- [ ] Buyuk ses dosyalarini parcalara bolerek yukleme
- [ ] Baglanti koparsa kaldigi yerden devam edebilme
- [ ] Mevcut tek seferde yukleme limitini asan dosyalar icin gerekli
- Detay: `BL-CHUNKED-UPLOAD-01` (`docs/backlog.md`)

#### Agent EXE UI Iyilestirmeleri ✅ TAMAMLANDI (0d99138)

- [x] Hostname bazli baglanti: Baglanti bilgisi header'dan kaldirildi; Web Panel butonu zaten erisim sagliyor
- [x] Tipografi: "Cikis" yazisi font/boyut duzenlendi (10pt, tam renk)
- [x] Ses kontrol alani (ModernSlider) tam yeniden tasarlandi: emoji kaldirildi, ince track, handle clamp, card_bg uyumu
- [x] Muzik/yayin aktif durum gostergesi: aktif olmayan buton muted renk (_BTN_MUTED)
- [x] Enter tusu ile login tetikleniyor
- [x] Panel stream stop'unda EXE zombie kalmasi duzeltildi (idle/error state detection)

#### Operasyonel Iyilestirmeler

- [ ] Cihaz erisimi ve kimlik yonetimi
  - Sahaya yayilan cihazlarin kullanici adi/sifre bilgilerinin merkezi takibi
  - Sifre unutulmasi durumunda kurtarma (recovery) proseduru
  - Kisa vade: Cihaz bilgilerini guvenli bir envanter dosyasinda tutma
  - Orta vade: Merkezi yonetim paneli veya provisioning akisi (cihaz sayisi arttikca)
- [ ] Dagitim (deployment) otomasyonu
  - Su an: deploy.sh ile tek cihaza SSH+rsync (3 cihaza kadar yeterli)
  - Kisa vade: deploy.sh'i birden fazla host icin dongulu calistirma
  - Orta vade: Ansible veya benzeri otomasyon (10+ cihazda)
  - Docker su an gereksiz; systemd tabanli kurulum yeterli

#### Dusuk Oncelikli Notlar

- [x] `datetime.utcnow()` deprecation uyarisi — `datetime.now(timezone.utc)` ile duzeltildi

Basari kriteri:

- [ ] Shuffle modu panelden acilip kapatilabiliyor, rastgele sira dogru calisiyor
- [ ] Buyuk dosyalar (>50 MB) parcali yuklenebiliyor, kesintide resume calisiyor
- [ ] Agent EXE'de hostname gosterimi dogru calisiyor, IP degisiminde guncelleniyor
- [ ] Ses alani gorsel revizyonu tamamlanmis, UX standartlarina uygun
- [ ] Cihaz envanter dosyasi olusturulmus, recovery proseduru dokumante edilmis
- [ ] Birden fazla cihaza deploy.sh ile deploy yapilabildigi dogrulanmis

---

## Scope Disi

Su an hedef olmayanlar:

- Agent'siz cozum
- Sadece browser ile sistem sesi capture
  - getDisplayMedia her acilista kullanici izni gerektirir (otomatik kabul edilemez)
  - HTTPS zorunlulugu, cross-browser uyumsuzluk, sekme kapaninca ses kesilmesi
  - EXE tabanli WASAPI yaklasimi bu kullanim icin dogru mimari; browser daha kirilgan
- Web paneli uyumluluk workaround'u gibi ele almak

## Uygulama Kurali

Siradaki is:

1. ~~Once Faz 1~~ ✅ Tamamlandi (2026-03-07)
2. ~~Faz 2~~ ✅ Tamamlandi (sistem tepsisi es gecildi, diger tum maddeler kapandi)
3. Sonra Faz 3

Bu belge tartisma notu degil, mevcut urun kararidir.
