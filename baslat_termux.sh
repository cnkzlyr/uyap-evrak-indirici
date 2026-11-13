#!/data/data/com.termux/files/usr/bin/bash
# =============================================================================
#  UYAP Avukat Portal İndirici - Android / Termux Başlatıcı
# =============================================================================

cd "$(dirname "$0")"

echo ""
echo "=================================================================="
echo "    UYAP Avukat Portal Evrak İndirici - Android / Termux          "
echo "=================================================================="
echo ""
echo "  1) Terminal (CLI) Modu - Son 1 Yıl (Önerilen)"
echo "  2) Terminal (CLI) Modu - Geçmiş Yıllar / Özel Tarih Aralığı"
echo "  3) Termux:X11 Grafik Arayüz Modu (Telefonda Pencere Açılır)"
echo "  4) Arka Planda Başlat (Headless Chromium)"
echo ""
echo "=================================================================="
read -p "Seçiminiz (1, 2, 3 veya 4) [Varsayılan: 1]: " secim

case "$secim" in
    2)
        echo ""
        echo "[*] Geçmiş Yıllar / Özel Tarih Aralığı Seçildi"
        read -p "Başlangıç Tarihi (GG.AA.YYYY) [Örn: 01.01.2020]: " start_d
        read -p "Bitiş Tarihi     (GG.AA.YYYY) [Örn: 13.11.2025]: " end_d
        args=("--cli")
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
        echo "[*] Termux:X11 Grafik Arayüzü başlatılıyor..."
        echo "[i] Lütfen Termux:X11 uygulamasını açınız."
        export DISPLAY=:0
        termux-x11 :0 >/dev/null 2>&1 &
        sleep 1.5
        python3 uyap_indirici.py
        ;;
    4)
        echo ""
        echo "[*] Arka Planda Headless Mod başlatılıyor..."
        python3 uyap_indirici.py --cli --headless
        ;;
    *)
        echo ""
        echo "[*] Terminal / CLI Modu (Son 1 Yıl) başlatılıyor..."
        python3 uyap_indirici.py --cli
        ;;
esac
