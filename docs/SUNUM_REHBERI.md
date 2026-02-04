# 🎤 AnnounceFlow - Müşteri Sunum Rehberi

**Versiyon:** v1.5.0 (Tam Sürüm)
**Durum:** %100 Teslime Hazır ✅

Bu rehber, projenin müşteriye sunumu ve teslimatı sırasında öne çıkarılması gereken özellikleri ve kullanım detaylarını içerir.

---

## 🌟 Öne Çıkan Özellikler (Satış Noktaları)

### 1. "Kafa Rahat" Sistemi 🧠
Bu sistem **"Set and Forget"** (Kur ve Unut) mantığıyla tasarlanmıştır.
- **Elektrik Kesintisi:** Cihaz elektriği geldiği anda otomatik başlar, son ayarlarını hatırlar.
- **Otomatik Döngü:** Müzik listesi bittiğinde otomatik başa döner, personel müdahalesi gerektirmez.

### 2. Akıllı Konum ve Zaman Yönetimi 🌍
- **Dinamik İl/İlçe:** Türkiye'nin 81 ili ve tüm ilçeleri sisteme entegredir. Diyanet/API üzerinden otomatik güncellenir.
- **Otomatik Sessizlik:**
    - **Mesai Dışı:** Belirlenen saatler dışında (örn. 22:00 - 09:00) sistem otomatik uykuya geçer.
    - **Özel Günler/Vakitler:** Seçilen konuma göre özel vakitlerde (ezan vb.) sistem otomatik olarak müziği durdurur, vakit bitince **kaldığı yerden** devam eder.

### 3. Profesyonel Anons Yönetimi 📢
Bir anons planlandığında veya manuel tetiklendiğinde:
1. Müzik sesi kısılmaz, **tamamen durur**.
2. Anons net bir şekilde çalınır.
3. Müzik, anons bittikten sonra **kaldığı yerden** devam eder (Şarkının başından değil).

---

## ✅ Tamamlanan 10 Altın Madde

Müşterinin özel istekleri eksiksiz yerine getirilmiştir:

1.  **Fast Food / Pi4 Uyumu:** ✅ Tamam.
2.  **TR Formatı:** ✅ Flatpickr ile 24 saat formatı zorunlu. AM/PM yok.
3.  **Otomatik Sessizlik:** ✅ Mesai ve Özel Vakit entegrasyonu.
4.  **Güvenlik:** ✅ Çift onaylı şifre değiştirme ekranı.
5.  **Şeffaflık:** ✅ Gerçek zamanlı disk/RAM görüntüleme (`get_system_stats()`).
6.  **Teknik Doğruluk:** ✅ "Sınırsız" yerine gerçek kapasite gösterimi.
7.  **Sonsuz Döngü:** ✅ Playlist sistemi.
8.  **Akıllı Kesme:** ✅ Anons önceliği.
9.  **Modern Agent:** ✅ Canvas tabanlı ModernButton ve ModernSlider.
10. **Tam Kapsam:** ✅ Tekrarlı planlarda anons seçebilme.

---

## 🎬 Demo Senaryosu

### Adım 1: Arayüz Tanıtımı
- `http://aflow.local:5001` adresine girin.
- **Koyu Mod** arayüzünün şıklığını gösterin.
- **"Sistem Limitleri"** (Ayarlar sayfasında) bölümünü göstererek cihazın kapasitesini vurgulayın.

### Adım 2: Konum Ayarı (WOW Faktörü)
- Ayarlar sayfasına gidin.
- **"Ezan Vakti / Konum"** bölümünü açın.
- Örneğin **Gaziantep** seçin.
- Altındaki ilçelerin (Araban, Şehitkamil, Yavuzeli vb.) **anında** yüklendiğini gösterin. (Bu özellik internetten canlı çekilir).

### Adım 3: Anons Testi
1. "Şu An Çalıyor" sayfasından bir müzik başlatın.
2. Müzik çalarken "Hızlı Çal" (veya planlanmış bir anons) ile araya girin.
3. Müziğin durduğunu, anonsun okunduğunu ve **müziğin kaldığı yerden devam ettiğini** dinletin.

---

## 🔧 Teknik Teslimat Bilgileri

**Cihaz:** Raspberry Pi 4
**Yazılım:** AnnounceFlow v1.5.0 (Enterprise)
**Erişim:**
- **Web:** `http://aflow.local:5001` (Kullanıcı: `admin` / Şifre: `admin123`)
- **SSH:** `ssh admin@aflow.local`

**Sorun Giderme:**
Eğer ses gelmezse veya sistem durursa:
- Fişi çekip takmanız yeterlidir. Sistem kendini toparlayacak şekilde kodlanmıştır (Systemd Service).

---
*Hazırlayan: Berkay Bakaç*
