#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2026 Av. Can KIZILYAR. Tüm hakları saklıdır. / All rights reserved.
"""
UYAP Evrak Yöneticisi - Katalog, filtreli indirme ve görüntüleyici
Copyright (c) 2026 Av. Can KIZILYAR. Tüm hakları saklıdır.
------------------------------------------------------------------
1) "Bağlan"   : Tarayıcıyı arka planda (görünmez) açar; oturum yoksa e-İmza veya Mobil İmza ile girer.
2) "UYAP'ı Tara": Tüm dosyaların evrak + ek listesini kataloğa yazar (indirmeden).
3) Filtrele   : taraf, müvekkil, açıklama, içerik, tarih aralığı / son N gün, birim, dosya no,
                dosya türü, yargı, açık/kapalı, evrak kategorisi (dahil/hariç), uzantı, asıl/ek, durum.
4) İndir      : filtrelenen tümü veya seçilenler. Her evrak bir kez indirilir; UDF ve TIF'ler PDF'e çevrilir.

PIN yalnızca bellekte tutulur; hiçbir dosyaya veya loga yazılmaz.
"""

from __future__ import annotations

import calendar
import json
import datetime as dt
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, filedialog

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evrak_katalog as ek  # noqa: E402
import gui_ekler as ge  # noqa: E402

ROW_LIMIT = 1000


# ---------------------------------------------------------------------------
# Küçük takvim seçici
# ---------------------------------------------------------------------------
class DatePicker(tk.Toplevel):
    AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
             "Eylül", "Ekim", "Kasım", "Aralık"]

    def __init__(self, master, var: tk.StringVar, on_pick=None):
        super().__init__(master)
        self.title("Tarih seç")
        self.resizable(False, False)
        self.transient(master)
        self.var, self.on_pick = var, on_pick
        try:
            d = dt.date.fromisoformat(var.get())
        except ValueError:
            d = dt.date.today()
        self.y, self.m = d.year, d.month
        top = ttk.Frame(self, padding=4)
        top.pack(fill="x")
        ttk.Button(top, text="«", width=3, command=lambda: self._shift(-12)).pack(side="left")
        ttk.Button(top, text="‹", width=3, command=lambda: self._shift(-1)).pack(side="left")
        self.lbl = ttk.Label(top, width=16, anchor="center")
        self.lbl.pack(side="left", expand=True)
        ttk.Button(top, text="›", width=3, command=lambda: self._shift(1)).pack(side="left")
        ttk.Button(top, text="»", width=3, command=lambda: self._shift(12)).pack(side="left")
        self.grid_f = ttk.Frame(self, padding=4)
        self.grid_f.pack()
        bot = ttk.Frame(self, padding=4)
        bot.pack(fill="x")
        ttk.Button(bot, text="Bugün", command=lambda: self._pick(dt.date.today())).pack(side="left")
        ttk.Button(bot, text="Temizle", command=lambda: self._pick(None)).pack(side="right")
        self._draw()
        self.grab_set()

    def _shift(self, months: int):
        idx = self.y * 12 + (self.m - 1) + months
        self.y, self.m = idx // 12, idx % 12 + 1
        self._draw()

    def _draw(self):
        for w in self.grid_f.winfo_children():
            w.destroy()
        self.lbl.config(text=f"{self.AYLAR[self.m - 1]} {self.y}")
        for i, g in enumerate(["Pt", "Sa", "Ça", "Pe", "Cu", "Ct", "Pz"]):
            ttk.Label(self.grid_f, text=g, width=4, anchor="center").grid(row=0, column=i)
        for r, week in enumerate(calendar.monthcalendar(self.y, self.m), start=1):
            for c, day in enumerate(week):
                if day:
                    ttk.Button(self.grid_f, text=str(day), width=4,
                               command=lambda d=day: self._pick(dt.date(self.y, self.m, d))).grid(row=r, column=c)

    def _pick(self, d):
        self.var.set(d.isoformat() if d else "")
        self.destroy()
        if self.on_pick:
            self.on_pick()


# ---------------------------------------------------------------------------
# Üç durumlu liste: 1. tık getir (yeşil), 2. tık hariç tut (kırmızı), 3. tık kaldır
# ---------------------------------------------------------------------------
class TriList(ttk.Frame):
    RENK = {1: ("#dff5e4", "#0b6b2b", "✔ "), -1: ("#fde0e0", "#a31515", "✖ "), 0: ("#ffffff", "#000000", "    ")}

    def __init__(self, master, height=8, search=False, labeler=None, on_change=None):
        super().__init__(master)
        self.labeler = labeler or (lambda k: k or "(boş)")
        self.on_change = on_change
        self.state: dict[str, int] = {}
        self.items: list[tuple[str, int]] = []
        self.visible: list[str] = []
        self.q = tk.StringVar()
        if search:
            sf = ttk.Frame(self)
            sf.pack(fill="x")
            ttk.Label(sf, text="🔍").pack(side="left")
            ttk.Entry(sf, textvariable=self.q).pack(side="left", fill="x", expand=True)
            self.q.trace_add("write", lambda *a: self.redraw())
        lf = ttk.Frame(self)
        lf.pack(fill="both", expand=True)
        self.lb = tk.Listbox(lf, height=height, activestyle="none", exportselection=False, width=44,
                             selectmode="single", highlightthickness=1)
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.lb.bind("<Button-1>", self._click)
        self.lb.bind("<Double-Button-1>", lambda e: "break")
        self.lb.bind("<B1-Motion>", lambda e: "break")
        bf = ttk.Frame(self)
        bf.pack(fill="x")
        self.sort = "sayi"
        self.btn_sort = ttk.Button(bf, text="Sırala: A→Z", width=12, command=self.toggle_sort)
        self.btn_sort.pack(side="right", padx=(2, 0))
        self.lbl = ttk.Label(bf, text="", foreground="#555")
        self.lbl.pack(side="left")
        ttk.Button(bf, text="Temizle", width=8, command=self.clear).pack(side="right")

    # --- durum
    def has_selection(self) -> bool:
        return any(self.state.values())

    def get(self) -> dict:
        return {"dahil": [k for k, v in self.state.items() if v == 1],
                "haric": [k for k, v in self.state.items() if v == -1]}

    def set_state(self, st: dict, notify: bool = True):
        self.state = {k: v for k, v in st.items() if v in (1, -1)}
        self.redraw()
        if notify and self.on_change:
            self.on_change()

    def clear(self, notify: bool = True):
        self.set_state({}, notify=notify)

    def set_items(self, items: list):
        self.items = list(items)
        have = {k for k, _ in self.items}
        for k in self.state:  # seçili ama artık sayısı 0 olanlar listede kalsın
            if k not in have:
                self.items.append((k, 0))
        self.redraw()

    # --- çizim
    def redraw(self):
        q = ek.fold(self.q.get())
        top = self.lb.yview()[0]
        self.lb.delete(0, "end")
        # Sıra sabit kalır (sayıya göre); tıklanan öğe yer değiştirmez, art arda tıklama aynı öğeye gider
        self.visible = []
        items = self.items
        if self.sort == "alfa":
            items = sorted(items, key=lambda it: ek.fold(self.labeler(it[0])))
        for k, c in items:
            label = self.labeler(k)
            if q and q not in ek.fold(label):
                continue
            s = self.state.get(k, 0)
            bg, fg, mark = self.RENK[s]
            self.lb.insert("end", f"{mark}{label}  ({c:,})".replace(",", "."))
            self.lb.itemconfig("end", background=bg, foreground=fg, selectbackground=bg, selectforeground=fg)
            self.visible.append(k)
        self.lb.yview_moveto(top)
        d = sum(1 for v in self.state.values() if v == 1)
        h = sum(1 for v in self.state.values() if v == -1)
        parts = ([f"{d} getir"] if d else []) + ([f"{h} hariç"] if h else [])
        self.lbl.config(text=", ".join(parts) if parts else "Seçim yok")

    def toggle_sort(self):
        self.set_sort("alfa" if self.sort == "sayi" else "sayi")

    def set_sort(self, mode: str):
        self.sort = mode if mode in ("sayi", "alfa") else "sayi"
        self.btn_sort.config(text="Sırala: Sayı" if self.sort == "alfa" else "Sırala: A→Z")
        self.redraw()

    def _click(self, e):
        i = self.lb.nearest(e.y)
        bbox = self.lb.bbox(i) if i >= 0 else None
        if i < 0 or i >= len(self.visible) or not bbox or not (bbox[1] <= e.y <= bbox[1] + bbox[3]):
            return "break"
        k = self.visible[i]
        nxt = {0: 1, 1: -1, -1: 0}[self.state.get(k, 0)]
        if nxt:
            self.state[k] = nxt
        else:
            self.state.pop(k, None)
        self.redraw()
        if self.on_change:
            self.on_change()
        return "break"


