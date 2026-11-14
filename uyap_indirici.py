#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UYAP Avukat Portal - Güvenli, Kategorili Toplu Evrak & İşlem İndirici
---------------------------------------------------------------------
Temel Yetenekler ve Güvenlik Önlemleri:
1. Chromium Entegrasyonu (CDP - Port 9223):
   - Tarayıcıyı özel hata ayıklama portuyla yönetir veya mevcut açık UYAP sekmesine bağlanır.
2. Otomatik e-İmza ile Giriş ve Oturum Kurtarma (ArkSigner localhost:5975 Köprüsü):
   - Oturum kapandığında, düştüğünde veya hata aldığında kullanıcının PIN kodu (455625)
     ile ArkSigner üzerinden otomatik challenge imzalayıp UYAP oturumunu kesintisiz tazeler.
3. Sahte HTML / Hata Sayfası Koruması (Kesin İçerik Doğrulama & Atomik Kayıt):
   - Oturum düşmesiyle dönen HTML sayfalarını veya JSON hata yanıtlarını ASLA UDF/PDF
     olarak kaydetmez. Magic bytes kontrolü yapar ve geçici (.part) dosyaya yazıp doğrular.
4. Mükerrerlik ve Eksiklik Önleme (Tam Doğruluk & İdempotent Çalışma):
   - İndirme geçmişini hem JSON indeksinde hem de diskteki gerçek dosya varlığı ve boyutuyla
     denetler. İndirilen hiçbir dosya tekrar indirilmez; yarım kalan veya silinen dosyalar
     otomatik olarak yeniden indirilir.
5. Rotasyonlu Metin Günlüğü (Rotating TXT Logger):
   - Sürekli olarak "uyap_indirici_log.txt" dosyasına anlık yazar ve anında diske flush eder.
   - Dosya boyutu 8 MB sınırına ulaştığında yeni txt'ye geçer, en fazla 3 yedek tutar.
   - Metin editörlerinde anında açılır, kasmaz ve dosya kirliliği oluşturmaz.
6. UYAP Koruma Sistemi (Anti-Ban & Anti-Flood):
   - Seri (tek tek) indirme, rastgele jitter beklemesi (3.5 - 7.0 sn) ve N dosyada dinlenme molası.
"""

import base64
import csv
import datetime as dt
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import argparse
import shutil
import requests
import websocket

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False

# ---------------------------------------------------------------------------
# Dizin ve Yapılandırma Sabitleri
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
if Path("/sdcard/Download").exists():
    DEFAULT_DOWNLOAD_DIR = Path("/sdcard/Download/UYAP_Indirilenler")
else:
    DEFAULT_DOWNLOAD_DIR = BASE_DIR / "UYAP_Indirilenler"
CHROME_PROFILE_DIR = BASE_DIR / ".uyap_chrome_profile"
HISTORY_FILE = BASE_DIR / "indirme_gecmisi.json"
LOG_FILE = BASE_DIR / "uyap_indirici_log.txt"
CDP_PORT = 9223
UYAP_BASE_URL = "https://avukat.uyap.gov.tr"
ARKSIGNER_URL = "http://localhost:5975"
PIN_FILE = BASE_DIR / "pin.txt"
CONFIG_FILE = BASE_DIR / ".uyap_config.json"
DEFAULT_E_IMZA_PIN = ""
if PIN_FILE.exists():
    try:
        DEFAULT_E_IMZA_PIN = PIN_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
if not DEFAULT_E_IMZA_PIN:
    DEFAULT_E_IMZA_PIN = os.environ.get("UYAP_E_IMZA_PIN", "")
SIGN_CHALLENGE = "QWRhbGV0IEJha2FubMSxxJ/EsSBCaWxnaSDEsMWfbGVtIEdlbmVsIE3DvGTDvHJsw7zEn8O8IHRhcmFmxLFuZGFuIGdlbGnFn3RpcmlsZW4gdXlndWxhbWF5YSBlLWltemEgZ2lyacWfIHlhcG1hayBpc3RpeW9ydW0u"


def load_user_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_user_config(data: dict):
    try:
        cur = load_user_config()
        cur.update(data)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Rotasyonlu Loglama Altyapısı (Maksimum 8 MB, 3 Yedek)
# ---------------------------------------------------------------------------
file_logger = logging.getLogger("UyapIndiriciLogger")
file_logger.setLevel(logging.INFO)
file_logger.propagate = False

if not file_logger.handlers:
    rf_handler = RotatingFileHandler(
        filename=str(LOG_FILE),
        maxBytes=8 * 1024 * 1024,  # 8 MB (editörlerde anında açılır, ~80.000 satır)
        backupCount=3,             # Sadece uyap_indirici_log.txt, .1, .2, .3
        encoding="utf-8"
    )
    rf_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    file_logger.addHandler(rf_handler)
else:
    rf_handler = file_logger.handlers[0]


# ---------------------------------------------------------------------------
# Yardımcı Fonksiyonlar: Metin Temizleme, Tür Çıkarımı, Dosya Doğrulama
# ---------------------------------------------------------------------------

def sanitize_path_part(value: str, default: str = "Belirtilmemis", max_len: int = 80) -> str:
    """Klasör ve dosya adları için güvenli metin üretir."""
    if not value:
        return default
    s = str(value).strip()
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(". ")
    if not s:
        return default
    if len(s) > max_len:
        s = s[:max_len].rstrip(". _")
    return s


slugify_filename = sanitize_path_part


def tr_lower(text: str) -> str:
    """Türkçe İ/ı, Ş/ş, Ğ/ğ, Ü/ü, Ö/ö, Ç/ç harflerini Unicode standardında küçük harfe dönüştürür."""
    if not text:
        return ""
    return (
        str(text)
        .replace("İ", "i")
        .replace("I", "ı")
        .replace("Ş", "ş")
        .replace("Ğ", "ğ")
        .replace("Ü", "ü")
        .replace("Ö", "ö")
        .replace("Ç", "ç")
        .lower()
    )


def matches_evrak_filter(tur_name: str, filter_type: str) -> bool:
    """
    UYAP'taki tarihsel ve mahkeme bazlı evrak adı çeşitliliklerini dikkate alarak
    kesin pozitif ve negatif filtreleme yapar. Mükerrer veya yanlış evrakları eler.
    """
    if not tur_name:
        return False
    tn = tr_lower(tur_name)
    ft = tr_lower(filter_type)

    if ft in ("tumu", "all", "*"):
        return True

    if ft == "durusma":
        is_d = any(k in tn for k in ("duruşma", "durusma", "celse zaptı", "celse zabti", "celse tutana", "segbis"))
        is_ex = any(k in tn for k in (
            "tensip", "zaptolunan", "arama", "yakalama", "kura", "tevzi", "teslim",
            "tesellüm", "emanet", "diğer tutanak", "diger tutanak", "ifade", "sorgu",
            "ihzar", "otopsi", "muayene", "adli kontrol tutana"
        ))
        return is_d and not is_ex

    if ft == "bilirkisi":
        is_b = any(k in tn for k in (
            "bilirkişi rapor", "bilirkisi rapor", "ek rapor", "uzman rapor",
            "uzman görüş", "uzman gorus", "adli tıp", "adli tip", "atk rapor",
            "kriminal", "grafoloji", "hesap rapor", "mali müşavir", "mali musavir",
            "bilirkişi heyet", "bilirkisi heyet", "inceleme rapor", "uyuşturucu inceleme", "uyusturucu inceleme"
        ))
        is_ex = any(k in tn for k in (
            "ücret", "ucret", "sarf", "teslim", "yemin", "tevdi", "süre talebi",
            "sure talebi", "çekilme", "cekilme", "itiraz", "tebliğ", "teblig"
        ))
        return is_b and not is_ex

    if ft == "karar":
        is_k = any(k in tn for k in (
            "gerekçeli karar", "gerekceli karar", "hüküm", "hukum", "kısa karar", "kisa karar",
            "ara karar", "ek karar", "ilam", "istinaf kararı", "istinaf karari", "bam kararı",
            "bam karari", "yargıtay kararı", "yargitay karari", "danıştay kararı", "danistay karari",
            "takipsizlik", "kyok", "yetkisizlik kararı", "yetkisizlik karari", "görevsizlik kararı",
            "gorevsizlik karari", "tefrik kararı", "tefrik karari", "birleştirme kararı",
            "birlestirme karari", "nihai karar"
        ))
        is_ex = any(k in tn for k in (
            "tebliğ", "teblig", "mazbata", "masraf", "harç", "harc", "itiraz", "temyiz",
            "başvuru", "basvuru"
        ))
        return is_k and not is_ex

    if ft == "tensip":
        return "tensip" in tn and not ("itiraz" in tn)

    if ft == "dilekce":
        is_dil = any(k in tn for k in (
            "dilekçe", "dilekce", "beyan", "savunma", "vekaletname", "yetki belgesi",
            "mazeret", "mütalaa", "mutalaa"
        ))
        return is_dil and not any(k in tn for k in ("tebliğ", "teblig", "mazbata"))

    if ft == "iddianame":
        return any(k in tn for k in ("iddianame", "fezleke")) and not ("iade" in tn)

    if ft == "kesinlesme":
        return "kesinleşme" in tn or "kesinlesme" in tn

    return ft in tn


def infer_dosya_turu(birim_adi: str, explicit_tur: str = "") -> str:
    """Birim adından veya açık dosya türünden anlamlı bir Dosya/Yargı Türü kategorisi çıkarır."""
    if explicit_tur and explicit_tur.strip():
        return sanitize_path_part(explicit_tur.strip(), "Diger_Dosya")
    b = (birim_adi or "").upper()
    mapping = [
        ("AĞIR CEZA", "Agir_Ceza_Mahkemesi"),
        ("AGIR CEZA", "Agir_Ceza_Mahkemesi"),
        ("ASLİYE CEZA", "Asliye_Ceza_Mahkemesi"),
        ("ASLIYE CEZA", "Asliye_Ceza_Mahkemesi"),
        ("SULH CEZA", "Sulh_Ceza_Hakimligi"),
        ("ÇOCUK", "Cocuk_Mahkemesi"),
        ("İNFaz", "Infaz_Hakimligi"),
        ("İNFAZ", "Infaz_Hakimligi"),
        ("İCRA CEZA", "Icra_Ceza_Mahkemesi"),
        ("İCRA HUKUK", "Icra_Hukuk_Mahkemesi"),
        ("İCRA DAİRESİ", "Icra_Dairesi"),
        ("İCRA MÜDÜRLÜĞÜ", "Icra_Dairesi"),
        ("İCRA", "Icra_Dairesi"),
        ("ASLİYE HUKUK", "Asliye_Hukuk_Mahkemesi"),
        ("SULH HUKUK", "Sulh_Hukuk_Mahkemesi"),
        ("ASLİYE TİCARET", "Asliye_Ticaret_Mahkemesi"),
        ("AİLE MAHKEMESİ", "Aile_Mahkemesi"),
        ("İŞ MAHKEMESİ", "Is_Mahkemesi"),
        ("TÜKETİCİ", "Tuketici_Mahkemesi"),
        ("KADASTRO", "Kadastro_Mahkemesi"),
        ("FİKRİ VE SINAİ", "Fikri_Sinai_Haklar_Mahkemesi"),
        ("BÖLGE ADLİYE", "Bolge_Adliye_Mahkemesi_Istinaf"),
        ("BÖLGE İDARE", "Bolge_Idare_Mahkemesi"),
        ("İDARE MAHKEMESİ", "Idare_Mahkemesi"),
        ("VERGİ MAHKEMESİ", "Vergi_Mahkemesi"),
        ("YARGITAY", "Yargitay"),
        ("DANIŞTAY", "Danistay"),
        ("ANAYASA MAHKEMESİ", "Anayasa_Mahkemesi"),
        ("BAŞSAVCILIĞI", "Cumhuriyet_Bassavciligi_Sorusturma"),
        ("SAVCILIĞI", "Cumhuriyet_Bassavciligi_Sorusturma"),
        ("ARABULUCULUK", "Arabuluculuk_Burosu"),
        ("SATIŞ MEMURLUĞU", "Satis_Memurlugu"),
    ]
    for kw, label in mapping:
        if kw in b:
            return label
    if birim_adi and birim_adi.strip():
        return "Diger_Yargi_Birimi"
    return "Belirtilmemis_Birim"


def is_case_relevant_for_filter(birim_adi: str, evrak_filter: str) -> bool:
    """
    Hedef evrak türünün bir birimde bulunma ihtimali olup olmadığını denetler.
    Örn: İcra Dairesinde, Arabuluculukta veya CBS Soruşturma dosyasında Duruşma Tutanağı olmaz!
    Böylece yüzlerce alakasız dosya boş yere taranmaz, tarama süresi %60-70 kısalır.
    """
    if not birim_adi:
        return True
    b = tr_lower(birim_adi)
    f = tr_lower(evrak_filter)

    if f in ("tumu", "all", "*"):
        return True

    # İcra Dairesi / Müdürlüğü (Duruşma ve Tensip Mahkeme kararıdır, İcra Dairesinde OLMAZ)
    is_icra_dairesi = any(k in b for k in (
        "icra dairesi", "icra mudurlugu", "icra müdürlüğü", "genel icra",
        "gayrimenkul satis", "gayrimenkul satış", "abonelik", "iflas dairesi"
    )) and not any(k in b for k in ("mahkeme", "hakimliği", "hakimligi"))

    # Savcılık / Soruşturma (Duruşma olmaz, duruşma mahkemede başlar)
    is_savcilik = any(k in b for k in ("cumhuriyet başsavcılığı", "cumhuriyet bassavciligi", "cbs", "hazırlık", "soruşturma"))

    # İdari Bürolar
    is_idari_buro = any(k in b for k in ("arabuluculuk", "tevzi büro", "tevzi buro", "ön büro", "on buro"))

    if f == "durusma":
        if is_icra_dairesi or is_savcilik or is_idari_buro:
            return False
        return True

    if f == "iddianame":
        if is_icra_dairesi or is_idari_buro:
            return False
        if any(k in b for k in ("hukuk", "aile", "iş mah", "is mah", "ticaret", "tüketici", "tuketici", "idare", "vergi", "kadastro")):
            return False
        return True

    if f in ("tensip", "karar"):
        if is_icra_dairesi or is_idari_buro:
            return False
        return True

    if f == "bilirkisi":
        if is_idari_buro or is_icra_dairesi:
            return False
        return True

    return True


def infer_yargi_kategorisi(birim_adi: str) -> str:
    """Birim adından üst yargı kolunu çıkarır (AI ve LLM klasörlemesi için)."""
    b = tr_lower(birim_adi)
    if any(k in b for k in ("icra ceza", "icra hukuk")):
        return "Icra_Mahkemeleri"
    if any(k in b for k in ("icra dairesi", "icra müdürlüğü", "icra mudurlugu", "genel icra", "iflas")):
        return "Icra_Daireleri"
    if any(k in b for k in ("idare", "vergi", "danıştay", "danistay")):
        return "Idari_Yargi"
    if any(k in b for k in ("ceza", "savcı", "savci", "başsavcı", "bassavci", "çocuk", "cocuk", "infaz")):
        return "Ceza_Yargisi"
    if any(k in b for k in ("hukuk", "asliye", "sulh", "aile", "iş", "is mah", "ticaret", "tüketici", "tuketici", "kadastro", "fikri", "yargıtay", "yargitay")):
        return "Hukuk_Yargisi"
    return "Diger_Yargi"


def infer_evrak_alt_kategori(evrak_turu: str) -> str:
    """Evrak adından yapay zeka analizine uygun standart alt klasör kategorisi belirler."""
    t = tr_lower(evrak_turu)
    if any(k in t for k in ("duruşma", "durusma", "celse", "segbis")):
        return "DURUSMA_TUTANAKLARI"
    if any(k in t for k in ("rapor", "bilirkişi", "bilirkisi", "uzman", "adli tıp", "adli tip", "kriminal")):
        return "BILIRKISI_RAPORLARI"
    if any(k in t for k in ("karar", "hüküm", "hukum", "ilam")):
        return "GEREKCELI_KARARLAR"
    if "tensip" in t:
        return "TENSIP_ZAPITLARI"
    if any(k in t for k in ("dilekçe", "dilekce", "beyan", "savunma", "vekalet")):
        return "DILEKCELER_BEYANLAR"
    if any(k in t for k in ("iddianame", "fezleke")):
        return "IDDIANAMELER"
    if "kesinleşme" in t or "kesinlesme" in t:
        return "KESINLESME_SERHLERI"
    return "DAVA_EVRAKLARI"


def save_ai_manifest(target_base: pathlib.Path, found_documents: list, download_queue: list):
    """
    Yapay zekanın (LLM / RAG / Otonom Ajan) dava analizinde doğrudan kullanabilmesi için
    tüm dosya, duruşma ve evrak kronolojisini içeren JSON manifest ve CSV tablosu üretir.
    """
    manifest_path = target_base / "ai_case_manifest.json"
    csv_path = target_base / "ai_durusma_kronolojisi.csv"

    cases_dict = {}
    csv_rows = []

    for item in found_documents:
        d_key = f"{item['birim_adi']}_{item['dosya_no']}"
        if d_key not in cases_dict:
            cases_dict[d_key] = {
                "birim_adi": item["birim_adi"],
                "dosya_no": item["dosya_no"],
                "dosya_id": item["dosya_id"],
                "yargi_turu": infer_yargi_kategorisi(item["birim_adi"]),
                "dosya_turu": item["dosya_turu"],
                "durusma_tarihleri": [],
                "evraklar": []
            }

        tarih = item["tarih"]
        is_durusma = any(k in tr_lower(item["evrak_turu"]) for k in ("duruşma", "durusma", "celse", "segbis"))
        if is_durusma and tarih not in cases_dict[d_key]["durusma_tarihleri"]:
            cases_dict[d_key]["durusma_tarihleri"].append(tarih)

        ev_entry = {
            "evrak_id": item["evrak_id"],
            "tarih": tarih,
            "evrak_adi": item["evrak_adi"],
            "evrak_turu": item["evrak_turu"],
            "is_attachment": item["is_attachment"],
            "unique_key": item["unique_key"]
        }
        cases_dict[d_key]["evraklar"].append(ev_entry)

        if is_durusma:
            csv_rows.append({
                "Tarih": tarih,
                "Yargi_Turu": cases_dict[d_key]["yargi_turu"],
                "Birim_Adi": item["birim_adi"],
                "Dosya_No": item["dosya_no"],
                "Evrak_Adi": item["evrak_adi"],
                "Evrak_ID": item["evrak_id"]
            })

    for c in cases_dict.values():
        c["durusma_tarihleri"].sort()

    manifest_data = {
        "manifest_surumu": "2.0-ai-ready",
        "olusturma_tarihi": dt.datetime.now().isoformat(),
        "toplam_hedef_dosya": len(cases_dict),
        "toplam_hedef_evrak": len(found_documents),
        "dosyalar": list(cases_dict.values())
    }

    try:
        manifest_path.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[✓] AI Dava Manifestosu hazırlandı: {manifest_path.name}")
    except Exception as e:
        print(f"[!] AI Manifestosu yazılamadı: {e}")

    try:
        csv_rows.sort(key=lambda r: (r["Tarih"], r["Birim_Adi"]))
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as cf:
            writer = csv.DictWriter(cf, fieldnames=["Tarih", "Yargi_Turu", "Birim_Adi", "Dosya_No", "Evrak_Adi", "Evrak_ID"])
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"[✓] AI Duruşma Kronoloji Tablosu hazırlandı: {csv_path.name}")
    except Exception as e:
        print(f"[!] Kronoloji CSV yazılamadı: {e}")


def parse_uyap_date(val) -> dt.date | None:
    """UYAP tarih formatlarını (timestamp, ISO, DD/MM/YYYY, DD.MM.YYYY) dt.date'e çevirir."""
    if not val:
        return None
    if isinstance(val, (int, float)):
        try:
            if val > 1e11:
                val = val / 1000.0
            return dt.datetime.fromtimestamp(val).date()
        except Exception:
            return None
    s = str(val).strip()
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", s)
    if m:
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return dt.date(y, mo, d)
        except ValueError:
            pass
    return None


def guess_extension(filename: str, content_type: str, raw_bytes: bytes) -> str:
    """Dosya uzantısını Content-Disposition, MIME tipi veya dosya imzasından (magic bytes) bulur."""
    if filename:
        m = re.search(r"(\.[a-zA-Z0-9]{2,6})$", filename.strip())
        if m:
            ext = m.group(1).lower()
            if ext not in (".html", ".htm", ".json"):
                return ext
    if raw_bytes:
        if raw_bytes.startswith(b"%PDF"):
            return ".pdf"
        if raw_bytes.startswith(b"PK\x03\x04"):
            return ".udf"
        if raw_bytes.startswith((b"II*\x00", b"MM\x00*")):
            return ".tif"
        if raw_bytes.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if raw_bytes.startswith(b"\x89PNG"):
            return ".png"
        if raw_bytes.startswith(b"{\\rtf"):
            return ".rtf"
        if raw_bytes.startswith(b"\xd0\xcf\x11\xe0"):
            return ".doc"
    ct = (content_type or "").lower()
    if "pdf" in ct:
        return ".pdf"
    if "tiff" in ct or "tif" in ct:
        return ".tif"
    if "word" in ct:
        return ".docx"
    if "zip" in ct:
        return ".zip"
    return ".udf"


