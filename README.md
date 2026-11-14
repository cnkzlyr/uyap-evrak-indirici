# 🏛️ UYAP Avukat Portal - Güvenli ve Kategorili Toplu Evrak İndirici & Hukuk Otomasyonu

UYAP Avukat Portal (`https://avukat.uyap.gov.tr`) üzerindeki tüm derdest ve kapalı dava dosyalarını, duruşma tutanaklarını, bilirkişi raporlarını, gerekçeli kararları, dilekçe/layihaları, tebligat mazbatalarını ve duruşma takvimini güvenli, kategorili ve mükerrersiz şekilde indiren, işleyen ve yapay zeka analizine hazır hale getiren profesyonel otomasyon sistemi.

Sistem **Windows 11 (Chrome / Edge)**, **Linux / macOS** ve **Android (Termux / Termux:X11)** platformlarında %100 yerel, güvenli ve kesintisiz çalışır.

---

## 🚀 Temel Özellikler

1. **Dava Dosyalarından Akıllı Evrak Filtreleme (700+ Açık ve Kapalı Dosya):**
   * UYAP'taki 27 yargı birimini tarar; İcra dairelerini otomatik ayıklayarak hız kazandırır.
   * Hedefe yönelik evrak türleri:
     * **Duruşma Zabıtları (`durusma`):** Celse zabıtları ve SEGBİS tutanakları. (Müzekkere ekleri ve talimatlar mükerrerlik motoruyla elenir).
     * **Bilirkişi Raporları (`bilirkisi`):** Kök/ek raporlar, uzman mütalaaları, ATK ve kriminal raporlar (ekleriyle birlikte).
     * **Gerekçeli Karar ve İlamlar (`karar`):** Mahkeme hükümleri, BAM İstinaf ve Yargıtay ilamları.
     * **Tensip Zabıtları (`tensip`):** İlk ve talimat tensip tutanakları.
     * **Dava & Cevap Layihaları (`layihalar` / `dilekce`):** Dava, cevap, replik, düplik, delil, bilirkişi itiraz ve beyan dilekçeleri.
     * **Tebligatlar (`tebligat`):** Fiziki PTT mazbataları ve UETS e-Tebligat evrakları.
     * **Müzekkere & Kurum Cevapları (`muzekkere`):** Banka, Tapu, SGK, EGM, Polnet vb. kurum cevapları.
     * **İddianame & Fezleke (`iddianame`):** Ceza soruşturma/kovuşturma evrakları.
     * **Tüm Evraklar (`tumu`):** Dosyadaki istisnasız tüm ana evraklar ve alt ekler (`ekEvrakListesi`).

2. **UDF -> Vektörel PDF Dönüştürücü (Havelsan & TrueType Embedded):**
   * Resmi Havelsan editör çekirdeği ile çalışır.
   * `comments.xml` (açıklama/yorum) içeren UDF'lerde çökmeyi engelleyen bellek içi ayrıştırıcı içerir.
   * Windows ve Linux TrueType fontlarını (`Times New Roman`, `Arial`) PDF içine tam gömer; bozulma veya karakter kaybı yaşanmaz.

3. **Tebligat & UETS Yasal Süre Yöneticisi:**
   * İndirilen fiziki PTT mazbatalarındaki barkodları (13 haneli) ve tebliğ sonuçlarını tespit eder.
   * UETS (Ulusal Elektronik Tebligat) delil kayıtlarını ve elektronik tebliğ tarihlerini okur.
   * **7201 sayılı Tebligat Kanunu Madde 7/a f.4** kuralını uygulayarak yasal tebliğ tarihini (ulaşmayı izleyen 5. günün sonu) ve itiraz sürelerini otomatik hesaplar.
   * Sonuçları Excel uyumlu `tebligat_takip_listesi.csv` kütüğüne işler.

4. **Akıllı Evrak Zenginleştirme ve İsimlendirme:**
   * UDF `content.xml` içeriğini okuyarak Mahkeme, Esas No, Karar No, Duruşma Tarihi ve temsil edilen **Müvekkil** bilgisini çıkarır.
   * UDF dosyasının dahili `documentproperties.xml` meta verilerini günceller.
   * Dosyaları tek başına %100 tanımlayıcı isimlerle yeniden adlandırır:  
     `{Tarih}_{Birim}_{Esas}_{Muv-Muvekkil}_{EvrakTuru}_{EvrakId}.udf`

5. **Duruşma Takvimi Oluşturucu (.ics / CSV):**
   * Geçmiş ve gelecek tüm duruşmaları sorgular.
   * Google Takvim, Outlook ve Apple Takvim uyumlu `durusma_takvimi.ics` üretir.
   * Duruşma saati, mahkeme, dosya no, taraflar ve e-Duruşma bağlantısını takvim kaydına işler.

