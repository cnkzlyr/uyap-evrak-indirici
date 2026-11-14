#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UYAP Evrak Kataloğu
-------------------
Tüm dava dosyalarındaki evrakları ve eklerini (yüz binlerce kayıt) indirmeden önce
yerel bir SQLite veritabanına yazar. Böylece:

  * Taraf / müvekkil / açıklama / içerik / tarih / birim / evrak türü / uzantı /
    açık-kapalı gibi filtrelerle saniyeler içinde süzülüp seçilerek indirilir.
  * Her evrak yalnızca BİR KEZ indirilir:
      - Kalıcı anahtar: asıl evrak için (gönderen birim, birim evrak no, tarih, tür),
        ek için (UYAP'ın gerçek ana evrak no'su, ek sırası). UYAP'ın şifreli
        evrakId'si oturumdan oturuma değiştiği için kullanılmaz.
      - İstinaf / birleşen / talimat dosyalarında görünen aynı evrak tek kayıttır.
      - Başka evrakın ekinde tekrar gelen aynı dosya: indirilen içerik SHA-256 ile
        karşılaştırılır, aynısı varsa ikinci kopya diske yazılmaz ("mükerrer" olarak işaretlenir).
      - Ek adı benzersiz bir dosya adıysa (ör. 2025070416582311037.tif) indirmeden
        önce de mükerrer sayılır.
  * İndirilen UDF'ler PDF'e, TIF/TIFF'ler (çok sayfalı) A4 PDF'e aynı adla dönüştürülür.
  * Ekler, ait oldukları evrakın adıyla başlar:  <asıl evrak adı>__EK01_<ek adı>.tif
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import os
import random
import re
import sqlite3
import subprocess
import threading
import time
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
KATALOG_DB = BASE_DIR / "uyap_katalog.db"

# ---------------------------------------------------------------------------
# Metin yardımcıları
# ---------------------------------------------------------------------------
_FOLD = str.maketrans({
    "İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g",
    "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c", "Â": "a", "â": "a",
    "Î": "i", "î": "i", "Û": "u", "û": "u",
})


def fold(text) -> str:
    """Türkçe harf/büyük-küçük farkını yok sayan arama biçimi ('ÇAĞLAR' -> 'caglar')."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text).translate(_FOLD).lower()).strip()


def iso_date(val) -> str:
    """'10/07/2025', '2025-07-10 12:00', '10.07.2025' -> '2025-07-10'."""
    s = str(val or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.match(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def safe_part(value, max_len: int = 60, default: str = "_") -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "")).strip()
    s = re.sub(r"\s+", " ", s).strip(". ")
    if len(s) > max_len:
        s = s[:max_len].rstrip(". _")
    return s or default


_EXT_RX = re.compile(r"\.(tiff?|pdf|udf|eyp|jpe?g|png|gif|bmp|docx?|xlsx?|odt|rtf|txt|zip|rar|7z|mp3|mp4|wav|avi|mov|xml|html?)$", re.I)


def ext_hint(name: str) -> str:
    m = _EXT_RX.search(str(name or "").strip())
    if not m:
        return ""
    e = m.group(1).lower()
    return {"tiff": "tif", "jpeg": "jpg", "htm": "html"}.get(e, e)


def strip_ext(name: str) -> str:
    return _EXT_RX.sub("", str(name or "").strip())


# ---------------------------------------------------------------------------
# Kategoriler (uyap_indirici.matches_evrak_filter kurallarıyla)
# ---------------------------------------------------------------------------
KATEGORILER = [
    ("durusma", "Duruşma Zaptı"),
    ("karar", "Karar / Hüküm / İlam"),
    ("bilirkisi", "Bilirkişi / Uzman Raporu"),
    ("tensip", "Tensip Zaptı"),
    ("iddianame", "İddianame / Fezleke"),
    ("kesinlesme", "Kesinleşme"),
    ("tebligat", "Tebligat / Mazbata"),
    ("muzekkere", "Müzekkere / Kurum Cevabı"),
    ("layihalar", "Dilekçe / Layiha / Beyan"),
]
KATEGORI_ADI = dict(KATEGORILER)
KATEGORI_ADI["diger"] = "Diğer"
KLASOR_ADI = {
    "durusma": "DURUSMA_TUTANAKLARI", "karar": "KARARLAR", "bilirkisi": "BILIRKISI_RAPORLARI",
    "tensip": "TENSIP_ZAPITLARI", "iddianame": "IDDIANAMELER", "kesinlesme": "KESINLESME",
    "tebligat": "TEBLIGATLAR", "muzekkere": "MUZEKKERE_CEVAPLARI", "layihalar": "DILEKCELER",
    "diger": "DIGER_EVRAKLAR",
}

_matcher = None


def kategori_of(tur: str) -> str:
    global _matcher
    if _matcher is None:
        try:
            from uyap_indirici import matches_evrak_filter as _m
        except Exception:  # bağımsız kullanımda basit eşleştirme
            def _m(t, f):
                return f in fold(t)
        _matcher = _m
    for kod, _ad in KATEGORILER:
        try:
            if _matcher(tur, kod):
                return kod
        except Exception:
            pass
    return "diger"


# ---------------------------------------------------------------------------
# Veritabanı
# ---------------------------------------------------------------------------
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS dosya(
  dkey TEXT PRIMARY KEY, birim TEXT, dosya_no TEXT, dosya_tur TEXT, durum TEXT, yargi TEXT,
  acilis TEXT, dosya_id TEXT, taraflar TEXT, taraf_ara TEXT, muvekkil TEXT, muvekkil_ara TEXT,
  evrak_sayisi INTEGER DEFAULT 0, taranma TEXT, oturum TEXT);
CREATE TABLE IF NOT EXISTS evrak(
  id INTEGER PRIMARY KEY, skey TEXT UNIQUE NOT NULL, dkey TEXT, grup TEXT, is_ek INTEGER,
  parent_skey TEXT, sira INTEGER, tur TEXT, ust_tur TEXT, aciklama TEXT, tarih TEXT, tip TEXT,
  gonderen TEXT, gonderen_dosya TEXT, birim_evrak_no TEXT, gercek_id TEXT,
  kategori TEXT, ust_kategori TEXT, uzanti TEXT, ad_anahtar TEXT, ara TEXT,
  ilk_ad INTEGER DEFAULT 1,
  evrak_id TEXT, oturum TEXT,
  durum TEXT DEFAULT 'yeni', dup_of TEXT, path TEXT, pdf_path TEXT, sha256 TEXT, size INTEGER,
  indirildi TEXT, ilk_gorulme TEXT, son_gorulme TEXT, hata TEXT);
CREATE TABLE IF NOT EXISTS evrak_dosya(skey TEXT, dkey TEXT, PRIMARY KEY(skey, dkey)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_e_dkey ON evrak(dkey);
CREATE INDEX IF NOT EXISTS ix_e_tarih ON evrak(tarih);
CREATE INDEX IF NOT EXISTS ix_e_kat ON evrak(kategori);
CREATE INDEX IF NOT EXISTS ix_e_durum ON evrak(durum);
CREATE INDEX IF NOT EXISTS ix_e_sha ON evrak(sha256);
CREATE INDEX IF NOT EXISTS ix_e_ad ON evrak(ad_anahtar);
CREATE INDEX IF NOT EXISTS ix_e_parent ON evrak(parent_skey);
CREATE INDEX IF NOT EXISTS ix_ed_dkey ON evrak_dosya(dkey);
CREATE VIRTUAL TABLE IF NOT EXISTS evrak_fts USING fts5(metin, tokenize="unicode61 remove_diacritics 2");
"""

