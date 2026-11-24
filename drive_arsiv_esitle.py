#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UYAP Google Drive Akıllı ve Tam Eşitleyici (Full Sync & Downloader Engine)
========================================================================
Copyright (c) 2026 Av. Can KIZILYAR. Tüm hakları saklıdır.

İşlem Akışı:
  1. ADIM (Yerel Eşitleme):
     Yerelde (/sdcard/Download/UYAP_Indirilenler) mevcut olup Google Drive'a
     henüz aktarılmamış tüm geçerli evrakları Drive'a yükler.

  2. ADIM (UYAP'tan Doğrudan İndirme ve Eşitleme):
     UYAP veritabanında (uyap_katalog.db) veya UYAP portalında kayıtlı olup
     ne telefonda ne de Google Drive'da BULUNMAYAN tüm eksik evrakları
     otomatik olarak UYAP Avukat Portalından indirir, PDF'e çevirir ve Drive'a yükler.

  3. FİLTRELER:
     Kullanıcı talimatı gereği "baydar", "casmoni", "emre oral", "turan solmaz"
     kelimelerini içeren dava ve evraklar otomatik olarak atlanır.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import requests

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "uyap_katalog.db"
LOCAL_ARCHIVE_DIR = BASE_DIR / r"G:\Drive'ım\UYAP_Arsiv"
SDCARD_ARCHIVE_DIR = Path("/sdcard/Download/UYAP_Indirilenler")
INDEX_JSON_PATH = SDCARD_ARCHIVE_DIR / "arsiv_indeksi.json"
if not SDCARD_ARCHIVE_DIR.exists():
    INDEX_JSON_PATH = BASE_DIR / "arsiv_indeksi.json"

DEFAULT_WEBHOOK = "https://script.google.com/macros/s/AKfycbz8r1AxGBR9QGCSulz5Hja35VkOWCe2VjfcDvHr6ArumkrFU4YJ2yd-uhccMJ6FIUKN/exec"

# Hariç tutulacak dosyalar (küçük harf)
EXCLUDE_KEYWORDS = ["baydar", "casmoni", "emre oral", "turan solmaz"]

# Kullanıcı Kimlik Bilgileri (Gerektiğinde Mobil İmza ile oturum açmak için)
USER_TC = "39412276734"
USER_GSM = "5525286225"
USER_OPERATOR = "turktelekom"
USER_PIN = "455625"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "drive_esitle.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("drive_esitle")

# Küresel HTTP Oturumu (Keep-Alive ve bağlantı havuzu)
_session = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=3)
_session.mount("https://", adapter)
_session.mount("http://", adapter)


def tr_lower(text: str) -> str:
    if not text:
        return ""
    trans = str.maketrans("İIĞÜŞÖÇ", "iığüşöç")
    return text.translate(trans).lower()


def is_excluded(text: str) -> bool:
    if not text:
        return False
    low = tr_lower(text)
    for kw in EXCLUDE_KEYWORDS:
        if kw in low:
            return True
    return False


def load_drive_index() -> dict:
    if INDEX_JSON_PATH.exists():
        try:
            return json.loads(INDEX_JSON_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_drive_index(index_data: dict):
    try:
        INDEX_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        INDEX_JSON_PATH.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"İndeks kaydedilemedi: {e}")


