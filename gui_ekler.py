#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UYAP Evrak Yöneticisi — arayüz yardımcıları: ipuçları, bildirimler (animasyonlu), kullanım kılavuzu,
araç menüsü komutları ve günlük paketleme.
Copyright (c) 2026 Av. Can KIZILYAR. Tüm hakları saklıdır.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk

UYGULAMA_ADI = "UYAP Evrak Yöneticisi"
SURUM = "3.0.0"
IMZA = "© 2026 Av. Can KIZILYAR — Tüm hakları saklıdır."

RENK = {
    "lacivert": "#1f3a5f", "lacivert2": "#2b4f7e", "acik": "#f4f6fa", "yesil": "#1a7f37",
    "kirmizi": "#a31515", "gri": "#6b7280", "vurgu": "#d9e6f7",
}


# ---------------------------------------------------------------------------
# Görünüm
# ---------------------------------------------------------------------------
def stil_uygula(root: tk.Tk):
    st = ttk.Style(root)
    for th in (("vista", "clam") if os.name == "nt" else ("clam",)):
        try:
            st.theme_use(th)
            break
        except tk.TclError:
            continue
    f = ("Segoe UI", 9) if os.name == "nt" else ("DejaVu Sans", 9)
    st.configure("Treeview", rowheight=24, font=f)
    st.configure("Treeview.Heading", font=(f[0], 9, "bold"))
    st.configure("Baslik.TFrame", background=RENK["lacivert"])
    st.configure("Baslik.TLabel", background=RENK["lacivert"], foreground="white", font=(f[0], 13, "bold"))
    st.configure("Imza.TLabel", background=RENK["lacivert"], foreground="#c9d6ea", font=(f[0], 9))
    st.configure("Vurgu.TButton", font=(f[0], 9, "bold"))
    st.configure("Ayrinti.TLabelframe.Label", font=(f[0], 9, "bold"))
    st.configure("Ipucu.TLabel", foreground=RENK["gri"])
    root.option_add("*Listbox.font", f)
    root.option_add("*Text.font", ("Consolas", 9))


class Ipucu:
    """Fare üzerine gelince açıklama gösteren küçük kutu."""

    def __init__(self, widget, metin: str, gecikme: int = 450):
        self.w, self.metin, self.gecikme = widget, metin, gecikme
        self.tip = None
        self._id = None
        widget.bind("<Enter>", self._planla, add="+")
        widget.bind("<Leave>", self._kapat, add="+")
        widget.bind("<ButtonPress>", self._kapat, add="+")

    def _planla(self, _e=None):
        self._id = self.w.after(self.gecikme, self._goster)

    def _goster(self):
        if self.tip:
            return
        x = self.w.winfo_rootx() + 12
        y = self.w.winfo_rooty() + self.w.winfo_height() + 4
        self.tip = tk.Toplevel(self.w)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.metin, justify="left", background="#fffbe6", relief="solid", borderwidth=1,
                 wraplength=420, padx=8, pady=5).pack()

    def _kapat(self, _e=None):
        if self._id:
            self.w.after_cancel(self._id)
            self._id = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


def bildirim(root: tk.Tk, metin: str, renk: str = "#1f3a5f", sure: int = 3500):
    """Sağ altta yumuşak açılıp kapanan (fade) bildirim."""
    try:
        t = tk.Toplevel(root)
        t.wm_overrideredirect(True)
        t.attributes("-topmost", True)
        try:
            t.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        tk.Label(t, text=metin, background=renk, foreground="white", padx=16, pady=10, wraplength=380,
                 justify="left", font=("Segoe UI", 10)).pack()
        root.update_idletasks()
        w, h = t.winfo_reqwidth(), t.winfo_reqheight()
        x = root.winfo_rootx() + root.winfo_width() - w - 24
        y = root.winfo_rooty() + root.winfo_height() - h - 24
        t.wm_geometry(f"+{max(x, 0)}+{max(y, 0)}")

        def fade(a, step):
            try:
                t.attributes("-alpha", a)
            except tk.TclError:
                return
            if step > 0 and a < 0.95:
                t.after(20, fade, min(a + 0.08, 0.95), step)
            elif step > 0:
                t.after(sure, fade, 0.95, -1)
            elif a > 0.05:
                t.after(25, fade, a - 0.07, -1)
            else:
                t.destroy()
        fade(0.0, 1)
    except tk.TclError:
        pass