DURUMLAR = {
    "yeni": "İndirilmedi",
    "indirildi": "İndirildi",
    "mukerrer": "Mükerrer (başka kopyası var)",
    "yok": "UYAP'ta dosyası yok",
    "hata": "Hata",
}


class Katalog:
    """Thread başına ayrı bağlantı kullanır (GUI + indirme iş parçacığı)."""

    def __init__(self, path: Path = KATALOG_DB):
        self.path = Path(path)
        self._local = threading.local()
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(dosya)")}
        if "tarama" not in cols:  # eski kataloglar için şema güncellemesi
            self.db.execute("ALTER TABLE dosya ADD COLUMN tarama TEXT")
            self.db.execute("UPDATE dosya SET tarama='tamam' WHERE evrak_sayisi > 0")
        self.db.commit()

    def set_tarama(self, dkey: str, durum: str):
        with self.db:
            self.db.execute("UPDATE dosya SET tarama=? WHERE dkey=?", (durum, dkey))

    def eksik_dkeys(self) -> set:
        return {r[0] for r in self.db.execute(
            "SELECT dkey FROM dosya WHERE tarama IS NULL OR tarama <> 'tamam'")}

    @property
    def db(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self.path), timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
        return c

    # -------------------------------------------------------------- dosyalar
    @staticmethod
    def dkey_of(birim: str, dosya_no: str) -> str:
        return f"{fold(birim)}|{str(dosya_no or '').strip()}"

    def upsert_dosya(self, case: dict, taraflar: list | None, avukat: str, yargi: str, oturum: str) -> str:
        birim = str(case.get("birimAdi") or case.get("mahkemeAdi") or "").strip()
        dno = str(case.get("dosyaNo") or case.get("dosyaNO") or "").strip()
        dkey = self.dkey_of(birim, dno)
        durum_txt = str(case.get("dosyaDurum") or "")
        if durum_txt.lower().startswith("kapal"):
            durum = "Kapalı"
        elif durum_txt.lower().startswith("açık") or durum_txt.lower().startswith("acik"):
            durum = "Açık"
        else:
            durum = str(case.get("dosyaDurumGenel") or "")
        taraf_txt, muv = "", []
        av = fold(avukat)
        if isinstance(taraflar, list):
            parts = []
            for t in taraflar:
                if not isinstance(t, dict):
                    continue
                adi, rol, vek = str(t.get("adi") or ""), str(t.get("rol") or ""), str(t.get("vekil") or "")
                parts.append(f"{adi} ({rol}){' vekil: ' + vek.strip('[]') if vek else ''}")
                if av and av in fold(vek):
                    muv.append(f"{adi} ({rol})")
            taraf_txt = "; ".join(parts)
        cur = self.db.execute("SELECT taraflar, muvekkil FROM dosya WHERE dkey=?", (dkey,)).fetchone()
        if cur and taraflar is None:  # taraf sorgusu başarısızsa eskisini koru
            taraf_txt, muv_txt = cur["taraflar"] or "", cur["muvekkil"] or ""
        else:
            muv_txt = "; ".join(muv)
        self.db.execute(
            """INSERT INTO dosya(dkey,birim,dosya_no,dosya_tur,durum,yargi,acilis,dosya_id,taraflar,taraf_ara,
                                 muvekkil,muvekkil_ara,taranma,oturum)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(dkey) DO UPDATE SET dosya_tur=excluded.dosya_tur, durum=excluded.durum,
                 yargi=excluded.yargi, acilis=excluded.acilis, dosya_id=excluded.dosya_id,
                 taraflar=excluded.taraflar, taraf_ara=excluded.taraf_ara, muvekkil=excluded.muvekkil,
                 muvekkil_ara=excluded.muvekkil_ara, taranma=excluded.taranma, oturum=excluded.oturum""",
            (dkey, birim, dno, str(case.get("dosyaTur") or ""), durum, yargi,
             iso_date(case.get("dosyaAcilisTarihi")), str(case.get("dosyaId") or case.get("dosyaID") or ""),
             taraf_txt, fold(taraf_txt), muv_txt, fold(muv_txt),
             dt.datetime.now().isoformat(timespec="seconds"), oturum),
        )
        return dkey

    # -------------------------------------------------------------- evraklar
    @staticmethod
    def _top_skey(ev: dict, tarih: str) -> str:
        no = str(ev.get("birimEvrakNo") or "").strip()
        ident = no or fold(ev.get("aciklama"))[:60]
        return f"T|{fold(ev.get('gonderenYerKisi'))}|{ident}|{tarih}|{fold(ev.get('tur'))}"

    def ingest_pages(self, dkey: str, pages: list, oturum: str) -> int:
        """list_dosya_evraklar.ajx yanıtını kataloğa yazar. Yazılan kayıt sayısını döner."""
        now = dt.datetime.now().isoformat(timespec="seconds")
        rows, links = [], []

        def tops():
            for p in pages or []:
                if isinstance(p, dict):
                    tum = p.get("tumEvraklar")
                    if isinstance(tum, dict):
                        for grup, lst in tum.items():
                            for ev in lst or []:
                                yield grup, ev
                    elif isinstance(tum, list):
                        for ev in tum:
                            yield "", ev
                elif isinstance(p, list):
                    for ev in p:
                        yield "", ev

        for grup, ev in tops():
            if not isinstance(ev, dict):
                continue
            tarih = iso_date(ev.get("onaylandigiTarih")) or iso_date(ev.get("sistemeGonderildigiTarih"))
            tur = str(ev.get("tur") or "Evrak").strip()
            acik = str(ev.get("aciklama") or "").strip()
            skey = self._top_skey(ev, tarih)
            ekler = [e for e in (ev.get("ekEvrakListesi") or []) if isinstance(e, dict)]
            gercek = str(ekler[0].get("anaEvrakId") or "") if ekler else ""
            kat = kategori_of(tur)
            gonderen = str(ev.get("gonderenYerKisi") or "")
            gdno = str(ev.get("gonderenDosyaNo") or "")
            rows.append((
                skey, dkey, grup, 0, None, 0, tur, "", acik, tarih, str(ev.get("tip") or ""),
                gonderen, gdno, str(ev.get("birimEvrakNo") or ""), gercek, kat, kat, ext_hint(tur) or "",
                None, fold(" ".join((tur, acik, gonderen, gdno, grup))),
                str(ev.get("evrakId") or ""), oturum, now, now,
            ))
            links.append((skey, dkey))
            for ek in ekler:
                ana = str(ek.get("anaEvrakId") or gercek or "")
                sira = int(ek.get("sira") or 0)
                ek_tur = str(ek.get("ekTuru") or "Ek").strip()
                ek_skey = f"K|{ana}|{sira}" if ana else f"K|{skey}|{sira}"
                # Benzersiz görünen dosya adları (tarama zaman damgası gibi) indirmeden önce mükerrer sayılır
                m = re.search(r"\d{12,}", ek_tur)
                ad_key = f"F|{m.group(0)}" if m else None
                rows.append((
                    ek_skey, dkey, grup, 1, skey, sira, ek_tur, tur, acik, tarih, "",
                    gonderen, gdno, str(ev.get("birimEvrakNo") or ""), ana, kategori_of(ek_tur), kat,
                    ext_hint(ek_tur), ad_key, fold(" ".join((ek_tur, tur, acik, gonderen, gdno, grup))),
                    str(ek.get("evrakId") or ""), oturum, now, now,
                ))
                links.append((ek_skey, dkey))

        if not rows:
            return 0
        with self.db:
            self.db.executemany(
                """INSERT INTO evrak(skey,dkey,grup,is_ek,parent_skey,sira,tur,ust_tur,aciklama,tarih,tip,
                     gonderen,gonderen_dosya,birim_evrak_no,gercek_id,kategori,ust_kategori,uzanti,ad_anahtar,ara,
                     evrak_id,oturum,ilk_gorulme,son_gorulme)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(skey) DO UPDATE SET evrak_id=excluded.evrak_id, oturum=excluded.oturum,
                     son_gorulme=excluded.son_gorulme,
                     -- Şifreli evrakId, görüldüğü dosyanın bağlamına aittir: ikisi birlikte güncellenir
                     dkey=excluded.dkey""",
                rows,
            )
            self.db.executemany("INSERT OR IGNORE INTO evrak_dosya(skey,dkey) VALUES(?,?)", links)
            self.db.execute(
                "UPDATE dosya SET evrak_sayisi=(SELECT COUNT(*) FROM evrak_dosya WHERE dkey=?) WHERE dkey=?",
                (dkey, dkey),
            )
        return len(rows)

    def finalize_scan(self):
        """Tarama sonrası mükerrer işaretleri ve 'aynı adlı ilk ek' bayraklarını hesaplar."""
        with self.db:
            # Aynı benzersiz dosya adına sahip ekler: ilki asıl, diğerleri mükerrer
            self.db.execute("""
                UPDATE evrak SET durum='mukerrer',
                  dup_of=(SELECT MIN(e2.skey) FROM evrak e2 WHERE e2.ad_anahtar=evrak.ad_anahtar)
                WHERE ad_anahtar IS NOT NULL AND durum='yeni'
                  AND skey <> (SELECT MIN(e2.skey) FROM evrak e2 WHERE e2.ad_anahtar=evrak.ad_anahtar
                                AND e2.durum <> 'mukerrer')""")
            # Tahmini mükerrer: aynı dosyada aynı adlı eklerden yalnızca ilki ilk_ad=1
            self.db.execute("UPDATE evrak SET ilk_ad=1")
            self.db.execute("""
                UPDATE evrak SET ilk_ad=0 WHERE is_ek=1 AND id NOT IN (
                  SELECT MIN(id) FROM evrak WHERE is_ek=1 GROUP BY dkey, lower(tur))""")

    # -------------------------------------------------------------- disk eşleştirme (eski indirmeler)
    def match_existing_disk(self, roots: list[Path], log=print) -> int:
        """Bu sistemden önce indirilmiş dosyaları (eski adlandırma) bulup 'indirildi' işaretler."""
        top_idx, ek_idx = {}, {}
        rx = re.compile(r"_(?:evrak_)?(\d+)$")
        for root in roots:
            if not root or not Path(root).exists():
                continue
            for dirpath, _d, files in os.walk(root):
                if "ISLEMLERIM" in dirpath.upper():
                    continue
                for fn in files:
                    stem, _, ext = fn.rpartition(".")
                    if not stem or ext.lower() in ("part", "csv", "json", "md", "ics", "pdf"):
                        continue
                    if not re.match(r"^\d{4}-\d{2}-\d{2}_", stem):
                        continue
                    m = rx.search(stem)
                    if not m:
                        continue
                    full = os.path.join(dirpath, fn)
                    num = m.group(1).lower()
                    if "_EK_" in stem:
                        ek_idx.setdefault(num, []).append((fold(stem), full))
                    else:
                        top_idx.setdefault((stem[:10], num), full)
        if not top_idx and not ek_idx:
            return 0
        n = 0
        cur = self.db.execute(
            "SELECT id, is_ek, tarih, birim_evrak_no, gercek_id, tur FROM evrak WHERE durum='yeni'")
        updates = []
        for r in cur:
            path = None
            if r["is_ek"]:
                cands = ek_idx.get(str(r["gercek_id"] or "").lower())
                if cands:
                    want = "_ek_" + fold(strip_ext(r["tur"]))[:25]
                    path = next((p for s, p in cands if want in s), None)
            else:
                path = top_idx.get((r["tarih"], str(r["birim_evrak_no"] or "").lower()))
            if path:
                pdf = str(Path(path).with_suffix(".pdf"))
                updates.append((path, pdf if os.path.exists(pdf) else None, r["id"]))
        with self.db:
            self.db.executemany(
                "UPDATE evrak SET durum='indirildi', path=?, pdf_path=?, hata='eski indirme' WHERE id=?", updates)
        n = len(updates)
        log(f"[✓] Diskte daha önce indirilmiş {n} evrak katalogda 'indirildi' olarak işaretlendi.")
        return n

    # -------------------------------------------------------------- sorgu / filtre
    def facets(self) -> dict:
        q = self.db.execute
        return {
            "birimler": [r[0] for r in q("SELECT DISTINCT birim FROM dosya ORDER BY birim")],
            "dosya_turleri": [r[0] for r in q("SELECT DISTINCT dosya_tur FROM dosya WHERE dosya_tur<>'' ORDER BY 1")],
            "yargi": [r[0] for r in q("SELECT DISTINCT yargi FROM dosya WHERE yargi<>'' ORDER BY 1")],
            "muvekkiller": sorted({m.strip() for (s,) in q("SELECT muvekkil FROM dosya WHERE muvekkil<>''")
                                   for m in s.split(";") if m.strip()}),
            "uzantilar": [r[0] for r in q("SELECT uzanti FROM evrak WHERE uzanti<>'' GROUP BY uzanti ORDER BY COUNT(*) DESC LIMIT 25")],
            "kategori_sayilari": {r[0]: r[1] for r in q("SELECT kategori, COUNT(*) FROM evrak GROUP BY kategori")},
        }

    def stats(self) -> dict:
        r = self.db.execute("""SELECT COUNT(*) n, SUM(is_ek) ek, SUM(durum='indirildi') ind,
               SUM(durum='mukerrer') muk, SUM(durum='yeni') yeni FROM evrak""").fetchone()
        d = self.db.execute("SELECT COUNT(*) FROM dosya").fetchone()[0]
        return {"evrak": r["n"] or 0, "ek": r["ek"] or 0, "indirildi": r["ind"] or 0,
                "mukerrer": r["muk"] or 0, "yeni": r["yeni"] or 0, "dosya": d}

    def build_where(self, f: dict) -> tuple[str, list]:
        """Filtre sözlüğünden SQL WHERE üretir. Tüm metin aramaları Türkçe-duyarsızdır."""
        w, p = [], []

        def like_all(col_expr: str, text: str):
            for tok in fold(text).split():
                w.append(f"{col_expr} LIKE ?")
                p.append(f"%{tok}%")

        if f.get("aciklama"):
            like_all("e.ara", f["aciklama"])
        if f.get("taraf"):
            for tok in fold(f["taraf"]).split():
                w.append("(e.ara LIKE ? OR d.taraf_ara LIKE ?)")
                p += [f"%{tok}%", f"%{tok}%"]
        if f.get("muvekkil"):
            like_all("d.muvekkil_ara", f["muvekkil"])
        if f.get("sadece_muvekkilli"):
            w.append("d.muvekkil <> ''")
        if f.get("icerik"):
            q = " ".join(f'"{t}"' for t in re.findall(r"\w+", f["icerik"]))
            if q:
                w.append("e.id IN (SELECT rowid FROM evrak_fts WHERE evrak_fts MATCH ?)")
                p.append(q)
        if f.get("birim"):
            like_all("fold_ac(d.birim)", f["birim"])
        if f.get("dosya_no"):
            w.append("(d.dosya_no LIKE ? OR e.gonderen_dosya LIKE ? OR e.grup LIKE ?)")
            p += [f"%{f['dosya_no']}%"] * 3
        if f.get("dosya_tur"):
            w.append("d.dosya_tur = ?")
            p.append(f["dosya_tur"])
        if f.get("yargi"):
            w.append("d.yargi = ?")
            p.append(f["yargi"])
        if f.get("dosya_durum") in ("Açık", "Kapalı"):
            w.append("d.durum = ?")
            p.append(f["dosya_durum"])
        if f.get("baslangic"):
            w.append("e.tarih >= ?")
            p.append(f["baslangic"])
        if f.get("bitis"):
            w.append("e.tarih <= ?")
            p.append(f["bitis"])
        if f.get("son_gun"):
            try:
                w.append("e.tarih >= ?")
                p.append((dt.date.today() - dt.timedelta(days=int(f["son_gun"]))).isoformat())
            except (TypeError, ValueError):
                pass
        kats = f.get("kategoriler") or []
        if kats:
            col = "e.ust_kategori" if f.get("kategori_ust") else "e.kategori"
            ph = ",".join("?" * len(kats))
            w.append(f"{col} {'NOT IN' if f.get('kategori_haric') else 'IN'} ({ph})")
            p += list(kats)
        if f.get("tur"):
            like_all("fold_ac(e.tur)", f["tur"])
        if f.get("tur_haric"):
            for tok in [t.strip() for t in str(f["tur_haric"]).split(",") if t.strip()]:
                w.append("e.ara NOT LIKE ?")
                p.append(f"%{fold(tok)}%")
        uz = f.get("uzantilar") or []
        if uz:
            ph = ",".join("?" * len(uz))
            w.append(f"e.uzanti IN ({ph})")
            p += list(uz)
        ek = f.get("ek")
        if ek == "asil":
            w.append("e.is_ek = 0")
        elif ek == "ek":
            w.append("e.is_ek = 1")
        durum = f.get("durum")
        if durum and durum != "hepsi":
            w.append("e.durum = ?")
            p.append(durum)
        if f.get("mukerrer_gizle", True):
            w.append("e.durum <> 'mukerrer'")
        if f.get("ayni_ad_tek"):
            w.append("e.ilk_ad = 1")
        return (" AND ".join(w) if w else "1=1"), p

    _BASE_FROM = "FROM evrak e LEFT JOIN dosya d ON d.dkey = e.dkey"

    def _ensure_funcs(self):
        if not getattr(self._local, "funcs", False):
            self.db.create_function("fold_ac", 1, fold, deterministic=True)
            self._local.funcs = True

    def count(self, f: dict) -> int:
        self._ensure_funcs()
        where, p = self.build_where(f)
        return self.db.execute(f"SELECT COUNT(*) {self._BASE_FROM} WHERE {where}", p).fetchone()[0]

    def query(self, f: dict, limit: int = 1000, offset: int = 0, order: str = "e.tarih DESC, e.dkey, e.id") -> list:
        self._ensure_funcs()
        where, p = self.build_where(f)
        sql = (f"SELECT e.*, d.birim, d.dosya_no, d.dosya_tur, d.durum AS dosya_durum, d.muvekkil, d.yargi "
               f"{self._BASE_FROM} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?")
        return self.db.execute(sql, p + [limit, offset]).fetchall()

    def ids_for(self, f: dict) -> list[int]:
        self._ensure_funcs()
        where, p = self.build_where(f)
        return [r[0] for r in self.db.execute(
            f"SELECT e.id {self._BASE_FROM} WHERE {where} ORDER BY e.dkey, e.tarih, e.parent_skey, e.sira", p)]

    def category_counts(self, f: dict) -> dict:
        self._ensure_funcs()
        g = dict(f)
        g["kategoriler"] = []
        where, p = self.build_where(g)
        return {r[0]: r[1] for r in self.db.execute(
            f"SELECT e.kategori, COUNT(*) {self._BASE_FROM} WHERE {where} GROUP BY e.kategori", p)}

    def get(self, eid: int):
        return self.db.execute(
            f"SELECT e.*, d.birim, d.dosya_no, d.dosya_tur, d.durum AS dosya_durum, d.yargi, d.dosya_id AS case_dosya_id "
            f"{self._BASE_FROM} WHERE e.id=?", (eid,)).fetchone()

    def by_skey(self, skey: str):
        return self.db.execute("SELECT * FROM evrak WHERE skey=?", (skey,)).fetchone()

    def find_sha(self, sha: str):
        return self.db.execute(
            "SELECT skey, path FROM evrak WHERE sha256=? AND durum='indirildi' AND path IS NOT NULL LIMIT 1",
            (sha,)).fetchone()

    def set_result(self, eid: int, **kw):
        cols = ", ".join(f"{k}=?" for k in kw)
        with self.db:
            self.db.execute(f"UPDATE evrak SET {cols} WHERE id=?", list(kw.values()) + [eid])

    def set_text(self, eid: int, text: str):
        if not text:
            return
        with self.db:
            self.db.execute("DELETE FROM evrak_fts WHERE rowid=?", (eid,))
            self.db.execute("INSERT INTO evrak_fts(rowid, metin) VALUES(?,?)", (eid, text[:200000]))