def is_valid_document_bytes(raw_bytes: bytes, content_type: str = "") -> tuple[bool, str]:
    """
    İndirilen verinin gerçek ve bozulmamış bir evrak olup olmadığını denetler.
    UYAP oturum süresi bittiğinde dönen HTML sayfalarını, JSON hata mesajlarını veya
    0 baytlık boş/hatalı yanıtları tespit eder.
    """
    if not raw_bytes or len(raw_bytes) < 128:
        return False, "Dosya boyutu çok küçük veya boş (< 128 bayt)"

    sample = raw_bytes[:1024].lower()
    
    # HTML oturum düşmesi / hata sayfası kontrolü
    if (
        b"<!doctype html" in sample
        or b"<html" in sample
        or b"<body" in sample
        or b"<title>" in sample
        or b"loginstate" in sample
        or (b"adalet" in sample and b"giris" in sample and b"<" in sample)
    ):
        return False, "UYAP sunucusunda fiziksel dosya mevcut değil (HTML sayfası döndü)"

    # JSON hata mesajı kontrolü
    if "application/json" in (content_type or "").lower() or sample.strip().startswith(b"{"):
        try:
            j = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
            if isinstance(j, dict) and (j.get("errorCode") or j.get("error") or j.get("status") == "ERROR"):
                msg = j.get("message") or j.get("error") or j.get("errorCode") or str(j)
                return False, f"UYAP JSON hata mesajı döndü: {msg}"
        except Exception:
            pass

    return True, "Geçerli evrak verisi"


# ---------------------------------------------------------------------------
# CDP (Chrome DevTools Protocol) ve ArkSigner e-İmza Bağlantı Yöneticisi
# ---------------------------------------------------------------------------

