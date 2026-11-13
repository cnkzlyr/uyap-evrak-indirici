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
   * **İşlem Önbelleği (`taranan_islem_cache.json`):** Çok yıllık geçmiş taramalarda yüzlerce dilimi her seferinde tekrar sorgulamak yerine önbellekten anında okur, indirmeye 0 saniyede başlar.

4. **Akıllı Boş Evrak / Ekli Dosya Yok Ayrımı & Sahte HTML Koruması:**
   * UYAP sunucusunda fiziksel eki olmayan kayıtlar (örn: bazı soruşturma inceleme talepleri veya sistem bildirimleri) ile gerçek oturum düşmelerini ayırt eder.
   * Oturum açıkken gelen boş yanıtları sahte Mobil İmza döngüsüne sokmadan saniyeler içinde "Ekli dosya yok" diyerek atlar.
   * Oturum düşmesiyle dönen HTML sayfalarını veya JSON hata yanıtlarını asla UDF/PDF olarak kaydetmez. Dosyaları önce `.part` uzantısıyla kaydeder, doğrular ve atomik olarak asıl ismine taşır.

5. **Otomatik Oturum Kurtarma (Self-Healing Session):**
   * UYAP'ın 40 dakikalık normal oturum süresi bittiğinde sistem durmaz; otomatik olarak telefonunuza yeni bir Mobil İmza onay bildirimi gönderir.
   * Siz telefonunuzdan onayladığınız anda e-Devlet izin ekranını otomatik geçerek kaldığı evraktan indirmeye devam eder.

6. **Optimize Edilmiş Güvenli Hızlı İndirme (Anti-Ban & Anti-Flood):**
   * UYAP güvenlik duvarlarını (WAF) tetiklemeyen rastgele jitter gecikmesi (1.2 - 2.8 sn) ve her 20 dosyada bir 10 saniyelik dinlenme molasıyla yüksek hız ve maksimum güvenliği dengeler.

7. **Rotasyonlu Metin Günlüğü (Rotating TXT Logger):**
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
Açılan interaktif menüden dilediğiniz modu seçebilirsiniz:
* **Seçenek 1 (Terminal CLI Modu - Son 1 Yıl [Önerilen]):** Son 365 güne ait tüm evrakları ekransız, doğrudan Termux siyah ekranında canlı ilerleme çubuğuyla indirir.
* **Seçenek 2 (Terminal CLI Modu - Geçmiş Yıllar / Özel Tarih Aralığı):** Belirleyeceğiniz başlangıç ve bitiş tarihleri (örn: `01.01.2020` - `13.11.2026`) arasındaki tüm geçmiş dava ve savcılık evraklarını 30'ar günlük dilimlerle eksiksiz tarayıp indirir.
* **Seçenek 3 (Termux:X11 Grafik Arayüzü):** Telefonunuzda masaüstü penceresi açar ve filtrelemeli tablo sunar.
* **Seçenek 4 (Headless Modu):** Chromium'u tamamen arkaplanda çalıştırır.

### 3. Komut Satırı (CLI) Parametreleri:
Script doğrudan parametrelerle de çalıştırılabilir:
```bash
# Son 1 yılın evraklarını indir:
python3 uyap_indirici.py --cli

# Özel tarih aralığı (Geçmiş yıllar):
python3 uyap_indirici.py --cli --start-date 01.01.2020 --end-date 13.11.2026

# Headless Chromium ile başlat:
python3 uyap_indirici.py --cli --headless
```

> [!TIP]
> **Android İndirme Konumu:** Android/Termux üzerinde indirilen evraklar doğrudan telefonunuzun dahili hafızasındaki **`İndirilenler/UYAP_Indirilenler`** (`/sdcard/Download/UYAP_Indirilenler`) klasörüne kaydedilir. Mahkeme ve evrak türüne göre hiyerarşik olarak klasörlenir. Dosya yöneticinizden hemen açabilirsiniz.

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

## 🗺️ Yol Haritası (Geliştirilmekte Olan Özellikler)

- [ ] **Dava Dosyalarından Evrak Türüne Göre Toplu İndirici:** Belirli tarih aralığında tüm derdest dosyalardaki *Duruşma Tutanakları*, *Bilirkişi Raporları*, *Gerekçeli Kararlar* vb. evrak türlerini tek tuşla topluca çekme.
- [ ] **UDF İçi Ek Dosyaları Otomatik Ayıklayıcı (Attachment Extractor):** İndirilen `.udf` dosyalarının içine gömülü PDF, JPEG, TIFF belgeleri tek tıkla klasörlere ayıklama.
- [ ] **Duruşma Takvimi & Ajanda Dışa Aktarıcı:** UYAP duruşma listesini doğrudan Google Takvim / iCal (`.ics`) formatında takvime işleme.
- [ ] **Yeni Evrak Nöbetçisi (Otomatik İzleme):** Dosyalara yeni evrak, tensip veya karar eklendiğinde arka planda tespit edip anlık bildirim gönderme.

---

## 🔒 Gizlilik ve Güvenlik

* Bu yazılım hiçbir verinizi üçüncü taraf sunuculara göndermez. Tüm iletişim doğrudan yerel cihazınız ile Adalet Bakanlığı UYAP sunucuları (`avukat.uyap.gov.tr`) arasında gerçekleşir.
* e-İmza PIN kodunuz ve indirilen dava evrakları `.gitignore` kapsamında korunur ve repoya dahil edilmez.

---

## 📄 Lisans

MIT Lisansı ile korunmaktadır.
