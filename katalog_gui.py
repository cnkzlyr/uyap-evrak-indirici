#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UYAP Evrak Kataloğu - Filtreleyerek Seçip İndirme Arayüzü
----------------------------------------------------------
1) "Bağlan"   : Tarayıcıyı arka planda (görünmez) açar; oturum yoksa e-İmza veya Mobil İmza ile girer.
2) "UYAP'ı Tara": Tüm dosyaların evrak + ek listesini kataloğa yazar (indirmeden).
3) Filtrele   : taraf, müvekkil, açıklama, içerik, tarih aralığı / son N gün, birim, dosya no,
                dosya türü, yargı, açık/kapalı, evrak kategorisi (dahil/hariç), uzantı, asıl/ek, durum.
4) İndir      : filtrelenen tümü veya seçilenler. Her evrak bir kez indirilir; UDF ve TIF'ler PDF'e çevrilir.

PIN yalnızca bellekte tutulur; hiçbir dosyaya veya loga yazılmaz.
"""

from __future__ import annotations

import calendar
import datetime as dt
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, filedialog

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evrak_katalog as ek  # noqa: E402

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
# Ana pencere
# ---------------------------------------------------------------------------
class KatalogApp:
    def __init__(self, root: tk.Tk):
        import uyap_indirici as ui
        self.ui = ui
        self.root = root
        root.title("UYAP Evrak Kataloğu — Filtrele, Seç, Bir Kez İndir")
        root.geometry("1400x860")
        root.minsize(1100, 680)
        self.kat = ek.Katalog()
        self.bridge = ui.UyapBrowserBridge(port=ui.CDP_PORT)
        self.download_dir = Path(ui.DEFAULT_DOWNLOAD_DIR)
        self.motor = ek.Motor(self.bridge, self.kat, self.download_dir, log=self.log, progress=self.on_progress)
        self.motor.relogin = self._relogin_worker
        self.ui_q: queue.Queue = queue.Queue()
        self.work_q: queue.Queue = queue.Queue()
        self.busy = False
        self.login_method = None   # ("eimza", pin) | ("mobil", tc, gsm, op)
        self._query_token = 0
        self._debounce = None
        self._build()
        threading.Thread(target=self._worker, daemon=True).start()
        self.root.after(100, self._drain)
        self.refresh_facets()
        self.apply_filters()

    # ------------------------------------------------------------------ arayüz
    def _build(self):
        st = ttk.Style()
        try:
            st.theme_use("vista" if os.name == "nt" else "clam")
        except tk.TclError:
            pass
        top = ttk.Frame(self.root, padding=(8, 6))
        top.pack(fill="x")
        self.lbl_session = ttk.Label(top, text="Oturum: bağlı değil", foreground="#a33")
        self.lbl_session.pack(side="left")
        self.var_headless = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Tarayıcıyı gizli çalıştır", variable=self.var_headless).pack(side="left", padx=12)
        ttk.Button(top, text="Bağlan / Giriş", command=self.on_connect).pack(side="left", padx=2)
        ttk.Button(top, text="UYAP'ı Tara (katalog güncelle)", command=self.on_scan).pack(side="left", padx=2)
        self.btn_pause = ttk.Button(top, text="Duraklat", command=self.on_pause)
        self.btn_pause.pack(side="left", padx=2)
        ttk.Button(top, text="Durdur", command=self.on_stop).pack(side="left", padx=2)
        self.lbl_stats = ttk.Label(top, text="")
        self.lbl_stats.pack(side="right")

        prog = ttk.Frame(self.root, padding=(8, 0))
        prog.pack(fill="x")
        self.pbar = ttk.Progressbar(prog, mode="determinate")
        self.pbar.pack(side="left", fill="x", expand=True)
        self.lbl_prog = ttk.Label(prog, text="", width=60)
        self.lbl_prog.pack(side="left", padx=6)

        body = ttk.PanedWindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=6)

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
        tf = ttk.Frame(right)
        tf.pack(fill="both", expand=True, pady=(4, 0))
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
        self.tree.bind("<Double-1>", self.on_open_doc)

        logf = ttk.LabelFrame(right, text="İşlem günlüğü", padding=2)
        logf.pack(fill="x", pady=(6, 0))
        self.txt_log = tk.Text(logf, height=9, wrap="none", font=("Consolas", 9))
        self.txt_log.pack(fill="x")

    def _build_filters(self, fp: ttk.Frame):
        self.v = {}
        r = 0

        def sec(title):
            nonlocal r
            ttk.Label(fp, text=title, font=("Segoe UI", 9, "bold")).grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 2))
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

        sec("Metin arama (Türkçe harf duyarsız)")
        entry("Taraf / kişi", "taraf")
        self.cb_muv = entry("Müvekkil", "muvekkil", combo=True)
        self.v["sadece_muvekkilli"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(fp, text="Yalnızca müvekkilim olan dosyalar", variable=self.v["sadece_muvekkilli"],
                        command=self.schedule_filter).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        entry("Açıklama / ad", "aciklama")
        entry("Evrak türü içerir", "tur")
        entry("Hariç kelimeler (virgül)", "tur_haric")
        entry("İçerik (indirilenler)", "icerik")

        sec("Dosya")
        self.cb_birim = entry("Birim", "birim", combo=True)
        entry("Dosya no", "dosya_no")
        self.cb_dtur = entry("Dosya türü", "dosya_tur", combo=True)
        self.cb_yargi = entry("Yargı", "yargi", combo=True)
        ttk.Label(fp, text="Dosya durumu").grid(row=r, column=0, sticky="w")
        self.v["dosya_durum"] = tk.StringVar(value="Tümü")
        dd = ttk.Combobox(fp, textvariable=self.v["dosya_durum"], values=["Tümü", "Açık", "Kapalı"], state="readonly", width=12)
        dd.grid(row=r, column=1, sticky="w"); dd.bind("<<ComboboxSelected>>", lambda e: self.schedule_filter(0)); r += 1

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

        sec("Evrak kategorisi")
        self.lb_kat = tk.Listbox(fp, selectmode="multiple", height=10, exportselection=False, width=40)
        self.lb_kat.grid(row=r, column=0, columnspan=3, sticky="we"); r += 1
        self.kat_codes = [k for k, _ in ek.KATEGORILER] + ["diger"]
        self.lb_kat.bind("<<ListboxSelect>>", lambda e: self.schedule_filter(0))
        self.v["kategori_haric"] = tk.BooleanVar(value=False)
        self.v["kategori_ust"] = tk.BooleanVar(value=False)
        kf = ttk.Frame(fp); kf.grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        ttk.Radiobutton(kf, text="Seçilenleri getir", variable=self.v["kategori_haric"], value=False, command=self.schedule_filter).pack(side="left")
        ttk.Radiobutton(kf, text="Seçilenleri hariç tut", variable=self.v["kategori_haric"], value=True, command=self.schedule_filter).pack(side="left")
        ttk.Checkbutton(fp, text="Eklerde ait olduğu evrakın kategorisine bak", variable=self.v["kategori_ust"],
                        command=self.schedule_filter).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1

        sec("Dosya biçimi / asıl-ek")
        self.lb_uz = tk.Listbox(fp, selectmode="multiple", height=5, exportselection=False, width=40)
        self.lb_uz.grid(row=r, column=0, columnspan=3, sticky="we"); r += 1
        self.lb_uz.bind("<<ListboxSelect>>", lambda e: self.schedule_filter(0))
        self.v["ek"] = tk.StringVar(value="hepsi")
        ef = ttk.Frame(fp); ef.grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        for t, val in (("Hepsi", "hepsi"), ("Yalnız asıl evrak", "asil"), ("Yalnız ekler", "ek")):
            ttk.Radiobutton(ef, text=t, variable=self.v["ek"], value=val, command=self.schedule_filter).pack(side="left")

        sec("Durum ve mükerrer")
        ttk.Label(fp, text="İndirme durumu").grid(row=r, column=0, sticky="w")
        self.v["durum"] = tk.StringVar(value="İndirilmedi")
        self.durum_map = {"Hepsi": "hepsi", "İndirilmedi": "yeni", "İndirildi": "indirildi", "Mükerrer": "mukerrer",
                          "UYAP'ta dosyası yok": "yok", "Hata": "hata"}
        cd = ttk.Combobox(fp, textvariable=self.v["durum"], values=list(self.durum_map), state="readonly", width=18)
        cd.grid(row=r, column=1, columnspan=2, sticky="w"); cd.bind("<<ComboboxSelected>>", lambda e: self.schedule_filter(0)); r += 1
        self.v["mukerrer_gizle"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(fp, text="Mükerrerleri gizle (kesin eşleşme)", variable=self.v["mukerrer_gizle"],
                        command=self.schedule_filter).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
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
    def collect_filters(self) -> dict:
        g = lambda k: self.v[k].get().strip() if isinstance(self.v[k], tk.StringVar) else self.v[k].get()  # noqa: E731
        f = {k: g(k) for k in ("taraf", "muvekkil", "aciklama", "tur", "tur_haric", "icerik", "birim", "dosya_no",
                               "dosya_tur", "yargi", "baslangic", "bitis", "son_gun")}
        if f["muvekkil"]:
            f["muvekkil"] = f["muvekkil"].split("(")[0].strip()
        for k in ("baslangic", "bitis"):
            if f[k] and not ek.iso_date(f[k]):
                f[k] = ""
            elif f[k]:
                f[k] = ek.iso_date(f[k])
        f["dosya_durum"] = self.v["dosya_durum"].get()
        f["sadece_muvekkilli"] = self.v["sadece_muvekkilli"].get()
        f["kategoriler"] = [self.kat_codes[i] for i in self.lb_kat.curselection()]
        f["kategori_haric"] = self.v["kategori_haric"].get()
        f["kategori_ust"] = self.v["kategori_ust"].get()
        f["uzantilar"] = [self.lb_uz.get(i).split(" ")[0] for i in self.lb_uz.curselection()]
        f["ek"] = self.v["ek"].get()
        f["durum"] = self.durum_map.get(self.v["durum"].get(), "hepsi")
        f["mukerrer_gizle"] = self.v["mukerrer_gizle"].get() and f["durum"] != "mukerrer"
        f["ayni_ad_tek"] = self.v["ayni_ad_tek"].get()
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

        def run():
            try:
                n = self.kat.count(f)
                rows = self.kat.query(f, limit=ROW_LIMIT)
                cats = self.kat.category_counts(f)
                self.ui_q.put(("results", tok, n, rows, cats))
            except Exception as e:
                self.ui_q.put(("results_err", tok, str(e)))
        threading.Thread(target=run, daemon=True).start()

    def _show_results(self, n, rows, cats):
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            tags = [r["durum"]] + (["ek"] if r["is_ek"] else [])
            evrak = f"   ↳ EK{int(r['sira'] or 0):02d}: {r['tur']}" if r["is_ek"] else r["tur"]
            self.tree.insert("", "end", iid=str(r["id"]), tags=tags, values=(
                r["tarih"], r["birim"] or "", r["dosya_no"] or "", "Ek" if r["is_ek"] else "Asıl", evrak,
                r["aciklama"] or "", ek.KATEGORI_ADI.get(r["kategori"], r["kategori"]),
                ek.DURUMLAR.get(r["durum"], r["durum"])))
        shown = f" (ilk {ROW_LIMIT} gösteriliyor)" if n > ROW_LIMIT else ""
        self.lbl_count.config(text=f"{n:,} evrak{shown}".replace(",", "."))
        self.btn_dl_all.config(text=f"Filtredekilerin tümünü indir ({n:,})".replace(",", "."))
        sel = set(self.lb_kat.curselection())
        self.lb_kat.delete(0, "end")
        for code in self.kat_codes:
            self.lb_kat.insert("end", f"{ek.KATEGORI_ADI[code]}  ({cats.get(code, 0):,})".replace(",", "."))
        for i in sel:
            self.lb_kat.selection_set(i)

    def refresh_facets(self):
        def run():
            try:
                self.ui_q.put(("facets", self.kat.facets(), self.kat.stats()))
            except Exception as e:
                self.ui_q.put(("log", f"[!] Katalog okunamadı: {e}"))
        threading.Thread(target=run, daemon=True).start()

    def _show_facets(self, fc, st):
        self.cb_birim["values"] = fc["birimler"]
        self.cb_dtur["values"] = [""] + fc["dosya_turleri"]
        self.cb_yargi["values"] = [""] + fc["yargi"]
        self.cb_muv["values"] = fc["muvekkiller"]
        sel = [self.lb_uz.get(i).split(" ")[0] for i in self.lb_uz.curselection()]
        self.lb_uz.delete(0, "end")
        for u in fc["uzantilar"]:
            self.lb_uz.insert("end", u)
            if u in sel:
                self.lb_uz.selection_set("end")
        self.lbl_stats.config(text=(f"Katalog: {st['dosya']} dosya · {st['evrak']:,} evrak · "
                                    f"{st['indirildi']:,} indirildi · {st['mukerrer']:,} mükerrer").replace(",", "."))
        if not st["evrak"]:
            self.log("[i] Katalog boş. Önce 'Bağlan / Giriş', sonra 'UYAP'ı Tara' ile evrak listesini çekin.")

    def reset_filters(self):
        for k, var in self.v.items():
            if k in ("min_d", "max_d", "dir"):
                continue
            if isinstance(var, tk.BooleanVar):
                var.set(k == "mukerrer_gizle")
            elif k == "dosya_durum":
                var.set("Tümü")
            elif k == "durum":
                var.set("İndirilmedi")
            elif k == "ek":
                var.set("hepsi")
            else:
                var.set("")
        self.lb_kat.selection_clear(0, "end")
        self.lb_uz.selection_clear(0, "end")
        self.schedule_filter(0)

    # ------------------------------------------------------------------ olaylar
    def log(self, msg: str):
        self.ui_q.put(("log", msg))

    def on_progress(self, done, total, text=""):
        self.ui_q.put(("progress", done, total, text))

    def _drain(self):
        try:
            while True:
                m = self.ui_q.get_nowait()
                kind = m[0]
                if kind == "log":
                    self.txt_log.insert("end", m[1] + "\n")
                    self.txt_log.see("end")
                    if int(self.txt_log.index("end-1c").split(".")[0]) > 3000:
                        self.txt_log.delete("1.0", "500.0")
                elif kind == "progress":
                    _, done, total, text = m
                    self.pbar.config(maximum=max(total, 1), value=done)
                    self.lbl_prog.config(text=f"{done}/{total}  {text}")
                elif kind == "results" and m[1] == self._query_token:
                    self._show_results(m[2], m[3], m[4])
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
                elif kind == "ask_login":
                    self._ask_login(*m[1:])
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _submit(self, name, fn):
        if self.busy:
            messagebox.showinfo("Meşgul", "Önceki işlem sürüyor. Bitmesini bekleyin veya 'Durdur'a basın.")
            return
        self.busy = True
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
                self.ui_q.put(("done",))

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
        self.ui_q.put(("session", "Oturum: kapalı — giriş gerekiyor", False))
        if not self.login_method and not self._ask_method_blocking():
            return False
        m = self.login_method
        if m[0] == "eimza":
            self.log("[*] e-İmza ile giriş yapılıyor...")
            ok, msg = self.bridge.auto_relogin(m[1])
            if not ok and "PIN" in msg.upper():
                self.login_method = None  # yanlış PIN tekrar denenmez (kart blokesine karşı)
        else:
            self.log("[*] Mobil İmza ile giriş: telefonunuza gelen onayı verin...")
            ok, msg = self.bridge.login_with_mobil_imza(m[1], m[2], m[3], status_cb=self.log)
        self.log(("[✓] " if ok else "[x] ") + str(msg))
        if ok:
            info = self.bridge.check_login_and_user()
            self._session_ok(info.get("user") or {})
        else:
            self.ui_q.put(("session", "Oturum: kapalı", False))
        return ok

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
        elif not messagebox.askyesno("UYAP'ı tara", "Tüm açık ve kapalı dosyaların evrak listeleri kataloğa yazılacak "
                                     "(indirme yapılmaz). 700+ dosyada birkaç dakika sürebilir. Başlasın mı?"):
            return

        def job():
            if self._ensure_session():
                self.motor.scan(include_closed=True, only_dkeys=only)
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
        if f["durum"] in ("hepsi", "indirildi", "mukerrer"):
            f["durum"] = "yeni"  # indirilmişleri tekrar kuyruğa alma
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

    def on_open_doc(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        r = self.kat.get(int(sel[0]))
        p = (r["pdf_path"] if r["pdf_path"] and os.path.exists(r["pdf_path"]) else r["path"]) if r else None
        if p and os.path.exists(p):
            self._open(p)
        else:
            self.log("[i] Bu evrak henüz indirilmemiş.")

    def on_open_folder(self):
        sel = self.tree.selection()
        r = self.kat.get(int(sel[0])) if sel else None
        p = Path(r["path"]).parent if r and r["path"] else self.download_dir
        self._open(str(p if p.exists() else self.download_dir))


def main():
    root = tk.Tk()
    KatalogApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
