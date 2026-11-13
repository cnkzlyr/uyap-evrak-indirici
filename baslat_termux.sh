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
echo ""
echo "=================================================================="
read -p "Seçiminiz (1-7) [Varsayılan: 1]: " secim

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
        echo "  1) Duruşma Tutanakları / Zabıtları"
        echo "  2) Bilirkişi Raporları"
        echo "  3) Gerekçeli Karar ve Hükümler"
        echo "  4) Tensip Zaptı"
        echo "  5) Özel Evrak Türü / Serbest Kelime Arama"
        read -p "Seçiminiz (1-5) [Varsayılan: 1]: " ev_sec
        case "$ev_sec" in
            2) tur="bilirkisi" ;;
            3) tur="karar" ;;
            4) tur="tensip" ;;
            5)
                read -p "Aramak istediğiniz evrak türü / kelime: " custom_tur
                tur="${custom_tur:-durusma}"
                ;;
            *) tur="durusma" ;;
        esac

        read -p "Evrak ekleri (attachments) de indirilsin mi? (e/H): " ek_sec
        read -p "Başlangıç Tarihi Filtresi (İsteğe bağlı GG.AA.YYYY): " start_d
        read -p "Bitiş Tarihi Filtresi     (İsteğe bağlı GG.AA.YYYY): " end_d

        args=("--cli" "--mode" "dosyalar" "--evrak-turu" "$tur")
        if [[ "$ek_sec" =~ ^[eEyY]$ ]]; then
            args+=("--include-attachments")
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
        read -p "Başlangıç Tarihi Filtresi (İsteğe bağlı GG.AA.YYYY): " start_d
        read -p "Bitiş Tarihi Filtresi     (İsteğe bağlı GG.AA.YYYY): " end_d
        args=("--cli" "--mode" "dosyalar" "--evrak-turu" "tumu" "--include-attachments")
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
    *)
        echo ""
        echo "[*] İşlemlerim (Son 1 Yıl) Terminal / CLI Modu başlatılıyor..."
        python3 uyap_indirici.py --cli --mode islemlerim
        ;;
esac