# ---------------------------------------------------------------------------
# Dosya adı ve klasör düzeni
# ---------------------------------------------------------------------------
def _short_dno(dno: str) -> str:
    return safe_part(str(dno or "").replace("/", "-"), 30)


def doc_stem(row) -> str:
    """Asıl evrak adı: 2025-07-10_Talimat Üst Yazısı_[açıklama]_no68987"""
    tur = safe_part(strip_ext(row["tur"] if not row["is_ek"] else row["ust_tur"]), 45)
    acik = safe_part(row["aciklama"], 35, default="") if row["aciklama"] else ""
    no = safe_part(row["birim_evrak_no"] or (row["gercek_id"] or "")[:12], 14, default="")
    parts = [row["tarih"] or "tarihsiz", tur]
    if acik:
        parts.append(acik)
    if no:
        parts.append(f"no{no}")
    return "_".join(parts)


def target_path_for(row, base: Path, ext: str) -> Path:
    """Klasör: <yargı>/<birim>/<dosya no>/[<gönderen dosya (farklıysa)>]/<asıl evrak kategorisi>/
    Ekler asıl evrakın adıyla başlar ve aynı klasöre yazılır, böylece yan yana sıralanır."""
    folder = base / safe_part(row["yargi"] or "Diger", 30) / safe_part(row["birim"], 55) / _short_dno(row["dosya_no"])
    grup = str(row["grup"] or "")
    gm = re.match(r"\s*(\d{4}/\d+)", grup)
    if gm and gm.group(1) != str(row["dosya_no"] or "").strip():
        folder = folder / safe_part(grup.replace("/", "-"), 45)
    folder = folder / KLASOR_ADI.get(row["ust_kategori"] or row["kategori"] or "diger", "DIGER_EVRAKLAR")
    stem = doc_stem(row)
    if row["is_ek"]:
        stem = f"{stem}__EK{int(row['sira'] or 0):02d}_{safe_part(strip_ext(row['tur']), 40)}"
    ext = ext if ext.startswith(".") else f".{ext}"
    # Windows yol sınırı (260): adı gerekirse kısalt
    room = 245 - len(str(folder)) - len(ext) - 1
    if room < 40:
        room = 40
    if len(stem) > room:
        stem = stem[:room].rstrip(". _")
    return folder / f"{stem}{ext}"


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(2, 1000):
        q = p.with_name(f"{p.stem}_{i}{p.suffix}")
        if not q.exists():
            return q
    return p


