# AnnounceFlow Kurulum Rehberi

---

## Web Paneli (Tüm Cihazlardan)

## Web Paneli (Tüm Cihazlardan)

**Adres:** `http://192.168.1.24:5001`
**Kullanıcı:** `admin`
**Şifre:** `admin123`

Herhangi bir tarayıcıdan (Chrome, Safari, Edge) bu adrese girin.

---

## Windows Uygulaması (4 Seçenek)

### Seçenek A: USB/E-posta ile EXE Alma (En Kolay)

1. Size verilen `AnnounceFlowAgent.exe` dosyasını alın
2. Masaüstüne koyun
3. Çift tıklayın
4. Pi adresi girin: `http://192.168.1.24:5001`

> **Dosya Nereden Gelir?** Teknik destek size USB bellek, e-posta veya bulut link ile gönderir.

---

### Seçenek B: Bulut Linkinden İndirme

1. Size verilen indirme linkini açın (Google Drive, OneDrive, Dropbox vb.)
2. `AnnounceFlowAgent.exe` dosyasını indirin
3. Masaüstüne taşıyın, çift tıklayın
4. Pi adresi girin: `http://192.168.1.24:5001`

---

### Seçenek C: Windows Terminalden Kurulum

**Adım 1: Python Kurulumu**
- `python.org/downloads` adresinden indirin
- **"Add Python to PATH"** kutusunu işaretleyin

**Adım 2: Dosyaları Kopyalayın**
Size verilen `agent` klasörünü Masaüstüne kopyalayın.

**Adım 3: PowerShell Açın**
- Başlat menüsünde "PowerShell" yazın, açın

**Adım 4: Komutları Çalıştırın**
```powershell
cd Desktop\agent
pip install pyinstaller pystray pillow requests
python build_agent.py
```

**Adım 5:** `dist\AnnounceFlowAgent.exe` oluşur, çift tıklayın.

---

### Seçenek D: Sadece Terminalle Çalıştırma (EXE'siz)

Python kuruluysa EXE olmadan da çalıştırabilirsiniz:

```powershell
cd Desktop\agent
pip install pystray pillow requests
python agent.py
```

> Her seferinde bu komutu çalıştırmanız gerekir.

---

## System Tray Nedir?

Ekranın sağ alt köşesinde (saatin yanında) küçük ikonlar:

```
                              [🔊][📶][📻] 14:30
                                     ↑
                               AnnounceFlow ikonu
```

- **Sağ tık:** Menü (Web Panel, Ayarlar, Çıkış)
- **Sol tık:** Hızlı kontrol

---

## Özet Bilgiler

| Bilgi | Değer |
|-------|-------|
| Web Adresi | http://192.168.1.24:5001 |
| Kullanıcı | admin |
| Şifre | admin123 |

---

*AnnounceFlow v1.6.3*
