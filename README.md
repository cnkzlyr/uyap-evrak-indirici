# UYAP Avukat Portal - Güvenli ve Kategorili Toplu Evrak İndirici

UYAP Avukat Portal (`https://avukat.uyap.gov.tr`) üzerinden son 1 yılın (veya seçilen tarih aralığının) tüm işlemlerini ve dava evraklarını güvenli, kategorili ve kesintisiz şekilde indiren gelişmiş masaüstü otomasyon aracı.

---

## 🚀 Öne Çıkan Özellikler

1. **Chromium CDP Entegrasyonu (Port 9223):**
   * Chromium'u özel hata ayıklama moduyla başlatır veya açık olan UYAP sekmesine otomatik bağlanır.
   * UYAP'ın dahili React SPA mimarisi ve session cookie'leri üzerinden güvenli şekilde çalışır.

2. **Otomatik e-İmza Yenileme & Oturum Kurtarma (ArkSigner Köprüsü):**
   * Yerel ArkSigner servisi (`http://localhost:5975`) ile entegre çalışır.
   * UYAP bağlantısı koptuğunda, oturum süresi dolduğunda veya hata aldığında kullanıcının PIN koduyla challenge imzalayarak oturumu arka planda otomatik yeniler ve indirmeye kesintisiz devam eder.

3. **Sahte HTML / Hata Sayfası Koruması:**
   * Oturum düşmesiyle dönen HTML giriş sayfalarını veya JSON hata mesajlarını ASLA UDF/PDF olarak kaydetmez.
   * Sihirli baytlar (magic bytes) denetimi yapar; dosyaları önce `.part` geçici uzantısıyla yazar, doğrular ve atomik olarak asıl ismine taşır.

4. **Tam Mükerrerlik ve Eksiklik Önleme (İdempotent Mimari):**
   * İndirilen dosyaların SHA-256 özetini ve diskteki gerçek dosya varlığını denetler.
   * Daha önce başarıyla indirilmiş dosyaları otomatik atlar (mükerrer indirmeyi önler).
   * Yarım kalan veya silinmiş dosyaları tespit ederek yalnızca eksik olanları indirir.

5. **Akıllı Kategorilendirme ve Esnek Klasörleme:**
   * Dosya/Yargı Türü (Ağır Ceza, Asliye Hukuk, İcra Dairesi vb.), Evrak Türü, İşlem Türü ve Tarihe göre filtreleme ve anlık arama imkanı sunar.
   * Dinamik klasör şablonları (örn: `{dosya_turu}/{evrak_turu}/{islem_turu}/{yil_ay}/{dosya_no}_{evrak_adi}`).

6. **UYAP Koruma Sistemi (Anti-Ban & Anti-Flood):**
   * Seri indirme, rastgele bekleme (jitter) süresi (varsayılan 3.5 - 7.0 sn) ve her N dosyada bir koruma molası verir.

7. **Rotasyonlu Metin Günlüğü (Rotating TXT Logger):**
   * `uyap_indirici_log.txt` dosyasına sürekli ve anlık yazar.
   * Dosya boyutu 8 MB sınırına ulaştığında yeni dosyaya geçer (maksimum 3 yedek tutar). Editörlerde kasmaz ve sistemde dosya kirliliği yaratmaz.

---

## 📦 Kurulum ve Gereksinimler

* **Python:** 3.10 veya üzeri
* **İşletim Sistemi:** Linux (Fedora, Ubuntu, Debian, Arch vb.) veya macOS
* **Bağımlılıklar:**
  ```bash
  pip install -r requirements.txt
  ```
  *(Sistemde Python Tkinter kurulu olmalıdır: `sudo dnf install python3-tkinter` veya `sudo apt install python3-tk`)*
* **Tarayıcı:** Chromium veya Google Chrome
* **e-İmza Sürücüsü:** Bilgisayarınızda e-İmza kart okuyucunuzun ve ArkSigner uygulamasının çalışıyor olması gerekmektedir.

---

## 🛠️ Kullanım

Uygulamayı başlatmak için:

```bash
./baslat.sh
```

veya

```bash
python3 uyap_indirici.py
```

### Adım Adım İşlem Sırası:

1. **Chromium'u Başlatın:** `1. Chromium ile UYAP'ı Aç` butonuna basın.
2. **e-İmza ile Giriş:** e-İmza PIN kodunuzu girip `e-İmza ile Giriş Yap / Yenile` butonuna basın veya tarayıcıdan giriş yapın.
3. **İşlemleri Tarayın:** Tarih aralığını belirleyip `3. İşlemlerim'i Tara` butonuna basın.
4. **Filtreleyin ve Klasör Şablonunu Seçin:** İstediğiniz mahkeme türü, evrak türü veya arama kelimesini seçin.
5. **Güvenli İndirmeyi Başlatın:** `Filtrelenen Tüm Evrakları Güvenli İndir` butonuna basarak arkanıza yaslanın.

---

## 🔒 Gizlilik ve Güvenlik

* Bu yazılım hiçbir verinizi veya dosyanızı üçüncü taraf sunuculara göndermez. Tüm iletişim doğrudan yerel bilgisayarınız (`localhost`) ile Adalet Bakanlığı UYAP sunucuları (`avukat.uyap.gov.tr`) arasında gerçekleşir.
* e-İmza PIN kodu ve indirilen dava evrakları `.gitignore` kapsamında tutulmakta olup kesinlikle versiyon kontrolüne dahil edilmez.

---

## 📄 Lisans

MIT Lisansı ile korunmaktadır.