# ---------------------------------------------------------------------------
# Dönüştürme ve metin çıkarma
# ---------------------------------------------------------------------------
A4_PX = (1654, 2339)  # 200 dpi


def tiff_to_pdf(src: Path) -> Path | None:
    """Tek/çok sayfalı TIF/TIFF'i A4 boyutlu çok sayfalı PDF'e çevirir (aynı ad, .pdf)."""
    try:
        from PIL import Image, ImageSequence
    except ImportError:
        return None
    dst = src.with_suffix(".pdf")
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    pages = []
    try:
        with Image.open(src) as im:
            for frame in ImageSequence.Iterator(im):
                fr = frame.copy()
                mode = "1" if fr.mode == "1" else ("L" if fr.mode in ("L", "P", "LA", "I;16", "I") else "RGB")
                fr = fr.convert(mode if mode != "1" else "L")
                w, h = fr.size
                landscape = w > h
                pw, ph = (A4_PX[1], A4_PX[0]) if landscape else A4_PX
                scale = min(pw / w, ph / h)
                nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
                fr = fr.resize((nw, nh), Image.LANCZOS)
                page = Image.new(fr.mode, (pw, ph), 255 if fr.mode == "L" else (255, 255, 255))
                page.paste(fr, ((pw - nw) // 2, (ph - nh) // 2))
                pages.append(page)
        if not pages:
            return None
        tmp = dst.with_suffix(".pdf.part")
        pages[0].save(tmp, "PDF", resolution=200.0, save_all=True, append_images=pages[1:])
        tmp.replace(dst)
        return dst
    except Exception:
        return None


def udf_folder_to_pdf(folder: Path, timeout: int = 600) -> None:
    """Klasördeki (alt klasörler dahil) PDF'i olmayan tüm UDF'leri Havelsan motoruyla tek JVM'de çevirir."""
    if not folder.exists():
        return
    jars = BASE_DIR / "jars"
    if not jars.exists():
        jars = BASE_DIR.parent / "jars"
    if not jars.exists():
        jars = Path("/data/data/com.termux/files/home/jars")
    cmd = [
        "java", "-Xmx512m", "-Djava.awt.headless=true",
        "--add-opens=java.base/java.util=ALL-UNNAMED", "--add-opens=java.base/java.lang=ALL-UNNAMED",
        "--add-opens=java.desktop/java.awt=ALL-UNNAMED", "--add-opens=java.desktop/javax.swing=ALL-UNNAMED",
        "-cp", f"{BASE_DIR}{os.pathsep}{jars / '*'}", "uyap.UdfBatchPdfConverter", str(folder),
    ]
    try:
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}  # CREATE_NO_WINDOW
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, **kw)
    except Exception:
        pass