# ---------------------------------------------------------------------------
# Kullanım kılavuzu
# ---------------------------------------------------------------------------
KILAVUZ = """UYAP EVRAK YÖNETİCİSİ — KULLANIM KILAVUZU
{imza}

1) BAŞLARKEN
   • "Bağlan / Giriş": Tarayıcı arka planda (görünmeden) açılır. UYAP oturumu yoksa e-İmza (PIN yalnızca
     bellekte tutulur) veya Mobil İmza ile giriş yapılır. Oturum indirme sırasında düşerse program aynı
     yöntemle yeniden girer ve AYNI evrakı tekrar dener; yarım/bozuk dosya veya HTML hata sayfası asla
     kaydedilmez, hiçbir evrak atlanmaz.
   • "UYAP'ı Tara": Dosyalarınızdaki tüm evrak ve eklerin LİSTESİNİ kataloğa yazar (indirme yapmaz).
     Günlük kullanımda "Evet: yalnızca AÇIK dosyaları güncelle" yeterlidir. Katalog kalıcıdır; programı
     kapatıp açınca yeniden taranmaz.

2) ASIL EVRAK ve EK NEDİR?
   UYAP'ta her evrakın (ör. "Talimat Üst Yazısı") altında ekleri olabilir (ör. "İfade Tutanağı.tif",
   "gelen_eyp_gönderisi.eyp"). Listede ekler "↳ EK03: …" diye girintili gösterilir.
   Örnek:  2025-07-10  Talimat Üst Yazısı                      (Asıl)
              ↳ EK01: Talimat Dosyasının Emniyetten İadesi     (Ek)
              ↳ EK02: 2025070416582311037.tif                  (Ek)
   • "Yalnız asıl evrak": eklerin hiçbiri gelmez (hızlı, az sayıda).
   • "Yalnız ekler": sadece ekler.
   • Evrak türü listesi ekleri AİT OLDUKLARI ASIL EVRAKIN türüyle süzer. Örn. "Genel Karar/Müzekkere"yi
     kırmızı (hariç) yaparsanız o müzekkerelere eklenmiş yüz binlerce dosya sureti de hariç kalır.
   • "Eklerde ait olduğu evrakın kategorisine bak": kategori listesini de aynı mantıkla çalıştırır.
     Örn. "Karar" yeşil + bu kutu işaretli = kararlar ve kararların ekleri.
   • "Aynı dosyada aynı adlı ekleri bir kez al (tahmini)": Aynı dosyada adı aynı olan eklerden ilkini alır.
     Çok yer kazandırır ama TAHMİNDİR: adı aynı, içeriği farklı ekler de elenebilir.
   • Diskte ekler asıl evrakın adıyla başlar, böylece yan yana durur:
       2025-07-10_Talimat Üst Yazısı_no68987.udf
       2025-07-10_Talimat Üst Yazısı_no68987__EK01_İfade Tutanağı.tif  (+ aynı adla .pdf)

3) FİLTRELER
   • Listeler: 1. tık = getir (yeşil ✔), 2. tık = hariç tut (kırmızı ✖), 3. tık = kaldır.
     Aynı listede hem getirip hem hariç tutabilirsiniz. Sayılar diğer filtrelere göre güncellenir.
     "Sırala: A→Z / Sayı" ile alfabetik ya da sayıya göre sıralayın.
   • Metin kutuları Türkçe harf duyarsızdır (çağlar = CAGLAR). Başına "-" koyduğunuz kelime hariç tutulur,
     tırnak içi tek ifade sayılır:   sungur -ahmet      "ağır ceza" -"sulh ceza"
   • İçerik araması yalnızca indirilmiş (veya önizlenmiş) evrakların metninde çalışır.
   • "Dosya biçimi (uzantı)": UYAP, indirilmeden önce asıl evrakların biçimini bildirmez; bu yüzden
     indirilmemiş asıl evraklar "Henüz bilinmiyor" görünür (büyük çoğunluğu .udf'dir). Eklerde biçim adından
     anlaşılıyorsa (…tif, …pdf, …eyp) baştan gösterilir. İndirilince gerçek biçim yazılır.
   • Filtreleriniz kapanışta kaydedilir, açılışta aynen gelir.

4) MÜKERRER (TEKRAR) KONTROLÜ
   • İstinaf / birleşen / talimat dosyalarında tekrar görünen aynı evrak katalogda TEK kayıttır.
   • Başka evrakın ekinde tekrar gelen aynı belge indirilince içerik özetiyle tanınır ve ikinci kez
     diske yazılmaz ("Mükerrer"). Adı benzersiz dosya adı olan ekler indirmeden önce elenir.
   • İndirme durumu listesinde "Mükerrer"i kırmızı yaparak bunları gizleyin.
   • Klasörden dosya silerseniz program açılışta bunu fark eder ve o evrakları "İndirilmedi"ye alır.

5) GÖRÜNTÜLEYİCİ ve ÖNBELLEK
   • Bir evraka çift tıklayın: indirilmişse PDF'i (yoksa kendisi) açılır. İndirilmemişse UYAP'tan hemen
     önbelleğe alınır, PDF'e çevrilir ve açılır; "Durum" sütununda "önbellekte" yazar. Sonra indirilirse
     yeniden çekilmez, önbellekten taşınır.
   • Alttaki ayrıntı panelinde seçili evrakın bilgileri ve ekleri görünür; ekine çift tıklayarak açabilirsiniz.

6) PDF DÖNÜŞÜMÜ (otomatik)
   • .udf → Havelsan UYAP Editör motoruyla PDF (yazı tipleri gömülü, Türkçe karakterler korunur).
     Her dosya grubunun indirmesi bitince arka planda yapılır; indirmeyi bekletmez.
   • .tif/.tiff (çok sayfalı) → A4 PDF (yatay sayfalar yatay A4).  .jpg/.png → A4 PDF.
   • .docx/.doc/.rtf/.odt → Word (yoksa LibreOffice) ile PDF.
   • PDF her zaman kaynağın yanına AYNI ADLA yazılır; var olanın üzerine yazılmaz.
   • Windows'ta dosyalara sağ tık > "PDF'e dönüştür (UYAP)" (Araçlar menüsünden kurulur). Windows 11'de
     "Daha fazla seçenek göster" altındadır; çoklu seçimde tek pencerede sırayla dönüştürür.
     Ayrıca sağ tık > Gönder > "PDF'e dönüştür (UYAP)".

7) GÜNLÜKLER (LOG)
   • Ekrandaki günlüğün yanında, bağlantı/oturum/deneme ayrıntılarının tamamı loglar/katalog.log dosyasına
     sürekli yazılır (5 MB'ta yeni dosyaya geçer, son 10 dosya tutulur).
   • Yardım > "Günlükleri paketle" Masaüstüne zip oluşturur; destek için bu dosyayı gönderebilirsiniz.
     (PIN ve şifre hiçbir günlüğe yazılmaz.)

8) DİĞER ARAÇLAR (Araçlar menüsü)
   İşlemlerim indirme, duruşma takvimi (.ics), tebligat ve süre analizi, evrak zenginleştirme,
   toplu PDF dönüştürme, UYAP tarayıcısını görünür açma, sağ tık menüsünü kurma/kaldırma.

9) KISAYOLLAR
   Ctrl+F: taraf aramasına git · F5: sonuçları yenile · Ctrl+B / ☰: filtre panelini gizle/göster
   Enter / çift tık: seçili evrakı aç · Ctrl+O: klasörde göster · Ctrl+D: seçilileri indir
"""