6. **Tam Güvenlik & Oturum Kurtarma:**
   * Oturum düşmelerinde dönen sahte HTML sayfalarını asla UDF/PDF olarak kaydetmez.
   * Atomik dosya yazımı (`.part` doğrulaması) ile bozuk indirmeleri engeller.
   * ArkSigner (Port 5975) ve Mobil İmza desteği ile kesintisiz oturum yeniler.

7. **Evrak Kataloğu: Tara → Filtrele → Seç → Bir Kez İndir** (`python uyap_indirici.py --katalog`, menü 12):
   * Tüm açık/kapalı dosyaların evrak ve ek listesi indirmeden yerel bir veritabanına (`uyap_katalog.db`) yazılır; yüz binlerce kayıt saniyeler içinde süzülür.
   * Filtreler: taraf, müvekkil (taraf/vekil bilgisinden otomatik), açıklama, evrak türü (dahil / hariç kelimeler), içerik (indirilenlerde tam metin), tarih aralığı (takvimden) veya son N gün, birim, dosya no, dosya türü, yargı, açık/kapalı, evrak kategorisi (dahil/hariç), uzantı (udf/tif/pdf/eyp…), asıl/ek, indirme durumu.
   * **Her evrak bir kez:** kalıcı anahtar (birim evrak no + tarih + tür; ekler için UYAP ana evrak no + sıra) sayesinde istinaf/birleşen/talimat dosyalarında tekrar görünen evrak tek kayıttır. Başka evrakın ekinde tekrar gelen aynı belge içerik özetiyle (UDF'de `content.xml` + imza) yakalanır ve ikinci kez diske yazılmaz.
   * UDF'ler aynı adla PDF'e, TIF/TIFF'ler (çok sayfalı) A4 PDF'e dönüştürülür.
   * Ekler ait oldukları evrakın adıyla başlar: `2025-07-10_Talimat Üst Yazısı_[…]_no68987__EK03_İfade Tutanağı.tif`
   * Tarayıcı arka planda (gizli) açılır; oturum yoksa e-İmza (PIN yalnızca bellekte) veya Mobil İmza ile girilir, oturum düşerse aynı yöntemle yenilenir.
   * Sunucu/zamanlanmış görev için: `python uyap_indirici.py --katalog-tara --headless` (yalnız kataloğu günceller).

---

## 🖥️ Windows 11 Kurulumu ve Kullanımı

### 1. Ön Gereksinimler
* **Python 3.10+:** [python.org](https://www.python.org) adresinden indirilebilir (Kurulumda *"Add Python to PATH"* seçilmelidir).
* **Java 17+ (JRE veya JDK):** UDF -> PDF dönüştürücü için gereklidir (`java -version` ile kontrol edebilirsiniz).
* **Google Chrome** veya **Microsoft Edge**.

Gerekli Python kütüphanelerini yükleyin:
```powershell
pip install requests websocket-client playwright pydantic
```

---

### 2. Adım 1: Chrome veya Edge'i Hata Ayıklama Modunda Başlatma (Port 9223)

UYAP oturumunun güvenli yönetilebilmesi için tarayıcınızı Port 9223 ile başlatıp UYAP Avukat Portal'a e-İmza veya Mobil İmza ile giriş yapınız:

**Google Chrome için:**
```cmd
chrome.exe --remote-debugging-port=9223 --user-data-dir="%LOCALAPPDATA%\Google\Chrome\User Data UYAP" https://avukat.uyap.gov.tr
```

**Microsoft Edge için:**
```cmd
msedge.exe --remote-debugging-port=9223 --user-data-dir="%LOCALAPPDATA%\Microsoft\Edge\User Data UYAP" https://avukat.uyap.gov.tr
```

> **Not:** Portal açıldığında normal şekilde e-İmzanızla veya Mobil İmzanızla UYAP ana sayfasına girmeniz yeterlidir. Tarayıcıyı kapatmayınız.

---

### 3. Adım 2: İnteraktif Windows Menüsü ile Kullanım (`baslat_windows.bat`)

Proje klasöründeki **`baslat_windows.bat`** dosyasına çift tıklayarak tüm işlemleri menüden yönetebilirsiniz:

```text
==================================================================
   UYAP Avukat Portal Evrak Indirici - Windows 11
==================================================================

  1) Islemlerim Indirici (Son 1 Yil - Hizli)
  2) Islemlerim Indirici (Gecmis Yillar / Ozel Tarih Araligi)
  3) Dava Dosyalarindan Belirli Evrak Indir (Durusma, Rapor, Karar vb.)
  4) Dava Dosyalarindan TUM Evraklari ve EKLERI Indir (Toplu Arsiv)
  5) Dava & Cevap Layihalari (Dava, Cevap, Bilirkisi Itiraz, Beyan)
  6) Tebligat & Mazbatalar (Fiziki PTT + UETS - 5 Gün, 1-2 Hafta, 30-90 Gün Seçimli)
  7) Durusma Takvimi Olustur (.ics / Google & Outlook Takvim + CSV)
  8) Indirilen UDF'leri PDF'e Donustur (TrueType Embedded PDF)
  9) Tebligat & Sure Analizi Calistir (tebligat_takip_listesi.csv)
 10) Evrak Zenginlestirme & Akilli Isimlendirme (enrich_and_rename)
 11) Chrome / Edge UYAP Oturumu Baslat (Port 9223)
==================================================================
```

---

### 4. Adım 3: Doğrudan Komut Satırı (PowerShell / CMD) Kullanımı

Terminalden doğrudan hedefli görevler çalıştırmak için aşağıdaki komutları kullanabilirsiniz:

#### ⚖️ Dava Dosyalarından Evrak İndirme (`--mode dosyalar`)

```powershell
# 1. Tüm dosyalardaki Duruşma Tutanaklarını indir (Açık + Kapalı / Karara Çıkmış):
python uyap_indirici.py --cli --mode dosyalar --evrak-turu durusma --scope all

# 2. Yalnızca belirli bir mahkeme veya esas numarasına ait duruşmaları indir:
python uyap_indirici.py --cli --mode dosyalar --evrak-turu durusma --filter-birim "erzurum"
python uyap_indirici.py --cli --mode dosyalar --evrak-turu durusma --filter-birim "2025/506"

# 3. Bilirkişi Raporlarını ve altındaki ek belgeleri indir (Yalnızca açık dosyalar):
python uyap_indirici.py --cli --mode dosyalar --evrak-turu bilirkisi --include-attachments --scope acik

# 4. Gerekçeli Kararları, BAM İstinaf ve Yargıtay İlamlarını indir:
python uyap_indirici.py --cli --mode dosyalar --evrak-turu karar --scope all

# 5. Dava, Cevap, Delil, Bilirkişi İtiraz ve Beyan Layihalarını indir:
python uyap_indirici.py --cli --mode dosyalar --evrak-turu layihalar --scope all

# 6. Fiziki PTT Mazbatalarını ve UETS Elektronik Tebligatlarını İndir:
# Son 5 günlük (Acil Yasal Süreli Tebligatlar) - Hukuk Mahkemeleri:
python uyap_indirici.py --cli --mode dosyalar --evrak-turu tebligat --yargi-turu hukuk --days 5

# Son 1 haftalık (7 gün) - Ceza Mahkemeleri:
python uyap_indirici.py --cli --mode dosyalar --evrak-turu tebligat --yargi-turu ceza --days 7

# Son 2 haftalık (14 gün) - Tüm Mahkemeler:
python uyap_indirici.py --cli --mode dosyalar --evrak-turu tebligat --days 14

# Son 30, 60 veya 90 günlük tebligatlar:
python uyap_indirici.py --cli --mode dosyalar --evrak-turu tebligat --days 30
python uyap_indirici.py --cli --mode dosyalar --evrak-turu tebligat --days 60
python uyap_indirici.py --cli --mode dosyalar --evrak-turu tebligat --days 90

# Belirli bir tarihten günümüze olan tebligatlar:
python uyap_indirici.py --cli --mode dosyalar --evrak-turu tebligat --start-date 01.10.2026

# 7. Kurum cevaplarını (Banka ekstreleri, SGK, Tapu, EGM müzekkereleri) indir:
python uyap_indirici.py --cli --mode dosyalar --evrak-turu muzekkere --scope all

# 8. Ceza dosyalarındaki İddianame ve Fezlekeleri indir:
python uyap_indirici.py --cli --mode dosyalar --evrak-turu iddianame --scope all

# 9. Tensip Zabıtlarını indir:
python uyap_indirici.py --cli --mode dosyalar --evrak-turu tensip --scope all

# 10. Tüm derdest dava dosyalarının tüm evraklarını ve eklerini eksiksiz arşivle:
python uyap_indirici.py --cli --mode dosyalar --evrak-turu tumu --include-attachments --scope all
```

#### 📅 Duruşma Takvimi Oluşturma (`--mode durusmalar`)

```powershell
# Geçmiş 3 ay ve gelecek 6 ayın duruşma takvimini .ics ve CSV olarak çıkar:
python uyap_indirici.py --cli --mode durusmalar

# Belirli bir tarih aralığındaki duruşmaları takvime dök:
python uyap_indirici.py --cli --mode durusmalar --start-date 01.01.2025 --end-date 31.12.2026
```

#### 📂 İşlemlerim Portalı Toplu Evrak İndirme (`--mode islemlerim`)

```powershell
# Son 1 yılın portaldan yapılan işlemlerini indir:
python uyap_indirici.py --cli --mode islemlerim

# 2020 - 2026 arası tüm geçmiş evrakları indir:
python uyap_indirici.py --cli --mode islemlerim --start-date 01.01.2020 --end-date 14.11.2026
```

---

### 5. Adım 4: Toplu UDF -> Vektörel PDF Dönüştürme

İndirilen UDF dosyalarını topluca PDF'e çevirmek için:

```cmd
udf_toplu_pdf.bat
```
veya doğrudan Java komutuyla:
```cmd
java -Xmx1024m -Djava.awt.headless=true --add-opens=java.base/java.util=ALL-UNNAMED --add-opens=java.base/java.lang=ALL-UNNAMED --add-opens=java.desktop/java.awt=ALL-UNNAMED --add-opens=java.desktop/javax.swing=ALL-UNNAMED -cp ".;jars/*" uyap.UdfBatchPdfConverter
```

> **Özellik:** Mevcut PDF'leri atlar; yalnızca yeni indirilen veya eksik kalan UDF'leri dönüştürür.

---

### 6. Adım 5: Tebligat & Yasal Süre Analizini Çalıştırma

İndirilen tüm fiziki mazbataları ve UETS evraklarını analiz edip Excel CSV tablosuna işlemek için:

```powershell
python tebligat_yoneticisi.py
```
* **Çıktı Dosyası:** `UYAP_Indirilenler/TEBLIGATLAR/tebligat_takip_listesi.csv`
* **İçerik:** PTT Barkod No, Muhatap, Muvekkil Eşleştirmesi, Tebligat Sonucu, UETS Delil ID, Tebligat Kanunu m.7/a 5 günlük yasal tebliğ tarihi ve süre uyarıları.

---

### 7. Adım 6: Evrak Zenginleştirme ve Akıllı Yeniden Adlandırma

Evrakların içeriklerini (content.xml) okuyup standart isimlendirmeye dönüştürmek için:

```powershell
# Önizleme (değişiklik yapmadan simüle eder):
python enrich_and_rename_documents.py --dry-run

# Gerçek uygulama:
python enrich_and_rename_documents.py
```

---

## 📱 Android / Termux Kullanımı

Android cihazınızda Termux üzerinden çalıştırmak için:

```bash
cd ~/uyap-evrak-indirici
./baslat_termux.sh
```

Açılan interaktif menüden dilediğiniz indirme modunu seçebilirsiniz. Mobil İmza veya e-İmza ile oturum yenileme arka planda otomatik gerçekleşir.

---

## 📂 Dosya ve Klasör Hiyerarşisi

İndirilen tüm evraklar mahkeme ve dosya hiyerarşisine uygun şekilde düzenlenir:

```
UYAP_Indirilenler /
├── DURUSMA_TUTANAKLARI /
│   ├── ai_case_manifest.json          <-- Yapay Zeka Dava Kütüphanesi
│   ├── ai_durusma_kronolojisi.csv     <-- Kronolojik Duruşma Tablosu
│   └── Ceza_Yargisi /
│       └── Erzurum_1._Agir_Ceza_Mahkemesi /
│           └── 2025_506 /
│               ├── 2025-12-17_Durusma_Zapti_1._Celse.udf
│               └── 2025-12-17_Durusma_Zapti_1._Celse.pdf
│
├── BILIRKISI_RAPORLARI /
├── GEREKCELI_KARARLAR /
├── DAVA_VE_CEVAP_LAYIHALARI /
├── TEBLIGATLAR /
│   └── tebligat_takip_listesi.csv     <-- Tebligat & Yasal Süre Tablosu
│
├── durusma_takvimi.ics                <-- Google & Outlook Takvim Dosyası
└── durusma_listesi.csv                <-- Duruşma Tablosu
```

---

## 🔒 Gizlilik, KVKK ve Güvenlik

* Bu yazılım **üçüncü taraf hiçbir sunucuya veri göndermez**. Tüm haberleşme doğrudan yerel bilgisayarınız ile Adalet Bakanlığı UYAP sunucuları (`avukat.uyap.gov.tr`) arasında gerçekleşir.
* Kişisel T.C. Kimlik, GSM ve dosya şifreleme anahtarları yerel ortamda korunur ve GitHub reposuna dahil edilmez (`.gitignore`).

---

## 📄 Lisans

Bu proje MIT Lisansı ile lisanslanmıştır.