# ---------------------------------------------------------------------------
# Ana pencere
# ---------------------------------------------------------------------------
class KatalogApp:
    def __init__(self, root: tk.Tk):
        import uyap_indirici as ui
        self.ui = ui
        self.root = root
        root.title(f"{ge.UYGULAMA_ADI} {ge.SURUM} — Av. Can KIZILYAR")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{int(sw * 0.9)}x{int(sh * 0.86)}+{int(sw * 0.04)}+{int(sh * 0.03)}")
        try:  # pencere / görev çubuğu simgesi
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
            self._icon = tk.PhotoImage(file=str(base / "assets" / "uyap_evrak_256.png"))
            root.iconphoto(True, self._icon)
        except Exception:
            pass
        root.minsize(min(760, sw), min(520, sh))
        self.kat = ek.Katalog()
        self.bridge = ui.UyapBrowserBridge(port=ui.CDP_PORT)
        self.download_dir = Path(ui.DEFAULT_DOWNLOAD_DIR)
        self.motor = ek.Motor(self.bridge, self.kat, self.download_dir, log=self._ui_log, progress=self.on_progress)
        self._last_progress = 0.0
        self._prog_text = ""
        self.motor.relogin = self._relogin_worker
        self.ui_q: queue.Queue = queue.Queue()
        self.work_q: queue.Queue = queue.Queue()
        self.busy = False
        self.login_method = None   # ("eimza", pin) | ("mobil", tc, gsm, op)
        self._query_token = 0
        self._debounce = None
        self._build()
        if not self._load_state():
            self.tri["durum"].set_state({"yeni": 1}, notify=False)  # ilk açılış: indirilmemişler
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._startup_sync, daemon=True).start()
        self.root.after(100, self._drain)
        self.refresh_facets()
        self.apply_filters()

    # ------------------------------------------------------------------ arayüz
    def _build(self):
        ge.stil_uygula(self.root)
        self._build_menu()
        head = ttk.Frame(self.root, style="Baslik.TFrame", padding=(12, 6))
        head.pack(fill="x")
        ttk.Label(head, text=f"⚖  {ge.UYGULAMA_ADI}", style="Baslik.TLabel").pack(side="left")
        ttk.Label(head, text=ge.IMZA, style="Imza.TLabel").pack(side="right")

        top = ttk.Frame(self.root, padding=(8, 6))
        top.pack(fill="x")
        b = ttk.Button(top, text="☰", width=3, command=self.toggle_filters)
        b.pack(side="left", padx=(0, 6))
        ge.Ipucu(b, "Filtre panelini gizle / göster (Ctrl+B). Dar ekranlarda (ör. Android/Termux) kendiliğinden gizlenir.")
        self.lbl_session = ttk.Label(top, text="Oturum: bağlı değil", foreground="#a33")
        self.lbl_session.pack(side="left")
        self.var_headless = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(top, text="Tarayıcıyı gizli çalıştır", variable=self.var_headless)
        cb.pack(side="left", padx=12)
        ge.Ipucu(cb, "İşaretliyse UYAP için açılan Chrome/Edge penceresi görünmez (arka planda çalışır). "
                     "Giriş e-İmza veya Mobil İmza ile otomatik yapılır.")
        b = ttk.Button(top, text="Bağlan / Giriş", command=self.on_connect, style="Vurgu.TButton")
        b.pack(side="left", padx=2)
        ge.Ipucu(b, "Tarayıcıyı hazırlar, UYAP oturumunu kontrol eder; kapalıysa giriş yöntemini sorar.")
        b = ttk.Button(top, text="UYAP'ı Tara", command=self.on_scan)
        b.pack(side="left", padx=2)
        ge.Ipucu(b, "Evrak LİSTESİNİ günceller (indirme yapmaz). Günlük kullanımda 'yalnızca açık dosyalar' yeterli.")
        self.btn_pause = ttk.Button(top, text="Duraklat", command=self.on_pause)
        self.btn_pause.pack(side="left", padx=2)
        ttk.Button(top, text="Durdur", command=self.on_stop).pack(side="left", padx=2)
        self.lbl_stats = ttk.Label(top, text="")
        self.lbl_stats.pack(side="right")

        prog = ttk.Frame(self.root, padding=(8, 0))
        prog.pack(fill="x")
        self.pbar = ttk.Progressbar(prog, mode="determinate")
        self.pbar.pack(side="left", fill="x", expand=True)
        self.lbl_prog = ttk.Label(prog, text="", width=95)
        self.lbl_prog.pack(side="left", padx=6)

        body = ttk.PanedWindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=6)
        self.body = body

        # ---- filtre paneli (kaydırılabilir)
        outer = ttk.Frame(body)
        canvas = tk.Canvas(outer, width=360, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        fp = ttk.Frame(canvas, padding=(2, 2, 10, 2))
        fp.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=fp, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units")
                        if str(e.widget).startswith(str(fp)) or e.widget is canvas else None)
        body.add(outer, weight=0)
        self.filter_outer = outer
        self._filters_visible = True
        self._build_filters(fp)

        # ---- sonuçlar
        right = ttk.Frame(body)
        body.add(right, weight=1)
        bar = ttk.Frame(right)
        bar.pack(fill="x")
        self.lbl_count = ttk.Label(bar, text="", font=("Segoe UI", 10, "bold"))
        self.lbl_count.pack(side="left")
        ttk.Button(bar, text="CSV'ye aktar", command=self.on_export).pack(side="right", padx=2)
        ttk.Button(bar, text="Klasörü aç", command=self.on_open_folder).pack(side="right", padx=2)
        ttk.Button(bar, text="Seçilileri indir", command=self.on_download_selected).pack(side="right", padx=2)
        self.btn_dl_all = ttk.Button(bar, text="Filtredekilerin tümünü indir", command=self.on_download_filtered)
        self.btn_dl_all.pack(side="right", padx=2)

        cols = ("tarih", "birim", "dosya", "tip", "evrak", "aciklama", "kategori", "durum")
        heads = ("Tarih", "Birim", "Dosya No", "Asıl/Ek", "Evrak / Ek Adı", "Açıklama", "Kategori", "Durum")
        widths = (82, 210, 80, 60, 300, 230, 130, 120)
        vpane = ttk.PanedWindow(right, orient="vertical")
        vpane.pack(fill="both", expand=True, pady=(4, 0))
        tf = ttk.Frame(vpane)
        vpane.add(tf, weight=4)
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, stretch=c in ("evrak", "aciklama"))
        vs = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.tag_configure("indirildi", foreground="#1a7f37")
        self.tree.tag_configure("mukerrer", foreground="#888888")
        self.tree.tag_configure("ek", background="#f6f8fa")
        self.tree.tag_configure("onbellek", foreground="#1f4e8c")
        self.tree.bind("<Double-1>", self.on_open_doc)
        self.tree.bind("<Return>", self.on_open_doc)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_detail())

        # Ayrıntı paneli (görüntüleyici)
        det = ttk.LabelFrame(vpane, text="Seçili evrak", padding=6, style="Ayrinti.TLabelframe")
        vpane.add(det, weight=1)
        self.lbl_detail = ttk.Label(det, text="Bir evrak seçin. Çift tık: aç (indirilmemişse önbelleğe alıp açar).",
                                    justify="left", wraplength=900)
        self.lbl_detail.pack(anchor="w", fill="x")
        dbf = ttk.Frame(det)
        dbf.pack(fill="x", pady=(4, 2))
        for txt, cmd, tip in (
                ("Aç", self.on_open_doc, "İndirilmişse PDF'ini (yoksa kendisini) açar; indirilmemişse önbelleğe alıp açar."),
                ("Orijinal dosyayı aç", lambda: self.on_open_doc(orijinal=True), "PDF yerine UDF/TIF/EYP aslını açar."),
                ("Klasörde göster", self.on_open_folder, "Dosyanın bulunduğu klasörü açar ve dosyayı seçer."),
                ("Seçilileri indir", self.on_download_selected, "Seçili evrakları indirme klasörüne kaydeder."),
                ("Asıl evrak + tüm eklerini seç", self.select_family, "Seçili evrakın asıl evrakını ve tüm eklerini listede seçer.")):
            b = ttk.Button(dbf, text=txt, command=cmd)
            b.pack(side="left", padx=2)
            ge.Ipucu(b, tip)
        self.lb_ekler = tk.Listbox(det, height=4, activestyle="none")
        self.lb_ekler.pack(fill="both", expand=True)
        self.lb_ekler.bind("<Double-Button-1>", self._open_family_item)
        self._family_ids: list = []

        logf = ttk.LabelFrame(vpane, text="İşlem günlüğü (tamamı loglar/katalog.log dosyasına da yazılır)", padding=2)
        vpane.add(logf, weight=1)
        self.txt_log = tk.Text(logf, height=7, wrap="none", font=("Consolas", 9))
        lsb = ttk.Scrollbar(logf, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        self.txt_log.pack(fill="both", expand=True)
        self.txt_log.tag_configure("hata", foreground="#a31515")
        self.txt_log.tag_configure("ok", foreground="#1a7f37")
        self.txt_log.tag_configure("pdf", foreground="#6b3fa0")

        # Kısayollar
        self.root.bind("<Control-b>", lambda e: self.toggle_filters())
        self.root.bind("<F5>", lambda e: self.apply_filters())
        self.root.bind("<Control-f>", lambda e: self._focus_search())
        self.root.bind("<Control-o>", lambda e: self.on_open_folder())
        self.root.bind("<Control-d>", lambda e: self.on_download_selected())
        self.root.bind("<Configure>", self._on_resize, add="+")

    def _build_filters(self, fp: ttk.Frame):
        self.v = {}
        self.tri: dict[str, TriList] = {}
        r = 0

        def sec(title, hint=None):
            nonlocal r
            ttk.Label(fp, text=title, font=("Segoe UI", 9, "bold")).grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 0))
            r += 1
            if hint:
                ttk.Label(fp, text=hint, foreground="#777", wraplength=340, justify="left").grid(row=r, column=0, columnspan=3, sticky="w")
                r += 1

        def entry(label, key, width=30, combo=False):
            nonlocal r
            ttk.Label(fp, text=label).grid(row=r, column=0, sticky="w")
            var = tk.StringVar()
            w = ttk.Combobox(fp, textvariable=var, width=width - 3) if combo else ttk.Entry(fp, textvariable=var, width=width)
            w.grid(row=r, column=1, columnspan=2, sticky="we", pady=1)
            var.trace_add("write", lambda *a: self.schedule_filter())
            if combo:
                w.bind("<<ComboboxSelected>>", lambda e: self.schedule_filter(0))
            self.v[key] = var
            r += 1
            return w

        def tri(key, title, height, search=False, labeler=None, hint=None):
            nonlocal r
            sec(title, hint)
            t = TriList(fp, height=height, search=search, labeler=labeler, on_change=lambda: self.schedule_filter(250))
            t.grid(row=r, column=0, columnspan=3, sticky="we")
            r += 1
            self.tri[key] = t
            return t

        ttk.Label(fp, text="Listelerde: 1. tık = getir (yeşil ✔), 2. tık = hariç tut (kırmızı ✖), 3. tık = kaldır.\n"
                           "Metinlerde: başına - koyulan kelime hariç tutulur (ör. ahmet -mehmet).",
                  foreground="#1f4e8c", justify="left", wraplength=340).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1

        sec("Metin arama (Türkçe harf duyarsız)")
        self.ent_taraf = entry("Taraf / kişi", "taraf")
        ge.Ipucu(self.ent_taraf, "Taraf adı, açıklamadaki kişi adları ve dosyanın taraf/vekil listesinde arar.\n"
                                 "Örnek: sungur -ahmet  →  Sungur geçen, Ahmet geçmeyen.")
        self.cb_muv = entry("Müvekkil", "muvekkil", combo=True)
        self.v["sadece_muvekkilli"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(fp, text="Yalnızca müvekkilim olan dosyalar", variable=self.v["sadece_muvekkilli"],
                        command=self.schedule_filter).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        entry("Açıklama / ad", "aciklama")
        entry("Evrak türü içerir", "tur")
        entry("İçerik (indirilenler)", "icerik")
        entry("Dosya no", "dosya_no")

        sec("Tarih")
        for label, key in (("Başlangıç", "baslangic"), ("Bitiş", "bitis")):
            ttk.Label(fp, text=label).grid(row=r, column=0, sticky="w")
            var = tk.StringVar()
            ttk.Entry(fp, textvariable=var, width=14).grid(row=r, column=1, sticky="w")
            ttk.Button(fp, text="📅", width=3, command=lambda v=var: DatePicker(self.root, v, self.schedule_filter)).grid(row=r, column=2, sticky="w")
            var.trace_add("write", lambda *a: self.schedule_filter())
            self.v[key] = var
            r += 1
        ttk.Label(fp, text="Son … gün").grid(row=r, column=0, sticky="w")
        self.v["son_gun"] = tk.StringVar()
        ttk.Spinbox(fp, from_=0, to=10000, textvariable=self.v["son_gun"], width=8).grid(row=r, column=1, sticky="w")
        self.v["son_gun"].trace_add("write", lambda *a: self.schedule_filter())
        r += 1
        qb = ttk.Frame(fp)
        qb.grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        for n in (7, 30, 90, 365):
            ttk.Button(qb, text=f"{n} gün", width=7, command=lambda n=n: self.v["son_gun"].set(str(n))).pack(side="left", padx=1)
        ttk.Button(qb, text="Temizle", width=8, command=lambda: [self.v[k].set("") for k in ("son_gun", "baslangic", "bitis")]).pack(side="left", padx=1)

        tri("durum", "İndirme durumu", 5, labeler=lambda k: ek.DURUMLAR.get(k, k or "?"))
        tri("kategori", "Evrak kategorisi", 10, labeler=lambda k: ek.KATEGORI_ADI.get(k, k or "?"))
        self.v["kategori_ust"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(fp, text="Eklerde ait olduğu evrakın kategorisine bak", variable=self.v["kategori_ust"],
                        command=self.schedule_filter).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        tri("tur", "Evrak türü (UYAP'taki gerçek türler)", 12, search=True, labeler=lambda k: k or "(türsüz)",
            hint="Ekler, ait oldukları asıl evrakın türüyle süzülür.")
        tri("birim", "Birim / mahkeme", 8, search=True)
        tri("dosya_tur", "Dosya türü", 6)
        tri("yargi", "Yargı", 4, labeler=lambda k: (k or "?").replace("_", " "))
        tri("dosya_durum", "Dosya durumu", 2, labeler=lambda k: k or "?")
        tri("uzanti", "Dosya biçimi (uzantı)", 5, labeler=lambda k: k or "Henüz bilinmiyor (indirilince belli olur)",
            hint="İndirilmemiş asıl evrakların biçimini UYAP önceden bildirmez (çoğu .udf'dir).")

        sec("Asıl evrak / ek")
        self.v["ek"] = tk.StringVar(value="hepsi")
        ef = ttk.Frame(fp); ef.grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        for t, val in (("Hepsi", "hepsi"), ("Yalnız asıl evrak", "asil"), ("Yalnız ekler", "ek")):
            ttk.Radiobutton(ef, text=t, variable=self.v["ek"], value=val, command=self.schedule_filter).pack(side="left")
        self.v["ayni_ad_tek"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(fp, text="Aynı dosyada aynı adlı ekleri bir kez al (tahmini)", variable=self.v["ayni_ad_tek"],
                        command=self.schedule_filter).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1

        sec("İndirme ayarları")
        ttk.Label(fp, text="Bekleme (sn)").grid(row=r, column=0, sticky="w")
        sf = ttk.Frame(fp); sf.grid(row=r, column=1, columnspan=2, sticky="w"); r += 1
        self.v["min_d"] = tk.StringVar(value=str(self.motor.min_d))
        self.v["max_d"] = tk.StringVar(value=str(self.motor.max_d))
        ttk.Entry(sf, textvariable=self.v["min_d"], width=5).pack(side="left")
        ttk.Label(sf, text="–").pack(side="left")
        ttk.Entry(sf, textvariable=self.v["max_d"], width=5).pack(side="left")
        ttk.Label(fp, text="Klasör").grid(row=r, column=0, sticky="w")
        self.v["dir"] = tk.StringVar(value=str(self.download_dir))
        ttk.Entry(fp, textvariable=self.v["dir"], width=24).grid(row=r, column=1, sticky="we")
        ttk.Button(fp, text="…", width=3, command=self.on_choose_dir).grid(row=r, column=2, sticky="w"); r += 1

        bf = ttk.Frame(fp); bf.grid(row=r, column=0, columnspan=3, sticky="we", pady=(10, 4)); r += 1
        ttk.Button(bf, text="Filtreleri sıfırla", command=self.reset_filters).pack(side="left")
        fp.columnconfigure(1, weight=1)

    # ------------------------------------------------------------------ filtre
    TEXT_KEYS = ("taraf", "muvekkil", "aciklama", "tur", "icerik", "dosya_no", "baslangic", "bitis", "son_gun")

    def collect_filters(self) -> dict:
        f = {k: self.v[k].get().strip() for k in self.TEXT_KEYS}
        if f["muvekkil"] and "(" in f["muvekkil"] and not f["muvekkil"].startswith("-"):
            f["muvekkil"] = f["muvekkil"].split("(")[0].strip()
        for k in ("baslangic", "bitis"):
            f[k] = ek.iso_date(f[k]) if f[k] else ""
        f["sadece_muvekkilli"] = self.v["sadece_muvekkilli"].get()
        f["kategori_ust"] = self.v["kategori_ust"].get()
        f["ek"] = self.v["ek"].get()
        f["ayni_ad_tek"] = self.v["ayni_ad_tek"].get()
        f["mukerrer_gizle"] = False  # mükerrerler 'İndirme durumu' listesinden yönetilir
        f["sec"] = {k: t.get() for k, t in self.tri.items() if t.has_selection()}
        return f

    def schedule_filter(self, delay: int = 450):
        if self._debounce:
            self.root.after_cancel(self._debounce)
        self._debounce = self.root.after(delay, self.apply_filters)

    def apply_filters(self):
        self._debounce = None
        f = self.collect_filters()
        self._query_token += 1
        tok = self._query_token
        self.lbl_count.config(text="Sorgulanıyor…")
        self._save_state()

        def run():
            try:
                n = self.kat.count(f)
                rows = self.kat.query(f, limit=ROW_LIMIT)
                self.ui_q.put(("results", tok, n, rows))
                # Liste sayıları: her biri kendi seçimi hariç diğer filtrelere göre (önce görünenler)
                for key in ("durum", "kategori", "tur", "birim", "dosya_tur", "yargi", "dosya_durum", "uzanti"):
                    if tok != self._query_token:
                        return
                    self.ui_q.put(("facet", tok, key, self.kat.facet_counts(f, key)))
            except Exception as e:
                self.ui_q.put(("results_err", tok, str(e)))
        threading.Thread(target=run, daemon=True).start()

    def _show_results(self, n, rows):
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            cached = bool(r["cache_path"]) and r["durum"] == "yeni"
            tags = [r["durum"]] + (["ek"] if r["is_ek"] else []) + (["onbellek"] if cached else [])
            evrak = f"   ↳ EK{int(r['sira'] or 0):02d}: {r['tur']}" if r["is_ek"] else r["tur"]
            self.tree.insert("", "end", iid=str(r["id"]), tags=tags, values=(
                r["tarih"], r["birim"] or "", r["dosya_no"] or "", "Ek" if r["is_ek"] else "Asıl", evrak,
                r["aciklama"] or "", ek.KATEGORI_ADI.get(r["kategori"], r["kategori"]),
                ek.DURUMLAR.get(r["durum"], r["durum"]) + (" · önbellekte" if cached else "")))
        shown = f" (ilk {ROW_LIMIT} gösteriliyor)" if n > ROW_LIMIT else ""
        self.lbl_count.config(text=f"{n:,} evrak{shown}".replace(",", "."))
        self.btn_dl_all.config(text=f"Filtredekilerin tümünü indir ({n:,})".replace(",", "."))

    def refresh_facets(self):
        def run():
            try:
                self.ui_q.put(("facets", self.kat.facets(), self.kat.stats()))
            except Exception as e:
                self.ui_q.put(("log", f"[!] Katalog okunamadı: {e}"))
        threading.Thread(target=run, daemon=True).start()

    def _show_facets(self, fc, st):
        self.cb_muv["values"] = fc["muvekkiller"]
        self.lbl_stats.config(text=(f"Katalog: {st['dosya']} dosya · {st['evrak']:,} evrak · "
                                    f"{st['indirildi']:,} indirildi · {st['mukerrer']:,} mükerrer").replace(",", "."))
        if not st["evrak"]:
            self.log("[i] Katalog boş. Önce 'Bağlan / Giriş', sonra 'UYAP'ı Tara' ile evrak listesini çekin.")

    def reset_filters(self):
        for k in self.TEXT_KEYS:
            self.v[k].set("")
        for k in ("sadece_muvekkilli", "kategori_ust", "ayni_ad_tek"):
            self.v[k].set(False)
        self.v["ek"].set("hepsi")
        for t in self.tri.values():
            t.clear(notify=False)
        self.tri["durum"].set_state({"yeni": 1})
        self.schedule_filter(0)

    # ------------------------------------------------------------------ filtre durumunu sakla / yükle
    STATE_FILE = ek.DATA_DIR / ".katalog_filtre.json"

    def _save_state(self):
        try:
            data = {"text": {k: self.v[k].get() for k in self.TEXT_KEYS},
                    "bool": {k: self.v[k].get() for k in ("sadece_muvekkilli", "kategori_ust", "ayni_ad_tek")},
                    "ek": self.v["ek"].get(),
                    "tri": {k: t.state for k, t in self.tri.items()},
                    "sirala": {k: t.sort for k, t in self.tri.items()},
                    "ayar": {k: self.v[k].get() for k in ("min_d", "max_d", "dir")},
                    "gizli_tarayici": self.var_headless.get()}
            self.STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    def _load_state(self) -> bool:
        try:
            data = json.loads(self.STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return False
        for k, val in (data.get("text") or {}).items():
            if k in self.v:
                self.v[k].set(val)
        for k, val in (data.get("bool") or {}).items():
            if k in self.v:
                self.v[k].set(bool(val))
        self.v["ek"].set(data.get("ek") or "hepsi")
        for k, mode in (data.get("sirala") or {}).items():
            if k in self.tri:
                self.tri[k].set_sort(mode)
        for k, st in (data.get("tri") or {}).items():
            if k in self.tri:
                self.tri[k].set_state({kk: int(vv) for kk, vv in st.items()}, notify=False)
        for k, val in (data.get("ayar") or {}).items():
            if k in self.v and val:
                self.v[k].set(val)
        if "gizli_tarayici" in data:
            self.var_headless.set(bool(data["gizli_tarayici"]))
        return True

    # ------------------------------------------------------------------ olaylar
    def log(self, msg: str):
        """Arayüz mesajı: hem ekrana hem loglar/katalog.log dosyasına."""
        ek.klog.info(msg)
        self.ui_q.put(("log", msg))

    def _ui_log(self, msg: str):
        """Motor kendi günlüğünü dosyaya yazar; buradan yalnızca ekrana gider."""
        self.ui_q.put(("log", msg))

    def on_progress(self, done, total, text=""):
        self.ui_q.put(("progress", done, total, text))

    def _drain(self):
        try:
            while True:
                m = self.ui_q.get_nowait()
                kind = m[0]
                if kind == "log":
                    t = m[1]
                    tag = "hata" if t.startswith(("[x]", "[!]", "[-]")) else "ok" if t.startswith("[✓]") else \
                        "pdf" if t.startswith("[pdf]") else ""
                    self.txt_log.insert("end", time.strftime("%H:%M:%S ") + t + "\n", tag)
                    self.txt_log.see("end")
                    if int(self.txt_log.index("end-1c").split(".")[0]) > 3000:
                        self.txt_log.delete("1.0", "500.0")
                elif kind == "progress":
                    _, done, total, text = m
                    self.pbar.config(maximum=max(total, 1), value=done)
                    self._prog_text = f"{done}/{total}  {text}"
                    self._last_progress = time.time()
                    self.lbl_prog.config(text=self._prog_text)
                elif kind == "results" and m[1] == self._query_token:
                    self._show_results(m[2], m[3])
                elif kind == "facet" and m[1] == self._query_token:
                    self.tri[m[2]].set_items(m[3])
                elif kind == "results_err" and m[1] == self._query_token:
                    self.lbl_count.config(text=f"Sorgu hatası: {m[2]}")
                elif kind == "facets":
                    self._show_facets(m[1], m[2])
                elif kind == "session":
                    self.lbl_session.config(text=m[1], foreground="#1a7f37" if m[2] else "#a33")
                elif kind == "done":
                    self.busy = False
                    self.refresh_facets()
                    self.apply_filters()
                    if len(m) > 1 and m[1]:
                        ge.bildirim(self.root, f"✓ {m[1]} tamamlandı")
                elif kind == "open":
                    try:
                        ge.dosya_ac(m[1])
                    except Exception as e:
                        self.log(f"[!] Açılamadı: {e}")
                    self.apply_filters()
                elif kind == "toast":
                    ge.bildirim(self.root, m[1], m[2] if len(m) > 2 else "#1f3a5f")
                elif kind == "ask_login":
                    self._ask_login(*m[1:])
        except queue.Empty:
            pass
        # Takılma göstergesi: indirme sürerken 45 sn'den uzun ilerleme yoksa kullanıcıya söyle
        if self.busy and self._last_progress and time.time() - self._last_progress > 45:
            self.lbl_prog.config(text=f"{self._prog_text}   ⏳ UYAP yanıtı bekleniyor "
                                      f"({int(time.time() - self._last_progress)} sn; 40 sn'de kesilip yeniden denenir)")
        if not getattr(self, "_closing", False):
            self.root.after(100, self._drain)

    def _submit(self, name, fn):
        if self.busy:
            messagebox.showinfo("Meşgul", "Önceki işlem sürüyor. Bitmesini bekleyin veya 'Durdur'a basın.")
            return
        self.busy = True
        self._last_progress = time.time()
        self.motor.stop_flag.clear()
        self.motor.pause_flag.clear()
        self.btn_pause.config(text="Duraklat")
        self.work_q.put((name, fn))

    def _worker(self):
        while True:
            name, fn = self.work_q.get()
            try:
                fn()
            except self.ui.UyapBrowserError as e:
                self.log(f"[x] Tarayıcı: {e}")
            except Exception as e:
                self.ui.file_logger.exception("katalog_gui %s", name)
                self.log(f"[x] {name} hatası: {e}")
            finally:
                self.ui_q.put(("done", name))

    def _startup_sync(self):
        """Açılışta: klasörden silinen/taşınan indirmeler 'İndirilmedi'ye döner."""
        try:
            n = self.kat.sync_disk()
            if n:
                self.log(f"[i] Diskte bulunamayan {n} evrak 'İndirilmedi' olarak işaretlendi (silinmiş/taşınmış).")
                self.ui_q.put(("done",))
        except Exception as e:
            self.log(f"[!] Disk kontrolü yapılamadı: {e}")

    def _on_close(self):
        self._save_state()
        self._closing = True
        self.root.destroy()

    def on_pause(self):
        if self.motor.pause_flag.is_set():
            self.motor.pause_flag.clear()
            self.btn_pause.config(text="Duraklat")
        else:
            self.motor.pause_flag.set()
            self.btn_pause.config(text="Devam et")

    def on_stop(self):
        self.motor.stop_flag.set()
        self.motor.pause_flag.clear()
        self.log("[!] Durdurma istendi; mevcut evrak bitince duracak.")

    def on_choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.v["dir"].get())
        if d:
            self.v["dir"].set(d)

    def _apply_settings(self):
        try:
            self.motor.min_d = max(0.2, float(self.v["min_d"].get().replace(",", ".")))
            self.motor.max_d = max(self.motor.min_d, float(self.v["max_d"].get().replace(",", ".")))
        except ValueError:
            pass
        d = Path(self.v["dir"].get().strip() or self.download_dir)
        self.download_dir = d
        self.motor.download_dir = d
        self.motor.base = d / "DAVA_EVRAKLARI"

    # ------------------------------------------------------------------ oturum
    def _ensure_session(self) -> bool:
        """İş parçacığında çalışır. Tarayıcıyı hazırlar, gerekirse giriş yaptırır."""
        ok, msg = self.bridge.ensure_browser(headless=self.var_headless.get())
        if not ok:
            self.log(f"[x] Tarayıcı açılamadı: {msg}")
            return False
        info = self.bridge.check_login_and_user()
        if info.get("user"):
            self._session_ok(info["user"])
            return True
        return self._relogin_worker()

    def _ask_method_blocking(self) -> bool:
        """İş parçacığından çağrılır: giriş yöntemini kullanıcıya sorar ve bekler."""
        ark_ok, _ = self.bridge.check_arksigner()
        ev, box = threading.Event(), {}
        self.ui_q.put(("ask_login", ark_ok, ev, box))
        ev.wait()
        if not box.get("method"):
            self.log("[i] Giriş iptal edildi.")
            return False
        self.login_method = box["method"]
        return True

    def _session_ok(self, user: dict):
        ad = " ".join(str(user.get(k) or "") for k in ("adi", "soyadi")).strip() or "giriş yapıldı"
        self.ui_q.put(("session", f"Oturum: açık ({ad})", True))

    def _relogin_worker(self) -> bool:
        """Oturumu yeniler. Kayıtlı yöntem başarısız olursa (ör. kart okunamadı) kullanıcıya bir kez
        daha sorar: kartı yeniden takıp tekrar deneyebilir veya Mobil İmza'ya geçebilir."""
        self.ui_q.put(("session", "Oturum: kapalı — giriş gerekiyor", False))
        for deneme in range(2):
            if not self.login_method and not self._ask_method_blocking():
                return False
            m = self.login_method
            if m[0] == "eimza":
                self.log("[*] e-İmza ile giriş yapılıyor...")
                ok, msg = self.bridge.auto_relogin(m[1])
            else:
                self.log("[*] Mobil İmza ile giriş: telefonunuza gelen onayı verin...")
                ok, msg = self.bridge.login_with_mobil_imza(m[1], m[2], m[3], status_cb=self.log)
            self.log(("[✓] " if ok else "[x] ") + str(msg))
            if ok:
                info = self.bridge.check_login_and_user()
                self._session_ok(info.get("user") or {})
                return True
            if "sertifika" in str(msg).lower() or "kart" in str(msg).lower():
                self.log("[!] e-İmza kartı okunamadı: kartın takılı olduğunu kontrol edin, gerekirse okuyucuyu "
                         "çıkarıp takın veya ArkSigner'ı yeniden başlatın. Ya da Mobil İmza'yı seçin.")
            # Yanlış PIN tekrar denenmez (kart blokesine karşı); her durumda yöntemi yeniden sor
            self.login_method = None
            self.ui_q.put(("session", "Oturum: kapalı", False))
        return False

    def _ask_login(self, ark_ok: bool, ev: threading.Event, box: dict):
        cfg = self.ui.load_user_config()
        w = tk.Toplevel(self.root)
        w.title("UYAP girişi")
        w.transient(self.root)
        w.resizable(False, False)
        frm = ttk.Frame(w, padding=12)
        frm.pack(fill="both")
        ttk.Label(frm, text="UYAP oturumu kapalı. Giriş yöntemi seçin:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        mode = tk.StringVar(value="eimza" if ark_ok else "mobil")
        ttk.Radiobutton(frm, text="e-İmza (ArkSigner)" + ("" if ark_ok else " — ArkSigner çalışmıyor"), variable=mode,
                        value="eimza", state="normal" if ark_ok else "disabled").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(frm, text="PIN").grid(row=2, column=0, sticky="e")
        pin = tk.StringVar()
        ttk.Entry(frm, textvariable=pin, show="•", width=16).grid(row=2, column=1, sticky="w")
        ttk.Radiobutton(frm, text="Mobil İmza (telefon onayı)", variable=mode, value="mobil").grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        tc = tk.StringVar(value=str(cfg.get("tc") or ""))
        gsm = tk.StringVar(value=str(cfg.get("gsm") or ""))
        op = tk.StringVar(value=str(cfg.get("operator") or "2"))
        ttk.Label(frm, text="TC Kimlik No").grid(row=4, column=0, sticky="e")
        ttk.Entry(frm, textvariable=tc, width=16).grid(row=4, column=1, sticky="w")
        ttk.Label(frm, text="GSM (5xx…)").grid(row=5, column=0, sticky="e")
        ttk.Entry(frm, textvariable=gsm, width=16).grid(row=5, column=1, sticky="w")
        ttk.Label(frm, text="Operatör").grid(row=6, column=0, sticky="e")
        opf = ttk.Frame(frm); opf.grid(row=6, column=1, sticky="w")
        for t, v in (("Turkcell", "1"), ("Vodafone", "2"), ("Türk Telekom", "3")):
            ttk.Radiobutton(opf, text=t, variable=op, value=v).pack(side="left")
        ttk.Label(frm, text="PIN yalnızca bu oturum boyunca bellekte tutulur, kaydedilmez.",
                  foreground="#666").grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def ok():
            if mode.get() == "eimza":
                if not pin.get().strip():
                    messagebox.showwarning("PIN", "PIN girin.", parent=w)
                    return
                box["method"] = ("eimza", pin.get().strip())
            else:
                t, g = tc.get().strip(), gsm.get().strip().lstrip("0")
                if len(t) != 11 or not t.isdigit() or len(g) != 10 or not g.startswith("5"):
                    messagebox.showwarning("Bilgi", "TC 11 hane, GSM 5 ile başlayan 10 hane olmalı.", parent=w)
                    return
                self.ui.save_user_config({"tc": t, "gsm": g, "operator": op.get()})
                box["method"] = ("mobil", t, g, op.get())
            w.destroy()
            ev.set()

        def cancel():
            w.destroy()
            ev.set()

        bf = ttk.Frame(frm); bf.grid(row=8, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(bf, text="Giriş yap", command=ok).pack(side="left", padx=4)
        ttk.Button(bf, text="Vazgeç", command=cancel).pack(side="left", padx=4)
        w.protocol("WM_DELETE_WINDOW", cancel)
        w.grab_set()

    # ------------------------------------------------------------------ işlemler
    def on_connect(self):
        self._submit("Bağlantı", self._ensure_session)

    def on_scan(self):
        eksik = self.kat.eksik_dkeys()
        n_dosya = self.kat.stats()["dosya"]
        only = None
        if n_dosya and eksik:
            ans = messagebox.askyesnocancel(
                "UYAP'ı tara",
                f"Katalogda {len(eksik)} dosyanın evrak listesi eksik veya hatalı.\n\n"
                "Evet: yalnızca eksik dosyaları tara\nHayır: tüm dosyaları baştan tara\nİptal: vazgeç")
            if ans is None:
                return
            only = eksik if ans else None
        include_closed = True
        if only is None:
            ans = messagebox.askyesnocancel(
                "UYAP'ı tara",
                "Evet: yalnızca AÇIK dosyaları güncelle (hızlı; kapalı dosyalara yeni evrak düşmez)\n"
                "Hayır: açık + kapalı tüm dosyaları tara (yavaş)\nİptal: vazgeç\n\nİndirme yapılmaz.")
            if ans is None:
                return
            include_closed = not ans

        def job():
            if self._ensure_session():
                self.motor.scan(include_closed=include_closed, only_dkeys=only)
        self._submit("Tarama", job)

    def _start_download(self, ids: list[int]):
        if not ids:
            messagebox.showinfo("İndir", "İndirilecek evrak yok.")
            return
        est_h = len(ids) * ((self.motor.min_d + self.motor.max_d) / 2 + 0.8) / 3600
        if not messagebox.askyesno("İndir", f"{len(ids):,} evrak indirilecek (tahmini {est_h:.1f} saat).\n"
                                   "Zaten indirilmiş ve mükerrer olanlar atlanır. Başlasın mı?".replace(",", ".")):
            return
        self._apply_settings()

        def job():
            if self._ensure_session():
                self.motor.download(ids)
        self._submit("İndirme", job)

    def on_download_selected(self):
        self._start_download([int(i) for i in self.tree.selection()])

    def on_download_filtered(self):
        f = self.collect_filters()
        f["sec"]["durum"] = {"dahil": ["yeni"], "haric": []}  # indirilmiş/mükerrerleri tekrar kuyruğa alma
        ids = self.kat.ids_for(f)
        self._start_download(ids)

    def on_export(self):
        p = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                         initialfile=f"uyap_katalog_{dt.date.today().isoformat()}.csv")
        if p:
            n = ek.export_csv(self.kat, self.collect_filters(), Path(p))
            self.log(f"[✓] {n} satır CSV'ye yazıldı: {p}")

    def _open(self, path: str):
        try:
            if os.name == "nt":
                os.startfile(path)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self.log(f"[!] Açılamadı: {e}")

    def on_open_doc(self, _e=None, orijinal: bool = False, eid: int | None = None):
        if eid is None:
            sel = self.tree.selection()
            if not sel:
                return
            eid = int(sel[0])
        r = self.kat.get(eid)
        if not r:
            return
        for cand in ((r["path"],) if orijinal else (r["pdf_path"], r["path"])) + \
                ((str(Path(r["cache_path"]).with_suffix(".pdf")), r["cache_path"]) if r["cache_path"] else ()):
            if cand and os.path.exists(cand):
                self._open(cand)
                return
        # İndirilmemiş: önbelleğe al ve aç (indirme sürerken de çalışır; ayrı iş parçacığı)
        self.log(f"[*] Önizleme için UYAP'tan alınıyor: {r['tur']} ...")

        def job():
            try:
                if not self.bridge.is_logged_in() and not self._ensure_session():
                    return
                pth = self.motor.fetch_to_cache(eid)
                if pth:
                    self.ui_q.put(("open", str(pth)))
                    self.ui_q.put(("toast", f"Önbelleğe alındı ve açıldı:\n{Path(pth).name}"))
            except Exception as e:
                ek.klog.exception("Önizleme hatası")
                self.log(f"[x] Önizleme hatası: {e}")
        threading.Thread(target=job, daemon=True, name="onizleme").start()

    def on_open_folder(self):
        sel = self.tree.selection()
        r = self.kat.get(int(sel[0])) if sel else None
        target = r["path"] if r and r["path"] and os.path.exists(r["path"]) else None
        try:
            ge.klasorde_goster(target or str(self.download_dir))
        except Exception as e:
            self.log(f"[!] Açılamadı: {e}")

    # ------------------------------------------------------------------ ayrıntı paneli
    def _show_detail(self):
        sel = self.tree.selection()
        if not sel:
            return
        r = self.kat.get(int(sel[0]))
        if not r:
            return
        durum = ek.DURUMLAR.get(r["durum"], r["durum"]) + (" · önbellekte" if r["cache_path"] else "")
        satirlar = [
            f"{'EK ' + str(r['sira']) + ' — ' if r['is_ek'] else ''}{r['tur']}    [{durum}]",
            f"{r['birim']}  ·  Dosya {r['dosya_no']}  ·  {r['dosya_tur'] or ''}  ·  {r['dosya_durum'] or ''}  ·  Tarih {r['tarih']}",
            f"Açıklama: {r['aciklama'] or '-'}    Gönderen: {r['gonderen'] or '-'} {r['gonderen_dosya'] or ''}",
            f"Kategori: {ek.KATEGORI_ADI.get(r['kategori'], r['kategori'])}"
            + (f"    Ait olduğu evrak: {r['ust_tur']}" if r["is_ek"] else ""),
        ]
        if r["path"]:
            satirlar.append(f"Dosya: {r['path']}" + (f"   (PDF: {Path(r['pdf_path']).name})" if r["pdf_path"] else ""))
        if r["hata"]:
            satirlar.append(f"Not: {r['hata']}")
        self.lbl_detail.config(text="\n".join(satirlar))
        parent = r["parent_skey"] if r["is_ek"] else r["skey"]
        fam = self.kat.db.execute(
            "SELECT id, is_ek, sira, tur, durum, cache_path FROM evrak WHERE skey=? OR parent_skey=? "
            "ORDER BY is_ek, sira", (parent, parent)).fetchall()
        self.lb_ekler.delete(0, "end")
        self._family_ids = []
        for f in fam:
            mark = "●" if f["id"] == r["id"] else " "
            txt = (f"{mark} ASIL: {f['tur']}" if not f["is_ek"] else f"{mark}   ↳ EK{int(f['sira'] or 0):02d}: {f['tur']}")
            txt += f"   [{ek.DURUMLAR.get(f['durum'], f['durum'])}{' · önbellekte' if f['cache_path'] else ''}]"
            self.lb_ekler.insert("end", txt)
            self._family_ids.append(f["id"])

    def _open_family_item(self, _e=None):
        sel = self.lb_ekler.curselection()
        if sel:
            self.on_open_doc(eid=self._family_ids[sel[0]])

    def select_family(self):
        if not self._family_ids:
            return
        missing = [str(i) for i in self._family_ids if not self.tree.exists(str(i))]
        self.tree.selection_set([str(i) for i in self._family_ids if self.tree.exists(str(i))])
        if missing:
            self.log(f"[i] {len(missing)} ek mevcut filtrede görünmüyor; 'Seçilileri indir' yalnızca görünenleri alır. "
                     "Hepsini indirmek için ayrıntı panelindeki listeden açabilir veya filtreyi gevşetebilirsiniz.")

    def _focus_search(self):
        self.toggle_filters(True)
        self.ent_taraf.focus_set()

    # ------------------------------------------------------------------ görünüm
    def toggle_filters(self, show: bool | None = None):
        show = (not self._filters_visible) if show is None else show
        if show and not self._filters_visible:
            self.body.insert(0, self.filter_outer, weight=0)
        elif not show and self._filters_visible:
            self.body.forget(self.filter_outer)
        self._filters_visible = show

    def _on_resize(self, e):
        if e.widget is not self.root:
            return
        narrow = e.width < 1000
        if narrow != getattr(self, "_narrow", None):
            self._narrow = narrow
            self.toggle_filters(not narrow)

    def zoom(self, delta: float):
        cur = float(self.root.tk.call("tk", "scaling"))
        self.root.tk.call("tk", "scaling", max(0.8, min(3.0, cur + delta)))
        self.log(f"[i] Ölçek: {max(0.8, min(3.0, cur + delta)):.2f} (bazı öğeler pencere yeniden açılınca güncellenir)")

    # ------------------------------------------------------------------ menüler
    def _build_menu(self):
        mb = tk.Menu(self.root)
        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="İndirme klasörünü aç", command=lambda: ge.dosya_ac(str(self.download_dir)))
        m.add_command(label="İndirme klasörünü değiştir…", command=self.on_choose_dir)
        m.add_command(label="Filtredekileri CSV'ye aktar…", command=self.on_export)
        m.add_separator()
        m.add_command(label="Önbelleği temizle", command=self.clear_cache)
        m.add_separator()
        m.add_command(label="Çıkış", command=self._on_close)
        mb.add_cascade(label="Dosya", menu=m)

        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Bağlan / Giriş", command=self.on_connect)
        m.add_command(label="UYAP'ı tara (katalog güncelle)", command=self.on_scan)
        m.add_command(label="Filtredekilerin tümünü indir", command=self.on_download_filtered)
        m.add_command(label="Seçilileri indir  (Ctrl+D)", command=self.on_download_selected)
        m.add_separator()
        m.add_command(label="Duraklat / devam", command=self.on_pause)
        m.add_command(label="Durdur", command=self.on_stop)
        mb.add_cascade(label="İşlemler", menu=m)

        m = tk.Menu(mb, tearoff=0)
        cmds = ge.arac_komutlari()
        m.add_command(label="İşlemlerim'i indir (son 1 yıl)…", command=lambda: self._run_console("İşlemlerim", cmds["islemlerim_1yil"]))
        m.add_command(label="İşlemlerim'i indir (tarih aralığı)…", command=self.on_islemlerim_aralik)
        m.add_command(label="Duruşma takvimi oluştur (.ics + CSV)…", command=lambda: self._run_console("Duruşma takvimi", cmds["durusma_takvimi"]))
        m.add_command(label="Tebligat ve süre analizi…", command=lambda: self._run_console("Tebligat analizi", cmds["tebligat"]))
        m.add_command(label="Evrak zenginleştirme ve akıllı adlandırma…", command=lambda: self._run_console("Zenginleştirme", cmds["zenginlestir"]))
        m.add_separator()
        m.add_command(label="Toplu PDF'e dönüştür (klasör seç)…", command=self.on_bulk_pdf)
        m.add_command(label="Dosyaları PDF'e dönüştür (dosya seç)…", command=lambda: self.on_bulk_pdf(files=True))
        m.add_command(label="Sağ tık 'PDF'e dönüştür' menüsünü kur", command=lambda: self._context_menu(True))
        m.add_command(label="Sağ tık menüsünü kaldır", command=lambda: self._context_menu(False))
        m.add_separator()
        m.add_command(label="UYAP tarayıcısını görünür aç", command=lambda: self._run_console("Tarayıcı", cmds["tarayici"]))
        mb.add_cascade(label="Araçlar", menu=m)

        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Filtre panelini gizle / göster  (Ctrl+B)", command=self.toggle_filters)
        m.add_command(label="Yakınlaştır", command=lambda: self.zoom(0.15))
        m.add_command(label="Uzaklaştır", command=lambda: self.zoom(-0.15))
        m.add_command(label="Filtreleri sıfırla", command=self.reset_filters)
        mb.add_cascade(label="Görünüm", menu=m)

        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Kullanım kılavuzu", command=lambda: ge.kilavuz_penceresi(self.root))
        m.add_command(label="Günlük (log) klasörünü aç", command=lambda: ge.dosya_ac(str(ek.LOG_DIR)))
        m.add_command(label="Günlükleri paketle (destek için zip)", command=self.on_pack_logs)
        m.add_separator()
        m.add_command(label="Hakkında", command=lambda: ge.hakkinda(self.root))
        mb.add_cascade(label="Yardım", menu=m)
        self.root.config(menu=mb)

    def _run_console(self, name: str, cmd: list):
        try:
            ge.konsolda_calistir(cmd, cwd=Path(__file__).resolve().parent)
            self.log(f"[*] {name} ayrı pencerede başlatıldı. (Aynı UYAP oturumunu kullanır.)")
        except Exception as e:
            self.log(f"[x] {name} başlatılamadı: {e}")

    def on_islemlerim_aralik(self):
        from tkinter import simpledialog
        bas = simpledialog.askstring("İşlemlerim", "Başlangıç tarihi (GG.AA.YYYY):", parent=self.root)
        if not bas:
            return
        bit = simpledialog.askstring("İşlemlerim", "Bitiş tarihi (GG.AA.YYYY):",
                                     initialvalue=dt.date.today().strftime("%d.%m.%Y"), parent=self.root)
        if bit:
            self._run_console("İşlemlerim", ge.islemlerim_tarih(bas.strip(), bit.strip()))

    def on_bulk_pdf(self, files: bool = False):
        import donusturucu as dn
        if files:
            paths = filedialog.askopenfilenames(title="PDF'e dönüştürülecek dosyalar",
                                                filetypes=[("Desteklenenler", " ".join(f"*{e}" for e in sorted(dn.DESTEKLENEN)))])
        else:
            d = filedialog.askdirectory(title="Klasör seç (alt klasörler dahil PDF'i olmayan tüm dosyalar)",
                                        initialdir=str(self.download_dir))
            paths = [d] if d else []
        if not paths:
            return

        def job():
            self.log(f"[*] PDF dönüştürme başladı ({len(paths)} seçim)...")
            r = dn.convert_many(paths, progress=lambda d, t, n: self.on_progress(d, t, f"PDF'e dönüştürülüyor: {n}"))
            self.log(f"[✓] PDF dönüştürme: {r['tamam']} yeni, {r['vardi']} zaten vardı, {r['hata']} hata (toplam {r['toplam']}).")
            for h in r["hatalar"][:20]:
                self.log(f"[x] {h}")
        self._submit("PDF dönüştürme", job)

    def _context_menu(self, install: bool):
        try:
            import sag_tik
            msg = sag_tik.kur() if install else sag_tik.kaldir()
            self.log(f"[✓] {msg}")
            ge.bildirim(self.root, msg + ("\nWindows 11: sağ tık > 'Daha fazla seçenek göster'." if install and os.name == "nt" else ""))
        except Exception as e:
            self.log(f"[x] Sağ tık menüsü: {e}")

    def clear_cache(self):
        n = 0
        if ek.CACHE_DIR.exists():
            for f in ek.CACHE_DIR.iterdir():
                try:
                    f.unlink()
                    n += 1
                except OSError:
                    pass
        with self.kat.db:
            self.kat.db.execute("UPDATE evrak SET cache_path=NULL WHERE cache_path IS NOT NULL")
        self.log(f"[✓] Önbellek temizlendi ({n} dosya).")
        self.apply_filters()

    def on_pack_logs(self):
        try:
            out = ge.gunlukleri_paketle([ek.LOG_DIR], [Path(self.ui.LOG_FILE)])
            self.log(f"[✓] Günlükler paketlendi: {out}")
            ge.bildirim(self.root, f"Günlükler Masaüstüne paketlendi:\n{out.name}")
            ge.klasorde_goster(str(out))
        except Exception as e:
            self.log(f"[x] Günlükler paketlenemedi: {e}")


def main():
    try:  # Windows: yüksek DPI ekranlarda bulanık görünmesin
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    KatalogApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
