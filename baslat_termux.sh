#!/data/data/com.termux/files/usr/bin/bash
# =============================================================================
#  UYAP Avukat Portal Evrak İndirici & Duruşma Yöneticisi - Android / Termux
# =============================================================================

cd "$(dirname "$0")"

echo ""
echo "=================================================================="
echo "    UYAP Avukat Portal Evrak İndirici - Android / Termux          "
echo "=================================================================="
echo ""
echo "  1) İşlemlerim İndirici (Son 1 Yıl - Hızlı)"
echo "  2) İşlemlerim İndirici (Geçmiş Yıllar / Özel Tarih Aralığı)"
echo "  3) Dava Dosyalarından Belirli Evrak İndir (Duruşma Zaptı, Rapor vb.)"
echo "  4) Dava Dosyalarından TÜM Evrakları ve EKLERİ İndir (Toplu Arşiv)"
echo "  5) Duruşma Takvimi Oluştur (.ics / Google Takvim + CSV)"
echo "  6) Termux:X11 Grafik Arayüz Modu (Telefonda Pencere Açılır)"
echo "  7) Arka Planda Başlat (Headless Chromium)"
echo "  8) İndirilen UDF'leri PDF'e Dönüştür (Toplu Vektörel PDF)"
echo ""
echo "=================================================================="
read -p "Seçiminiz (1-8) [Varsayılan: 1]: " secim

