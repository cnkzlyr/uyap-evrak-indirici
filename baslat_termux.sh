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
echo "  1) Terminal (CLI) Modu - Ekransız & Çok Hızlı (Önerilen)"
echo "  2) Termux:X11 Grafik Arayüz Modu (Telefonda Pencere Açılır)"
echo "  3) Arka Planda Başlat (Headless Chromium)"
echo ""
echo "=================================================================="
read -p "Seçiminiz (1, 2 veya 3) [Varsayılan: 1]: " secim

case "$secim" in
    2)
        echo ""
        echo "[*] Termux:X11 Grafik Arayüzü başlatılıyor..."
        echo "[i] Lütfen Termux:X11 uygulamasını açınız."
        export DISPLAY=:0
        termux-x11 :0 >/dev/null 2>&1 &
        sleep 1.5
        python3 uyap_indirici.py
        ;;
    3)
        echo ""
        echo "[*] Arka Planda Headless Mod başlatılıyor..."
        python3 uyap_indirici.py --cli --headless
        ;;
    *)
        echo ""
        echo "[*] Terminal / CLI Modu başlatılıyor..."
        python3 uyap_indirici.py --cli
        ;;
esac
