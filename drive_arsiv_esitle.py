#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UYAP Google Drive Akıllı ve Aşamalı Eşitleyici (Drive Sync Engine)
==================================================================
Kullanıcı talimatına göre:
  - Hariç tutulacak dosyalar ("baydar", "casmoni", "emre oral", "turan solmaz" vb.)
  - Zamana göre aşamalı aktarım:
      1. Aşama: Son 1 gün
      2. Aşama: Son 7 gün (1 hafta)
      3. Aşama: Son 30 gün (1 ay)
      4. Aşama: Son 60 gün (2 ay)
      5. Aşama: Tüm kalanlar
  - Yerel fiziksel dosyaları (PDF/UDF) ve katalogdaki evrakları Google Drive Webhook üzerinden aktarır.
  - İndeksi (arsiv_indeksi.json) anlık günceller.
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
DB_PATH = BASE_DIR / "uyap_katalog.db"
LOCAL_ARCHIVE_DIR = BASE_DIR / r"G:\Drive'ım\UYAP_Arsiv"
SDCARD_ARCHIVE_DIR = Path("/sdcard/Download/UYAP_Indirilenler")
INDEX_JSON_PATH = SDCARD_ARCHIVE_DIR / "arsiv_indeksi.json"
if not SDCARD_ARCHIVE_DIR.exists():
    INDEX_JSON_PATH = BASE_DIR / "arsiv_indeksi.json"

DEFAULT_WEBHOOK = "https://script.google.com/macros/s/AKfycbz8r1AxGBR9QGCSulz5Hja35VkOWCe2VjfcDvHr6ArumkrFU4YJ2yd-uhccMJ6FIUKN/exec"

# Hariç tutulacak filtreler (küçük harf)
EXCLUDE_KEYWORDS = ["baydar", "casmoni", "emre oral", "turan solmaz"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "drive_esitle.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("drive_esitle")


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
    # 25 MB üzeri dosyaları tek seferde webhook ile atmak time-out yaratabilir
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

        resp = requests.post(webhook_url, json=payload, timeout=60)
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


def esitle(asama: str = "hepsi", limit: int = 50, dry_run: bool = False):
    logger.info("=" * 65)
    logger.info("  UYAP GOOGLE DRIVE AKILLI EŞİTLEME MOTORU")
    logger.info("=" * 65)

    index = load_drive_index()
    logger.info(f"[*] Mevcut Drive İndeksinde {len(index)} dosya kayıtlı.")

    if not DB_PATH.exists():
        logger.error(f"[!] Katalog veritabanı bulunamadı: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Dosya filtrelerini al (baydar, casmoni, emre oral, turan solmaz olan dkey'leri ele)
    dosya_rows = cur.execute("SELECT dkey, birim, dosya_no, taraflar, muvekkil FROM dosya").fetchall()
    excluded_dkeys = set()
    for d in dosya_rows:
        dkey = d[0]
        combined_text = f"{d[0]} {d[1]} {d[2]} {d[3] or ''} {d[4] or ''}"
        if is_excluded(combined_text):
            excluded_dkeys.add(dkey)

    logger.info(f"[*] Filtrelenen (Hariç Tutulan) Dosya Sayısı: {len(excluded_dkeys)} adet ({', '.join(EXCLUDE_KEYWORDS)}).")

    # Tarih aralıkları
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
    logger.info(f"[*] Aşama '{asama}': İncelenen evrak sayısı: {len(rows)}")

    # Yerel fiziksel dosyaların önbellek haritasını oluştur (/sdcard ve yerel kök)
    logger.info("[*] Yerel dosya önbelleği taranıyor...")
    file_cache = {}
    scan_roots = [SDCARD_ARCHIVE_DIR, BASE_DIR / "UYAP_Indirilenler", BASE_DIR / r"G:\Drive'ım\UYAP_Arsiv"]
    for sr in scan_roots:
        if sr.exists():
            for root, _, files in os.walk(sr):
                for fn in files:
                    fp = Path(root) / fn
                    if fp.stat().st_size > 0:
                        file_cache[fn] = fp
    logger.info(f"[*] Yerelde {len(file_cache):,} tekil dosya tespit edildi.")

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

    aday_evraklar = []
    atlan_filtre = 0
    for r in rows:
        eid, dkey, tur, aciklama, tarih, gercek_id, evrak_uuid, uzanti, path, pdf_path, ara = r
        if dkey in excluded_dkeys:
            atlan_filtre += 1
            continue
        
        # Filtre kelimeleri kontrolü (baydar, casmoni, emre oral, turan solmaz)
        combined_evrak_info = f"{dkey} {tur or ''} {aciklama or ''} {ara or ''}"
        if is_excluded(combined_evrak_info):
            atlan_filtre += 1
            continue

        # Zaten Drive'a yüklenmiş mi?
        evrak_key = str(evrak_uuid or gercek_id or eid)
        if evrak_key in index or str(gercek_id) in index or str(eid) in index:
            continue

        # Yerel dosya mevcut mu?
        local_target = resolve_local(path, pdf_path)

        if local_target and local_target.stat().st_size > 0:
            aday_evraklar.append({
                "id": eid,
                "dkey": dkey,
                "tur": tur,
                "aciklama": aciklama,
                "tarih": tarih,
                "key": evrak_key,
                "gercek_id": gercek_id,
                "local_file": local_target
            })

    logger.info(f"[+] Drive'a yüklenmeye hazır yerel evrak sayısı: {len(aday_evraklar)}")

    yuklenen_sayisi = 0
    for item in aday_evraklar[:limit]:
        fpath = item["local_file"]
        dkey = item["dkey"]
        
        # Klasör yapısı: UYAP_Arsiv / DAVA_EVRAKLARI / Mahkeme_DosyaNo
        folder_clean = re.sub(r'[/\\:*?"<>|]', '_', dkey.replace('|', '_')).strip()
        remote_folder = f"UYAP_Arsiv/DAVA_EVRAKLARI/{folder_clean}"

        logger.info(f"[*] Yükleniyor ({yuklenen_sayisi+1}/{min(len(aday_evraklar), limit)}): {fpath.name}")
        
        if dry_run:
            logger.info(f"    [DRY-RUN] Hedef: {remote_folder}/{fpath.name}")
            yuklenen_sayisi += 1
            continue

        res = upload_to_drive_webhook(fpath, remote_folder)
        if res.get("status") == "success":
            logger.info(f"    [✓] Drive'a Yüklendi! FileID: {res.get('file_id')}")
            # İndeksi güncelle
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
            yuklenen_sayisi += 1
            # Sunucuyu boğmamak için kısa bekleme
            time.sleep(1)
        else:
            logger.warning(f"    [!] Yükleme başarısız: {res.get('message')}")

    logger.info("=" * 65)
    logger.info(f"[✓] İşlem tamamlandı. Yüklenen: {yuklenen_sayisi} dosya.")
    logger.info("=" * 65)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UYAP Google Drive Eşitleyici")
    parser.add_argument("--asama", choices=["1gun", "1hafta", "1ay", "2ay", "hepsi"], default="1ay", help="Zaman aşaması")
    parser.add_argument("--limit", type=int, default=50, help="Bu turda yüklenecek maksimum dosya")
    parser.add_argument("--dry-run", action="store_true", help="Gerçek yükleme yapmadan listele")
    args = parser.parse_args()

    esitle(asama=args.asama, limit=args.limit, dry_run=args.dry_run)