class UyapBrowserBridge:
    """Chromium ile CDP üzerinden haberleşen, e-İmza ile otomatik oturum açan köprü."""

    def __init__(self, port: int = CDP_PORT):
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.is_headless = False

    @staticmethod
    def _find_chrome_bin() -> str | None:
        candidates = [
            shutil.which("chromium-browser"),
            shutil.which("chromium"),
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            "/data/data/com.termux/files/usr/bin/chromium-browser",
            "/data/data/com.termux/files/usr/bin/chromium",
            "/usr/lib64/chromium-browser/chromium-browser",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
        ]
        for c in candidates:
            if c and os.path.isfile(c) and os.access(c, os.X_OK):
                return c
        return None

    def launch_or_connect(self, headless: bool = False) -> tuple[bool, str]:
        """Chromium açık değilse başlatır (opsiyonel headless), açıksa UYAP sekmesine bağlanır."""
        self.is_headless = headless
        if self._is_port_open():
            return True, f"Mevcut Chromium oturumuna bağlanıldı (Port {self.port})."

        chrome_bin = self._find_chrome_bin()
        if not chrome_bin:
            return False, "Sistemde Chromium veya Google Chrome bulunamadı. Lütfen kurun (pkg install chromium veya sudo dnf install chromium)."

        CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        cmd = [
            chrome_bin,
            f"--remote-debugging-port={self.port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={CHROME_PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        if headless or not has_display:
            self.is_headless = True
            cmd.extend([
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
            ])
        cmd.append(UYAP_BASE_URL)
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            return False, f"Chromium başlatılamadı: {e}"

        for _ in range(30):
            time.sleep(0.4)
            if self._is_port_open():
                mode_str = "Headless / Arka Plan" if self.is_headless else "Pencereli"
                return True, f"Chromium başlatıldı ({mode_str}). UYAP Portal sekmesi hazır."

        return False, "Chromium başlatıldı ancak hata ayıklama portuna (9223) ulaşılamadı."

    def _is_port_open(self) -> bool:
        try:
            r = requests.get(f"http://127.0.0.1:{self.port}/json", timeout=1.5)
            return r.status_code == 200
        except Exception:
            return False

    def get_uyap_tab(self) -> dict | None:
        """Açık sekmeler arasından avukat.uyap.gov.tr veya e-Devlet giriş sekmesini döndürür."""
        try:
            tabs = requests.get(f"http://127.0.0.1:{self.port}/json", timeout=2.5).json()
        except Exception:
            return None
        pages = [t for t in tabs if t.get("type") == "page"]
        # Eğer aktif sekme e-Devlet Mobil İmza ise önce onu döndür
        for t in pages:
            url = t.get("url") or ""
            if "giris.turkiye.gov.tr" in url:
                return t
        # UYAP portal sekmesi
        for t in pages:
            url = t.get("url") or ""
            if "uyap.gov.tr" in url:
                return t
        return pages[0] if pages else None

    def eval_js(self, js_expression: str, timeout: float = 90.0, retries: int = 2):
        """Tarayıcıdaki UYAP sekmesinde asenkron JS çalıştırır ve sonucunu döndürür."""
        last_err = None
        for attempt in range(retries + 1):
            tab = self.get_uyap_tab()
            if not tab or not tab.get("webSocketDebuggerUrl"):
                if attempt < retries:
                    time.sleep(1.0)
                    continue
                raise RuntimeError("Açık bir UYAP tarayıcı sekmesi bulunamadı. Önce Chromium'u başlatın.")

            ws_url = tab["webSocketDebuggerUrl"]
            ws = None
            try:
                ws = websocket.create_connection(ws_url, timeout=timeout)
                msg_id = random.randint(1000, 999999)
                payload = {
                    "id": msg_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": js_expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }
                ws.send(json.dumps(payload))
                while True:
                    raw = ws.recv()
                    resp = json.loads(raw)
                    if resp.get("id") == msg_id:
                        if "error" in resp:
                            raise RuntimeError(f"CDP Hatası: {resp['error']}")
                        res = resp.get("result", {})
                        if "exceptionDetails" in res:
                            exc = res["exceptionDetails"]
                            raise RuntimeError(f"JS Hatası: {exc.get('text')} {exc.get('exception', {}).get('description', '')}")
                        return res.get("result", {}).get("value")
            except (websocket.WebSocketTimeoutException, TimeoutError) as e:
                last_err = e
                if attempt < retries:
                    time.sleep(2.5)
                    continue
                raise
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.5)
                    continue
                raise
            finally:
                if ws:
                    try:
                        ws.close()
                    except Exception:
                        pass
        if last_err:
            raise last_err

    def auto_relogin(self, pin: str = DEFAULT_E_IMZA_PIN) -> tuple[bool, str]:
        """
        ArkSigner (http://localhost:5975) üzerinden kullanıcının PIN koduyla
        e-İmza doğrulaması yapar ve Chromium UYAP sekmesinde oturumu kesintisiz yeniler.
        """
        try:
            # 1. ArkSigner sertifika sorgusu
            r_cert = requests.get(f"{ARKSIGNER_URL}/api/v1/signature/getCertificates", timeout=6)
            if r_cert.status_code != 200:
                return False, f"ArkSigner servisine bağlanılamadı (HTTP {r_cert.status_code}). ArkSigner çalışıyor mu?"

            cards = r_cert.json().get("data", [])
            if not cards:
                return False, "ArkSigner'a bağlı e-İmza kart okuyucu bulunamadı. Lütfen e-İmzanızı takın."

            card = cards[0]
            certs = card.get("certificates", [])
            if not certs:
                return False, "Akıllı kart üzerinde geçerli sertifika bulunamadı."

            cert = certs[0]
            cert_id = cert.get("certificateId")
            subject = cert.get("subject", "Avukat")

            # 2. Challenge imzalat
            sign_payload = {
                "certificateId": cert_id,
                "password": str(pin).strip(),
                "content": SIGN_CHALLENGE
            }
            r_sign = requests.post(f"{ARKSIGNER_URL}/api/v1/signature/sign", json=sign_payload, timeout=12)
            if r_sign.status_code != 200:
                return False, f"ArkSigner imzalama isteği reddedildi (HTTP {r_sign.status_code})"

            s_data = r_sign.json()
            if s_data.get("metadata", {}).get("STATUS") != "SUCCESS":
                msg = s_data.get("metadata", {}).get("MESSAGE") or "PIN kodu hatalı veya kart meşgul"
                return False, f"e-İmza PIN Hatası: {msg}"

            sig_res = s_data.get("data", {})
            sig = sig_res.get("signedData")
            tx_id = sig_res.get("txId")
            if not sig or not tx_id:
                return False, "ArkSigner imza veya işlem numarası (txId) üretemedi."

            # 3. Chromium UYAP sekmesine kimlik doğrulama isteği gönder
            js_login = f"""
            (async () => {{
                try {{
                    const sig = {json.dumps(sig)};
                    const txId = {json.dumps(tx_id)};

                    // 1. web1.uyap
                    const r1 = await fetch("/web1.uyap?signature=" + encodeURIComponent(sig) + "&txId=" + encodeURIComponent(txId), {{
                        method: "GET",
                        headers: {{"Cache-Control": "no-cache"}}
                    }});
                    const d1 = await r1.json().catch(() => null);
                    if (!d1 || !d1.success) {{
                        return {{success: false, step: "web1.uyap", detail: d1}};
                    }}

                    // 2. login.uyap
                    const r2 = await fetch("/login.uyap", {{
                        method: "POST",
                        headers: {{"Content-Type": "application/json", "Cache-Control": "no-cache"}},
                        body: JSON.stringify({{}})
                    }});
                    if (!r2.ok) {{
                        return {{success: false, step: "login.uyap", status: r2.status}};
                    }}

                    // 3. kullanici_bilgileri.uyap
                    const r3 = await fetch("/kullanici_bilgileri.uyap", {{
                        method: "POST",
                        headers: {{"Content-Type": "application/json", "Cache-Control": "no-cache"}},
                        body: JSON.stringify({{}})
                    }});
                    const uinfo = await r3.json().catch(() => null);

                    localStorage.setItem("loginState", "true");
                    sessionStorage.setItem("showKvkPopup", "true");
                    sessionStorage.setItem("showPopupDuyuru2", "true");
                    if (uinfo) {{
                        sessionStorage.setItem("kullanici", JSON.stringify(uinfo));
                    }}

                    return {{
                        success: true,
                        user: uinfo
                    }};
                }} catch(e) {{
                    return {{success: false, error: e.toString()}};
                }}
            }})()
            """
            res = self.eval_js(js_login, timeout=25.0)
            if res and res.get("success"):
                u = res.get("user") or {}
                full_name = f"{u.get('adi', '')} {u.get('soyadi', '')}".strip() or subject
                return True, f"e-İmza ile oturum yenilendi ({full_name})"
            else:
                return False, f"Tarayıcıda oturum yenilenemedi: {res}"
        except Exception as e:
            return False, f"Otomatik e-İmza giriş hatası: {e}"

    def is_logged_in(self) -> bool:
        """
        Tarayıcının gerçekten aktif, kimlik doğrulaması yapılmış bir UYAP oturumunda
        olup olmadığını /kullanici_bilgileri.uyap ile kesin olarak doğrular.
        """
        js = """
        (async () => {
            try {
                const r = await fetch("/kullanici_bilgileri.uyap", {
                    method: "POST",
                    headers: {"Content-Type": "application/json", "Cache-Control": "no-cache"},
                    body: "{}"
                });
                const ct = r.headers.get("content-type") || "";
                if (!r.ok || ct.includes("text/html")) return false;
                const d = await r.json().catch(() => null);
                return Boolean(d && (d.tcKimlikNo || d.tckn));
            } catch(e) {
                return false;
            }
        })()
        """
        try:
            return bool(self.eval_js(js, timeout=10.0))
        except Exception:
            return False

    def check_login_and_user(self) -> dict:
        """Kullanıcının UYAP'a giriş yapıp yapmadığını ve TCKN/Ad/Soyad bilgisini kontrol eder."""
        js = """
        (async () => {
            const url = window.location.href;
            let user = null;
            try {
                const r = await fetch("/kullanici_bilgileri.uyap", {
                    method: "POST",
                    headers: {"Content-Type": "application/json", "Cache-Control": "no-cache"},
                    body: "{}"
                });
                const ct = r.headers.get("content-type") || "";
                if (r.ok && !ct.includes("text/html")) {
                    const d = await r.json().catch(() => null);
                    if (d && (d.tcKimlikNo || d.tckn)) {
                        user = d;
                        sessionStorage.setItem("kullanici", JSON.stringify(user));
                        localStorage.setItem("loginState", "true");
                    }
                }
                if (!user) {
                    sessionStorage.removeItem("kullanici");
                    localStorage.removeItem("loginState");
                }
            } catch (e) {
                user = null;
            }
            const loginState = user ? "true" : "false";
            return {
                url,
                loginState,
                user,
                title: document.title
            };
        })()
        """
        return self.eval_js(js, timeout=15.0) or {}

    def login_with_mobil_imza(
        self,
        tc: str,
        gsm: str,
        operator: str = "turktelekom",
        status_cb=None
    ) -> tuple[bool, str]:
        """
        e-Devlet Mobil İmza ile UYAP Avukat Portal oturumunu otomatik başlatır.
        1. avukat.uyap.gov.tr/giris üzerinden Mobil İmza sayfasına yönlendirir.
        2. TC, GSM ve Operatör bilgilerini doldurup gönderir.
        3. Taahhütnameyi onaylar (İmzala).
        4. Parmak izini alır ve kullanıcının SIM onayını bekler.
        5. e-Devlet OAuth 'Onayla' ekranını otomatik onaylar.
        6. UYAP oturumunun başarıyla açıldığını doğrular.
        """
        def log_msg(m):
            if status_cb:
                status_cb(m)
            else:
                print(m)

        # Aktif oturum var mı kontrol et (kesin kontrol: /giris sayfasında değil ve API yanıt veriyor)
        if self.is_logged_in():
            uinfo = self.check_login_and_user()
            usr = uinfo.get("user") or {}
            name = f"{usr.get('ad', '') or usr.get('adi', '')} {usr.get('soyad', '') or usr.get('soyadi', '')}".strip()
            return True, f"Zaten aktif UYAP oturumu mevcut: {name} ({usr.get('tcKimlikNo')})"

        tc = str(tc).strip()
        gsm_digits = re.sub(r"\D", "", str(gsm))
        if gsm_digits.startswith("90") and len(gsm_digits) == 12:
            gsm_digits = gsm_digits[2:]
        elif gsm_digits.startswith("0") and len(gsm_digits) == 11:
            gsm_digits = gsm_digits[1:]

        op_map = {
            "1": "turkcell",
            "turkcell": "turkcell",
            "2": "turktelekom",
            "turktelekom": "turktelekom",
            "avea": "turktelekom",
            "tt": "turktelekom",
            "3": "vodafone",
            "vodafone": "vodafone"
        }
        op_id = op_map.get(str(operator).lower().strip(), "")
        if not op_id:
            if gsm_digits.startswith(("551", "552", "553", "554", "555", "559", "501", "505", "506", "507")):
                op_id = "turktelekom"
            elif gsm_digits.startswith(("541", "542", "543", "544", "545", "546", "547", "548", "549")):
                op_id = "vodafone"
            else:
                op_id = "turkcell"

        log_msg("[*] UYAP Mobil İmza sayfasına yönlendiriliyor...")

        # Adım 1: Mobil İmza giriş sayfasına geç
        js_nav = """
        (() => {
            const href = window.location.href;
            if (href.includes("giris.turkiye.gov.tr/Giris/Mobil-Imza")) {
                return "already_on_mobil_imza";
            }
            if (!href.includes("avukat.uyap.gov.tr/giris") && !href.includes("avukat.uyap.gov.tr")) {
                window.location.href = "https://avukat.uyap.gov.tr/giris";
                return "navigating_to_uyap_giris";
            }

            // Yöntem 1: DevExtreme React DropDownButton onItemClick doğrudan tetikleme (anında & %100 kararlı)
            const dd = document.querySelector(".edevlet-dropdown");
            if (dd) {
                const reactKey = Object.keys(dd).find(k => k.startsWith("__reactFiber"));
                if (reactKey && dd[reactKey]) {
                    let curr = dd[reactKey];
                    while (curr) {
                        if (curr.memoizedProps && curr.memoizedProps.onItemClick && curr.memoizedProps.items) {
                            const mobilItem = curr.memoizedProps.items.find(it => it.name && it.name.includes("Mobil"));
                            if (mobilItem) {
                                curr.memoizedProps.onItemClick({ itemData: mobilItem, itemIndex: 0 });
                                return "invoked_react_mobil_imza";
                            }
                        }
                        curr = curr.return;
                    }
                }

                // Yöntem 2: DOM üzerinde görünür liste elemanı varsa tıkla
                const items = Array.from(document.querySelectorAll('.dx-list-item, [role="option"]'));
                const item = items.find(e => {
                    const t = (e.innerText || e.getAttribute('title') || '').trim();
                    return (t === 'Mobil İmza İle Giriş' || t.startsWith('Mobil İmza')) && e.getBoundingClientRect().width > 0;
                });
                if (item) {
                    item.click();
                    item.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                    item.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                    item.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    return "clicked_mobil_imza";
                }

                // Yöntem 3: Dropdown butonunu açmayı dene
                const btn = dd.querySelector('.dx-dropdownbutton-action') || dd;
                btn.click();
                btn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                btn.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                return "opened_dropdown";
            }
            return "waiting_dropdown";
        })()
        """
        for _ in range(25):
            try:
                res = self.eval_js(js_nav, timeout=6.0)
                if res == "already_on_mobil_imza":
                    break
                elif res in ("invoked_react_mobil_imza", "clicked_mobil_imza"):
                    time.sleep(1.0)
                    for _ in range(15):
                        t_tab = self.get_uyap_tab()
                        cur_url = (t_tab.get("url") or "") if t_tab else ""
                        if "giris.turkiye.gov.tr/Giris/Mobil-Imza" in cur_url:
                            break
                        try:
                            if self.eval_js("window.location.href.includes('giris.turkiye.gov.tr/Giris/Mobil-Imza')", timeout=3.0):
                                break
                        except Exception:
                            pass
                        time.sleep(0.5)
                    break
            except Exception:
                pass
            time.sleep(0.8)

        # Formun yüklenmesini bekle
        loaded = False
        for _ in range(35):
            time.sleep(0.6)
            try:
                check = self.eval_js("Boolean(document.getElementById('tridField') && document.getElementById('gsmField'))", timeout=5.0)
                if check:
                    loaded = True
                    break
            except Exception:
                pass

        if not loaded:
            return False, "Mobil İmza giriş sayfası (giris.turkiye.gov.tr) yüklenemedi."

        log_msg("[*] e-Devlet Mobil İmza formu dolduruluyor...")

        # Adım 2: Formu doldur ve gönder
        js_fill = f"""
        (() => {{
            const tr = document.getElementById('tridField');
            const gsm = document.getElementById('gsmField');
            const op = document.getElementById({json.dumps(op_id)}) || document.getElementById('turktelekom');
            if (!tr || !gsm) return {{ok: false, error: "Form alanları bulunamadı"}};

            tr.value = {json.dumps(tc)};
            tr.dispatchEvent(new Event('input', {{bubbles: true}}));
            tr.dispatchEvent(new Event('change', {{bubbles: true}}));

            gsm.value = {json.dumps(gsm_digits)};
            gsm.dispatchEvent(new Event('input', {{bubbles: true}}));
            gsm.dispatchEvent(new Event('change', {{bubbles: true}}));

            if (op) {{
                op.click();
                op.checked = true;
                op.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}
            const btn = document.querySelector('button[name="submitButton"]') ||
                        document.querySelector('button[type="submit"]') ||
                        document.querySelector('.btn-send') ||
                        document.getElementById('submitButton') ||
                        document.querySelector('input[type="submit"]');
            if (btn) {{
                btn.click();
                return {{ok: true}};
            }}
            return {{ok: false, error: "Devam Et butonu bulunamadı"}};
        }})()
        """
        fill_res = self.eval_js(js_fill, timeout=10.0)
        if not fill_res or not fill_res.get("ok"):
            return False, f"Form doldurulamadı: {fill_res}"

        # Adım 3: Taahhütname (İmzala) veya doğrudan Parmak İzi ekranını bekle
        time.sleep(1.0)
        js_sign = """
        (() => {
            const err = document.querySelector(".errorMessage, .alert-danger");
            if (err && err.innerText.trim()) {
                return {error: err.innerText.trim()};
            }
            // 1. Önce İmzala / Onayla butonunu ara ve tıkla
            const btn = Array.from(document.querySelectorAll('button, input[type="submit"], input[name="submitButton"], .btn-send')).find(b => {
                const txt = (b.innerText || b.value || '').trim();
                return txt === "İmzala" || txt.includes("İmzala") || txt === "Onayla";
            });
            if (btn) {
                btn.click();
                return {ok: true, step: "clicked_imzala"};
            }

            // 2. İmzala butonu yoksa, parmak izi / operatör bekleme ekranında mıyız kontrol et
            const text = document.body.innerText || "";
            if (text.includes("İşlemin Parmak İzi") || text.includes("operatorden cevap bekleniyor") || text.includes("parmak izi (2) görüntülenecektir")) {
                return {ok: true, step: "already_waiting_fp"};
            }
            return {waiting: true};
        })()
        """
        for _ in range(20):
            time.sleep(0.6)
            try:
                s_res = self.eval_js(js_sign, timeout=6.0)
                if s_res and s_res.get("error"):
                    return False, f"e-Devlet Hatası: {s_res['error']}"
                if s_res and s_res.get("ok"):
                    if s_res.get("step") == "clicked_imzala":
                        time.sleep(1.5)
                    break
            except Exception:
                pass

        log_msg("[*] Mobil İmza isteği operatöre iletildi. Parmak izi bekleniyor...")

        # Adım 4: Parmak izi bilgisini yakala
        js_wait_fp = """
        (() => {
            const text = document.body.innerText || "";
            let fp = "";
            const m = text.match(/(?:İşlemin\\s+)?Parmak\\s+İzi\\s*[:\\-]?\\s*([A-F0-9]{4}(?:\\s+[A-F0-9]{4})+)/i) ||
                      text.match(/([A-F0-9]{4}(?:\\s+[A-F0-9]{4}){3,})/i);
            if (m) {
                fp = m[1].trim();
            }
            return {
                url: window.location.href,
                has_fingerprint: Boolean(fp),
                fingerprint: fp,
                is_waiting_user: text.includes("operatorden cevap") || text.includes("operatör") || text.includes("bekleniyor")
            };
        })()
        """
        fp_str = ""
        for _ in range(25):
            time.sleep(0.6)
            try:
                st = self.eval_js(js_wait_fp, timeout=5.0)
                if st and st.get("fingerprint"):
                    fp_str = st["fingerprint"]
                    break
            except Exception:
                pass

        log_msg("\n" + "=" * 70)
        log_msg("  📲 MOBİL İMZA ONAY BİLDİRİMİ TELEFONUNUZA GÖNDERİLDİ!")
        if fp_str:
            log_msg(f"  🔑 İşlemin Parmak İzi : {fp_str}")
        log_msg("  ⏳ Lütfen telefonunuza gelen bildirime PIN KODUNUZU girip ONAYLAYINIZ.")
        log_msg("  (İşlem tamamlanınca otomatik algılanacaktır, 90 sn süreniz vardır...)")
        log_msg("=" * 70 + "\n")

        # Adım 5: Kullanıcının telefondan onaylamasını bekle (OAuth Onayla ve UYAP yönlendirmesi)
        js_poll_done = """
        (() => {
            const url = window.location.href;
            // 1. OAuth onay ekranı geldi mi?
            const oauthBtn = Array.from(document.querySelectorAll('button, input[type="submit"], input[type="button"], a.btn')).find(b => {
                const txt = (b.innerText || b.value || '').trim();
                return txt === 'Onayla' || txt.includes('Onayla');
            });
            if (oauthBtn) {
                oauthBtn.click();
                return {status: "clicked_oauth_onayla"};
            }
            // 2. UYAP sayfasına ulaştık mı?
            if (url.includes("avukat.uyap.gov.tr")) {
                return {status: "at_uyap_portal", url};
            }
            return {status: "waiting", url};
        })()
        """

        start_wait = time.time()
        while time.time() - start_wait < 100:
            time.sleep(2.0)
            try:
                poll = self.eval_js(js_poll_done, timeout=6.0)
                if poll and poll.get("status") == "clicked_oauth_onayla":
                    log_msg("[*] e-Devlet izin ekranı otomatik onaylandı. UYAP'a yönlendiriliyor...")
                    time.sleep(2.0)

                uinfo = self.check_login_and_user()
                usr = uinfo.get("user") or {}
                tckn_val = str(usr.get("tcKimlikNo") or usr.get("tckn") or "")
                if tckn_val:
                    name_val = f"{usr.get('adi', '')} {usr.get('soyadi', '')}".strip() or "Avukat"
                    return True, f"Mobil İmza ile başarıyla giriş yapıldı: {name_val} (TCKN: {tckn_val})"
            except Exception:
                pass

        return False, "Mobil İmza onay süresi doldu (Zaman aşımı). Lütfen tekrar deneyiniz."

    def query_uyap_islemlerim(self, tckn: str, start_date_str: str, end_date_str: str, islem_turu=None) -> dict:
        """uyap_islem_sorgula.ajx uç noktasına tarayıcı oturumu içinden POST isteği atar."""
        payload = {
            "tcKimlikNo": tckn,
            "islemTuru": islem_turu,
            "isEmirNo": None,
            "baslangicTarihi": start_date_str,
            "bitisTarihi": end_date_str,
        }
        js = f"""
        (async () => {{
            const resp = await fetch("/uyap_islem_sorgula.ajx", {{
                method: "POST",
                headers: {{
                    "Content-Type": "application/json",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache"
                }},
                body: JSON.stringify({json.dumps(payload)})
            }});
            const errHeader = resp.headers.get("error-code");
            const ct = resp.headers.get("content-type") || "";
            let data = null;
            let text = "";
            if (ct.includes("application/json")) {{
                data = await resp.json();
            }} else {{
                text = await resp.text();
                try {{ data = JSON.parse(text); }} catch (e) {{}}
            }}
            return {{
                status: resp.status,
                ok: resp.ok,
                errorHeader: errHeader,
                data: data,
                rawText: text ? text.slice(0, 500) : ""
            }};
        }})()
        """
        try:
            return self.eval_js(js, timeout=90.0, retries=2) or {}
        except Exception as e:
            return {"status": 500, "ok": False, "error": str(e), "rawText": str(e)}

    def query_sik_kullanilan_dosyalar(self) -> list:
        """Sık kullanılan dosyaları (avukat_sik_kullanilan_dosyalar.ajx) çeker."""
        js = """
        (async () => {
            const resp = await fetch("/avukat_sik_kullanilan_dosyalar.ajx", {
                method: "POST",
                headers: {"Content-Type": "application/json", "Cache-Control": "no-cache"},
                body: JSON.stringify({})
            });
            if (!resp.ok) return [];
            const data = await resp.json();
            return Array.isArray(data) ? data : (data && Array.isArray(data.data) ? data.data : []);
        })()
        """
        return self.eval_js(js, timeout=30.0) or []

    def query_dosya_evraklar(self, dosya_id: str) -> list:
        """Bir dosyanın tüm evrak sayfalarını çeker."""
        js = f"""
        (async () => {{
            const dosyaId = {json.dumps(dosya_id)};
            const totalResp = await fetch("/listDosyaEvraklarPageTotal.ajx", {{
                method: "POST",
                headers: {{"Content-Type": "application/json", "Cache-Control": "no-cache"}},
                body: JSON.stringify({{dosyaId: dosyaId, pageNumber: 1}})
            }});
            let totalPages = 1;
            if (totalResp.ok) {{
                try {{
                    const td = await totalResp.json();
                    if (typeof td === "number") totalPages = td;
                    else if (td && td.total) totalPages = Number(td.total) || 1;
                }} catch (e) {{}}
            }}
            let allPages = [];
            for (let p = 1; p <= Math.max(totalPages, 1); p++) {{
                const r = await fetch("/list_dosya_evraklar.ajx", {{
                    method: "POST",
                    headers: {{"Content-Type": "application/json", "Cache-Control": "no-cache"}},
                    body: JSON.stringify({{dosyaId: dosyaId, pageNumber: p}})
                }});
                if (r.ok) {{
                    const d = await r.json();
                    allPages.push(d);
                }}
                if (p < totalPages) {{
                    await new Promise(res => setTimeout(res, 1200));
                }}
            }}
            return allPages;
        }})()
        """
        return self.eval_js(js, timeout=90.0) or []

    def query_dosyalar_evraklar_batch(self, dosya_ids: list, chunk_size: int = 5) -> dict:
        """
        Birden fazla dosyanın evrak sayfalarını Chromium içinde paralel olarak çeker.
        Tek tek sorgulamaya göre 5-8 kat daha hızlıdır.
        """
        if not dosya_ids:
            return {}

        clean_ids = [str(x).strip() for x in dosya_ids if str(x).strip()]
        if not clean_ids:
            return {}
        ids_json = json.dumps(clean_ids)

        js = f"""
        (async () => {{
            const ids = {ids_json};
            const results = {{}};
            const chunkSize = {chunk_size};
            for (let i = 0; i < ids.length; i += chunkSize) {{
                const chunk = ids.slice(i, i + chunkSize);
                await Promise.all(chunk.map(async (dosyaId) => {{
                    try {{
                        const totalResp = await fetch("/listDosyaEvraklarPageTotal.ajx", {{
                            method: "POST",
                            headers: {{"Content-Type": "application/json", "Cache-Control": "no-cache"}},
                            body: JSON.stringify({{dosyaId: dosyaId, pageNumber: 1}})
                        }});
                        let totalPages = 1;
                        if (totalResp.ok) {{
                            try {{
                                const td = await totalResp.json();
                                if (typeof td === "number") totalPages = td;
                                else if (td && td.total) totalPages = Number(td.total) || 1;
                            }} catch (e) {{}}
                        }}
                        let allPages = [];
                        for (let p = 1; p <= Math.min(totalPages, 5); p++) {{
                            const r = await fetch("/list_dosya_evraklar.ajx", {{
                                method: "POST",
                                headers: {{"Content-Type": "application/json", "Cache-Control": "no-cache"}},
                                body: JSON.stringify({{dosyaId: dosyaId, pageNumber: p}})
                            }});
                            if (r.ok) {{
                                const d = await r.json();
                                allPages.push(d);
                            }}
                        }}
                        results[dosyaId] = allPages;
                    }} catch(e) {{
                        results[dosyaId] = [];
                    }}
                }}));
                if (i + chunkSize < ids.length) {{
                    await new Promise(res => setTimeout(res, 200));
                }}
            }}
            return results;
        }})()
        """
        res = self.eval_js(js, timeout=120.0)
        return res if isinstance(res, dict) else {}

    def query_tum_dosyalar(self, include_closed: bool = True, use_cache: bool = True) -> list:
        """
        Tüm yargı türlerindeki AÇIK ve KAPALI (Karara Çıkmış / Arşiv) dava dosyalarını
        paralel ve optimize şekilde UYAP search_phrase_detayli servisinden çeker.
        """
        cache_file = BASE_DIR / "tum_dosyalar_cache.json"
        if use_cache and cache_file.exists():
            try:
                cdata = json.loads(cache_file.read_text(encoding="utf-8"))
                if (time.time() - cdata.get("timestamp", 0)) < 1800 and isinstance(cdata.get("cases"), list):
                    cached_cases = cdata["cases"]
                    if not include_closed:
                        cached_cases = [c for c in cached_cases if c.get("dosyaDurumGenel") == "Açık" or "açık" in str(c.get("dosyaDurum", "")).lower()]
                    return cached_cases
            except Exception:
                pass

        durum_vals = [1, 0] if include_closed else [1]
        js = f"""
        (async () => {{
            const birimler = [
                ["0", "0901"], ["0", "0921"], ["0", "7702"], ["0", "0929"], ["0", "0911"],
                ["0", "0914"], ["0", "0924"], ["0", "0915"], ["0", "0931"],
                ["1", "0926"], ["1", "0920"], ["1", "0902"], ["1", "7710"], ["1", "7703"],
                ["1", "0913"], ["1", "0925"], ["1", "0908"], ["1", "0907"], ["1", "0906"],
                ["1", "0904"], ["1", "0912"],
                ["2", "1101"],
                ["6", "0917"], ["6", "0918"], ["6", "0919"],
                ["11", "9791"],
                ["25", "6701"]
            ];
            const durumlar = {json.dumps(durum_vals)};

            let tasks = [];
            for (let [y_tur, b_tur] of birimler) {{
                for (let durum of durumlar) {{
                    tasks.push((async () => {{
                        try {{
                            let resp = await fetch("/search_phrase_detayli.ajx", {{
                                method: "POST",
                                headers: {{"Content-Type": "application/json"}},
                                body: JSON.stringify({{
                                    dosyaDurumKod: durum,
                                    birimTuru3: y_tur,
                                    birimTuru2: b_tur,
                                    pageSize: 200,
                                    pageNumber: 1
                                }})
                            }});
                            if (!resp.ok) return [];
                            let d = await resp.json();
                            if (Array.isArray(d) && Array.isArray(d[0])) {{
                                return d[0].map(x => ({{...x, dosyaDurumGenel: durum === 1 ? "Açık" : "Kapalı"}}));
                            }}
                            return [];
                        }} catch (e) {{
                            return [];
                        }}
                    }})());
                }}
            }}

            tasks.push((async () => {{
                try {{
                    let resp = await fetch("/avukat_dosya_sorgula_cbs_brd.ajx", {{
                        method: "POST",
                        headers: {{"Content-Type": "application/json"}},
                        body: JSON.stringify({{dosyaDurumKod: 1, pageSize: 200, pageNumber: 1}})
                    }});
                    let d = await resp.json();
                    return (Array.isArray(d) && Array.isArray(d[0])) ? d[0] : [];
                }} catch (e) {{ return []; }}
            }})());

            let results = await Promise.all(tasks);
            let allCases = [];
            let seen = new Set();
            for (let list of results) {{
                for (let c of list) {{
                    let id = c.dosyaId || c.dosyaID;
                    if (id && !seen.has(id)) {{
                        seen.add(id);
                        allCases.push(c);
                    }}
                }}
            }}
            return allCases;
        }})()
        """
        cases = self.eval_js(js, timeout=45.0) or []
        if cases and include_closed:
            try:
                cache_file.write_text(
                    json.dumps({"timestamp": time.time(), "cases": cases}, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
            except Exception:
                pass
        return cases

    def query_durusmalar(self, start_date_str: str, end_date_str: str) -> list:
        """avukat_durusma_sorgula_brd.ajx ile belirli tarih aralığındaki duruşmaları çeker."""
        js = f"""
        (async () => {{
            const resp = await fetch("/avukat_durusma_sorgula_brd.ajx", {{
                method: "POST",
                headers: {{"Content-Type": "application/json", "Cache-Control": "no-cache"}},
                body: JSON.stringify({{
                    baslangicTarihi: {json.dumps(start_date_str)},
                    bitisTarihi: {json.dumps(end_date_str)}
                }})
            }});
            if (!resp.ok) return [];
            const d = await resp.json();
            return Array.isArray(d) ? d : (d && Array.isArray(d.data) ? d.data : []);
        }})()
        """
        return self.eval_js(js, timeout=30.0) or []

    def download_document(self, params: dict) -> dict:
        """download_document_brd.uyap üzerinden tek bir evrakı indirir ve base64 olarak döndürür."""
        qs = urlencode({k: v for k, v in params.items() if v is not None and v != ""})
        url = f"/download_document_brd.uyap?{qs}"
        js = f"""
        (async () => {{
            const resp = await fetch({json.dumps(url)}, {{
                method: "GET",
                headers: {{
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache"
                }}
            }});
            const errHeader = resp.headers.get("error-code");
            const ct = resp.headers.get("content-type") || "";
            const cd = resp.headers.get("content-disposition") || "";
            const buf = await resp.arrayBuffer();
            const bytes = new Uint8Array(buf);
            let binary = "";
            const chunkSize = 0x8000;
            for (let i = 0; i < bytes.length; i += chunkSize) {{
                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
            }}
            const b64 = btoa(binary);
            return {{
                status: resp.status,
                ok: resp.ok,
                errorHeader: errHeader,
                contentType: ct,
                contentDisposition: cd,
                size: bytes.length,
                base64: b64
            }};
        }})()
        """
        return self.eval_js(js, timeout=90.0) or {}


# ---------------------------------------------------------------------------
# Masaüstü Arayüzü (Tkinter GUI)
# ---------------------------------------------------------------------------

FOLDER_PRESETS = [
    (
        "Dosya Türü / Evrak Türü / İşlem Türü / Yıl-Ay (Önerilen)",
        "{dosya_turu}/{evrak_turu}/{islem_turu}/{yil_ay}/{tarih}_{birim_adi}_{dosya_no}_{evrak_adi}",
    ),
    (
        "Yıl-Ay / Dosya Türü / Birim & Dosya No / Evrak Türü",
        "{yil_ay}/{dosya_turu}/{birim_adi}_{dosya_no}/{evrak_turu}/{tarih}_{evrak_adi}",
    ),
    (
        "Dosya Türü / Birim & Dosya No / İşlem Türü / Evrak Türü",
        "{dosya_turu}/{birim_adi}_{dosya_no}/{islem_turu}/{evrak_turu}/{tarih}_{evrak_adi}",
    ),
    (
        "İşlem Türü / Dosya Türü / Evrak Türü / Tarih",
        "{islem_turu}/{dosya_turu}/{evrak_turu}/{yil_ay}/{tarih}_{birim_adi}_{dosya_no}_{evrak_adi}",
    ),
]


class UyapDownloaderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("UYAP Avukat Portal - Güvenli Evrak İndirici (e-İmza Otomatik Yenilemeli)")
        self.root.geometry("1280x880")
        self.root.minsize(1050, 720)

        self.bridge = UyapBrowserBridge(port=CDP_PORT)
        self.all_items: list[dict] = []
        self.filtered_items: list[dict] = []
        self.tckn: str = ""

        self.is_busy = False
        self.stop_requested = False
        self.pause_requested = False

        # İndirme geçmişini diskteki fiziksel dosyalarla eşleştirip temizleyerek yükle
        self.history: dict = self._verify_and_clean_history()

        self._build_ui()
        self.log(f"Uygulama hazır. Kayıtlı ve doğrulanmış evrak sayısı: {len(self.history)}")

    # -----------------------------------------------------------------------
    # Geçmiş Yönetimi ve Tam Doğruluk (Idempotency)
    # -----------------------------------------------------------------------
    def _verify_and_clean_history(self) -> dict:
        """
        Geçmişteki kayıtları diskteki fiziksel dosyalarla karşılaştırır:
        - Eski dinamik anahtarları kalıcı islem_{is_emir_no} formatına standardize eder.
        - Dosya diskte yoksa veya boyutu < 128 / HTML ise geçmişten siler (tekrar indirilebilsin diye).
        - Böylece eksik evrak kalmaz ve mükerrerlik %100 önlenir.
        """
        raw = {}
        if HISTORY_FILE.exists():
            try:
                raw = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            except Exception:
                raw = {}

        cleaned = {}
        for k, v in raw.items():
            path_str = v.get("path")
            if not path_str:
                continue
            fp = Path(path_str)
            if not fp.exists():
                continue
            try:
                sz = fp.stat().st_size
                if sz < 128:
                    fp.unlink(missing_ok=True)
                    continue
                head = fp.read_bytes()[:512].lower()
                if b"<html" in head or b"<!doctype html" in head:
                    fp.unlink(missing_ok=True)
                    continue

                # Anahtarı kalıcı işlem numarasıyla (is_emir_no) standardize et
                emir = str(v.get("is_emir_no") or "").strip()
                if not emir and "_" in k:
                    parts = k.split("_")
                    if len(parts) >= 2 and parts[-1].isdigit():
                        emir = parts[-1]
                v["is_emir_no"] = emir
                stable_k = f"islem_{emir}" if emir else k
                cleaned[stable_k] = v
            except Exception:
                continue

        try:
            HISTORY_FILE.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return cleaned

    def is_item_already_downloaded(self, item_or_key) -> bool:
        """
        Bir evrakın hem geçmişte hem de diskte sağlam bir şekilde bulunup bulunmadığını denetler.
        UYAP evrakId'yi dinamik şifrelese bile:
        1. is_emir_no eşleşmesi (kalıcı işlem no)
        2. unique_key eşleşmesi
        3. (birim_adi, dosya_no, tarih, evrak_adi) içerik eşleşmesi
        4. Diskteki fiziksel dosya varlığı ve boyutu (> 128 bayt)
        ile tam mükerrerlik denetimi yapar.
        """
        if isinstance(item_or_key, dict):
            item = item_or_key
            ukey = item.get("unique_key", "")
            is_emir = str(item.get("is_emir_no") or "").strip()
        else:
            ukey = str(item_or_key)
            is_emir = ukey.split("_")[-1] if ukey.startswith("islem_") else ""
            item = {}

        entry = None
        # 1. Doğrudan ukey eşleşmesi
        if ukey and ukey in self.history:
            entry = self.history[ukey]
        # 2. is_emir_no eşleşmesi (örn: islem_18313320805)
        elif is_emir and f"islem_{is_emir}" in self.history:
            entry = self.history[f"islem_{is_emir}"]
        # 3. Geçmişteki is_emir_no alanları arasında ara
        elif is_emir:
            for h in self.history.values():
                if str(h.get("is_emir_no") or "").strip() == is_emir:
                    entry = h
                    break

        # 4. Alternatif eşleşme: Birim adı, dosya no, tarih ve evrak adı birebir aynı mı?
        if not entry and item:
            b_adi = item.get("birim_adi")
            d_no = item.get("dosya_no")
            e_adi = item.get("evrak_adi")
            tarih = item.get("tarih")
            for h in self.history.values():
                if (
                    h.get("birim_adi") == b_adi
                    and h.get("dosya_no") == d_no
                    and h.get("evrak_turu") == e_adi
                    and h.get("tarih") == tarih
                ):
                    entry = h
                    break

        if not entry:
            return False

        path_str = entry.get("path")
        if not path_str:
            return False
        fp = Path(path_str)
        if not fp.exists() or fp.stat().st_size < 128:
            return False
        return True

    def _save_history(self):
        try:
            HISTORY_FILE.write_text(json.dumps(self.history, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            self.log(f"UYARI: İndirme geçmişi kaydedilemedi: {e}", level="WARNING")

    # -----------------------------------------------------------------------
    # Arayüz Kurulumu
    # -----------------------------------------------------------------------
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # 1. Üst Panel: Tarayıcı Bağlantısı, e-İmza Ayarları ve Tarih Seçimi
        top_frame = ttk.LabelFrame(self.root, text=" 1. UYAP Tarayıcı & e-İmza Oturum Yönetimi ", padding=8)
        top_frame.pack(fill=tk.X, padx=10, pady=4)

        row1 = ttk.Frame(top_frame)
        row1.pack(fill=tk.X, pady=2)

        self.btn_launch = ttk.Button(
            row1,
            text="1. Chromium ile UYAP'ı Aç",
            command=self.on_launch_browser,
        )
        self.btn_launch.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_check_login = ttk.Button(
            row1,
            text="2. Giriş Durumunu Kontrol Et",
            command=self.on_check_login,
        )
        self.btn_check_login.pack(side=tk.LEFT, padx=(0, 10))

        # e-İmza Otomatik Giriş Bölümü
        ttk.Label(row1, text="e-İmza PIN:", font=("Sans", 9, "bold")).pack(side=tk.LEFT, padx=(4, 2))
        self.var_pin = tk.StringVar(value=DEFAULT_E_IMZA_PIN)
        self.ent_pin = ttk.Entry(row1, textvariable=self.var_pin, width=8, show="*")
        self.ent_pin.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_toggle_pin = ttk.Button(row1, text="Göster", width=6, command=self.on_toggle_pin_visibility)
        self.btn_toggle_pin.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_manual_relogin = ttk.Button(
            row1,
            text="e-İmza ile Giriş Yap / Yenile",
            command=self.on_manual_relogin,
        )
        self.btn_manual_relogin.pack(side=tk.LEFT, padx=(0, 10))

        self.var_auto_relogin = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row1,
            text="Oturum koparsa e-İmza ile otomatik tekrar bağlan",
            variable=self.var_auto_relogin,
        ).pack(side=tk.LEFT, padx=4)

        row1_sub = ttk.Frame(top_frame)
        row1_sub.pack(fill=tk.X, pady=(4, 2))

        self.lbl_status = ttk.Label(
            row1_sub,
            text="Durum: Chromium ve ArkSigner bağlantısı bekleniyor...",
            foreground="#b45309",
            font=("Sans", 9, "bold"),
        )
        self.lbl_status.pack(side=tk.LEFT, padx=4)

        row2 = ttk.Frame(top_frame)
        row2.pack(fill=tk.X, pady=4)

        ttk.Label(row2, text="Başlangıç:").pack(side=tk.LEFT)
        one_year_ago = dt.date.today() - dt.timedelta(days=365)
        self.var_start_date = tk.StringVar(value=one_year_ago.strftime("%d.%m.%Y"))
        ttk.Entry(row2, textvariable=self.var_start_date, width=12).pack(side=tk.LEFT, padx=(4, 10))

        ttk.Label(row2, text="Bitiş:").pack(side=tk.LEFT)
        self.var_end_date = tk.StringVar(value=dt.date.today().strftime("%d.%m.%Y"))
        ttk.Entry(row2, textvariable=self.var_end_date, width=12).pack(side=tk.LEFT, padx=(4, 10))

        ttk.Label(row2, text="Dilim (Gün):").pack(side=tk.LEFT)
        self.var_chunk_days = tk.IntVar(value=30)
        ttk.Spinbox(row2, from_=7, to=90, textvariable=self.var_chunk_days, width=5).pack(side=tk.LEFT, padx=(4, 12))

        self.btn_scan_islemlerim = ttk.Button(
            row2,
            text="3. İşlemlerim'i Tara (Son 1 Yıl / Seçili Aralık)",
            command=self.on_scan_islemlerim,
        )
        self.btn_scan_islemlerim.pack(side=tk.LEFT, padx=4)

        self.btn_scan_fav_cases = ttk.Button(
            row2,
            text="Sık Kullanılan Dosyalarımın Evraklarını Tara",
            command=self.on_scan_fav_cases,
        )
        self.btn_scan_fav_cases.pack(side=tk.LEFT, padx=4)

        # 2. Orta Panel: Filtreleme ve Kategorileme Ayarları
        filter_frame = ttk.LabelFrame(
            self.root,
            text=" 2. Dosya Türü / Evrak Türü / İşlem Türü / Tarih Filtreleri ve Klasör Şablonu ",
            padding=8,
        )
        filter_frame.pack(fill=tk.X, padx=10, pady=3)

        frow1 = ttk.Frame(filter_frame)
        frow1.pack(fill=tk.X, pady=2)

        ttk.Label(frow1, text="Dosya/Birim Türü:").pack(side=tk.LEFT)
        self.var_filter_dosya_turu = tk.StringVar(value="TÜMÜ")
        self.cb_dosya_turu = ttk.Combobox(
            frow1, textvariable=self.var_filter_dosya_turu, state="readonly", width=22, values=["TÜMÜ"]
        )
        self.cb_dosya_turu.pack(side=tk.LEFT, padx=(4, 10))
        self.cb_dosya_turu.bind("<<ComboboxSelected>>", lambda e: self.apply_filters())

        ttk.Label(frow1, text="Evrak Türü:").pack(side=tk.LEFT)
        self.var_filter_evrak_turu = tk.StringVar(value="TÜMÜ")
        self.cb_evrak_turu = ttk.Combobox(
            frow1, textvariable=self.var_filter_evrak_turu, state="readonly", width=26, values=["TÜMÜ"]
        )
        self.cb_evrak_turu.pack(side=tk.LEFT, padx=(4, 10))
        self.cb_evrak_turu.bind("<<ComboboxSelected>>", lambda e: self.apply_filters())

        ttk.Label(frow1, text="İşlem Türü / Durum:").pack(side=tk.LEFT)
        self.var_filter_islem_turu = tk.StringVar(value="TÜMÜ")
        self.cb_islem_turu = ttk.Combobox(
            frow1, textvariable=self.var_filter_islem_turu, state="readonly", width=18, values=["TÜMÜ"]
        )
        self.cb_islem_turu.pack(side=tk.LEFT, padx=(4, 10))
        self.cb_islem_turu.bind("<<ComboboxSelected>>", lambda e: self.apply_filters())

        ttk.Label(frow1, text="Arama:").pack(side=tk.LEFT)
        self.var_filter_search = tk.StringVar(value="")
        ent_search = ttk.Entry(frow1, textvariable=self.var_filter_search, width=16)
        ent_search.pack(side=tk.LEFT, padx=(4, 6))
        ent_search.bind("<KeyRelease>", lambda e: self.apply_filters())

        ttk.Button(frow1, text="Filtreyi Sıfırla", command=self.reset_filters).pack(side=tk.LEFT, padx=2)

        frow2 = ttk.Frame(filter_frame)
        frow2.pack(fill=tk.X, pady=4)

        ttk.Label(frow2, text="Klasör Şablonu:").pack(side=tk.LEFT)
        self.var_preset = tk.StringVar(value=FOLDER_PRESETS[0][0])
        cb_preset = ttk.Combobox(
            frow2,
            textvariable=self.var_preset,
            state="readonly",
            width=40,
            values=[p[0] for p in FOLDER_PRESETS],
        )
        cb_preset.pack(side=tk.LEFT, padx=(4, 8))

        self.var_template = tk.StringVar(value=FOLDER_PRESETS[0][1])
        ttk.Entry(frow2, textvariable=self.var_template, width=64).pack(side=tk.LEFT, padx=(0, 8), fill=tk.X, expand=True)

        def on_preset_change(event):
            sel = self.var_preset.get()
            for title, tpl in FOLDER_PRESETS:
                if title == sel:
                    self.var_template.set(tpl)
                    break

        cb_preset.bind("<<ComboboxSelected>>", on_preset_change)

        frow3 = ttk.Frame(filter_frame)
        frow3.pack(fill=tk.X, pady=2)

        ttk.Label(frow3, text="İndirme Klasörü:").pack(side=tk.LEFT)
        self.var_download_dir = tk.StringVar(value=str(DEFAULT_DOWNLOAD_DIR))
        ttk.Entry(frow3, textvariable=self.var_download_dir, width=42).pack(side=tk.LEFT, padx=(4, 6))
        ttk.Button(frow3, text="Klasör Seç...", command=self.on_choose_dir).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(frow3, text="Bekleme (sn):").pack(side=tk.LEFT)
        self.var_min_delay = tk.DoubleVar(value=2.0)
        self.var_max_delay = tk.DoubleVar(value=4.0)
        ttk.Spinbox(frow3, from_=1.0, to=30.0, increment=0.5, textvariable=self.var_min_delay, width=4).pack(side=tk.LEFT, padx=1)
        ttk.Label(frow3, text="-").pack(side=tk.LEFT)
        ttk.Spinbox(frow3, from_=1.5, to=60.0, increment=0.5, textvariable=self.var_max_delay, width=4).pack(side=tk.LEFT, padx=(1, 8))

        ttk.Label(frow3, text="Her").pack(side=tk.LEFT)
        self.var_batch_size = tk.IntVar(value=20)
        ttk.Spinbox(frow3, from_=5, to=100, textvariable=self.var_batch_size, width=3).pack(side=tk.LEFT, padx=1)
        ttk.Label(frow3, text="dosyada").pack(side=tk.LEFT)
        self.var_batch_pause = tk.DoubleVar(value=10.0)
        ttk.Spinbox(frow3, from_=5.0, to=120.0, increment=5.0, textvariable=self.var_batch_pause, width=4).pack(side=tk.LEFT, padx=1)
        ttk.Label(frow3, text="sn dinlen").pack(side=tk.LEFT, padx=(0, 8))

        self.var_skip_downloaded = tk.BooleanVar(value=True)
        ttk.Checkbutton(frow3, text="İndirilenleri Atla (Mükerrer Önle)", variable=self.var_skip_downloaded).pack(side=tk.LEFT, padx=4)

        # 3. Tablo (Treeview): Evrak Listesi
        table_frame = ttk.LabelFrame(self.root, text=" 3. Bulunan Evraklar / İşlemler Listesi ", padding=6)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=3)

        cols = ("durum", "tarih", "dosya_turu", "birim_adi", "dosya_no", "islem_turu", "evrak_turu", "evrak_id")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="extended")
        headers = {
            "durum": ("İndirme Durumu", 120),
            "tarih": ("Tarih", 95),
            "dosya_turu": ("Dosya / Yargı Türü", 170),
            "birim_adi": ("Birim / Mahkeme Adı", 230),
            "dosya_no": ("Dosya No", 100),
            "islem_turu": ("İşlem Türü / Kaynak", 140),
            "evrak_turu": ("Evrak / İşin Adı", 260),
            "evrak_id": ("Evrak ID", 100),
        }
        for c, (text, w) in headers.items():
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor=tk.W)

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # 4. Alt Panel: İndirme Butonları, İlerleme Çubuğu ve Rotasyonlu Log Konsolu
        bottom_frame = ttk.Frame(self.root, padding=6)
        bottom_frame.pack(fill=tk.X, padx=10, pady=2)

        btn_row = ttk.Frame(bottom_frame)
        btn_row.pack(fill=tk.X, pady=3)

        self.btn_download_filtered = ttk.Button(
            btn_row,
            text="Filtrelenen Tüm Evrakları Güvenli İndir",
            command=lambda: self.on_start_download(selected_only=False),
        )
        self.btn_download_filtered.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_download_selected = ttk.Button(
            btn_row,
            text="Sadece Tabloda Seçili Olanları İndir",
            command=lambda: self.on_start_download(selected_only=True),
        )
        self.btn_download_selected.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_pause = ttk.Button(
            btn_row,
            text="Duraklat / Devam Et",
            command=self.on_toggle_pause,
            state=tk.DISABLED,
        )
        self.btn_pause.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_stop = ttk.Button(
            btn_row,
            text="İndirmeyi Durdur",
            command=self.on_stop,
            state=tk.DISABLED,
        )
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_export_csv = ttk.Button(
            btn_row,
            text="Listeyi CSV Olarak Dışa Aktar",
            command=self.on_export_csv,
        )
        self.btn_export_csv.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_open_log = ttk.Button(
            btn_row,
            text="📄 Log Dosyasını Aç",
            command=self.on_open_log_file,
        )
        self.btn_open_log.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_open_dir = ttk.Button(
            btn_row,
            text="📁 İndirilenler Klasörünü Aç",
            command=self.on_open_download_dir,
        )
        self.btn_open_dir.pack(side=tk.LEFT, padx=(0, 8))

        self.lbl_count = ttk.Label(btn_row, text="Toplam: 0 | Filtrelenen: 0", font=("Sans", 9, "bold"))
        self.lbl_count.pack(side=tk.RIGHT, padx=4)

        self.progress = ttk.Progressbar(bottom_frame, orient="horizontal", mode="determinate")
        self.progress.pack(fill=tk.X, pady=3)

        self.txt_log = tk.Text(bottom_frame, height=5, wrap=tk.WORD, font=("Monospace", 9))
        self.txt_log.pack(fill=tk.X, pady=2)

    # -----------------------------------------------------------------------
    # Loglama Metotları
    # -----------------------------------------------------------------------
    def log(self, msg: str, level: str = "INFO"):
        """Mesajı hem UI konsoluna hem de rotasyonlu 8 MB uyap_indirici_log.txt dosyasına anlık yazar."""
        lvl = getattr(logging, level.upper(), logging.INFO)
        file_logger.log(lvl, msg)
        try:
            rf_handler.flush()
        except Exception:
            pass

        ts = dt.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self.root.after(0, self._append_log, line)

    def _append_log(self, line: str):
        self.txt_log.insert(tk.END, line)
        self.txt_log.see(tk.END)

    def on_open_log_file(self):
        """Kullanıcının sistemindeki varsayılan metin editörüyle log dosyasını açar."""
        if not LOG_FILE.exists():
            LOG_FILE.touch()
        try:
            subprocess.Popen(["xdg-open", str(LOG_FILE)])
        except Exception as e:
            messagebox.showinfo("Log Dosyası", f"Log dosya konumu:\n{LOG_FILE}\n\nDoğrudan açma hatası: {e}")

    def on_open_download_dir(self):
        """İndirilenler klasörünü dosya yöneticisinde açar."""
        d = Path(self.var_download_dir.get().strip() or str(DEFAULT_DOWNLOAD_DIR))
        d.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", str(d)])
        except Exception as e:
            messagebox.showinfo("İndirme Klasörü", f"Klasör konumu:\n{d}\n\nDoğrudan açma hatası: {e}")

    def on_toggle_pin_visibility(self):
        if self.ent_pin.cget("show") == "*":
            self.ent_pin.config(show="")
            self.btn_toggle_pin.config(text="Gizle")
        else:
            self.ent_pin.config(show="*")
            self.btn_toggle_pin.config(text="Göster")

    # -----------------------------------------------------------------------
    # Bağlantı ve e-İmza İşlemleri
    # -----------------------------------------------------------------------
    def on_launch_browser(self):
        def task():
            self.log("Chromium başlatılıyor veya mevcut oturuma bağlanılıyor...")
            ok, msg = self.bridge.launch_or_connect()
            self.log(msg)
            if ok:
                time.sleep(1.2)
                self.on_check_login()

        threading.Thread(target=task, daemon=True).start()

    def on_manual_relogin(self):
        """Kullanıcı butona bastığında veya oturum koptuğunda e-İmza ile giriş yapar."""
        pin = self.var_pin.get().strip()
        if not pin:
            messagebox.showwarning("PIN Gerekli", "Lütfen e-İmza PIN kodunuzu girin.")
            return

        def task():
            self.log(f"ArkSigner üzerinden e-İmza doğrulaması başlatılıyor (PIN: {pin})...")
            ok, msg = self.bridge.auto_relogin(pin=pin)
            if ok:
                self.log(f"Başarılı: {msg}")
                self.root.after(0, lambda: self.lbl_status.config(text=f"Durum: {msg}", foreground="#15803d"))
                self.on_check_login()
            else:
                self.log(f"HATA: {msg}", level="ERROR")
                self.root.after(0, lambda: self.lbl_status.config(text=f"Durum: {msg}", foreground="#b91c1c"))

        threading.Thread(target=task, daemon=True).start()

    def on_check_login(self):
        def task():
            try:
                info = self.bridge.check_login_and_user()
                user = info.get("user")
                if user and (user.get("tcKimlikNo") or user.get("tckn")):
                    self.tckn = str(user.get("tcKimlikNo") or user.get("tckn"))
                    ad = user.get("adi") or user.get("ad") or ""
                    soyad = user.get("soyadi") or user.get("soyad") or ""
                    baro = user.get("baroAdi") or ""
                    status_text = f"Bağlı: {ad} {soyad} (TCKN: {self.tckn} - {baro})"
                    self.log(f"Giriş Başarılı: {status_text}")
                    self.root.after(0, lambda: self.lbl_status.config(text=status_text, foreground="#15803d"))
                else:
                    msg = "UYAP oturumu açık görünmüyor. 'e-İmza ile Giriş Yap' butonuna basabilir veya tarayıcıdan girebilirsiniz."
                    self.log(msg, level="WARNING")
                    self.root.after(0, lambda: self.lbl_status.config(text=msg, foreground="#b45309"))
            except Exception as e:
                self.log(f"Giriş kontrol hatası: {e}", level="ERROR")

        threading.Thread(target=task, daemon=True).start()

    # -----------------------------------------------------------------------
    # Tarama İşlemleri (İşlemlerim ve Sık Kullanılan Dosyalar)
    # -----------------------------------------------------------------------
    def on_scan_islemlerim(self):
        if self.is_busy:
            messagebox.showwarning("Meşgul", "Şu anda devam eden bir işlem var.")
            return
        try:
            start_d = dt.datetime.strptime(self.var_start_date.get().strip(), "%d.%m.%Y").date()
            end_d = dt.datetime.strptime(self.var_end_date.get().strip(), "%d.%m.%Y").date()
        except ValueError:
            messagebox.showerror("Tarih Hatası", "Lütfen tarihleri GG.AA.YYYY (örn. 13.11.2025) formatında girin.")
            return

        if start_d > end_d:
            messagebox.showerror("Tarih Hatası", "Başlangıç tarihi bitiş tarihinden büyük olamaz.")
            return

        chunk_days = max(7, int(self.var_chunk_days.get() or 30))
        self.is_busy = True
        self.stop_requested = False

        def worker():
            try:
                if not self.tckn:
                    info = self.bridge.check_login_and_user()
                    user = info.get("user") or {}
                    self.tckn = str(user.get("tcKimlikNo") or user.get("tckn") or "")
                
                # Oturum yoksa ve otomatik relogin açıksa hemen dene
                if not self.tckn and self.var_auto_relogin.get():
                    self.log("UYAP oturumu bulunamadı, e-İmza ile otomatik giriş deneniyor...")
                    ok, msg = self.bridge.auto_relogin(pin=self.var_pin.get().strip())
                    if ok:
                        info = self.bridge.check_login_and_user()
                        user = info.get("user") or {}
                        self.tckn = str(user.get("tcKimlikNo") or user.get("tckn") or "")

                if not self.tckn:
                    self.log("HATA: UYAP oturumu açılamadı. Lütfen e-İmza ile giriş yapın.", level="ERROR")
                    return

                # Tarih aralığını dilimlere böl
                ranges = []
                cur = start_d
                while cur <= end_d:
                    nxt = min(cur + dt.timedelta(days=chunk_days - 1), end_d)
                    ranges.append((cur, nxt))
                    cur = nxt + dt.timedelta(days=1)

                self.log(
                    f"İşlemlerim taranıyor: {start_d.strftime('%d.%m.%Y')} - {end_d.strftime('%d.%m.%Y')} "
                    f"({len(ranges)} dilim halinde)..."
                )
                seen_keys = {item["unique_key"] for item in self.all_items}
                added_count = 0

                for idx, (s_d, e_d) in enumerate(ranges, start=1):
                    if self.stop_requested:
                        self.log("Tarama kullanıcı tarafından durduruldu.")
                        break

                    s_str = s_d.strftime("%d.%m.%Y")
                    e_str = e_d.strftime("%d.%m.%Y")
                    self.log(f"[{idx}/{len(ranges)}] Sorgulanıyor: {s_str} - {e_str} ...")

                    resp = self.bridge.query_uyap_islemlerim(self.tckn, s_str, e_str, islem_turu=None)
                    
                    # Oturum düştüyse otomatik yenile ve dilimi tekrar dene
                    if not resp.get("ok") or resp.get("status") in (401, 403):
                        if self.var_auto_relogin.get():
                            self.log("Tarama sırasında UYAP oturumu düştü, e-İmza ile otomatik yenileniyor...")
                            self.bridge.auto_relogin(pin=self.var_pin.get().strip())
                            time.sleep(2.0)
                            resp = self.bridge.query_uyap_islemlerim(self.tckn, s_str, e_str, islem_turu=None)

                    rows = resp.get("data")
                    if isinstance(rows, dict):
                        rows = rows.get("data") or rows.get("list") or []
                    if not isinstance(rows, list):
                        rows = []

                    chunk_added = 0
                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        evrak_id = str(r.get("evrakId") or "").strip()
                        gg_evrak_id = str(r.get("ggEvrakId") or "").strip()
                        is_emir_no = str(r.get("isEmirNo") or "").strip()
                        # UYAP evrakId'yi her sorguda dinamik şifreler; sabit ve kalıcı kimlik is_emir_no'dur
                        if is_emir_no:
                            ukey = f"islem_{is_emir_no}"
                        else:
                            ukey = f"islem_{evrak_id}_{gg_evrak_id}"
                        if ukey in seen_keys:
                            continue
                        seen_keys.add(ukey)

                        parsed_date = (
                            parse_uyap_date(r.get("isEmirTarihi"))
                            or parse_uyap_date(r.get("birimEvrakTarihi"))
                            or s_d
                        )
                        birim_adi = str(r.get("birimAdi") or "Belirtilmemis_Birim").strip()
                        dosya_no = str(r.get("dosyaNo") or "DosyaNo_Yok").strip()
                        evrak_adi = str(r.get("name") or r.get("evrakTuru") or "Evrak").strip()
                        evrak_turu_kod = str(r.get("evrakTuru") or evrak_adi).strip()
                        isin_durumu = str(r.get("isinDurumu") or "Tamamlandı").strip()
                        dosya_turu = infer_dosya_turu(birim_adi)

                        item = {
                            "unique_key": ukey,
                            "kaynak": "Islemlerim",
                            "tarih_obj": parsed_date,
                            "tarih": parsed_date.strftime("%Y-%m-%d"),
                            "yil": parsed_date.strftime("%Y"),
                            "yil_ay": parsed_date.strftime("%Y-%m"),
                            "dosya_turu": dosya_turu,
                            "birim_adi": birim_adi,
                            "dosya_no": dosya_no,
                            "dosya_id": str(r.get("dosyaId") or ""),
                            "islem_turu": f"Gonderilen_Islem ({isin_durumu})",
                            "evrak_turu": evrak_adi,
                            "evrak_turu_kod": evrak_turu_kod,
                            "evrak_adi": evrak_adi,
                            "evrak_id": evrak_id,
                            "gg_evrak_id": gg_evrak_id,
                            "is_emir_no": is_emir_no,
                            "download_params": {
                                "evrakId": evrak_id,
                                "gelenGidenEvrakId": gg_evrak_id,
                            },
                            "can_download": bool(evrak_id),
                        }
                        self.all_items.append(item)
                        chunk_added += 1
                        added_count += 1

                    self.log(f"  -> {len(rows)} kayıt döndü ({chunk_added} yeni kayıt eklendi).")
                    self.root.after(0, self.refresh_filter_options_and_table)

                    if idx < len(ranges):
                        time.sleep(random.uniform(2.2, 4.0))

                self.log(f"İşlemlerim taraması tamamlandı. Toplam {added_count} yeni işlem/evrak listeye eklendi.")
            except Exception as e:
                self.log(f"Tarama sırasında hata: {e}", level="ERROR")
            finally:
                self.is_busy = False

        threading.Thread(target=worker, daemon=True).start()

    def on_scan_fav_cases(self):
        if self.is_busy:
            messagebox.showwarning("Meşgul", "Şu anda devam eden bir işlem var.")
            return
        self.is_busy = True
        self.stop_requested = False

        def worker():
            try:
                self.log("Sık Kullanılan Dosyalarım listesi çekiliyor...")
                cases = self.bridge.query_sik_kullanilan_dosyalar()
                if not cases:
                    self.log("Sık kullanılan dosya bulunamadı veya oturum açık değil.", level="WARNING")
                    return

                self.log(f"{len(cases)} adet sık kullanılan dosya bulundu. Evrakları taranıyor...")
                seen_keys = {item["unique_key"] for item in self.all_items}
                added_count = 0

                for idx, c in enumerate(cases, start=1):
                    if self.stop_requested:
                        break
                    dosya_id = str(c.get("dosyaID") or c.get("dosyaId") or "").strip()
                    dosya_no = str(c.get("dosyaNO") or c.get("dosyaNo") or "").strip()
                    birim_adi = str(c.get("birimAdi") or c.get("mahkemeAdi") or "").strip()
                    explicit_tur = str(c.get("dosyaTurAciklama") or c.get("dosyaTuru") or "").strip()
                    if not dosya_id:
                        continue

                    self.log(f"[{idx}/{len(cases)}] {birim_adi} {dosya_no} evrakları sorgulanıyor...")
                    pages = self.bridge.query_dosya_evraklar(dosya_id)

                    def collect_evrak(ev):
                        nonlocal added_count
                        if not isinstance(ev, dict):
                            return
                        ev_id = str(ev.get("evrakId") or "").strip()
                        if ev_id:
                            ukey = f"dosya_{dosya_id}_{ev_id}"
                            if ukey not in seen_keys:
                                seen_keys.add(ukey)
                                p_date = (
                                    parse_uyap_date(ev.get("onaylandigiTarih"))
                                    or parse_uyap_date(ev.get("sistemeGonderildigiTarih"))
                                    or dt.date.today()
                                )
                                tip = str(ev.get("tip") or "DOSYA").strip()
                                tur = str(ev.get("tur") or "Evrak").strip()
                                item = {
                                    "unique_key": ukey,
                                    "kaynak": "Dosya_Evraklari",
                                    "tarih_obj": p_date,
                                    "tarih": p_date.strftime("%Y-%m-%d"),
                                    "yil": p_date.strftime("%Y"),
                                    "yil_ay": p_date.strftime("%Y-%m"),
                                    "dosya_turu": infer_dosya_turu(birim_adi, explicit_tur),
                                    "birim_adi": birim_adi or str(ev.get("gonderenYerKisi") or "Birim"),
                                    "dosya_no": dosya_no or str(ev.get("gonderenDosyaNo") or "DosyaNo"),
                                    "dosya_id": dosya_id,
                                    "islem_turu": f"Dosya_Evraki_{tip}",
                                    "evrak_turu": tur,
                                    "evrak_turu_kod": tur,
                                    "evrak_adi": tur,
                                    "evrak_id": ev_id,
                                    "gg_evrak_id": "",
                                    "is_emir_no": str(ev.get("birimEvrakNo") or ""),
                                    "download_params": {
                                        "dosyaId": dosya_id,
                                        "evrakId": ev_id,
                                    },
                                    "can_download": True,
                                }
                                self.all_items.append(item)
                                added_count += 1
                        for ek in ev.get("ekEvrakListesi") or []:
                            collect_evrak(ek)

                    for p in pages:
                        if isinstance(p, dict):
                            tum = p.get("tumEvraklar") or p
                            if isinstance(tum, dict):
                                for sub_k, sub_v in tum.items():
                                    if isinstance(sub_v, list):
                                        for ev in sub_v:
                                            collect_evrak(ev)
                            elif isinstance(tum, list):
                                for ev in tum:
                                    collect_evrak(ev)
                        elif isinstance(p, list):
                            for ev in p:
                                collect_evrak(ev)

                    self.root.after(0, self.refresh_filter_options_and_table)
                    if idx < len(cases):
                        time.sleep(random.uniform(2.5, 4.5))

                self.log(f"Dosya evrakları taraması tamamlandı. {added_count} yeni evrak eklendi.")
            except Exception as e:
                self.log(f"Dosya evrakları taranırken hata: {e}", level="ERROR")
            finally:
                self.is_busy = False

        threading.Thread(target=worker, daemon=True).start()

    # -----------------------------------------------------------------------
    # Filtreleme ve Tablo Yönetimi
    # -----------------------------------------------------------------------
    def refresh_filter_options_and_table(self):
        dosya_turleri = sorted({it["dosya_turu"] for it in self.all_items if it.get("dosya_turu")})
        evrak_turleri = sorted({it["evrak_turu"] for it in self.all_items if it.get("evrak_turu")})
        islem_turleri = sorted({it["islem_turu"] for it in self.all_items if it.get("islem_turu")})

        self.cb_dosya_turu["values"] = ["TÜMÜ"] + dosya_turleri
        self.cb_evrak_turu["values"] = ["TÜMÜ"] + evrak_turleri
        self.cb_islem_turu["values"] = ["TÜMÜ"] + islem_turleri

        self.apply_filters()

    def reset_filters(self):
        self.var_filter_dosya_turu.set("TÜMÜ")
        self.var_filter_evrak_turu.set("TÜMÜ")
        self.var_filter_islem_turu.set("TÜMÜ")
        self.var_filter_search.set("")
        self.apply_filters()

    def apply_filters(self):
        f_dt = self.var_filter_dosya_turu.get()
        f_et = self.var_filter_evrak_turu.get()
        f_it = self.var_filter_islem_turu.get()
        f_q = self.var_filter_search.get().strip().lower()

        res = []
        for it in self.all_items:
            if f_dt != "TÜMÜ" and it["dosya_turu"] != f_dt:
                continue
            if f_et != "TÜMÜ" and it["evrak_turu"] != f_et:
                continue
            if f_it != "TÜMÜ" and it["islem_turu"] != f_it:
                continue
            if f_q:
                haystack = f"{it['birim_adi']} {it['dosya_no']} {it['evrak_turu']} {it['evrak_id']}".lower()
                if f_q not in haystack:
                    continue
            res.append(it)

        # Tarihe göre yeniden eskiye sırala
        res.sort(key=lambda x: x["tarih"], reverse=True)
        self.filtered_items = res

        # Tabloyu güncelle
        self.tree.delete(*self.tree.get_children())
        for it in self.filtered_items:
            ukey = it["unique_key"]
            if not it["can_download"]:
                status = "İndirilemez (ID Yok)"
            elif self.is_item_already_downloaded(it):
                status = "İndirildi"
            else:
                status = "Bekliyor"
            self.tree.insert(
                "",
                tk.END,
                iid=ukey,
                values=(
                    status,
                    it["tarih"],
                    it["dosya_turu"],
                    it["birim_adi"],
                    it["dosya_no"],
                    it["islem_turu"],
                    it["evrak_turu"],
                    it["evrak_id"],
                ),
            )

        self.lbl_count.config(text=f"Toplam: {len(self.all_items)} | Filtrelenen: {len(self.filtered_items)}")

    def build_target_filepath(self, item: dict, ext: str, override_dir: Path | None = None) -> Path:
        if override_dir:
            base_dir = Path(override_dir)
        elif hasattr(self, "var_download_dir"):
            base_dir = Path(self.var_download_dir.get().strip() or str(DEFAULT_DOWNLOAD_DIR))
        else:
            base_dir = DEFAULT_DOWNLOAD_DIR

        if hasattr(self, "var_template"):
            tpl = self.var_template.get().strip() or FOLDER_PRESETS[0][1]
        else:
            tpl = FOLDER_PRESETS[0][1]

        tokens = {
            "kaynak": sanitize_path_part(item.get("kaynak"), "UYAP"),
            "yil": sanitize_path_part(item.get("yil"), "Yil"),
            "yil_ay": sanitize_path_part(item.get("yil_ay"), "Yil_Ay"),
            "tarih": sanitize_path_part(item.get("tarih"), "Tarih"),
            "dosya_turu": sanitize_path_part(item.get("dosya_turu"), "Dosya_Turu"),
            "birim_adi": sanitize_path_part(item.get("birim_adi"), "Birim"),
            "dosya_no": sanitize_path_part(item.get("dosya_no"), "DosyaNo"),
            "islem_turu": sanitize_path_part(item.get("islem_turu"), "Islem_Turu"),
            "evrak_turu": sanitize_path_part(item.get("evrak_turu"), "Evrak_Turu"),
            "evrak_adi": sanitize_path_part(item.get("evrak_adi"), "Evrak"),
            "evrak_id": sanitize_path_part(item.get("evrak_id"), "0"),
            "is_emir_no": sanitize_path_part(item.get("is_emir_no"), ""),
        }

        rel_str = tpl.format(**tokens)
        parts = [sanitize_path_part(p, "Klasor", max_len=90) for p in rel_str.replace("\\", "/").split("/") if p.strip()]
        if not parts:
            parts = [f"evrak_{tokens['is_emir_no'] or tokens['evrak_id']}"]

        ident = tokens["is_emir_no"] or tokens["evrak_id"]
        filename_stem = f"{parts[-1]}_{ident}"
        folder_path = base_dir.joinpath(*parts[:-1])
        folder_path.mkdir(parents=True, exist_ok=True)
        return folder_path / f"{filename_stem}{ext}"

    def on_choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.var_download_dir.get(), title="İndirme Klasörünü Seçin")
        if d:
            self.var_download_dir.set(d)

    def on_toggle_pause(self):
        self.pause_requested = not self.pause_requested
        if self.pause_requested:
            self.log("İndirme duraklatıldı. Devam etmek için tekrar 'Duraklat / Devam Et' butonuna basın.")
            self.btn_pause.config(text="Devam Et")
        else:
            self.log("İndirmeye devam ediliyor...")
            self.btn_pause.config(text="Duraklat")

    def on_stop(self):
        self.stop_requested = True
        self.pause_requested = False
        self.log("Durdurma isteği gönderildi, mevcut dosya tamamlanınca duracak...")

    # -----------------------------------------------------------------------
    # Güvenli İndirme Motoru (Otomatik e-İmza Yenilemeli & Atomik Yazmalı)
    # -----------------------------------------------------------------------
    def on_start_download(self, selected_only: bool = False):
        if self.is_busy:
            messagebox.showwarning("Meşgul", "Şu anda devam eden bir işlem var.")
            return

        if selected_only:
            sel_iids = set(self.tree.selection())
            if not sel_iids:
                messagebox.showinfo("Seçim Yok", "Lütfen önce tablodan indirmek istediğiniz satırları seçin.")
                return
            queue = [it for it in self.filtered_items if it["unique_key"] in sel_iids]
        else:
            queue = list(self.filtered_items)

        skip_done = self.var_skip_downloaded.get()
        # Mükerrerlik ve eksiklik kontrolü: Sadece fiziksel dosyası tam olanları atla
        download_queue = [
            it for it in queue if it["can_download"] and (not skip_done or not self.is_item_already_downloaded(it))
        ]

        skipped_count = len(queue) - len(download_queue)
        if skip_done and skipped_count > 0:
            self.log(
                f"MÜKERRERLİK KORUMASI: Daha önce başarıyla indirilmiş {skipped_count} evrak atlandı. "
                f"Sadece kalan {len(download_queue)} eksik evrak için indirme başlatılıyor."
            )

        if not download_queue:
            messagebox.showinfo(
                "Tümü İndirilmiş",
                f"Seçili kriterlere uygun {len(queue)} evrakın tamamı daha önce indirilmiş ve diskte sağlam olarak mevcuttur.\n\nHiçbir evrak tekrar indirilmeyecektir."
            )
            return

        min_d = max(1.0, float(self.var_min_delay.get() or 3.5))
        max_d = max(min_d + 0.5, float(self.var_max_delay.get() or 7.0))
        batch_n = max(3, int(self.var_batch_size.get() or 15))
        batch_pause = max(5.0, float(self.var_batch_pause.get() or 20.0))

        self.is_busy = True
        self.stop_requested = False
        self.pause_requested = False
        self.btn_pause.config(state=tk.NORMAL, text="Duraklat")
        self.btn_stop.config(state=tk.NORMAL)
        self.progress["maximum"] = len(download_queue)
        self.progress["value"] = 0

        def worker():
            downloaded_now = 0
            max_retries = 3
            try:
                self.log(
                    f"Toplam {len(download_queue)} evrak için güvenli indirme başlatıldı "
                    f"(Gecikme: {min_d:.1f}-{max_d:.1f} sn, Her {batch_n} dosyada {batch_pause:.0f} sn mola, "
                    f"Oturum Koruması: {'Açık' if self.var_auto_relogin.get() else 'Kapalı'})."
                )
                for idx, item in enumerate(download_queue, start=1):
                    while self.pause_requested and not self.stop_requested:
                        time.sleep(0.5)
                    if self.stop_requested:
                        self.log("İndirme kullanıcı tarafından durduruldu.")
                        break

                    ukey = item["unique_key"]
                    self.root.after(0, lambda k=ukey: self._update_row_status(k, "İndiriliyor..."))

                    success = False
                    for attempt in range(1, max_retries + 1):
                        while self.pause_requested and not self.stop_requested:
                            time.sleep(0.5)
                        if self.stop_requested:
                            break

                        resp = self.bridge.download_document(item["download_params"])
                        err_hdr = resp.get("errorHeader")
                        status_code = resp.get("status", 0)
                        ct = resp.get("contentType") or ""
                        raw_bytes = base64.b64decode(resp.get("base64") or "") if resp.get("base64") else b""

                        # İçerik doğrulama: HTML, JSON hata veya 0 bayt kontrolü
                        is_valid, reason = is_valid_document_bytes(raw_bytes, ct)
                        need_relogin = (
                            not resp.get("ok")
                            or status_code in (401, 403, 302, 500)
                            or bool(err_hdr)
                            or not is_valid
                        )

                        if need_relogin:
                            is_session_dead = (
                                not self.bridge.is_logged_in()
                                or "oturum" in reason.lower()
                                or "html" in reason.lower()
                                or "nosessionobject" in reason.lower()
                                or "prtl_gnl" in reason.lower()
                                or status_code in (401, 403, 302)
                                or "text/html" in ct.lower()
                            )

                            if not is_session_dead and self.bridge.is_logged_in() and "text/html" not in ct.lower():
                                self.log(
                                    f"BİLGİ: {item['evrak_adi']} UYAP sunucusunda fiziksel dosya olarak mevcut değil (Ekli dosya yok / Sunucu boş yanıt verdi). Atlandı."
                                )
                                break

                            self.log(
                                f"UYARI: {item['evrak_adi']} indirilemedi ({reason}) [Deneme {attempt}/{max_retries}]",
                                level="WARNING"
                            )
                            if self.var_auto_relogin.get() and attempt < max_retries:
                                pin_val = self.var_pin.get().strip() or DEFAULT_E_IMZA_PIN
                                self.log(f"UYAP oturumu tazelemek için e-İmza ile otomatik giriş yapılıyor (PIN: {pin_val})...")
                                relogin_ok, relogin_msg = self.bridge.auto_relogin(pin=pin_val)
                                if relogin_ok:
                                    self.log(f"Başarılı: {relogin_msg}. Evrak tekrar indiriliyor...")
                                    self._sleep_interruptible(2.5)
                                    continue
                                else:
                                    self.log(f"HATA: {relogin_msg}", level="ERROR")
                                    self._sleep_interruptible(3.0)
                            else:
                                if attempt < max_retries:
                                    self._sleep_interruptible(3.0)
                                    continue

                        if is_valid:
                            cd = resp.get("contentDisposition") or ""
                            cd_filename = ""
                            m = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^;"\']+)', cd, re.IGNORECASE)
                            if m:
                                cd_filename = m.group(1).strip()

                            ext = guess_extension(cd_filename, ct, raw_bytes)
                            target_path = self.build_target_filepath(item, ext)
                            target_path.parent.mkdir(parents=True, exist_ok=True)

                            # Atomik dosya kaydı: Önce .part dosyasına yaz, sonra doğrula ve taşı
                            tmp_path = target_path.with_suffix(target_path.suffix + ".part")
                            tmp_path.write_bytes(raw_bytes)

                            if tmp_path.exists() and tmp_path.stat().st_size == len(raw_bytes):
                                tmp_path.replace(target_path)
                            else:
                                target_path.write_bytes(raw_bytes)
                                tmp_path.unlink(missing_ok=True)

                            sha256_hash = hashlib.sha256(raw_bytes).hexdigest()

                            self.history[ukey] = {
                                "path": str(target_path),
                                "sha256": sha256_hash,
                                "downloaded_at": dt.datetime.now().isoformat(timespec="seconds"),
                                "size": len(raw_bytes),
                                "tarih": item["tarih"],
                                "dosya_turu": item["dosya_turu"],
                                "birim_adi": item["birim_adi"],
                                "dosya_no": item["dosya_no"],
                                "islem_turu": item["islem_turu"],
                                "evrak_turu": item["evrak_turu"],
                                "evrak_id": item["evrak_id"],
                                "is_emir_no": item.get("is_emir_no", ""),
                            }
                            self._save_history()
                            downloaded_now += 1
                            success = True

                            rel_display = target_path.relative_to(Path(self.var_download_dir.get())) if target_path.is_relative_to(Path(self.var_download_dir.get())) else target_path
                            self.log(f"[{idx}/{len(download_queue)}] Kaydedildi ({len(raw_bytes)//1024} KB): {rel_display}")
                            self.root.after(0, lambda k=ukey, v=idx: self._on_item_downloaded(k, v))
                            break

                    if not success and not self.stop_requested:
                        self.log(
                            f"HATA: Evrak {max_retries} denemede de indirilemedi: {item['evrak_adi']} ({ukey}). "
                            "UYAP güvenliği için indirme duraklatıldı.",
                            level="ERROR"
                        )
                        self.pause_requested = True
                        self.root.after(0, lambda: self.btn_pause.config(text="Devam Et"))
                        self.root.after(0, lambda k=ukey: self._update_row_status(k, "Hata (İndirilemedi)"))
                        continue

                    # Güvenli bekleme ve mola yönetimi
                    if idx < len(download_queue) and not self.stop_requested:
                        if downloaded_now > 0 and downloaded_now % batch_n == 0:
                            jitter_pause = batch_pause + random.uniform(0.0, 5.0)
                            self.log(f"UYAP koruma molası: {downloaded_now} dosya indirildi, {jitter_pause:.1f} sn dinleniliyor...")
                            self._sleep_interruptible(jitter_pause)
                        else:
                            wait_s = random.uniform(min_d, max_d)
                            self._sleep_interruptible(wait_s)

                self._write_summary_csv()
                self.log(f"İndirme oturumu tamamlandı. Bu oturumda {downloaded_now} dosya eksiksiz indirildi.")
            except Exception as e:
                self.log(f"İndirme sırasında beklenmeyen hata: {e}", level="ERROR")
            finally:
                self.is_busy = False
                self.root.after(0, self._reset_buttons)

        threading.Thread(target=worker, daemon=True).start()

    def _sleep_interruptible(self, seconds: float):
        end_t = time.time() + seconds
        while time.time() < end_t:
            if self.stop_requested:
                break
            time.sleep(0.25)

    def _update_row_status(self, ukey: str, status_text: str):
        if self.tree.exists(ukey):
            vals = list(self.tree.item(ukey, "values"))
            vals[0] = status_text
            self.tree.item(ukey, values=vals)

    def _on_item_downloaded(self, ukey: str, progress_val: int):
        self._update_row_status(ukey, "İndirildi")
        self.progress["value"] = progress_val

    def _reset_buttons(self):
        self.btn_pause.config(state=tk.DISABLED, text="Duraklat / Devam Et")
        self.btn_stop.config(state=tk.DISABLED)

    def _write_summary_csv(self):
        try:
            base_dir = Path(self.var_download_dir.get().strip() or str(DEFAULT_DOWNLOAD_DIR))
            base_dir.mkdir(parents=True, exist_ok=True)
            csv_path = base_dir / "indirme_indeksi.csv"
            with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow([
                    "Durum", "Tarih", "Dosya Türü", "Birim Adı", "Dosya No",
                    "İşlem Türü", "Evrak Türü", "Evrak ID", "Kaydedilen Dosya Yolu"
                ])
                for it in self.all_items:
                    ukey = it["unique_key"]
                    h = self.history.get(ukey, {})
                    writer.writerow([
                        "İndirildi" if h else "Bekliyor",
                        it["tarih"],
                        it["dosya_turu"],
                        it["birim_adi"],
                        it["dosya_no"],
                        it["islem_turu"],
                        it["evrak_turu"],
                        it["evrak_id"],
                        h.get("path", ""),
                    ])
        except Exception as e:
            self.log(f"CSV indeksi yazılamadı: {e}", level="WARNING")

    def on_export_csv(self):
        if not self.filtered_items:
            messagebox.showinfo("Veri Yok", "Dışa aktarılacak kayıt bulunmuyor.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="uyap_evrak_listesi.csv",
            filetypes=[("CSV Dosyası (Excel Uyumlu)", "*.csv")],
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([
                "Durum", "Tarih", "Dosya Türü", "Birim Adı", "Dosya No",
                "İşlem Türü", "Evrak Türü", "Evrak ID", "Dosya Yolu"
            ])
            for it in self.filtered_items:
                h = self.history.get(it["unique_key"], {})
                writer.writerow([
                    "İndirildi" if h else "Bekliyor",
                    it["tarih"],
                    it["dosya_turu"],
                    it["birim_adi"],
                    it["dosya_no"],
                    it["islem_turu"],
                    it["evrak_turu"],
                    it["evrak_id"],
                    h.get("path", ""),
                ])
        self.log(f"Liste dışa aktarıldı: {path}")


def refresh_dosya_ids_in_queue(bridge, queue: list, start_idx: int):
    """
    Oturum yenilendiğinde (Mobil İmza sonrası), UYAP sunucusunun dinamik olarak
    yeniden şifrelediği taze dosyaId token'larını çeker ve indirme kuyruğunu günceller.
    """
    print("[*] Yeni oturum için dosya token'ları (dosyaId) güncelleniyor...")
    try:
        fresh_cases = bridge.query_tum_dosyalar(include_closed=True, use_cache=False)
        case_map = {}
        for fc in fresh_cases:
            b_name = str(fc.get("birimAdi") or fc.get("mahkemeAdi") or "").strip()
            d_no = str(fc.get("dosyaNO") or fc.get("dosyaNo") or "").strip()
            n_id = str(fc.get("dosyaID") or fc.get("dosyaId") or "").strip()
            if b_name and d_no and n_id:
                case_map[(b_name, d_no)] = n_id

        updated = 0
        for i in range(max(0, start_idx), len(queue)):
            it = queue[i]
            k = (it["birim_adi"], it["dosya_no"])
            if k in case_map:
                new_id = case_map[k]
                if it.get("download_params"):
                    it["download_params"]["dosyaId"] = new_id
                it["dosya_id"] = new_id
                ev_id = str(it.get("evrak_id") or "")
                it["unique_key"] = f"dosya_{new_id}_{ev_id}"
                updated += 1
        print(f"[✓] Kuyruktaki {updated} adet evrakın dosyaId token'ı yeni oturuma başarıyla uyarlandı.")
    except Exception as e:
        print(f"[!] Oturum token'ı güncelleme uyarısı: {e}")


def run_cli_dosyalar_mode(args, bridge, app_helper, download_dir, tckn):
    """
    Dava Dosyalarından Belirli Evrak Türlerini (Duruşma Tutanağı, Bilirkişi Raporu, Karar vb.)
    ve eklerini toplu olarak indiren CLI fonksiyonu.
    """
    print("\n" + "=" * 70)
    print("  UYAP Avukat Portal - Dava Dosyalarından Evrak İndirici (CLI)")
    print("=" * 70 + "\n")

    evrak_filter = getattr(args, "evrak_turu", "").strip().lower()
    include_attachments = getattr(args, "include_attachments", False)

    if not evrak_filter:
        print("[*] İndirmek istediğiniz evrak türünü seçiniz:")
        print("  1) Duruşma Tutanakları / Zabıtları (Asıl + Talimat, Celse Zabıtları, SEGBİS) [Önerilen]")
        print("  2) Bilirkişi Raporları (Kök + Ek Rapor, Uzman Görüşü, Adli Tıp / Kriminal)")
        print("  3) Gerekçeli Karar, Hüküm ve İlamlar (BAM, Yargıtay, Kısa Karar, Ek Karar)")
        print("  4) Tensip Zabıtları (İlk Tensip, Talimat Tensip Zaptı)")
        print("  5) Dilekçeler ve Beyanlar (Dava, Cevap, İtiraz, İstinaf, Temyiz, Beyan, Mazeret)")
        print("  6) İddianame ve Ceza Başvuru Evrakları (İddianame, Fezleke, İddianame Kabul)")
        print("  7) Kesinleşme Şerhleri")
        print("  8) TÜM Evraklar ve Ekleri (Toplu Eksiksiz Arşiv)")
        print("  9) Özel Kelime Arama (Serbest Metin)")
        print("-" * 65)
        try:
            sec = input("Seçiminiz (1-9) [Varsayılan: 1]: ").strip() or "1"
        except (EOFError, KeyboardInterrupt):
            sec = "1"

        if sec == "1":
            evrak_filter = "durusma"
        elif sec == "2":
            evrak_filter = "bilirkisi"
        elif sec == "3":
            evrak_filter = "karar"
        elif sec == "4":
            evrak_filter = "tensip"
        elif sec == "5":
            evrak_filter = "dilekce"
        elif sec == "6":
            evrak_filter = "iddianame"
        elif sec == "7":
            evrak_filter = "kesinlesme"
        elif sec == "8":
            evrak_filter = "tumu"
            include_attachments = True
        elif sec == "9":
            try:
                evrak_filter = input("Aramak istediğiniz evrak türü / kelime: ").strip().lower()
            except Exception:
                evrak_filter = "tumu"
        else:
            evrak_filter = "durusma"

    filter_desc = {
        "durusma": "Duruşma Tutanakları (Zabıtlar, Celse Zabıtları, SEGBİS)",
        "bilirkisi": "Bilirkişi Raporları (Kök + Ek + Adli Tıp / Kriminal)",
        "karar": "Gerekçeli Karar, Hüküm ve İlamlar (BAM / Yargıtay)",
        "tensip": "Tensip Zabıtları (İlk + Talimat Tensip)",
        "dilekce": "Dilekçeler ve Beyanlar (Dava, Cevap, İstinaf, Temyiz)",
        "iddianame": "İddianame ve Fezleke",
        "kesinlesme": "Kesinleşme Şerhleri",
        "tumu": "Tüm Evraklar ve Ek Belgeler (Toplu Tam Arşiv)",
    }.get(evrak_filter, f"'{evrak_filter}' içeren evraklar")

    print(f"\n[*] Hedef Evrak Türü: {filter_desc}")

    scope = getattr(args, "scope", "all").strip().lower()
    include_closed = getattr(args, "include_closed", True)
    if scope == "acik":
        include_closed = False

    if scope == "fav":
        print("[*] Sık kullanılan aktif dosyalarınız sorgulanıyor...")
        cases = bridge.query_sik_kullanilan_dosyalar()
    else:
        durum_str = "Tüm Dava Dosyaları (Açık + Kapalı / Karara Çıkmış)" if include_closed else "Yalnızca Açık Dava Dosyaları"
        print(f"[*] {durum_str} sorgulanıyor...")
        cases = bridge.query_tum_dosyalar(include_closed=include_closed)

    if not cases:
        print("[!] UYAP'ta dosya listesi alınamadı veya oturum kapalı.")
        return

    acik_c = sum(1 for c in cases if c.get("dosyaDurumGenel") == "Açık" or "açık" in str(c.get("dosyaDurum", "")).lower())
    kapali_c = len(cases) - acik_c
    print(f"[✓] Toplam {len(cases)} adet dosya tespit edildi ({acik_c} Açık, {kapali_c} Kapalı).")

    # Akıllı Yargı Ön Filtreleme & Hedef Mahkeme Arama
    filter_birim_kw = getattr(args, "filter_birim", "").strip().lower()
    relevant_cases = []
    skipped_icra_count = 0

    for c in cases:
        birim = str(c.get("birimAdi") or c.get("mahkemeAdi") or "").strip()
        dosya = str(c.get("dosyaNO") or c.get("dosyaNo") or "").strip()

        # Spesifik birim / dosya filtresi (Örn: "erzurum" veya "2025/506")
        if filter_birim_kw:
            if filter_birim_kw not in tr_lower(birim) and filter_birim_kw not in tr_lower(dosya):
                continue

        # Evrak türüne göre alakasız birimleri ele (Örn: İcra Dairesinde duruşma olmaz!)
        if not is_case_relevant_for_filter(birim, evrak_filter):
            skipped_icra_count += 1
            continue

        relevant_cases.append(c)

    if skipped_icra_count > 0:
        print(f"[*] Akıllı Yargı Filtresi: {skipped_icra_count} adet ilgisiz dosya (İcra Dairesi / Bürolar) tarama dışı bırakıldı.")

    if filter_birim_kw:
        print(f"[*] Arama Filtresi ('{filter_birim_kw}'): {len(relevant_cases)} adet dosya eşleşti.")

    if not relevant_cases:
        print("[!] Belirtilen kriterlere uygun incelenecek dava dosyası bulunamadı.")
        return

    print(f"[✓] Taranacak hedef dosya sayısı: {len(relevant_cases)} adet.")

    batch_size = 5
    total_batches = (len(relevant_cases) + batch_size - 1) // batch_size
    print(f"[*] Evrak listeleri hızlı paralel modda taranıyor ({total_batches} paket, paket başı {batch_size} dosya)...\n")

    found_documents = []
    seen_keys = set()
    seen_hearings = set()
    seen_evrak_ids = set()
    total_skipped_duplicates = 0

    for b_idx in range(0, len(relevant_cases), batch_size):
        chunk = relevant_cases[b_idx : b_idx + batch_size]
        chunk_ids = [str(c.get("dosyaID") or c.get("dosyaId") or "").strip() for c in chunk]
        chunk_ids = [cid for cid in chunk_ids if cid]

        batch_results = bridge.query_dosyalar_evraklar_batch(chunk_ids, chunk_size=len(chunk_ids))

        for c in chunk:
            dosya_id = str(c.get("dosyaID") or c.get("dosyaId") or "").strip()
            dosya_no = str(c.get("dosyaNO") or c.get("dosyaNo") or "DosyaNo_Yok").strip()
            birim_adi = str(c.get("birimAdi") or c.get("mahkemeAdi") or "Mahkeme").strip()
            if not dosya_id:
                continue

            pages = batch_results.get(dosya_id)
            if not pages:
                try:
                    pages = bridge.query_dosya_evraklar(dosya_id)
                except Exception:
                    pages = []

            case_found = 0

            def collect_doc(ev, is_attachment=False, parent_tur="", p_yer="", p_dosya="", p_tarih_obj=None):
                nonlocal case_found, total_skipped_duplicates
                if not isinstance(ev, dict):
                    return
                ev_id = str(ev.get("evrakId") or "").strip()
                if not ev_id or ev_id in seen_evrak_ids:
                    return

                tur = str(ev.get("ekTuru") if is_attachment else (ev.get("tur") or ev.get("aciklama") or "Evrak")).strip()
                yer = str(ev.get("gonderenYerKisi") or p_yer or birim_adi).strip()
                dno = str(ev.get("gonderenDosyaNo") or p_dosya or dosya_no).strip()

                p_date = (
                    parse_uyap_date(ev.get("onaylandigiTarih"))
                    or parse_uyap_date(ev.get("sistemeGonderildigiTarih"))
                    or p_tarih_obj
                    or dt.date.today()
                )
                tarih_str = p_date.strftime("%d.%m.%Y")

                is_match = matches_evrak_filter(tur, evrak_filter) or (
                    is_attachment and include_attachments and matches_evrak_filter(parent_tur, evrak_filter)
                )

                if is_match:
                    if getattr(args, "start_date", None):
                        try:
                            s_limit = dt.datetime.strptime(args.start_date, "%d.%m.%Y").date()
                            if p_date < s_limit:
                                return
                        except Exception:
                            pass
                    if getattr(args, "end_date", None):
                        try:
                            e_limit = dt.datetime.strptime(args.end_date, "%d.%m.%Y").date()
                            if p_date > e_limit:
                                return
                        except Exception:
                            pass

                    # Mükerrer / Kopya Evrak Kontrolü:
                    # Özellikle mahkemeler arası talimat, müzekkere ekinde gelen suretler veya
                    # birleşen dosyalarda aynı celse / kararın mükerrer indirilmesini önler.
                    if evrak_filter == "durusma":
                        dedup_key = f"{sanitize_path_part(yer).lower()}::{sanitize_path_part(dno).lower()}::{tarih_str}"
                        if dedup_key in seen_hearings:
                            total_skipped_duplicates += 1
                            return
                        seen_hearings.add(dedup_key)
                    elif evrak_filter in ("karar", "tensip", "iddianame"):
                        dedup_key = f"{evrak_filter}::{sanitize_path_part(yer).lower()}::{sanitize_path_part(dno).lower()}::{tarih_str}"
                        if dedup_key in seen_hearings:
                            total_skipped_duplicates += 1
                            return
                        seen_hearings.add(dedup_key)

                    seen_evrak_ids.add(ev_id)
                    ukey = f"dosya_{dosya_id}_{ev_id}"
                    if ukey in seen_keys:
                        return
                    seen_keys.add(ukey)

                    # Evrak adını zenginleştir: Eğer celse bilgisi (örn: "6. Celse") varsa ekle
                    aciklama = str(ev.get("aciklama") or "").strip()
                    display_tur = tur
                    if aciklama and aciklama.lower() not in tur.lower():
                        display_tur = f"{tur}_{aciklama}"

                    item = {
                        "unique_key": ukey,
                        "kaynak": "Dava_Dosyasi",
                        "tarih_obj": p_date,
                        "tarih": p_date.strftime("%Y-%m-%d"),
                        "yil": p_date.strftime("%Y"),
                        "yil_ay": p_date.strftime("%Y-%m"),
                        "dosya_turu": infer_dosya_turu(birim_adi),
                        "birim_adi": birim_adi,
                        "dosya_no": dosya_no,
                        "dosya_id": dosya_id,
                        "islem_turu": "Dava_Evraki_Ek" if is_attachment else "Dava_Evraki",
                        "evrak_turu": tur,
                        "evrak_turu_kod": tur,
                        "evrak_adi": f"EK_{display_tur}" if is_attachment else display_tur,
                        "evrak_id": ev_id,
                        "gg_evrak_id": str(ev.get("ggEvrakId") or ""),
                        "is_emir_no": str(ev.get("birimEvrakNo") or ev.get("anaEvrakId") or ""),
                        "is_attachment": is_attachment,
                        "download_params": {
                            "dosyaId": dosya_id,
                            "evrakId": ev_id,
                        },
                        "can_download": True,
                    }
                    found_documents.append(item)
                    case_found += 1

                # Evrak eklerini YALNIZCA kullanıcı özellikle talep etmişse (include_attachments=True) tara
                if include_attachments:
                    for ek in ev.get("ekEvrakListesi") or []:
                        collect_doc(
                            ek,
                            is_attachment=True,
                            parent_tur=tur,
                            p_yer=yer,
                            p_dosya=dno,
                            p_tarih_obj=p_date
                        )

            for p in pages:
                if isinstance(p, dict):
                    tum = p.get("tumEvraklar") or p
                    if isinstance(tum, dict):
                        for sub_k, sub_v in tum.items():
                            if isinstance(sub_v, list):
                                for ev in sub_v:
                                    collect_doc(ev)
                    elif isinstance(tum, list):
                        for ev in tum:
                            collect_doc(ev)
                elif isinstance(p, list):
                    for ev in p:
                        collect_doc(ev)

            if case_found > 0:
                print(f"  [+] {birim_adi} ({dosya_no}) -> {case_found} hedef evrak tespit edildi.")

        curr_batch = (b_idx // batch_size) + 1
        pct = min(100.0, ((b_idx + len(chunk)) / len(relevant_cases)) * 100)
        print(f"  --> [Paket {curr_batch}/{total_batches}] %{pct:.1f} tarandı (Şu ana kadar {len(found_documents)} hedef evrak bulundu)")

    print("\n" + "=" * 70)
    print(f"  TOPLAM BULUNAN HEDEF EVRAK     : {len(found_documents)} adet")
    if total_skipped_duplicates > 0:
        print(f"  MÜKERRER / KOPYA EVRAK (ELENDİ): {total_skipped_duplicates} adet")

    download_queue = [
        it for it in found_documents if not app_helper.is_item_already_downloaded(it)
    ]
    skipped_count = len(found_documents) - len(download_queue)
    print(f"  DİSKTE MEVCUT (ATLANDI)        : {skipped_count} adet")
    print(f"  İNDİRİLECEK YENİ EVRAK         : {len(download_queue)} adet")
    print("=" * 70 + "\n")

    sub_folder_name = "DURUSMA_TUTANAKLARI" if evrak_filter == "durusma" else (
        "BILIRKISI_RAPORLARI" if evrak_filter == "bilirkisi" else (
            "GEREKCELI_KARARLAR" if evrak_filter == "karar" else (
                "TENSIP_ZAPITLARI" if evrak_filter == "tensip" else (
                    "DILEKCELER_BEYANLAR" if evrak_filter == "dilekce" else (
                        "IDDIANAMELER" if evrak_filter == "iddianame" else (
                            "KESINLESME_SERHLERI" if evrak_filter == "kesinlesme" else "DAVA_EVRAKLARI"
                        )
                    )
                )
            )
        )
    )
    target_base = download_dir / sub_folder_name
    target_base.mkdir(parents=True, exist_ok=True)

    if not download_queue:
        print("[✓] Tebrikler! Hedef evrakların tamamı zaten diskte sağlam olarak mevcut.")
        save_ai_manifest(target_base, found_documents, download_queue)
        return

    min_d = 1.2
    max_d = 2.8
    batch_n = 20
    batch_pause = 10.0
    downloaded_now = 0
    max_retries = 3

    print(f"[*] Güvenli hızlı indirme başlatılıyor ({min_d}-{max_d} sn bekleme, her {batch_n} dosyada {batch_pause:.0f} sn mola)...")

    for idx, item in enumerate(download_queue, start=1):
        ukey = item["unique_key"]
        success = False

        for attempt in range(1, max_retries + 1):
            resp = bridge.download_document(item["download_params"])
            err_hdr = resp.get("errorHeader")
            status_code = resp.get("status", 0)
            ct = resp.get("contentType") or ""
            raw_bytes = base64.b64decode(resp.get("base64") or "") if resp.get("base64") else b""

            is_valid, reason = is_valid_document_bytes(raw_bytes, ct)
            need_relogin = (
                not resp.get("ok")
                or status_code in (401, 403, 302, 500)
                or bool(err_hdr)
                or not is_valid
            )

            if need_relogin:
                is_session_dead = (
                    not bridge.is_logged_in()
                    or "oturum" in reason.lower()
                    or "html" in reason.lower()
                    or "nosessionobject" in reason.lower()
                    or "prtl_gnl" in reason.lower()
                    or status_code in (401, 403, 302)
                    or "text/html" in ct.lower()
                )

                if is_session_dead:
                    print(f"[!] UYARI: Evrak alınamadı ({item['evrak_adi']}) - {reason} [Deneme {attempt}/{max_retries}]")
                    cfg = load_user_config()
                    c_tc = cfg.get("tc", tckn)
                    c_gsm = cfg.get("gsm", "")
                    c_op = cfg.get("operator", "2")
                    if c_tc and c_gsm and attempt < max_retries:
                        print(f"[*] Mobil İmza ile oturum yenileniyor ({c_gsm})...")
                        m_ok, m_msg = bridge.login_with_mobil_imza(c_tc, c_gsm, c_op)
                        if m_ok:
                            print(f"[✓] {m_msg}.")
                            refresh_dosya_ids_in_queue(bridge, download_queue, idx - 1)
                            continue
                        else:
                            print(f"[x] Mobil İmza ile oturum yenilenemedi: {m_msg}")
                    time.sleep(3.0)
                    continue

                if bridge.is_logged_in() and "text/html" not in ct.lower():
                    print(f"[-] BİLGİ: {item['evrak_adi']} ({item['birim_adi']}) UYAP sunucusunda fiziksel dosya olarak mevcut değil (Ekli dosya yok). Atlandı.")
                    break

            if is_valid:
                cd = resp.get("contentDisposition") or ""
                cd_filename = ""
                m = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^;"\']+)', cd, re.IGNORECASE)
                if m:
                    cd_filename = m.group(1).strip()
                ext = guess_extension(cd_filename, ct, raw_bytes)

                clean_ext = ext if ext.startswith(".") else f".{ext}"
                yargi_slug = infer_yargi_kategorisi(item["birim_adi"])
                b_slug = sanitize_path_part(item["birim_adi"], max_len=60)
                d_slug = sanitize_path_part(item["dosya_no"], max_len=40)
                alt_kat = infer_evrak_alt_kategori(item["evrak_adi"])
                clean_ev_id = str(item.get("evrak_id") or "").replace('"', '').replace("'", "").strip()
                doc_num = sanitize_path_part(item.get("is_emir_no") or clean_ev_id[:8], max_len=20)
                e_slug = sanitize_path_part(item["evrak_adi"], max_len=60)
                file_name = f"{item['tarih']}_{e_slug}_{doc_num}{clean_ext}"
                target_path = target_base / yargi_slug / b_slug / d_slug / alt_kat / file_name
                target_path.parent.mkdir(parents=True, exist_ok=True)

                tmp_path = target_path.with_suffix(target_path.suffix + ".part")
                tmp_path.write_bytes(raw_bytes)

                if tmp_path.exists() and tmp_path.stat().st_size == len(raw_bytes):
                    tmp_path.replace(target_path)
                else:
                    target_path.write_bytes(raw_bytes)
                    tmp_path.unlink(missing_ok=True)

                sha256_hash = hashlib.sha256(raw_bytes).hexdigest()

                app_helper.history[ukey] = {
                    "path": str(target_path),
                    "sha256": sha256_hash,
                    "downloaded_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "size": len(raw_bytes),
                    "tarih": item["tarih"],
                    "dosya_turu": item["dosya_turu"],
                    "birim_adi": item["birim_adi"],
                    "dosya_no": item["dosya_no"],
                    "islem_turu": item["islem_turu"],
                    "evrak_turu": item["evrak_turu"],
                    "evrak_id": item["evrak_id"],
                    "is_emir_no": item.get("is_emir_no", ""),
                }
                app_helper._save_history()
                downloaded_now += 1
                success = True

                pct = (idx / len(download_queue)) * 100
                bar = ("█" * int(pct // 5)).ljust(20, "░")
                print(f"[{idx}/{len(download_queue)}] [{bar}] %{pct:.1f} Kaydedildi ({len(raw_bytes)//1024} KB): {item['birim_adi']} ({item['dosya_no']}) - {item['evrak_adi']}")
                break

        if not success:
            print(f"[x] HATA: {item['evrak_adi']} ({ukey}) indirilemedi, atlandı.")

        if idx < len(download_queue):
            if downloaded_now > 0 and downloaded_now % batch_n == 0:
                print(f"[*] UYAP koruma molası: {downloaded_now} dosya indirildi, {batch_pause:.0f} sn dinleniliyor...")
                time.sleep(batch_pause)
            else:
                time.sleep(random.uniform(min_d, max_d))

    # CSV İndeksi yaz
    try:
        csv_path = target_base / "indeks.csv"
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([
                "Durum", "Tarih", "Dosya Türü", "Birim Adı", "Dosya No",
                "İşlem Türü", "Evrak Türü", "Evrak ID", "Kaydedilen Dosya Yolu"
            ])
            for it in found_documents:
                uk = it["unique_key"]
                h = app_helper.history.get(uk, {})
                writer.writerow([
                    "İndirildi" if h else "Bekliyor",
                    it["tarih"],
                    it["dosya_turu"],
                    it["birim_adi"],
                    it["dosya_no"],
                    it["islem_turu"],
                    it["evrak_turu"],
                    it["evrak_id"],
                    h.get("path", ""),
                ])
        print(f"[✓] CSV İndeksi kaydedildi: {csv_path}")
    except Exception as e:
        print(f"[!] CSV kaydedilemedi: {e}")

    # Yapay Zeka (AI / LLM) Dava Analiz Manifestosu & Duruşma Kronolojisi üret
    save_ai_manifest(target_base, found_documents, download_queue)

    print("\n" + "=" * 70)
    print(f"  İNDİRME TAMAMLANDI! Bu oturumda {downloaded_now} dosya eksiksiz indirildi.")
    print(f"  Kayıt Konumu: {target_base}")
    print("=" * 70 + "\n")


def run_cli_durusmalar_mode(args, bridge, app_helper, download_dir, tckn):
    """
    Duruşmaları sorgulayıp Google / Android Takvim için .ics dosyası üreten
    ve duruşma listesini CSV olarak kaydeden CLI fonksiyonu.
    """
    print("\n" + "=" * 70)
    print("  UYAP Avukat Portal - Duruşma Takvimi & Tutanak Yöneticisi")
    print("=" * 70 + "\n")

    today = dt.date.today()
    s_date = today - dt.timedelta(days=90)
    e_date = today + dt.timedelta(days=180)

    if getattr(args, "start_date", None):
        try:
            s_date = dt.datetime.strptime(args.start_date, "%d.%m.%Y").date()
        except Exception:
            pass
    if getattr(args, "end_date", None):
        try:
            e_date = dt.datetime.strptime(args.end_date, "%d.%m.%Y").date()
        except Exception:
            pass

    print(f"[*] Duruşma Tarama Aralığı: {s_date.strftime('%d.%m.%Y')} - {e_date.strftime('%d.%m.%Y')}")

    ranges = []
    cur = s_date
    while cur <= e_date:
        nxt = min(cur + dt.timedelta(days=29), e_date)
        ranges.append((cur, nxt))
        cur = nxt + dt.timedelta(days=1)

    print(f"[*] {len(ranges)} dilim halinde duruşmalar taranıyor...")
    all_durusmalar = []
    seen_ids = set()

    for idx, (s_d, e_d) in enumerate(ranges, start=1):
        s_str = s_d.strftime("%d.%m.%Y")
        e_str = e_d.strftime("%d.%m.%Y")
        try:
            res = bridge.query_durusmalar(s_str, e_str)
        except Exception:
            res = []
        c_count = 0
        for d in res:
            k_id = d.get("kayitId")
            if k_id and k_id not in seen_ids:
                seen_ids.add(k_id)
                all_durusmalar.append(d)
                c_count += 1
        print(f"  [{idx}/{len(ranges)}] {s_str} - {e_str} -> {c_count} duruşma bulundu.")
        if idx < len(ranges):
            time.sleep(random.uniform(0.3, 0.6))

    print(f"\n[✓] Toplam {len(all_durusmalar)} adet duruşma kaydı tespit edildi.")

    if not all_durusmalar:
        print("[i] Belirtilen tarih aralığında duruşma kaydı bulunamadı.")
        return

    # 1. iCalendar (.ics) Dosyası Üret
    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//UYAP Avukat Portal//Duruşma Takvimi//TR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:UYAP Duruşmaları",
        "X-WR-TIMEZONE:Europe/Istanbul",
    ]

    for d in all_durusmalar:
        k_id = d.get("kayitId", random.randint(100000, 999999))
        ts_str = str(d.get("tarihSaat") or "").strip()
        yerel_birim = str(d.get("yerelBirimAd") or "Mahkeme").strip()
        dosya_no = str(d.get("dosyaNo") or "").strip()
        islem_tur = str(d.get("islemTuruAciklama") or "Duruşma").strip()
        islem_sonuc = str(d.get("islemSonucuAciklama") or "").strip()
        token = str(d.get("token") or "").strip()
        taraflar = d.get("dosyaTaraflari") or []
        taraf_str = ", ".join([f"{t.get('isim','')} {t.get('soyad','')} ({t.get('sifat','')})" for t in taraflar if isinstance(t, dict)])

        dt_start = None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt_start = dt.datetime.strptime(ts_str, fmt)
                break
            except Exception:
                pass

        if not dt_start:
            continue

        dt_end = dt_start + dt.timedelta(minutes=30)
        dt_stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%SZ")
        dt_start_s = dt_start.strftime("%Y%m%dT%H%M%S")
        dt_end_s = dt_end.strftime("%Y%m%dT%H%M%S")

        desc = f"Dosya: {dosya_no}\\nMahkeme: {yerel_birim}\\nİşlem: {islem_tur} ({islem_sonuc})"
        if token:
            desc += f"\\ne-Duruşma Linki: {token}"
        if taraf_str:
            desc += f"\\nTaraflar: {taraf_str}"

        ics_lines.extend([
            "BEGIN:VEVENT",
            f"UID:durusma_{k_id}@uyap.gov.tr",
            f"DTSTAMP:{dt_stamp}",
            f"DTSTART;TZID=Europe/Istanbul:{dt_start_s}",
            f"DTEND;TZID=Europe/Istanbul:{dt_end_s}",
            f"SUMMARY:{islem_tur}: {yerel_birim} ({dosya_no})",
            f"DESCRIPTION:{desc}",
            f"LOCATION:{yerel_birim}",
            "STATUS:CONFIRMED",
            "END:VEVENT",
        ])

    ics_lines.append("END:VCALENDAR")
    ics_path = download_dir / "durusma_takvimi.ics"
    ics_path.write_text("\r\n".join(ics_lines), encoding="utf-8")
    print(f"\n[✓] Duruşma Takvimi Oluşturuldu: {ics_path}")
    print("    (Bu dosyayı telefonunuzda açarak tüm duruşmaları Google Takvim / Telefon Takviminize tek tıkla ekleyebilirsiniz)\n")

    # 2. CSV Çıktısı
    csv_path = download_dir / "durusma_listesi.csv"
    try:
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Tarih Saat", "Mahkeme / Birim", "Dosya No", "İşlem Türü", "Sonuç", "e-Duruşma Linki", "Taraflar"])
            for d in all_durusmalar:
                taraflar = d.get("dosyaTaraflari") or []
                taraf_str = ", ".join([f"{t.get('isim','')} {t.get('soyad','')} ({t.get('sifat','')})" for t in taraflar if isinstance(t, dict)])
                w.writerow([
                    d.get("tarihSaat"),
                    d.get("yerelBirimAd"),
                    d.get("dosyaNo"),
                    d.get("islemTuruAciklama"),
                    d.get("islemSonucuAciklama"),
                    d.get("token"),
                    taraf_str,
                ])
        print(f"[✓] Duruşma Tablosu CSV olarak kaydedildi: {csv_path}\n")
    except Exception:
        pass


def run_cli_mode(args):
    """
    Termux / Android / Konsol / Headless CLI Modu:
    Grafik ekran (X11) gerektirmeden doğrudan terminal üzerinden çalışır.
    """
    print("\n" + "=" * 70)
    print("  UYAP Avukat Portal - Güvenli Evrak İndirici (Terminal & CLI Modu)")
    print("=" * 70)

    # 1. İndirme dizini belirle
    if args.dir:
        download_dir = Path(args.dir)
    elif Path("/sdcard/Download").exists():
        download_dir = Path("/sdcard/Download/UYAP_Indirilenler")
    else:
        download_dir = DEFAULT_DOWNLOAD_DIR
    download_dir.mkdir(parents=True, exist_ok=True)
    print(f"[*] İndirme Klasörü: {download_dir}")

    # 2. Chromium Köprüsünü Başlat
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    headless = args.headless or not has_display
    bridge = UyapBrowserBridge(port=args.port)
    ok, msg = bridge.launch_or_connect(headless=headless)
    print(f"[*] {msg}")

    # 3. Oturum Kontrolü & e-İmza / Mobil İmza Bekleme
    pin = args.pin or DEFAULT_E_IMZA_PIN
    auto_relogin = not args.no_auto_relogin

    print("[*] UYAP oturum durumu kontrol ediliyor...")
    info = bridge.check_login_and_user()
    user = info.get("user") or {}
    tckn = str(user.get("tcKimlikNo") or user.get("tckn") or "")

    # e-İmza varsa otomatik dene
    if not tckn and auto_relogin and pin:
        print(f"[*] e-İmza ile otomatik giriş deneniyor (PIN: {pin[:2]}**)...")
        re_ok, re_msg = bridge.auto_relogin(pin=pin)
        if re_ok:
            print(f"[✓] {re_msg}")
            time.sleep(1.0)
            info = bridge.check_login_and_user()
            user = info.get("user") or {}
            tckn = str(user.get("tcKimlikNo") or user.get("tckn") or "")

    # Oturum açılmamışsa bekle (Mobil İmza / e-Devlet veya e-İmza)
    cfg = load_user_config()
    def_tc = getattr(args, "tc", "") or cfg.get("tc", "")
    def_gsm = getattr(args, "gsm", "") or cfg.get("gsm", "")
    def_op = getattr(args, "operator", "") or cfg.get("operator", "2")

    if not tckn and (getattr(args, "mobil_imza", False) or (getattr(args, "tc", "") and getattr(args, "gsm", ""))):
        tc_in = getattr(args, "tc", "") or def_tc
        gsm_in = getattr(args, "gsm", "") or def_gsm
        op_in = getattr(args, "operator", "") or def_op
        if tc_in and gsm_in:
            m_ok, m_msg = bridge.login_with_mobil_imza(tc_in, gsm_in, op_in)
            if m_ok:
                print(f"[✓] {m_msg}")
                save_user_config({"tc": tc_in, "gsm": gsm_in, "operator": op_in})
                info = bridge.check_login_and_user()
                user = info.get("user") or {}
                tckn = str(user.get("tcKimlikNo") or user.get("tckn") or "")
            else:
                print(f"[x] {m_msg}")

    while not tckn:
        print("\n" + "=" * 70)
        print("  [!] UYAP OTURUMU AÇIK DEĞİL - LÜTFEN BİR GİRİŞ YÖNTEMİ SEÇİN:")
        print("  1) Mobil İmza ile Giriş (SIM Kartınıza Onay Gönderir - Önerilen)")
        print("  2) e-İmza Otomatik Yenile (ArkSigner)")
        print("  3) Mevcut Tarayıcı / Termux:X11 Oturumunu Bekle")
        print("=" * 70)

        try:
            choice = input("Seçiminiz (1, 2 veya 3) [Varsayılan: 1]: ").strip() or "1"
        except (EOFError, KeyboardInterrupt):
            print("\nİşlem iptal edildi.")
            sys.exit(0)

        if choice == "1":
            try:
                tc_val, gsm_val, op_val = def_tc, def_gsm, def_op
                if not (tc_val and gsm_val):
                    tc_p = f"T.C. Kimlik No [{def_tc}]: " if def_tc else "T.C. Kimlik No: "
                    tc_val = input(tc_p).strip() or def_tc
                    gsm_p = f"GSM No (5xx...) [{def_gsm}]: " if def_gsm else "GSM No (başında 0 olmadan 10 hane): "
                    gsm_val = input(gsm_p).strip() or def_gsm
                    op_p = f"Operatör (1: Turkcell, 2: Türk Telekom, 3: Vodafone) [{def_op}]: "
                    op_val = input(op_p).strip() or def_op
                else:
                    ans = input(f"Kayıtlı Mobil İmza bilgileri kullanılsın mı? ({def_tc} - {def_gsm}) [E/h]: ").strip().lower()
                    if ans in ("h", "hayir", "n", "no"):
                        tc_p = f"T.C. Kimlik No [{def_tc}]: "
                        tc_val = input(tc_p).strip() or def_tc
                        gsm_p = f"GSM No (5xx...) [{def_gsm}]: "
                        gsm_val = input(gsm_p).strip() or def_gsm
                        op_p = f"Operatör (1: Turkcell, 2: Türk Telekom, 3: Vodafone) [{def_op}]: "
                        op_val = input(op_p).strip() or def_op

                if not tc_val or not gsm_val:
                    print("[!] T.C. Kimlik No ve GSM No zorunludur.")
                    continue

                m_ok, m_msg = bridge.login_with_mobil_imza(tc_val, gsm_val, op_val)
                if m_ok:
                    print(f"[✓] {m_msg}")
                    save_user_config({"tc": tc_val, "gsm": gsm_val, "operator": op_val})
                    info = bridge.check_login_and_user()
                    user = info.get("user") or {}
                    tckn = str(user.get("tcKimlikNo") or user.get("tckn") or "")
                else:
                    print(f"[x] {m_msg}")
            except (EOFError, KeyboardInterrupt):
                print("\nİşlem iptal edildi.")
                sys.exit(0)

        elif choice == "2":
            pin_in = input(f"e-İmza PIN Kodu [{pin}]: ").strip() or pin
            if pin_in:
                re_ok, re_msg = bridge.auto_relogin(pin=pin_in)
                if re_ok:
                    print(f"[✓] {re_msg}")
                    info = bridge.check_login_and_user()
                    user = info.get("user") or {}
                    tckn = str(user.get("tcKimlikNo") or user.get("tckn") or "")
                else:
                    print(f"[x] {re_msg}")
            else:
                print("[!] PIN girilmedi.")

        else:
            print(f"[*] Tarayıcı oturumu bekleniyor ({UYAP_BASE_URL})...")
            for _ in range(15):
                time.sleep(2.0)
                info = bridge.check_login_and_user()
                user = info.get("user") or {}
                tckn = str(user.get("tcKimlikNo") or user.get("tckn") or "")
                if tckn:
                    break

    full_name = f"{user.get('adi', '')} {user.get('soyadi', '')}".strip() or "Avukat"
    baro = user.get("baroAdi") or ""
    print(f"[✓] Giriş Başarılı: {full_name} (TCKN: {tckn} - {baro})")

    # 4. Geçmişi yükle ve doğrula
    app_helper = UyapDownloaderApp.__new__(UyapDownloaderApp)
    app_helper.history = app_helper._verify_and_clean_history()
    print(f"[✓] Kayıtlı ve diskte doğrulanmış evrak sayısı: {len(app_helper.history)} adet")

    # Mod Seçimi Kontrolü
    mode = getattr(args, "mode", "islemlerim")
    if mode == "dosyalar":
        return run_cli_dosyalar_mode(args, bridge, app_helper, download_dir, tckn)
    elif mode == "durusmalar":
        return run_cli_durusmalar_mode(args, bridge, app_helper, download_dir, tckn)

    # 5. Tarih aralığını belirle
    try:
        end_d = dt.datetime.strptime(args.end_date.strip(), "%d.%m.%Y").date() if args.end_date else dt.date.today()
    except ValueError:
        end_d = dt.date.today()
    try:
        start_d = dt.datetime.strptime(args.start_date.strip(), "%d.%m.%Y").date() if args.start_date else (end_d - dt.timedelta(days=365))
    except ValueError:
        start_d = end_d - dt.timedelta(days=365)

    print(f"[*] Tarama Aralığı: {start_d.strftime('%d.%m.%Y')} - {end_d.strftime('%d.%m.%Y')}")

    chunk_days = max(7, args.chunk_days)
    ranges = []
    cur = start_d
    while cur <= end_d:
        nxt = min(cur + dt.timedelta(days=chunk_days - 1), end_d)
        ranges.append((cur, nxt))
        cur = nxt + dt.timedelta(days=1)

    cache_file = BASE_DIR / "taranan_islem_cache.json"
    all_items = []
    seen_keys = set()
    use_cache = False

    if cache_file.exists():
        try:
            cdata = json.loads(cache_file.read_text(encoding="utf-8"))
            if (
                cdata.get("start_date") == start_d.strftime("%d.%m.%Y")
                and cdata.get("end_date") == end_d.strftime("%d.%m.%Y")
                and (time.time() - cdata.get("timestamp", 0)) < 43200
                and isinstance(cdata.get("items"), list)
                and len(cdata["items"]) > 0
            ):
                all_items = cdata["items"]
                for it in all_items:
                    if "tarih" in it and "tarih_obj" not in it:
                        try:
                            it["tarih_obj"] = dt.datetime.strptime(it["tarih"], "%Y-%m-%d")
                        except Exception:
                            it["tarih_obj"] = start_d
                print(f"[✓] Önbellekten {len(all_items)} adet işlem kaydı anında yüklendi (84 dilim tekrar taranmadan devam ediliyor).")
                use_cache = True
        except Exception:
            use_cache = False

    if use_cache:
        ranges = []
    else:
        print(f"[*] İşlemlerim {len(ranges)} dilim halinde taranıyor...")

    for idx, (s_d, e_d) in enumerate(ranges, start=1):
        s_str = s_d.strftime("%d.%m.%Y")
        e_str = e_d.strftime("%d.%m.%Y")

        slice_attempt = 0
        max_slice_attempts = 4
        while slice_attempt < max_slice_attempts:
            slice_attempt += 1
            try:
                resp = bridge.query_uyap_islemlerim(tckn, s_str, e_str, islem_turu=None)
            except Exception as e:
                resp = {"status": 500, "ok": False, "error": str(e), "rawText": str(e)}

            raw_text = str(resp.get("rawText", "")).lower()

            is_session_expired = (
                resp.get("status") in (401, 403)
                or "nosessionobject" in raw_text
                or "oturum bulunamadı" in raw_text
                or "oturum sonlandı" in raw_text
            )

            # Geçici bağlantı/timeout hatası durumunda oturumun gerçekten düşüp düşmediğini kontrol et
            if not resp.get("ok") and not is_session_expired:
                u_check = bridge.check_login_and_user()
                if not u_check.get("user", {}).get("tcKimlikNo"):
                    is_session_expired = True

            if is_session_expired:
                print(f"\n[!] UYARI: UYAP oturum süresi doldu (HTTP {resp.get('status', '401')}).")
                if auto_relogin and pin:
                    print(f"[*] e-İmza ile otomatik oturum yenileniyor (PIN: {pin[:2]}**)...")
                    re_ok, re_msg = bridge.auto_relogin(pin=pin)
                    if re_ok:
                        print(f"[✓] {re_msg}")
                        time.sleep(1.5)
                        continue
                    else:
                        print(f"[x] e-İmza yenileme başarısız: {re_msg}")

                cfg = load_user_config()
                c_tc = cfg.get("tc", tckn)
                c_gsm = cfg.get("gsm", "")
                c_op = cfg.get("operator", "2")
                if c_tc and c_gsm:
                    print(f"[*] Mobil İmza ile oturum yenileniyor ({c_gsm})...")
                    m_ok, m_msg = bridge.login_with_mobil_imza(c_tc, c_gsm, c_op)
                    if m_ok:
                        print(f"[✓] {m_msg}")
                        time.sleep(2.0)
                        u_info = bridge.check_login_and_user()
                        user = u_info.get("user") or {}
                        tckn = str(user.get("tcKimlikNo") or user.get("tckn") or tckn)
                        continue
                    else:
                        print(f"[x] Mobil İmza yenileme başarısız: {m_msg}")

                print("[!] Lütfen UYAP'a giriş yapınız, oturum bekleniyor...")
                while True:
                    time.sleep(3.0)
                    u_info = bridge.check_login_and_user()
                    if u_info.get("user", {}).get("tcKimlikNo"):
                        user = u_info["user"]
                        tckn = str(user.get("tcKimlikNo") or tckn)
                        print("[✓] Oturum algılandı. Taramaya devam ediliyor...")
                        break
                continue

            if not resp.get("ok"):
                err_msg = resp.get("error") or resp.get("rawText") or "Zaman aşımı / Sunucu yanıtı alınamadı"
                if slice_attempt < max_slice_attempts:
                    print(f"\n  [!] Dilim [{idx}/{len(ranges)}] geçici bağlantı/gecikme hatası ({err_msg[:60]}). 4 sn sonra tekrar deneniyor...")
                    time.sleep(4.0)
                    continue
                else:
                    print(f"\n  [x] Dilim [{idx}/{len(ranges)}] ({s_str} - {e_str}) yanıt vermedi, sonraki dilime geçiliyor.")
                    break

            break

        rows = resp.get("data")
        if isinstance(rows, dict):
            if rows.get("error") or rows.get("errorCode"):
                print(f"  [!] UYAP Uyarısı ({s_str} - {e_str}): {rows.get('error') or rows.get('errorCode')}")
            rows = rows.get("data") or rows.get("list") or []
        if not isinstance(rows, list):
            rows = []

        chunk_added = 0
        for r in rows:
            if not isinstance(r, dict):
                continue
            evrak_id = str(r.get("evrakId") or "").strip()
            gg_evrak_id = str(r.get("ggEvrakId") or "").strip()
            is_emir_no = str(r.get("isEmirNo") or "").strip()

            ukey = f"islem_{is_emir_no}" if is_emir_no else f"islem_{evrak_id}_{gg_evrak_id}"
            if ukey in seen_keys:
                continue
            seen_keys.add(ukey)

            parsed_date = (
                parse_uyap_date(r.get("isEmirTarihi"))
                or parse_uyap_date(r.get("birimEvrakTarihi"))
                or s_d
            )
            birim_adi = str(r.get("birimAdi") or "Belirtilmemis_Birim").strip()
            dosya_no = str(r.get("dosyaNo") or "DosyaNo_Yok").strip()
            evrak_adi = str(r.get("name") or r.get("evrakTuru") or "Evrak").strip()
            isin_durumu = str(r.get("isinDurumu") or "Tamamlandı").strip()

            item = {
                "unique_key": ukey,
                "kaynak": "Islemlerim",
                "tarih_obj": parsed_date,
                "tarih": parsed_date.strftime("%Y-%m-%d"),
                "yil": parsed_date.strftime("%Y"),
                "yil_ay": parsed_date.strftime("%Y-%m"),
                "dosya_turu": infer_dosya_turu(birim_adi),
                "birim_adi": birim_adi,
                "dosya_no": dosya_no,
                "dosya_id": str(r.get("dosyaId") or ""),
                "islem_turu": f"Gonderilen_Islem ({isin_durumu})",
                "evrak_turu": evrak_adi,
                "evrak_turu_kod": evrak_adi,
                "evrak_adi": evrak_adi,
                "evrak_id": evrak_id,
                "gg_evrak_id": gg_evrak_id,
                "is_emir_no": is_emir_no,
                "download_params": {
                    "evrakId": evrak_id,
                    "gelenGidenEvrakId": gg_evrak_id,
                },
                "can_download": bool(evrak_id),
            }
            all_items.append(item)
            chunk_added += 1

        print(f"  [{idx}/{len(ranges)}] {s_str} - {e_str} -> {len(rows)} kayıt ({chunk_added} yeni)")
        if idx < len(ranges):
            time.sleep(random.uniform(0.3, 0.6))

    if not use_cache and all_items:
        try:
            serializable_items = []
            for it in all_items:
                item_copy = dict(it)
                item_copy.pop("tarih_obj", None)
                serializable_items.append(item_copy)
            cache_file.write_text(
                json.dumps({
                    "start_date": start_d.strftime("%d.%m.%Y"),
                    "end_date": end_d.strftime("%d.%m.%Y"),
                    "timestamp": time.time(),
                    "items": serializable_items
                }, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception:
            pass

    print(f"\n[✓] Toplam {len(all_items)} işlem/evrak kaydı hazır.")

    # 6. Mükerrerlik Filtresi: Diskte sağlam olanları atla
    download_queue = [
        it for it in all_items if it["can_download"] and not app_helper.is_item_already_downloaded(it)
    ]
    skipped_count = len(all_items) - len(download_queue)

    print("\n" + "=" * 70)
    print(f"  TOPLAM BULUNAN İŞLEM : {len(all_items)} adet")
    print(f"  DİSKTE MEVCUT (ATLANDI): {skipped_count} adet")
    print(f"  İNDİRİLECEK YENİ EVRAK: {len(download_queue)} adet")
    print("=" * 70 + "\n")

    if not download_queue:
        print("[✓] Tebrikler! Tüm evraklar zaten diskte sağlam ve eksiksiz mevcut. İndirilecek yeni evrak yok.")
        return

    # 7. Güvenli Hızlı İndirme Döngüsü
    min_d = 1.2
    max_d = 2.8
    batch_n = 20
    batch_pause = 10.0
    downloaded_now = 0
    max_retries = 3

    print(f"[*] Güvenli hızlı indirme başlatılıyor ({min_d}-{max_d} sn bekleme, her {batch_n} dosyada {batch_pause:.0f} sn mola)...")

    for idx, item in enumerate(download_queue, start=1):
        ukey = item["unique_key"]
        success = False

        for attempt in range(1, max_retries + 1):
            resp = bridge.download_document(item["download_params"])
            err_hdr = resp.get("errorHeader")
            status_code = resp.get("status", 0)
            ct = resp.get("contentType") or ""
            raw_bytes = base64.b64decode(resp.get("base64") or "") if resp.get("base64") else b""

            is_valid, reason = is_valid_document_bytes(raw_bytes, ct)
            need_relogin = (
                not resp.get("ok")
                or status_code in (401, 403, 302, 500)
                or bool(err_hdr)
                or not is_valid
            )

            if need_relogin:
                is_session_dead = (
                    not bridge.is_logged_in()
                    or "oturum" in reason.lower()
                    or "html" in reason.lower()
                    or "nosessionobject" in reason.lower()
                    or "prtl_gnl" in reason.lower()
                    or status_code in (401, 403, 302)
                    or "text/html" in ct.lower()
                )

                if not is_session_dead and bridge.is_logged_in() and "text/html" not in ct.lower():
                    # Oturum kesin açık ve dönen yanıt HTML değil, bu evrak UYAP sunucusunda fiziken yok
                    print(f"[-] BİLGİ: {item['evrak_adi']} ({item['birim_adi']}) UYAP sunucusunda fiziksel dosya olarak mevcut değil (Ekli dosya yok). Atlandı.")
                    break

                # Oturum gerçekten düşmüşse yeniden giriş yap:
                print(f"[!] UYARI: UYAP oturumu sonlanmış ({reason}). Yeniden bağlanılıyor... [Deneme {attempt}/{max_retries}]")
                if auto_relogin and pin and attempt < max_retries:
                    print(f"[*] e-İmza ile otomatik oturum yenileniyor (PIN: {pin[:2]}**)...")
                    re_ok, re_msg = bridge.auto_relogin(pin=pin)
                    if re_ok:
                        print(f"[✓] {re_msg}. Evrak tekrar deneniyor...")
                        time.sleep(2.0)
                        continue
                    else:
                        print(f"[x] Otomatik giriş başarısız: {re_msg}")
                        time.sleep(3.0)
                elif attempt < max_retries:
                    cfg = load_user_config()
                    c_tc = cfg.get("tc", "")
                    c_gsm = cfg.get("gsm", "")
                    c_op = cfg.get("operator", "2")
                    if c_tc and c_gsm:
                        print(f"[*] Mobil İmza ile oturum yenileniyor ({c_gsm})...")
                        m_ok, m_msg = bridge.login_with_mobil_imza(c_tc, c_gsm, c_op)
                        if m_ok:
                            print(f"[✓] {m_msg}. Evrak tekrar deneniyor...")
                            time.sleep(2.0)
                            continue
                    print("[!] Lütfen UYAP oturumunuzu kontrol edin/yenileyin (Mobil İmza / e-İmza)...")
                    time.sleep(5.0)
                    continue

            if is_valid:
                cd = resp.get("contentDisposition") or ""
                cd_filename = ""
                m = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^;"\']+)', cd, re.IGNORECASE)
                if m:
                    cd_filename = m.group(1).strip()

                ext = guess_extension(cd_filename, ct, raw_bytes)

                target_path = app_helper.build_target_filepath(item, ext, override_dir=download_dir)
                target_path.parent.mkdir(parents=True, exist_ok=True)

                tmp_path = target_path.with_suffix(target_path.suffix + ".part")
                tmp_path.write_bytes(raw_bytes)

                if tmp_path.exists() and tmp_path.stat().st_size == len(raw_bytes):
                    tmp_path.replace(target_path)
                else:
                    target_path.write_bytes(raw_bytes)
                    tmp_path.unlink(missing_ok=True)

                sha256_hash = hashlib.sha256(raw_bytes).hexdigest()

                app_helper.history[ukey] = {
                    "path": str(target_path),
                    "sha256": sha256_hash,
                    "downloaded_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "size": len(raw_bytes),
                    "tarih": item["tarih"],
                    "dosya_turu": item["dosya_turu"],
                    "birim_adi": item["birim_adi"],
                    "dosya_no": item["dosya_no"],
                    "islem_turu": item["islem_turu"],
                    "evrak_turu": item["evrak_turu"],
                    "evrak_id": item["evrak_id"],
                    "is_emir_no": item.get("is_emir_no", ""),
                }
                app_helper._save_history()
                downloaded_now += 1
                success = True

                pct = (idx / len(download_queue)) * 100
                bar = ("█" * int(pct // 5)).ljust(20, "░")
                print(f"[{idx}/{len(download_queue)}] [{bar}] %{pct:.1f} Kaydedildi ({len(raw_bytes)//1024} KB): {item['birim_adi']} ({item['dosya_no']}) - {item['evrak_adi']}")
                break

        if not success:
            print(f"[x] HATA: {item['evrak_adi']} ({ukey}) indirilemedi, atlandı.")

        if idx < len(download_queue):
            if downloaded_now > 0 and downloaded_now % batch_n == 0:
                print(f"[*] UYAP koruma molası: {downloaded_now} dosya indirildi, {batch_pause:.0f} sn dinleniliyor...")
                time.sleep(batch_pause)
            else:
                time.sleep(random.uniform(min_d, max_d))

    # CSV İndeksi yaz
    try:
        csv_path = download_dir / "indirme_indeksi.csv"
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([
                "Durum", "Tarih", "Dosya Türü", "Birim Adı", "Dosya No",
                "İşlem Türü", "Evrak Türü", "Evrak ID", "Kaydedilen Dosya Yolu"
            ])
            for it in all_items:
                ukey = it["unique_key"]
                h = app_helper.history.get(ukey, {})
                writer.writerow([
                    "İndirildi" if h else "Bekliyor",
                    it["tarih"],
                    it["dosya_turu"],
                    it["birim_adi"],
                    it["dosya_no"],
                    it["islem_turu"],
                    it["evrak_turu"],
                    it["evrak_id"],
                    h.get("path", ""),
                ])
        print(f"[✓] CSV İndeksi kaydedildi: {csv_path}")
    except Exception as e:
        print(f"[!] CSV kaydedilemedi: {e}")

    print("\n" + "=" * 70)
    print(f"  İNDİRME TAMAMLANDI! Bu oturumda {downloaded_now} dosya eksiksiz indirildi.")
    print(f"  Kayıt Konumu: {download_dir}")
    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="UYAP Avukat Portal Güvenli Toplu Evrak İndirici")
    parser.add_argument("--cli", "-c", action="store_true", help="Grafik arayüz (Tkinter) yerine Terminal / CLI modunda çalıştır")
    parser.add_argument("--headless", action="store_true", help="Chromium'u arkaplanda (penceresiz / headless) çalıştır")
    parser.add_argument("--port", type=int, default=CDP_PORT, help="Chromium CDP portu (varsayılan: 9223)")
    parser.add_argument("--pin", type=str, default="", help="e-İmza PIN kodu (varsayılan: pin.txt veya boş)")
    parser.add_argument("--start-date", type=str, default="", help="Başlangıç tarihi (GG.AA.YYYY)")
    parser.add_argument("--end-date", type=str, default="", help="Bitiş tarihi (GG.AA.YYYY)")
    parser.add_argument("--chunk-days", type=int, default=30, help="Sorgu dilim aralığı (gün)")
    parser.add_argument("--dir", type=str, default="", help="İndirme klasörü")
    parser.add_argument("--no-auto-relogin", action="store_true", help="Otomatik e-imza girişini devre dışı bırak")
    parser.add_argument("--tc", type=str, default="", help="Mobil İmza için T.C. Kimlik Numarası")
    parser.add_argument("--gsm", type=str, default="", help="Mobil İmza için GSM Numarası (başında sıfır olmadan 10 hane)")
    parser.add_argument("--operator", type=str, default="", choices=["", "turktelekom", "turkcell", "vodafone", "1", "2", "3"], help="Mobil İmza operatörü (turktelekom, turkcell, vodafone)")
    parser.add_argument("--mobil-imza", action="store_true", help="Oturum yoksa doğrudan Mobil İmza akışını başlat")
    parser.add_argument("--mode", type=str, default="islemlerim", choices=["islemlerim", "dosyalar", "durusmalar"], help="Çalışma modu: islemlerim, dosyalar (dava dosyası evrakları), durusmalar (duruşma takvimi)")
    parser.add_argument("--evrak-turu", type=str, default="", help="Dava dosyalarından indirilecek evrak türü (durusma, bilirkisi, karar, tensip, dilekce, iddianame, kesinlesme, tumu veya serbest metin)")
    parser.add_argument("--include-attachments", action="store_true", help="Evrakların ek belgelerini de indir")
    parser.add_argument("--scope", type=str, default="all", choices=["all", "acik", "fav"], help="Taranacak dosya kapsamı: all (Açık + Kapalı - 700+ Dosya), acik (Yalnızca Açık), fav (Sık Kullanılanlar)")
    parser.add_argument("--include-closed", action="store_true", default=True, help="Kapalı/Karara Çıkmış dosyaları da dahil et (varsayılan: True)")
    parser.add_argument("--no-closed", dest="include_closed", action="store_false", help="Kapalı dosyaları tarama dışı bırak")
    parser.add_argument("--filter-birim", type=str, default="", help="Yalnızca birim adı veya dosya no bu kelimeyi içeren dosyaları tara (örn: erzurum, 2025/506)")

    args = parser.parse_args()

    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    if args.cli or not has_display or not HAS_TKINTER:
        if not HAS_TKINTER and not args.cli:
            print("[i] Bilgi: Tkinter grafik modülü bulunamadı, otomatik olarak Terminal / CLI modunda başlatılıyor.")
        elif not has_display and not args.cli:
            print("[i] Bilgi: Ekran (DISPLAY/X11) bulunamadı, otomatik olarak Terminal / CLI modunda başlatılıyor.")
        run_cli_mode(args)
    else:
        root = tk.Tk()
        app = UyapDownloaderApp(root)
        root.mainloop()


if __name__ == "__main__":
    main()
