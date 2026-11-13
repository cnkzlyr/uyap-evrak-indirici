# UYAP Avukat Portal - Güvenli ve Kategorili Toplu Evrak İndirici

UYAP Avukat Portal (`https://avukat.uyap.gov.tr`) üzerinden son 1 yılın (veya seçilen tarih aralığının) tüm işlemlerini ve dava evraklarını güvenli, kategorili ve kesintisiz şekilde indiren gelişmiş otomasyon aracı.

Masaüstü Linux/macOS sistemlerinin yanı sıra **Android (Termux)** üzerinde hem **Terminal (CLI)**, hem **Headless**, hem de **Termux:X11** grafik arayüzü ile eksiksiz çalışır.

---

## 🚀 Öne Çıkan Özellikler

1. **Çoklu Çalışma Modları (Masaüstü GUI, Termux:X11, CLI & Headless):**
   * **Pencereli Grafik Arayüz (Tkinter):** Masaüstü ortamlarında ve Android `Termux:X11` ile pencereli arayüz.
   * **Terminal / CLI Modu (`--cli`):** Ekransız, grafik kartı gerektirmeyen, doğrudan terminalde canlı ilerleme çubuğuyla çalışan mod.
   * **Headless Modu (`--headless`):** Chromium'u arkaplanda (penceresiz) çalıştırarak minimum kaynak tüketimi sağlar.

2. **Esnek Kimlik Doğrulama (Otomatik Mobil İmza & e-İmza Desteği):**
   * **Mobil İmza Otomasyonu (Termux/Android):** Ekransız (CLI / Headless) çalışırken T.C. Kimlik ve GSM numaranızla doğrudan telefonunuza SIM onay bildirimi ve parmak izi gönderir. Ekrana gelen bildirime PIN kodunuzu girip onayladığınız an UYAP oturumu açılır ve indirme otomatik başlar. Bilgiler yerel yapılandırmada güvenle saklanır, tekrar girmeniz gerekmez.
   * **e-İmza Otomatik Yenileme:** Bilgisayarda ArkSigner (`localhost:5975`) üzerinden PIN ile kesintisiz otomatik oturum açma.

3. **Tam Mükerrerlik ve Eksiklik Önleme (Kalıcı İş Emir No Mimarisi):**
   * UYAP'ın sorgu bazlı dinamik şifrelediği geçici evrak kodları yerine kalıcı **İş Emir Numarası (`isEmirNo`)** ve içerik eşleşmesi kullanır.
   * Diskte sağlam bulunan dosyaları anında atlar (sıfır kota kaybı). Sadece eksik veya silinmiş evrakları indirir.

4. **Sahte HTML / Hata Sayfası Koruması:**
   * Oturum düşmesiyle dönen HTML sayfalarını veya JSON hata yanıtlarını asla UDF/PDF olarak kaydetmez.
   * Dosyaları önce `.part` uzantısıyla kaydeder, doğrular ve atomik olarak asıl ismine taşır.

5. **UYAP Koruma Sistemi (Anti-Ban & Anti-Flood):**
   * Seri indirme, rastgele jitter beklemesi (2.5 - 6.0 sn) ve her N dosyada dinlenme molası verir.

6. **Rotasyonlu Metin Günlüğü (Rotating TXT Logger):**
   * `uyap_indirici_log.txt` dosyasına sürekli ve anlık yazar.
   * Dosya boyutu 8 MB sınırına ulaştığında yeni dosyaya geçer (maksimum 3 yedek tutar).

---

## 📱 Android / Termux Kurulumu ve Kullanımı

Android cihazınızda UYAP'tan evrak indirmek için iki seçeneğiniz vardır:

### 1. Hızlı Otomatik Kurulum:
Termux uygulamasında şu komutu çalıştırmanız yeterlidir:
```bash
bash termux_kurulum.sh
```

### 2. Çalıştırma Seçenekleri:
Kurulumdan sonra başlatıcıyı çalıştırın:
```bash
./baslat_termux.sh
```
Açılan menüden dilediğiniz modu seçebilirsiniz:
* **Seçenek 1 (Terminal / CLI Modu - Önerilen):** Ekran veya X11 açmadan, doğrudan Termux siyah ekranında canlı ilerleme çubuğuyla çalışır.
* **Seçenek 2 (Termux:X11 Grafik Arayüzü):** Telefonunuzda masaüstü penceresi açılır.
* **Seçenek 3 (Headless Modu):** Chromium'u tamamen arkaplanda çalıştırır.

> [!TIP]
> **Android İndirme Konumu:** Android/Termux üzerinde indirilen evraklar doğrudan telefonunuzun dahili hafızasındaki **`İndirilenler/UYAP_Indirilenler`** (`/sdcard/Download/UYAP_Indirilenler`) klasörüne kaydedilir. Dosya yöneticinizden hemen açabilirsiniz.

---

## 💻 Masaüstü Kurulumu (Linux / macOS)

### Gereksinimler:
* Python 3.10 veya üzeri
* Chromium veya Google Chrome
* ArkSigner (e-İmza ile otomatik yenileme için)

### Bağımlılıklar:
```bash
pip install -r requirements.txt
```

### Başlatma:
```bash
./baslat.sh
```
veya doğrudan CLI/Headless başlatmak için:
```bash
python3 uyap_indirici.py --cli --headless
```

---

## 🔒 Gizlilik ve Güvenlik

* Bu yazılım hiçbir verinizi üçüncü taraf sunuculara göndermez. Tüm iletişim doğrudan yerel cihazınız ile Adalet Bakanlığı UYAP sunucuları (`avukat.uyap.gov.tr`) arasında gerçekleşir.
* e-İmza PIN kodunuz ve indirilen dava evrakları `.gitignore` kapsamında korunur ve repoya dahil edilmez.

---

## 📄 Lisans

MIT Lisansı ile korunmaktadır.