def kilavuz_penceresi(root: tk.Tk):
    w = tk.Toplevel(root)
    w.title(f"{UYGULAMA_ADI} — Kullanım kılavuzu")
    w.geometry("860x640")
    txt = tk.Text(w, wrap="word", padx=14, pady=10, font=("Segoe UI", 10))
    sb = ttk.Scrollbar(w, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    txt.pack(fill="both", expand=True)
    txt.insert("1.0", KILAVUZ.format(imza=IMZA))
    txt.configure(state="disabled")


def hakkinda(root: tk.Tk):
    from tkinter import messagebox
    messagebox.showinfo(
        "Hakkında",
        f"{UYGULAMA_ADI} {SURUM}\n\nUYAP Avukat Portal için evrak kataloğu, filtreli toplu indirme, "
        f"görüntüleyici ve PDF dönüştürücü.\n\nGeliştiren: Av. Can KIZILYAR\n{IMZA}\n\n"
        "Bu yazılım UYAP'ın resmi bir ürünü değildir. UDF dönüştürme için Havelsan UYAP Editör "
        "kütüphaneleri kullanıcının kendi kurulumundan kullanılır.", parent=root)


# ---------------------------------------------------------------------------
# Araç komutları
# ---------------------------------------------------------------------------
def konsol_exe() -> str:
    """Paketlenmiş uygulamada konsol araçları için ayrı (konsollu) çalıştırılabilir dosya."""
    exe = Path(sys.executable)
    k = exe.with_name("UYAPEvrakKonsol" + exe.suffix)
    return str(k if k.exists() else exe)


def _uygulama_komutu(*args) -> list[str]:
    if getattr(sys, "frozen", False):
        return [konsol_exe(), *args]
    return [sys.executable, str(Path(__file__).resolve().parent / "uyap_indirici.py"), *args]


def konsolda_calistir(args: list[str], cwd: Path | None = None):
    """Etkileşimli komut satırı araçlarını ayrı bir konsol penceresinde başlatır."""
    cmd = args
    if os.name == "nt":
        subprocess.Popen(cmd, cwd=cwd, creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif sys.platform == "darwin":
        import shlex
        script = " ".join(shlex.quote(c) for c in cmd)
        subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{script}"'])
    else:
        for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["konsole", "-e"], ["xterm", "-e"]):
            try:
                subprocess.Popen(term + cmd, cwd=cwd)
                return
            except OSError:
                continue
        subprocess.Popen(cmd, cwd=cwd)


def arac_komutlari() -> dict:
    py = sys.executable
    here = Path(__file__).resolve().parent

    def script(name, *a):
        if getattr(sys, "frozen", False):
            return [konsol_exe(), f"--{name}", *a]
        return [py, str(here / f"{name}.py"), *a]
    return {
        "islemlerim_1yil": _uygulama_komutu("--cli", "--mode", "islemlerim"),
        "durusma_takvimi": _uygulama_komutu("--cli", "--mode", "durusmalar"),
        "tebligat": script("tebligat_yoneticisi"),
        "zenginlestir": script("enrich_and_rename_documents"),
        "tarayici": _uygulama_komutu("--browser-only"),
    }


def islemlerim_tarih(start: str, end: str) -> list[str]:
    return _uygulama_komutu("--cli", "--mode", "islemlerim", "--start-date", start, "--end-date", end)


def gunlukleri_paketle(log_dirs: list[Path], extra_files: list[Path]) -> Path:
    masaustu = Path.home() / "Desktop"
    if not masaustu.exists():
        masaustu = Path.home()
    out = masaustu / f"uyap_gunlukler_{dt.datetime.now():%Y%m%d_%H%M%S}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for d in log_dirs:
            if d.exists():
                for f in d.glob("*"):
                    if f.is_file():
                        z.write(f, f"loglar/{f.name}")
        for f in extra_files:
            if f.exists() and f.is_file():
                z.write(f, f.name)
        z.writestr("BILGI.txt", f"{UYGULAMA_ADI} {SURUM}\n{dt.datetime.now()}\nPython {sys.version}\n{sys.platform}\n")
    return out


def dosya_ac(path: str):
    if os.name == "nt":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def klasorde_goster(path: str):
    p = Path(path)
    if os.name == "nt" and p.exists() and p.is_file():
        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        dosya_ac(str(p.parent if p.is_file() else p))