def upload_to_drive_webhook(file_path: Path, remote_folder: str, webhook_url: str = DEFAULT_WEBHOOK) -> dict:
    """Dosyayı doğrudan Apps Script Webhook üzerinden Google Drive'a yükler."""
    if not file_path.exists() or file_path.stat().st_size == 0:
        return {"status": "error", "message": "Dosya yok veya 0 bayt"}

    sz = file_path.stat().st_size
    if sz > 25 * 1024 * 1024:
        logger.warning(f"Dosya çok büyük ({round(sz/(1024*1024), 1)} MB), atlanıyor: {file_path.name}")
        return {"status": "error", "message": "Dosya 25MB üzerinde"}

    try:
        raw = file_path.read_bytes()
        b64_content = base64.b64encode(raw).decode("utf-8")
        payload = {
            "filename": file_path.name,
            "folder_path": remote_folder,
            "content": b64_content
        }

        resp = _session.post(webhook_url, json=payload, timeout=45)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success" or "fileId" in data:
                return {
                    "status": "success",
                    "file_id": data.get("fileId", ""),
                    "url": data.get("url", f"https://drive.google.com/file/d/{data.get('fileId')}/view"),
                    "filename": file_path.name,
                    "folder": remote_folder
                }
            return {"status": "error", "message": f"Webhook hatası: {resp.text[:200]}"}
        return {"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def build_local_file_cache() -> dict[str, Path]:
    """Yereldeki tüm dosyaları tek seferde haritalandırır."""
    cache = {}
    scan_roots = [SDCARD_ARCHIVE_DIR, BASE_DIR / "UYAP_Indirilenler", BASE_DIR / r"G:\Drive'ım\UYAP_Arsiv"]
    for sr in scan_roots:
        if sr.exists():
            for root, _, files in os.walk(sr):
                for fn in files:
                    fp = Path(root) / fn
                    if fp.stat().st_size > 0:
                        cache[fn] = fp
    return cache


def esitle_yerel(index: dict, limit: int = 100, asama: str = "hepsi", dry_run: bool = False) -> int:
    """1. Adım: Yerelde mevcut olup henüz Drive'a aktarılmamış dosyaları yükler."""
    logger.info("-----------------------------------------------------------------")
    logger.info("  AŞAMA 1: YEREL DOSYALARIN GOOGLE DRIVE EŞİTLEMESİ")
    logger.info("-----------------------------------------------------------------")

    if not DB_PATH.exists():
        logger.error(f"[!] Katalog veritabanı bulunamadı: {DB_PATH}")
        return 0

    file_cache = build_local_file_cache()
    logger.info(f"[*] Yerelde {len(file_cache):,} tekil dosya tespit edildi.")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Filtrelenen dosyaları tespit et
    dosya_rows = cur.execute("SELECT dkey, birim, dosya_no, taraflar, muvekkil FROM dosya").fetchall()
    excluded_dkeys = set()
    for d in dosya_rows:
        combined_text = f"{d[0]} {d[1]} {d[2]} {d[3] or ''} {d[4] or ''}"
        if is_excluded(combined_text):
            excluded_dkeys.add(d[0])

    bugun = datetime.now()
    tarih_filtre = ""
    if asama == "1gun":
        sinir = (bugun - timedelta(days=1)).strftime("%Y-%m-%d")
        tarih_filtre = f"AND tarih >= '{sinir}'"
    elif asama == "1hafta":
        sinir = (bugun - timedelta(days=7)).strftime("%Y-%m-%d")
        tarih_filtre = f"AND tarih >= '{sinir}'"
    elif asama == "1ay":
        sinir = (bugun - timedelta(days=30)).strftime("%Y-%m-%d")
        tarih_filtre = f"AND tarih >= '{sinir}'"
    elif asama == "2ay":
        sinir = (bugun - timedelta(days=60)).strftime("%Y-%m-%d")
        tarih_filtre = f"AND tarih >= '{sinir}'"

    query = f"""
        SELECT id, dkey, tur, aciklama, tarih, gercek_id, evrak_id, uzanti, path, pdf_path, ara
        FROM evrak
        WHERE 1=1 {tarih_filtre}
        ORDER BY tarih DESC
    """
    rows = cur.execute(query).fetchall()

    def resolve_local(p_str: str, pdf_str: str) -> Path | None:
        for raw in [pdf_str, p_str]:
            if not raw:
                continue
            p = Path(raw)
            if p.exists() and p.stat().st_size > 0:
                return p
            fname = re.split(r'[\\/]', raw)[-1]
            if fname and fname in file_cache:
                return file_cache[fname]
        return None

    adaylar = []
    for r in rows:
        eid, dkey, tur, aciklama, tarih, gercek_id, evrak_uuid, uzanti, path, pdf_path, ara = r
        if dkey in excluded_dkeys or is_excluded(f"{dkey} {tur or ''} {aciklama or ''} {ara or ''}"):
            continue

        evrak_key = str(evrak_uuid or gercek_id or eid)
        if evrak_key in index or str(gercek_id) in index or str(eid) in index:
            continue

        local_file = resolve_local(path, pdf_path)
        if local_file and local_file.stat().st_size > 0:
            adaylar.append({
                "id": eid,
                "dkey": dkey,
                "tur": tur,
                "aciklama": aciklama,
                "tarih": tarih,
                "key": evrak_key,
                "gercek_id": gercek_id,
                "local_file": local_file
            })

    conn.close()
    logger.info(f"[+] Drive'a yüklenmeye hazır yerel dosya sayısı: {len(adaylar)}")

    yuklenen = 0
    for item in adaylar[:limit]:
        fpath = item["local_file"]
        dkey = item["dkey"]
        folder_clean = re.sub(r'[/\\:*?"<>|]', '_', dkey.replace('|', '_')).strip()
        remote_folder = f"UYAP_Arsiv/DAVA_EVRAKLARI/{folder_clean}"

        logger.info(f"[*] Yükleniyor ({yuklenen+1}/{min(len(adaylar), limit)}): {fpath.name}")
        if dry_run:
            logger.info(f"    [DRY-RUN] Hedef: {remote_folder}/{fpath.name}")
            yuklenen += 1
            continue

        res = upload_to_drive_webhook(fpath, remote_folder)
        if res.get("status") == "success":
            logger.info(f"    [✓] Drive'a Yüklendi! FileID: {res.get('file_id')}")
            index[item["key"]] = {
                "drive_file_id": res.get("file_id"),
                "drive_url": res.get("url"),
                "evrak_id": item["key"],
                "gercek_id": item["gercek_id"],
                "dkey": dkey,
                "tur": item["tur"],
                "tarih": item["tarih"],
                "yukleme_zamani": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            save_drive_index(index)
            yuklenen += 1
            time.sleep(0.5)
        else:
            logger.warning(f"    [!] Yükleme başarısız: {res.get('message')}")

    logger.info(f"[✓] Aşama 1 Tamamlandı: {yuklenen} yerel evrak Google Drive'a aktarıldı.")
    return yuklenen


def ensure_uyap_session(bridge) -> bool:
    """UYAP oturumunu kontrol eder, yoksa e-imza veya Mobil İmza ile oturum açar."""
    ok, _ = bridge.ensure_browser(headless=True)
    if not ok:
        logger.error("[!] Tarayıcı başlatılamadı.")
        return False

    if bridge.is_logged_in():
        logger.info("[✓] Mevcut UYAP oturumu aktif.")
        return True

    # 1. Öncelik: e-İmza (ArkSigner USB kartı takılıysa)
    logger.info("[*] e-İmza donanımı kontrol ediliyor...")
    try:
        ok, msg = bridge.auto_relogin(pin=USER_PIN)
        if ok:
            logger.info(f"[✓] e-İmza ile oturum açıldı: {msg}")
            return True
    except Exception as e:
        logger.info(f"[*] e-İmza atlandı: {e}")

    # 2. Öncelik: Mobil İmza (SIM onaylı)
    logger.info(f"[*] Mobil İmza isteği gönderiliyor (GSM: {USER_GSM})...")
    try:
        ok, msg = bridge.login_with_mobil_imza(
            tc=USER_TC,
            gsm=USER_GSM,
            operator=USER_OPERATOR,
            timeout_s=180.0
        )
        if ok:
            logger.info(f"[✓] Mobil İmza ile oturum açıldı: {msg}")
            return True
        logger.warning(f"[!] Mobil İmza girişi başarısız: {msg}")
    except Exception as e:
        logger.warning(f"[!] Mobil İmza istisnası: {e}")

    return False


def esitle_uyap_portal(index: dict, limit: int = 50, dry_run: bool = False) -> int:
    """2. Adım: Yerelde olmayan tüm evrakları UYAP'tan indirir, PDF yapar ve Drive'a yükler."""
    logger.info("-----------------------------------------------------------------")
    logger.info("  AŞAMA 2: UYAP PORTALINDAN EKSİK EVRAKLARI İNDİRME VE EŞİTLEME")
    logger.info("-----------------------------------------------------------------")

    try:
        from uyap_indirici import UyapBrowserBridge, sanitize_path_part
        import donusturucu
    except ImportError as e:
        logger.error(f"[!] Modül import hatası: {e}")
        return 0

    if not DB_PATH.exists():
        logger.error(f"[!] Katalog DB yok: {DB_PATH}")
        return 0

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Driveda ve yerelde olmayan evrakları en yeni tarihten eskiye doğru çek
    query = """
        SELECT e.id, e.dkey, e.tur, e.aciklama, e.tarih, e.evrak_id, e.gercek_id, d.dosya_id, d.birim, d.dosya_no
        FROM evrak e
        JOIN dosya d ON e.dkey = d.dkey
        WHERE e.evrak_id IS NOT NULL AND e.evrak_id != ''
        ORDER BY e.tarih DESC
    """
    rows = cur.execute(query).fetchall()

    indirilecekler = []
    file_cache = build_local_file_cache()

    for r in rows:
        eid, dkey, tur, aciklama, tarih, evrak_uuid, gercek_id, dosya_id, birim, dosya_no = r
        if is_excluded(f"{dkey} {tur or ''} {aciklama or ''}"):
            continue

        evrak_key = str(evrak_uuid or gercek_id or eid)
        if evrak_key in index or str(gercek_id) in index or str(eid) in index:
            continue

        clean_dosya_id = str(dosya_id or "").strip().strip('"')
        if not clean_dosya_id:
            continue

        indirilecekler.append({
            "id": eid,
            "dkey": dkey,
            "tur": tur,
            "aciklama": aciklama,
            "tarih": tarih,
            "evrak_id": str(evrak_uuid).strip().strip('"'),
            "dosya_id": clean_dosya_id,
            "birim": birim,
            "dosya_no": dosya_no
        })

    conn.close()
    logger.info(f"[*] UYAP'tan indirilmesi gereken eksik evrak sayısı: {len(indirilecekler)}")

    if not indirilecekler:
        logger.info("[✓] UYAP'tan indirilecek eksik evrak bulunamadı, tüm arşiv eşit durumda!")
        return 0

    bridge = UyapBrowserBridge()
    if not dry_run:
        if not ensure_uyap_session(bridge):
            logger.error("[!] UYAP oturumu açılamadığı için indirme adımı ertelendi.")
            return 0

    indirilen_ve_yuklenen = 0
    save_root = SDCARD_ARCHIVE_DIR
    save_root.mkdir(parents=True, exist_ok=True)

    for item in indirilecekler[:limit]:
        dkey = item["dkey"]
        ev_id = item["evrak_id"]
        d_id = item["dosya_id"]
        tur = item["tur"] or "Evrak"
        tarih = item["tarih"] or "Tarihsiz"
        birim = item["birim"] or "Mahkeme"
        dosya_no = item["dosya_no"] or "DosyaNo"

        folder_clean = re.sub(r'[/\\:*?"<>|]', '_', dkey.replace('|', '_')).strip()
        case_dir = save_root / folder_clean
        case_dir.mkdir(parents=True, exist_ok=True)
        pdf_dir = case_dir / "PDFler"
        pdf_dir.mkdir(parents=True, exist_ok=True)

        san_tur = sanitize_path_part(tur).replace(" ", "_")
        san_ack = sanitize_path_part(item["aciklama"] or "").replace(" ", "_")[:25]
        fname_base = f"{tarih}_{san_tur}"
        if san_ack and san_ack.lower() not in san_tur.lower():
            fname_base += f"_{san_ack}"
        fname_base += f"_{ev_id[:8]}"

        logger.info(f"[*] UYAP'tan İndiriliyor ({indirilen_ve_yuklenen+1}/{min(len(indirilecekler), limit)}): {fname_base}")

        if dry_run:
            indirilen_ve_yuklenen += 1
            continue

        try:
            res = bridge.download_document({"dosyaId": d_id, "evrakId": ev_id})
            raw_b64 = res.get("base64")
            if not raw_b64:
                logger.warning(f"    [!] Belge indirilemedi: {res.get('error') or res.get('errorHeader')}")
                continue

            raw_bytes = base64.b64decode(raw_b64)
            is_pdf = raw_bytes.startswith(b"%PDF")
            is_udf = raw_bytes.startswith(b"PK")
            ext = ".pdf" if is_pdf else (".udf" if is_udf else ".bin")

            dest_raw = case_dir / f"{fname_base}{ext}"
            dest_raw.write_bytes(raw_bytes)

            dest_pdf = None
            if is_pdf:
                dest_pdf = dest_raw
            elif is_udf:
                try:
                    c_path = donusturucu.udf_to_pdf(dest_raw)
                    if c_path and Path(c_path).exists():
                        target_pdf = pdf_dir / f"{fname_base}.pdf"
                        import shutil
                        shutil.copy2(c_path, target_pdf)
                        dest_pdf = target_pdf
                except Exception as ex:
                    logger.warning(f"    [!] PDF dönüşüm hatası: {ex}")

            # Drive'a yükle
            file_to_drive = dest_pdf if (dest_pdf and dest_pdf.exists()) else dest_raw
            remote_folder = f"UYAP_Arsiv/DAVA_EVRAKLARI/{folder_clean}"
            up_res = upload_to_drive_webhook(file_to_drive, remote_folder)

            if up_res.get("status") == "success":
                logger.info(f"    [✓] Drive'a Aktarıldı! FileID: {up_res.get('file_id')}")
                index[ev_id] = {
                    "drive_file_id": up_res.get("file_id"),
                    "drive_url": up_res.get("url"),
                    "evrak_id": ev_id,
                    "dkey": dkey,
                    "tur": tur,
                    "tarih": tarih,
                    "local_raw": str(dest_raw),
                    "local_pdf": str(dest_pdf) if dest_pdf else "",
                    "yukleme_zamani": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                save_drive_index(index)
                indirilen_ve_yuklenen += 1
                time.sleep(1)
            else:
                logger.warning(f"    [!] Drive yükleme hatası: {up_res.get('message')}")

        except Exception as e:
            logger.error(f"    [!] Beklenmeyen hata ({ev_id}): {e}")

    logger.info(f"[✓] Aşama 2 Tamamlandı: {indirilen_ve_yuklenen} yeni evrak UYAP'tan indirilip Drive'a aktarıldı.")
    return indirilen_ve_yuklenen


def calistir_tam_esitleme(asama: str = "hepsi", limit: int = 100, dry_run: bool = False):
    logger.info("=" * 65)
    logger.info("  UYAP TAM OTONOM ARŞİV VE GOOGLE DRIVE EŞİTLEME MOTORU")
    logger.info("=" * 65)

    index = load_drive_index()
    logger.info(f"[*] Mevcut Drive İndeksinde {len(index)} evrak kayıtlı.")

    # 1. Aşama: Yerelde mevcut olup henüz Drive'da olmayanları eşitle
    yerel_yuklenen = esitle_yerel(index=index, limit=limit, asama=asama, dry_run=dry_run)

    # 2. Aşama: Ne yerelde ne Drive'da olan eksikleri UYAP'tan indir ve Drive'a eşitle
    uyap_yuklenen = esitle_uyap_portal(index=index, limit=limit, dry_run=dry_run)

    logger.info("=" * 65)
    logger.info("  TAM EŞİTLEME RAPORU:")
    logger.info(f"  • Yerelden Drive'a Aktarılan Evrak     : {yerel_yuklenen}")
    logger.info(f"  • UYAP'tan İndirilip Aktarılan Evrak   : {uyap_yuklenen}")
    logger.info(f"  • Drive'daki Güncel Toplam Evrak      : {len(index)}")
    logger.info("=" * 65)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UYAP Google Drive Akıllı ve Tam Eşitleyici")
    parser.add_argument("--asama", choices=["1gun", "1hafta", "1ay", "2ay", "hepsi"], default="hepsi", help="Zaman aşaması")
    parser.add_argument("--limit", type=int, default=100, help="Her turda işlenecek maksimum evrak sayısı")
    parser.add_argument("--dry-run", action="store_true", help="Gerçek indirme ve yükleme yapmadan simüle et")
    args = parser.parse_args()

    calistir_tam_esitleme(asama=args.asama, limit=args.limit, dry_run=args.dry_run)
