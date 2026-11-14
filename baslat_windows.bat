@echo off
chcp 65001 >nul
title UYAP Avukat Portal Evrak Indirici - Windows 11
color 0B

cd /d "%~dp0"

echo ==================================================================
echo    UYAP Avukat Portal Evrak Indirici - Windows 11
echo ==================================================================
echo.
echo   1) Islemlerim Indirici (Son 1 Yil - Hizli)
echo   2) Islemlerim Indirici (Gecmis Yillar / Ozel Tarih Araligi)
echo   3) Dava Dosyalarindan Belirli Evrak Indir (Durusma Zapti, Rapor vb.)
echo   4) Dava Dosyalarindan TUM Evraklari ve EKLERI Indir (Toplu Arsiv)
echo   5) Dava ^& Cevap Layihalari (Dava, Cevap, Bilirkisi Itiraz, Beyan)
echo   6) Tebligat ^& Mazbatalar (Fiziki PTT + UETS Elektronik Tebligat)
echo   7) Indirilen UDF'leri PDF'e Donustur (TrueType Embedded PDF)
echo   8) Chrome / Edge UYAP Oturumu Baslat (Port 9223)
echo.
echo ==================================================================
set /p secim="Seciminiz (1-8) [Varsayilan: 1]: "
if "%secim%"=="" set secim=1

if "%secim%"=="1" (
    echo.
    echo [*] Islemlerim (Son 1 Yil) indiriliyor...
    python uyap_indirici.py --cli --mode islemlerim --quick-mode
    goto bitir
)

if "%secim%"=="2" (
    echo.
    echo [*] Islemlerim - Ozel Tarih Araligi
    set /p start_d="Baslangic Tarihi (GG.AA.YYYY) [Orn: 01.01.2020]: "
    set /p end_d="Bitis Tarihi     (GG.AA.YYYY) [Orn: 14.11.2026]: "
    python uyap_indirici.py --cli --mode islemlerim --start-date "%start_d%" --end-date "%end_d%"
    goto bitir
)

if "%secim%"=="3" (
    echo.
    echo [*] Dava Dosyalarindan Belirli Evrak Indirme
    echo   1) Durusma Tutanaklari / Zabitlari [Onerilen]
    echo   2) Bilirkisi Raporlari
    echo   3) Gerekceli Karar, Hukum ve Ilamlar
    echo   4) Tensip Zabitlari
    echo   5) Muzekkere ve Kurum Cevaplari (Banka, Tapu, SGK vb.)
    echo   6) Iddianame ve Fezleke
    echo   7) Kesinlesme Serhleri
    echo   8) Ozel Evrak Turu / Serbest Kelime Arama
    set /p ev_sec="Seciminiz (1-8) [Varsayilan: 1]: "
    if "%ev_sec%"=="" set ev_sec=1
    
    set TUR=durusma
    if "%ev_sec%"=="2" set TUR=bilirkisi
    if "%ev_sec%"=="3" set TUR=karar
    if "%ev_sec%"=="4" set TUR=tensip
    if "%ev_sec%"=="5" set TUR=muzekkere
    if "%ev_sec%"=="6" set TUR=iddianame
    if "%ev_sec%"=="7" set TUR=kesinlesme
    if "%ev_sec%"=="8" (
        set /p custom_type="Aranacak Evrak Turu / Kelime: "
        set TUR=%custom_type%
    )
    
    echo.
    echo   Dosya Kapsami:
    echo     1) Tum Dosyalar (Acik + Kapali / Karara Cikmis) [Onerilen]
    echo     2) Yalnizca Acik / Derdest Dosyalar
    echo     3) Sik Kullanilan Dosyalar (Hizli)
    set /p kap_sec="Seciminiz (1-3) [Varsayilan: 1]: "
    if "%kap_sec%"=="" set kap_sec=1
    
    set SCOPE=all
    if "%kap_sec%"=="2" set SCOPE=open
    if "%kap_sec%"=="3" set SCOPE=favorites
    
    python uyap_indirici.py --cli --mode evrak_turu --target-type "%TUR%" --dosya-scope "%SCOPE%"
    goto bitir
)

if "%secim%"=="4" (
    echo.
    echo [*] Dava Dosyalarindan TUM Evraklari ve Ekleri Indirme
    python uyap_indirici.py --cli --mode tum_dosyalar --dosya-scope all
    goto bitir
)

if "%secim%"=="5" (
    echo.
    echo [*] Dava ^& Cevap Layihalari Indiriliyor (Dava, Cevap, Bilirkisi Itiraz, Beyan)...
    python uyap_indirici.py --cli --mode evrak_turu --target-type layihalar --dosya-scope all
    goto bitir
)

if "%secim%"=="6" (
    echo.
    echo [*] Tebligat ^& Mazbatalar Indiriliyor (Fiziki PTT + UETS)...
    python uyap_indirici.py --cli --mode evrak_turu --target-type tebligat --dosya-scope all
    goto bitir
)

if "%secim%"=="7" (
    echo.
    echo [*] UDF -^> Vektorel PDF Donusturucu Calistiriliyor...
    call udf_toplu_pdf.bat
    goto bitir
)

if "%secim%"=="8" (
    echo.
    echo [*] Chromium / Chrome / Edge UYAP Oturumu Baslatiliyor (Port 9223)...
    start chrome.exe --remote-debugging-port=9223 --user-data-dir="%LOCALAPPDATA%\Google\Chrome\User Data UYAP" https://avukat.uyap.gov.tr
    if errorlevel 1 (
        start msedge.exe --remote-debugging-port=9223 --user-data-dir="%LOCALAPPDATA%\Microsoft\Edge\User Data UYAP" https://avukat.uyap.gov.tr
    )
    goto bitir
)

:bitir
echo.
echo [✓] Islem tamamlandi.
pause