case "$secim" in
    2)
        echo ""
        echo "[*] İşlemlerim - Geçmiş Yıllar / Özel Tarih Aralığı Seçildi"
        read -p "Başlangıç Tarihi (GG.AA.YYYY) [Örn: 01.01.2020]: " start_d
        read -p "Bitiş Tarihi     (GG.AA.YYYY) [Örn: 13.11.2026]: " end_d
        args=("--cli" "--mode" "islemlerim")
        if [ -n "$start_d" ]; then
            args+=("--start-date" "$start_d")
        fi
        if [ -n "$end_d" ]; then
            args+=("--end-date" "$end_d")
        fi
        python3 uyap_indirici.py "${args[@]}"
        ;;
    3)
        echo ""
        echo "[*] Dava Dosyalarından Belirli Evrak İndirme Seçildi"
        echo "  1) Duruşma Tutanakları / Zabıtları (Asıl + Talimat, Celse Zabıtları, SEGBİS) [Önerilen]"
        echo "  2) Bilirkişi Raporları (Kök + Ek Rapor, Uzman Görüşü, Adli Tıp / Kriminal)"
        echo "  3) Gerekçeli Karar, Hüküm ve İlamlar (BAM, Yargıtay, Kısa Karar, Ek Karar)"
        echo "  4) Tensip Zabıtları (İlk + Talimat Tensip Zaptı)"
        echo "  5) Dilekçeler ve Beyanlar (Dava, Cevap, İtiraz, İstinaf, Temyiz, Beyan, Mazeret)"
        echo "  6) İddianame ve Fezleke (Ceza Soruşturma / Kovuşturma)"
        echo "  7) Kesinleşme Şerhleri"
        echo "  8) Özel Evrak Türü / Serbest Kelime Arama"
        read -p "Seçiminiz (1-8) [Varsayılan: 1]: " ev_sec
        case "$ev_sec" in
            2) tur="bilirkisi" ;;
            3) tur="karar" ;;
            4) tur="tensip" ;;
            5) tur="dilekce" ;;
            6) tur="iddianame" ;;
            7) tur="kesinlesme" ;;
            8)
                read -p "Aramak istediğiniz evrak türü / kelime: " custom_tur
                tur="${custom_tur:-durusma}"
                ;;
            *) tur="durusma" ;;
        esac

        echo ""
        echo "  Dosya Kapsamı:"
        echo "    1) Tüm Dosyalar (Açık + Kapalı / Karara Çıkmış - 700+ Dosya) [Önerilen]"
        echo "    2) Yalnızca Açık / Derdest Dosyalar"
        echo "    3) Sık Kullanılan Dosyalar (32 Dosya - Hızlı)"
        read -p "  Seçiminiz (1-3) [Varsayılan: 1]: " scope_sec
        case "$scope_sec" in
            2) scope="acik" ;;
            3) scope="fav" ;;
            *) scope="all" ;;
        esac

        read -p "Evrak ekleri (attachments) de indirilsin mi? (e/H): " ek_sec
        read -p "Filtrelemek istediğiniz Mahkeme / Dosya No (Tümü için boş geçiniz): " filter_kw
        read -p "Başlangıç Tarihi Filtresi (İsteğe bağlı GG.AA.YYYY): " start_d
        read -p "Bitiş Tarihi Filtresi     (İsteğe bağlı GG.AA.YYYY): " end_d

        args=("--cli" "--mode" "dosyalar" "--evrak-turu" "$tur" "--scope" "$scope")
        if [[ "$ek_sec" =~ ^[eEyY]$ ]]; then
            args+=("--include-attachments")
        fi
        if [ -n "$filter_kw" ]; then
            args+=("--filter-birim" "$filter_kw")
        fi
        if [ -n "$start_d" ]; then
            args+=("--start-date" "$start_d")
        fi
        if [ -n "$end_d" ]; then
            args+=("--end-date" "$end_d")
        fi
        python3 uyap_indirici.py "${args[@]}"
        ;;
    4)
        echo ""
        echo "[*] Dava Dosyalarından TÜM Evraklar ve EKLERİ İndiriliyor (Tam Arşiv)..."
        echo "  Dosya Kapsamı:"
        echo "    1) Tüm Dosyalar (Açık + Kapalı / Karara Çıkmış - 700+ Dosya) [Önerilen]"
        echo "    2) Yalnızca Açık / Derdest Dosyalar"
        echo "    3) Sık Kullanılan Dosyalar (32 Dosya - Hızlı)"
        read -p "  Seçiminiz (1-3) [Varsayılan: 1]: " scope_sec
        case "$scope_sec" in
            2) scope="acik" ;;
            3) scope="fav" ;;
            *) scope="all" ;;
        esac

        read -p "Filtrelemek istediğiniz Mahkeme / Dosya No (Tümü için boş geçiniz): " filter_kw
        read -p "Başlangıç Tarihi Filtresi (İsteğe bağlı GG.AA.YYYY): " start_d
        read -p "Bitiş Tarihi Filtresi     (İsteğe bağlı GG.AA.YYYY): " end_d
        args=("--cli" "--mode" "dosyalar" "--evrak-turu" "tumu" "--include-attachments" "--scope" "$scope")
        if [ -n "$filter_kw" ]; then
            args+=("--filter-birim" "$filter_kw")
        fi
        if [ -n "$start_d" ]; then
            args+=("--start-date" "$start_d")
        fi
        if [ -n "$end_d" ]; then
            args+=("--end-date" "$end_d")
        fi
        python3 uyap_indirici.py "${args[@]}"
        ;;
    5)
        echo ""
        echo "[*] Duruşma Takvimi ve Tutanak Yöneticisi Seçildi"
        echo "    (Google Takvim / Android Takvim için .ics dosyası ve CSV üretilir)"
        read -p "Başlangıç Tarihi (GG.AA.YYYY) [Varsayılan: 3 ay önce]: " start_d
        read -p "Bitiş Tarihi     (GG.AA.YYYY) [Varsayılan: 6 ay sonra]: " end_d
        args=("--cli" "--mode" "durusmalar")
        if [ -n "$start_d" ]; then
            args+=("--start-date" "$start_d")
        fi
        if [ -n "$end_d" ]; then
            args+=("--end-date" "$end_d")
        fi
        python3 uyap_indirici.py "${args[@]}"
        ;;
    6)
        echo ""
        echo "[*] Termux:X11 Grafik Arayüzü başlatılıyor..."
        echo "[i] Lütfen Termux:X11 uygulamasını açınız."
        export DISPLAY=:0
        termux-x11 :0 >/dev/null 2>&1 &
        sleep 1.5
        python3 uyap_indirici.py
        ;;
    7)
        echo ""
        echo "[*] Arka Planda Headless Mod başlatılıyor..."
        python3 uyap_indirici.py --cli --headless
        ;;
    8)
        echo ""
        echo "[*] Tüm İndirilen UDF Belgeleri PDF Formatına Dönüştürülüyor..."
        ./udf_toplu_pdf.sh /sdcard/Download/UYAP_Indirilenler
        ;;
    *)
        echo ""
        echo "[*] İşlemlerim (Son 1 Yıl) Terminal / CLI Modu başlatılıyor..."
        python3 uyap_indirici.py --cli --mode islemlerim
        ;;
esac