def content_fingerprint(raw: bytes) -> str:
    """Aynı evrakın farklı indirmelerinde değişmeyen özet.
    UYAP, UDF'yi her indirmede yeniden paketler (zip zaman damgası ve documentproperties.xml değişir);
    bu yüzden UDF/zip için yalnızca içerik dosyaları (content.xml, sign.sgn ...) özetlenir."""
    if raw[:2] == b"PK":
        try:
            h = hashlib.sha256()
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = sorted(n for n in z.namelist() if not n.lower().endswith("documentproperties.xml"))
                for n in names:
                    h.update(n.encode("utf-8", "ignore") + b"\0" + z.read(n) + b"\0")
            return "z:" + h.hexdigest()
        except Exception:
            pass
    return hashlib.sha256(raw).hexdigest()


def extract_text(path: Path, raw: bytes | None = None) -> str:
    """İçerik araması için metin: UDF (content.xml) ve PDF (pypdf, ilk 30 sayfa)."""
    try:
        ext = path.suffix.lower()
        data = raw if raw is not None else path.read_bytes()
        if ext == ".udf" or data[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                name = next((n for n in z.namelist() if n.lower().endswith("content.xml")), None)
                if name:
                    xml = z.read(name).decode("utf-8", "ignore")
                    m = re.search(r"<!\[CDATA\[(.*?)\]\]>", xml, re.S)
                    txt = m.group(1) if m else re.sub(r"<[^>]+>", " ", xml)
                    return re.sub(r"\s+", " ", txt).strip()
        if ext == ".pdf" or data[:4] == b"%PDF":
            from pypdf import PdfReader
            r = PdfReader(io.BytesIO(data))
            return re.sub(r"\s+", " ", " ".join((pg.extract_text() or "") for pg in r.pages[:30])).strip()
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Tarama ve indirme motoru (GUI ve CLI ortak)
# ---------------------------------------------------------------------------
class Motor:
    """UYAP tarama + indirme. Tüm köprü (tarayıcı) çağrıları tek iş parçacığından yapılmalıdır."""

    def __init__(self, bridge, katalog: Katalog, download_dir: Path, log=print, progress=None):
        self.bridge = bridge
        self.kat = katalog
        self.download_dir = Path(download_dir)
        self.base = self.download_dir / "DAVA_EVRAKLARI"
        self.log = log
        self.progress = progress or (lambda done, total, text="": None)
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()
        self.oturum = ""
        self.case_ids: dict[str, str] = {}
        self.relogin = None  # () -> bool ; GUI/CLI tarafından atanır
        self.min_d, self.max_d, self.batch_n, self.batch_pause = 0.6, 1.2, 30, 6.0

    # ------------------------------------------------------------------ yardımcılar
    def _new_session(self):
        self.oturum = dt.datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0, 999):03d}"
        self.case_ids = {}

    def _sleep(self, s: float):
        end = time.time() + s
        while time.time() < end and not self.stop_flag.is_set():
            time.sleep(0.1)
        while self.pause_flag.is_set() and not self.stop_flag.is_set():
            time.sleep(0.2)

    def _avukat_adi(self, all_taraf: list) -> str:
        try:
            info = self.bridge.check_login_and_user() or {}
            u = info.get("user") or {}
            ad = " ".join(str(u.get(k) or "") for k in ("adi", "soyadi")).strip()
            if ad:
                return ad
        except Exception:
            pass
        # En çok dosyada vekil olarak geçen isim avukatın kendisidir
        cnt = {}
        for tl in all_taraf:
            names = set()
            for t in tl or []:
                for n in str((t or {}).get("vekil") or "").strip("[]").split(","):
                    n = n.strip()
                    if n:
                        names.add(n)
            for n in names:
                cnt[n] = cnt.get(n, 0) + 1
        return max(cnt, key=cnt.get) if cnt else ""

    def _taraflar_batch(self, ids: list) -> dict:
        import json as _json
        js = """(async () => { const ids = %s; const out = {};
          await Promise.all(ids.map(async (id) => { try {
            const r = await fetch("/dosya_taraf_bilgileri_brd.ajx", {method:"POST",
              headers:{"Content-Type":"application/json"}, body: JSON.stringify({dosyaId: id})});
            const d = await r.json(); out[id] = Array.isArray(d) ? d : null; } catch(e) { out[id] = null; } }));
          return out; })()""" % _json.dumps(ids)
        try:
            res = self.bridge.eval_js(js, timeout=60.0)
            return res if isinstance(res, dict) else {}
        except Exception:
            return {}

    def _evraklar_batch(self, ids: list) -> dict:
        """Dosyaların TÜM evrak sayfalarını paralel çeker (sayfa sınırı yok).
        Başarılıysa {id: [sayfalar]}, değilse {id: {"err": "oturum"|"<UYAP hata kodu>"|..., "msg": ...}}."""
        import json as _json
        js = """(async () => { const ids = %s; const out = {};
          const post = (u, b) => fetch(u, {method:"POST", headers:{"Content-Type":"application/json","Cache-Control":"no-cache"}, body: JSON.stringify(b)});
          await Promise.all(ids.map(async (id) => { try {
            const pages = [];
            const r1 = await post("/list_dosya_evraklar.ajx", {dosyaId: id, pageNumber: 1});
            const ct = r1.headers.get("content-type") || "";
            const txt = await r1.text();
            if (!r1.ok || ct.includes("text/html") || /^\\s*</.test(txt)) { out[id] = {err: "oturum", msg: "HTTP " + r1.status}; return; }
            let d1; try { d1 = JSON.parse(txt); } catch(e) { out[id] = {err: "oturum", msg: "JSON değil"}; return; }
            if (d1 && !Array.isArray(d1) && d1.errorCode) { out[id] = {err: String(d1.errorCode), msg: String(d1.error || "")}; return; }
            pages.push(d1);
            let total = Number(d1 && d1.pageTotal) || 1;
            if (total <= 1) { try { const t = await (await post("/listDosyaEvraklarPageTotal.ajx", {dosyaId: id, pageNumber: 1})).json();
                 total = typeof t === "number" ? t : (Number(t && t.total) || 1); } catch(e) {} }
            for (let p = 2; p <= Math.min(total, 200); p++) {
              const r = await post("/list_dosya_evraklar.ajx", {dosyaId: id, pageNumber: p});
              if (r.ok) pages.push(await r.json());
            }
            out[id] = pages; } catch(e) { out[id] = {err: "ag", msg: String(e)}; } }));
          return out; })()""" % _json.dumps(ids)
        try:
            res = self.bridge.eval_js(js, timeout=240.0)
            return res if isinstance(res, dict) else {}
        except Exception:
            return {}

    # ------------------------------------------------------------------ tarama
    @staticmethod
    def _case_dkey(c: dict) -> str:
        return Katalog.dkey_of(c.get("birimAdi") or c.get("mahkemeAdi"), c.get("dosyaNo") or c.get("dosyaNO"))

    def _fetch_cases(self, include_closed: bool) -> list:
        cases = self.bridge.query_tum_dosyalar(include_closed=include_closed, use_cache=False) or []
        self.case_ids = {self._case_dkey(c): str(c.get("dosyaId") or c.get("dosyaID") or "") for c in cases}
        return cases

    def scan(self, include_closed: bool = True, batch: int = 6, only_dkeys: set | None = None) -> int:
        """Dosyaların evrak listelerini kataloğa yazar. Oturum düşerse yeniden giriş yaptırıp
        kaldığı dosyadan devam eder; taranamayan dosyalar 'tarama' sütununda işaretli kalır
        (sonra only_dkeys=katalog.eksik_dkeys() ile yalnızca onlar taranabilir)."""
        from uyap_indirici import infer_yargi_kategorisi
        self._new_session()
        self.log("[*] UYAP'taki tüm dosyalar sorgulanıyor...")
        cases = self._fetch_cases(include_closed)
        if not cases:
            if self._session_dead() and self._relogin():
                cases = self._fetch_cases(include_closed)
            if not cases:
                self.log("[!] Dosya listesi alınamadı (oturum kapalı olabilir).")
                return 0
        if only_dkeys is not None:
            cases = [c for c in cases if self._case_dkey(c) in only_dkeys]
        self.log(f"[✓] {len(cases)} dosya taranacak. Taraf ve evrak listeleri çekiliyor...")
        taraf_map = {}  # dkey -> taraf listesi
        for i in range(0, len(cases), 20):
            if self.stop_flag.is_set():
                return 0
            chunk = cases[i:i + 20]
            res = self._taraflar_batch([str(c.get("dosyaId") or c.get("dosyaID") or "") for c in chunk])
            for c in chunk:
                v = res.get(str(c.get("dosyaId") or c.get("dosyaID") or ""))
                if v:
                    taraf_map[self._case_dkey(c)] = v
            self.progress(min(i + 20, len(cases)), len(cases), "Taraf bilgileri")
        avukat = self._avukat_adi(list(taraf_map.values()))
        if avukat:
            self.log(f"[i] Müvekkil tespiti için vekil adı: {avukat}")

        total_rows, done, n_ok, n_err = 0, 0, 0, 0
        pending = list(cases)
        relogins = 0
        while pending:
            if self.stop_flag.is_set():
                self.log("[!] Tarama durduruldu.")
                break
            chunk, pending = pending[:batch], pending[batch:]
            res = self._evraklar_batch([self.case_ids.get(self._case_dkey(c), "") for c in chunk])
            retry = []
            for c in chunk:
                dkey = self._case_dkey(c)
                cid = self.case_ids.get(dkey, "")
                self.kat.upsert_dosya(c, taraf_map.get(dkey), avukat,
                                      infer_yargi_kategorisi(str(c.get("birimAdi") or "")), self.oturum)
                pages = res.get(cid)
                if isinstance(pages, list):
                    total_rows += self.kat.ingest_pages(dkey, pages, self.oturum)
                    self.kat.set_tarama(dkey, "tamam")
                    n_ok += 1
                    done += 1
                elif isinstance(pages, dict) and pages.get("err") not in ("oturum", "ag", None):
                    # UYAP'ın kalıcı yanıtı (ör. vekil kaydı yok): tekrar denemeye gerek yok
                    self.kat.set_tarama(dkey, f"hata: {pages.get('err')} {pages.get('msg', '')[:120]}")
                    n_err += 1
                    done += 1
                else:
                    retry.append(c)
            if retry:
                if self._session_dead():
                    relogins += 1
                    if relogins > 5 or not self._relogin():
                        for c in retry + pending:
                            self.kat.set_tarama(self._case_dkey(c), "hata: oturum düştü")
                        self.log(f"[x] Oturum yenilenemedi. {len(retry) + len(pending)} dosya taranamadı; "
                                 "tekrar giriş yapıp 'Yalnızca eksikleri tara' ile devam edin.")
                        pending = []
                    else:
                        self._fetch_cases(include_closed)  # dosya kimlikleri oturuma özgü: tazele
                        pending = retry + pending
                else:
                    # Geçici ağ/sunucu hatası: bir kez daha, sonra hatalı say
                    for c in retry:
                        c["_deneme"] = c.get("_deneme", 0) + 1
                        if c["_deneme"] >= 3:
                            self.kat.set_tarama(self._case_dkey(c), "hata: yanıt alınamadı")
                            n_err += 1
                            done += 1
                        else:
                            pending.append(c)
                    self._sleep(3.0)
            self.kat.db.commit()
            self.progress(done, len(cases), f"Evrak listeleri ({total_rows} kayıt, {n_err} hatalı)")
            self._sleep(0.3)
        self.log(f"[i] Tarama: {n_ok} dosya tamam, {n_err} dosya UYAP'tan liste vermedi"
                 + (", kalanlar oturum nedeniyle yarım" if pending or self.stop_flag.is_set() else "") + ".")
        self.kat.finalize_scan()
        self.kat.match_existing_disk([self.download_dir / n for n in (
            "DAVA_EVRAKLARI", "DURUSMA_TUTANAKLARI", "GEREKCELI_KARARLAR", "TEBLIGATLAR",
            "BILIRKISI_RAPORLARI", "TENSIP_ZAPITLARI", "DILEKCELER_LAYIHALAR", "IDDIANAMELER",
            "MUZEKKERE_VE_KURUM_CEVAPLARI", "KESINLESME_SERHLERI")], log=self.log)
        st = self.kat.stats()
        self.log(f"[✓] Katalog güncel: {st['dosya']} dosya, {st['evrak']} evrak ({st['ek']} ek), "
                 f"{st['mukerrer']} mükerrer, {st['indirildi']} indirilmiş, {st['yeni']} indirilmemiş.")
        return total_rows

    # ------------------------------------------------------------------ indirme
    def _refresh_case(self, dkey: str) -> bool:
        """Bu oturum için dosyanın taze (şifreli) kimliklerini alır ve kataloğu günceller."""
        if not self.case_ids:
            cases = self.bridge.query_tum_dosyalar(include_closed=True, use_cache=False) or []
            for c in cases:
                self.case_ids[Katalog.dkey_of(c.get("birimAdi"), c.get("dosyaNo") or c.get("dosyaNO"))] = \
                    str(c.get("dosyaId") or c.get("dosyaID") or "")
        cid = self.case_ids.get(dkey)
        if not cid:
            return False
        res = self._evraklar_batch([cid])
        pages = res.get(cid)
        if not isinstance(pages, list):
            return False
        self.kat.ingest_pages(dkey, pages, self.oturum)
        self.kat.set_tarama(dkey, "tamam")
        with self.kat.db:
            self.kat.db.execute("UPDATE dosya SET dosya_id=?, oturum=? WHERE dkey=?", (cid, self.oturum, dkey))
        return True

    def _session_dead(self) -> bool:
        try:
            return not self.bridge.is_logged_in()
        except Exception:
            return True

    def _relogin(self) -> bool:
        self.log("[!] UYAP oturumu düştü, yeniden giriş deneniyor...")
        ok = bool(self.relogin and self.relogin())
        if ok:
            self._new_session()
            self.log("[✓] Oturum yenilendi.")
        else:
            self.log("[x] Oturum yenilenemedi; işlem durduruldu.")
        return ok

    def download(self, ids: list[int]) -> dict:
        from uyap_indirici import is_valid_document_bytes, guess_extension
        import base64
        stats = {"indirildi": 0, "mukerrer": 0, "yok": 0, "hata": 0, "atlandi": 0}
        if not ids:
            return stats
        if not self.oturum:
            self._new_session()
        total = len(ids)
        # dosya bazında grupla (sıralı geldiği varsayılır)
        groups: list[tuple[str, list[int]]] = []
        for eid in ids:
            r = self.kat.db.execute("SELECT dkey FROM evrak WHERE id=?", (eid,)).fetchone()
            if not r:
                continue
            if groups and groups[-1][0] == r[0]:
                groups[-1][1].append(eid)
            else:
                groups.append((r[0], [eid]))
        done = 0
        since_pause = 0
        self.log(f"[*] {total} evrak, {len(groups)} dosya için indirme başlıyor "
                 f"({self.min_d}-{self.max_d} sn bekleme, her {self.batch_n} dosyada {self.batch_pause:.0f} sn mola)")
        for dkey, eids in groups:
            if self.stop_flag.is_set():
                break
            folders = set()
            refreshed: set = set()
            for eid in eids:
                if self.stop_flag.is_set():
                    break
                done += 1
                row = self.kat.get(eid)
                if not row or row["durum"] in ("indirildi", "mukerrer"):
                    stats["atlandi"] += 1
                    continue
                if row["oturum"] != self.oturum and row["dkey"] not in refreshed:
                    refreshed.add(row["dkey"])
                    if not self._refresh_case(row["dkey"]):
                        if not (self._session_dead() and self._relogin() and self._refresh_case(row["dkey"])):
                            self.log(f"[!] {row['birim']} {row['dosya_no']}: dosya UYAP listesinde bulunamadı, atlandı.")
                            stats["hata"] += 1
                            continue
                    row = self.kat.get(eid)
                if row["oturum"] != self.oturum:
                    stats["yok"] += 1
                    self.kat.set_result(eid, hata="UYAP listesinde artık yok")
                    continue
                dkey = row["dkey"]
                cid = self.case_ids.get(dkey) or row["case_dosya_id"]
                resp, raw, ok = {}, b"", False
                for attempt in range(1, 6):
                    resp = self.bridge.download_document({"dosyaId": cid, "evrakId": row["evrak_id"]}) or {}
                    raw = base64.b64decode(resp.get("base64") or "") if resp.get("base64") else b""
                    ok, reason = is_valid_document_bytes(raw, resp.get("contentType") or "")
                    if ok:
                        break
                    if self._session_dead():
                        if not self._relogin():
                            self.stop_flag.set()
                            break
                        self._refresh_case(dkey)
                        row = self.kat.get(eid)
                        cid = self.case_ids.get(dkey) or cid
                        continue
                    if resp.get("ok") and "html" not in (resp.get("contentType") or "").lower():
                        break  # UYAP'ta fiziksel dosya yok
                    self._sleep(2.0 * attempt)
                if not ok:
                    if self.stop_flag.is_set():
                        break
                    st = "yok" if resp.get("ok") else "hata"
                    stats[st] += 1
                    self.kat.set_result(eid, durum=st, hata=str(resp.get("errorHeader") or resp.get("status") or "")[:200])
                    self.log(f"[-] {row['tur']} ({row['birim']} {row['dosya_no']}): dosya alınamadı ({st}).")
                    continue
                sha = content_fingerprint(raw)
                dup = self.kat.find_sha(sha)
                if dup and dup["path"] and os.path.exists(dup["path"]):
                    stats["mukerrer"] += 1
                    self.kat.set_result(eid, durum="mukerrer", dup_of=dup["skey"], sha256=sha, size=len(raw),
                                        path=dup["path"], indirildi=dt.datetime.now().isoformat(timespec="seconds"))
                    self.log(f"[=] Mükerrer (aynı içerik zaten var): {row['tur']} -> {Path(dup['path']).name}")
                else:
                    cd = resp.get("contentDisposition") or ""
                    m = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^;"\']+)', cd, re.I)
                    ext = guess_extension(m.group(1) if m else "", resp.get("contentType") or "", raw)
                    ext = ext if ext.startswith(".") else f".{ext}"
                    path = unique_path(target_path_for(row, self.base, ext))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_name(path.name + ".part")
                    tmp.write_bytes(raw)
                    tmp.replace(path)
                    folders.add(path.parent)
                    pdf = None
                    if ext.lower() in (".tif", ".tiff"):
                        pdf = tiff_to_pdf(path)
                    elif ext.lower() == ".pdf":
                        pdf = path
                    self.kat.set_result(eid, durum="indirildi", path=str(path), pdf_path=str(pdf) if pdf else None,
                                        sha256=sha, size=len(raw), uzanti=ext.lstrip(".").lower(), hata=None,
                                        indirildi=dt.datetime.now().isoformat(timespec="seconds"))
                    self.kat.set_text(eid, extract_text(path, raw))
                    stats["indirildi"] += 1
                    since_pause += 1
                    self.log(f"[{done}/{total}] {len(raw)//1024} KB  {row['birim']} ({row['dosya_no']})  {path.name}")
                self.progress(done, total, f"{stats['indirildi']} indirildi, {stats['mukerrer']} mükerrer")
                if since_pause and since_pause % self.batch_n == 0:
                    since_pause = 0
                    self._sleep(self.batch_pause)
                else:
                    self._sleep(random.uniform(self.min_d, self.max_d))
            # Dosya grubu bitti: UDF -> PDF (tek JVM)
            for fo in folders:
                udf_folder_to_pdf(fo)
                with self.kat.db:
                    for r in self.kat.db.execute(
                            "SELECT id, path FROM evrak WHERE pdf_path IS NULL AND path LIKE ? AND path LIKE '%.udf'",
                            (str(fo) + os.sep + "%",)).fetchall():
                        pdf = Path(r["path"]).with_suffix(".pdf")
                        if pdf.exists():
                            self.kat.db.execute("UPDATE evrak SET pdf_path=? WHERE id=?", (str(pdf), r["id"]))
        self.log(f"[✓] Bitti: {stats['indirildi']} indirildi, {stats['mukerrer']} mükerrer (kaydedilmedi), "
                 f"{stats['yok']} UYAP'ta dosyası yok, {stats['hata']} hata, {stats['atlandi']} zaten vardı.")
        return stats


def export_csv(katalog: Katalog, f: dict, path: Path) -> int:
    import csv
    rows = katalog.query(f, limit=10_000_000)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Tarih", "Birim", "Dosya No", "Dosya Durumu", "Asıl/Ek", "Evrak Türü", "Üst Evrak",
                    "Açıklama", "Kategori", "Müvekkil", "Durum", "Dosya Yolu", "PDF"])
        for r in rows:
            w.writerow([r["tarih"], r["birim"], r["dosya_no"], r["dosya_durum"], "Ek" if r["is_ek"] else "Asıl",
                        r["tur"], r["ust_tur"], r["aciklama"], KATEGORI_ADI.get(r["kategori"], r["kategori"]),
                        r["muvekkil"], DURUMLAR.get(r["durum"], r["durum"]), r["path"] or "", r["pdf_path"] or ""])
    return len(rows)
