# UYAP Avukat Portal - Güvenli ve Kategorili Toplu Evrak İndirici

UYAP Avukat Portal (`https://avukat.uyap.gov.tr`) üzerinden son 1 yılın (veya seçilen tarih aralığının) tüm işlemlerini, dava evraklarını ve duruşma takvimini güvenli, kategorili ve kesintisiz şekilde indiren gelişmiş otomasyon aracı.

Masaüstü Linux/macOS sistemlerinin yanı sıra **Android (Termux)** üzerinde hem **Terminal (CLI)**, hem **Headless**, hem de **Termux:X11** grafik arayüzü ile eksiksiz çalışır.

---

## 🚀 Öne Çıkan Özellikler

1. **Dava Dosyalarından Evrak Türüne Göre Toplu İndirme (YENİ!):**
   * Tüm derdest dava dosyalarınız taranır.
   * Dilediğiniz evrak türü filtrelenerek indirilir:
     * **Duruşma Tutanakları / Zabıtları**
     * **Bilirkişi Raporları**
     * **Gerekçeli Karar ve Hükümler**
     * **Tensip Zabıtları**
     * **Özel Serbest Kelime Filtresi** (Örn: "keşif", "cevap", "istinaf", vb.)
   * Evraklar `DURUSMA_TUTANAKLARI`, `BILIRKISI_RAPORLARI` gibi özel klasörler altında mahkeme ve dosya no bazında düzenli şekilde arşivlenir.

2. **Evrak Ek Belgelerinin (Attachments) İndirilmesi (YENİ!):**
   * UYAP'taki dilekçe ve müzekkerelerin altındaki tüm ekler (`ekEvrakListesi`) taranır.
   * PDF, TIFF, JPEG ve UDF formatındaki tüm ek belgeler ana evrakla bağlantılı olarak otomatik indirilir.

3. **Duruşma Takvimi Oluşturucu & Dışa Aktarma (.ics ve CSV) (YENİ!):**
   * UYAP'taki geçmiş ve gelecek tüm duruşmalarınızı otomatik sorgular.
   * **Google Takvim / Android Takvim (.ics):** Tek tıkla telefonunuzun takvimine veya Google Takvim'e aktarabileceğiniz iCalendar (`durusma_takvimi.ics`) dosyası üretir. Duruşma saati, mahkeme, dosya no, taraflar ve e-Duruşma bağlantısı takvim etkinliğine otomatik eklenir.
   * **Duruşma Tablosu (.csv):** Tarih, mahkeme, esas no, işlem türü, sonuç ve taraf bilgilerini içeren Excel uyumlu CSV tablosu üretir.

4. **Çoklu Çalışma Modları (CLI, Headless & Termux:X11):**
   * **Terminal / CLI Modu (`--cli`):** Grafik kartı gerektirmeden doğrudan terminalde canlı ilerleme çubuğuyla çalışır.
   * **Headless Modu (`--headless`):** Chromium'u arkaplanda penceresiz çalıştırarak minimum kaynak tüketimi sağlar.
   * **Termux:X11 Grafik Arayüzü:** Android telefonda masaüstü penceresi açarak Tkinter tablosu sunar.

5. **Otomatik Mobil İmza ve e-İmza Desteği:**
   * **Mobil İmza Otomasyonu (Termux/Android):** T.C. Kimlik ve GSM numaranız ile telefonunuza doğrudan SIM onay bildirimi ve parmak izi gönderir. PIN kodunuzu girip onayladığınız an UYAP oturumu açılır. Bilgiler yerel yapılandırmada güvenle saklanır.
   * **e-İmza Otomatik Yenileme:** Bilgisayarda ArkSigner (`localhost:5975`) üzerinden PIN ile otomatik oturum açma.

6. **Tam Mükerrerlik Önleme & Akıllı Önbellek:**
   * Kalıcı **İş Emir Numarası (`isEmirNo`)** ve içerik doğrulaması kullanır.
   * Diskte sağlam bulunan dosyaları anında atlar (sıfır kota kaybı). Sadece eksik evrakları indirir.
   * **İşlem Önbelleği (`taranan_islem_cache.json`):** Çok yıllık geçmiş taramalarda yüzlerce dilimi tekrar sorgulamadan önbellekten anında okur.

7. **Akıllı Boş Evrak / Ekli Dosya Yok Ayrımı & Sahte HTML Koruması:**
   * UYAP sunucusunda fiziksel eki olmayan kayıtlar ile gerçek oturum düşmelerini ayırt eder.
   * Oturum düşmesiyle dönen HTML veya JSON hata sayfalarını asla UDF/PDF olarak kaydetmez. Dosyaları önce `.part` uzantısıyla kaydeder, doğrular ve atomik olarak asıl ismine taşır.

8. **Otomatik Oturum Kurtarma (Self-Healing Session):**
   * UYAP oturum süresi dolduğunda otomatik olarak telefonunuza yeni bir Mobil İmza onay bildirimi gönderir ve kaldığı evraktan indirmeye devam eder.

9. **Optimize Edilmiş Güvenli Hızlı İndirme (Anti-Ban & Anti-Flood):**
   * WAF tetiklemeyen rastgele jitter gecikmesi (1.2 - 2.8 sn) ve her 20 dosyada bir 10 saniyelik dinlenme molası uygular.

---

## 📱 Android / Termux Başlatıcı Menüsü

Termux'ta başlatıcıyı çalıştırmanız yeterlidir:
```bash
./baslat_termux.sh
```

Açılan interaktif menü:
```text
==================================================================
    UYAP Avukat Portal Evrak İndirici - Android / Termux          
==================================================================

  1) İşlemlerim İndirici (Son 1 Yıl - Hızlı)
  2) İşlemlerim İndirici (Geçmiş Yıllar / Özel Tarih Aralığı)
  3) Dava Dosyalarından Belirli Evrak İndir (Duruşma Zaptı, Rapor vb.)
  4) Dava Dosyalarından TÜM Evrakları ve EKLERİ İndir (Toplu Arşiv)
  5) Duruşma Takvimi Oluştur (.ics / Google Takvim + CSV)
  6) Termux:X11 Grafik Arayüz Modu (Telefonda Pencere Açılır)
  7) Arka Planda Başlat (Headless Chromium)

==================================================================
Seçiminiz (1-7) [Varsayılan: 1]:
```

---

## 💻 Komut Satırı (CLI) Kullanım Örnekleri

### 1. Dava Dosyalarından Belirli Evrakları İndirme
```bash
# Tüm dosyalardaki Duruşma Tutanaklarını indir:
python3 uyap_indirici.py --cli --mode dosyalar --evrak-turu durusma

# Bilirkişi Raporlarını ve eklerini indir:
python3 uyap_indirici.py --cli --mode dosyalar --evrak-turu bilirkisi --include-attachments

# Belirli bir tarih aralığındaki Gerekçeli Kararları indir:
python3 uyap_indirici.py --cli --mode dosyalar --evrak-turu karar --start-date 01.01.2023 --end-date 13.11.2026

# Tüm dava evraklarını ve eklerini eksiksiz indir:
python3 uyap_indirici.py --cli --mode dosyalar --evrak-turu tumu --include-attachments
```

### 2. Duruşma Takvimi ve Tutanak Yöneticisi
```bash
# Son 3 ay ve önümüzdeki 6 ayın duruşmalarını Google Takvim (.ics) ve CSV olarak kaydet:
python3 uyap_indirici.py --cli --mode durusmalar

# Belirli tarih aralığındaki duruşmaları takvime aktar:
python3 uyap_indirici.py --cli --mode durusmalar --start-date 01.01.2024 --end-date 31.12.2024
```

### 3. İşlemlerim Portalı Toplu Evrak İndirme
```bash
# Son 1 yılın evraklarını indir:
python3 uyap_indirici.py --cli --mode islemlerim

# 2020-2026 arası tüm geçmiş evrakları indir:
python3 uyap_indirici.py --cli --mode islemlerim --start-date 01.01.2020 --end-date 13.11.2026
```

---

## 📂 Dosya ve Klasör Hiyerarşisi

İndirilen tüm evraklar Android cihazınızda doğrudan telefon hafızasına kaydedilir:
* **Ana Dizin:** `/sdcard/Download/UYAP_Indirilenler/`
* **Dava Dosyası Evrakları:**
  * `DURUSMA_TUTANAKLARI/<Mahkeme>/<Dosya_No>/<Tarih>_<Evrak>.udf`
  * `BILIRKISI_RAPORLARI/<Mahkeme>/<Dosya_No>/<Tarih>_<Evrak>.pdf`
  * `GEREKCELI_KARARLAR/<Mahkeme>/<Dosya_No>/<Tarih>_<Evrak>.udf`
* **Takvim Dosyaları:**
  * `durusma_takvimi.ics` (Google Takvim / Android Takvim uyumlu)
  * `durusma_listesi.csv` (Excel / Tablo formatında)
* **İndeks Raporu:**
  * `indirme_indeksi.csv` (İndirilen tüm evrakların tarih, mahkeme, dosya no, tür ve disk yolu dökümü)

---

## 🔒 Gizlilik ve Güvenlik

* Bu yazılım hiçbir verinizi üçüncü taraf sunuculara göndermez. Tüm iletişim doğrudan yerel cihazınız ile Adalet Bakanlığı UYAP sunucuları (`avukat.uyap.gov.tr`) arasında gerçekleşir.
* Kişisel T.C. Kimlik, GSM ve dosya bilgileri `.gitignore` kapsamında korunur ve repoya dahil edilmez.

---

## 📄 Lisans

MIT Lisansı ile korunmaktadır.
