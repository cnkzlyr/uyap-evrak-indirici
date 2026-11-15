#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2026 Av. Can KIZILYAR. Tüm hakları saklıdır. / All rights reserved.
"""
UYAP Nöbetçi Bot & Arka Plan İzleyici (Watcher)
==============================================
Avukat Portal'ı arka planda izler, açık (derdest) dosyalardaki yeni evrakları,
karşı taraf dilekçelerini, tensip ve duruşma zabıtlarını, bilirkişi raporlarını
ve tebligatları otomatik yakalar; indirip PDF'e dönüştürür ve Telegram'dan
anlık bildirim + PDF eki olarak avukata iletir.

Özellikler:
  1. Oturum Koruyucu (Keep-Alive Heartbeat): UYAP oturumunu 3 dakikada bir pingle canlı tutar.
  2. Açık Dosya Nöbetçisi: Yalnızca derdest (açık) 149 dosyayı nazikçe sorgular (WAF dostu).
  3. Anlık İndirme & PDF: Yeni evrak düştüğü an indirilir, UDF/TIF ise anında PDF yapılır.
  4. Hukuki Süre Analizi: Tebligat, tensip veya bilirkişi raporu için yasal süreleri hesaplar.
  5. Telegram Entegrasyonu: Yeni evrakı, dosya bilgilerini ve PDF belgesini doğrudan iletir.
  6. Duruşma Hatırlatıcı: Yaklaşan duruşmaları gün başında / öncesinde hatırlatır.
  7. Google Drive Senkronizasyon Kancası: İnen evrakları ve SQLite yedeğini Drive'a eşitler.

Kullanım:
  python uyap_watcher.py --setup           # Telegram bot ayarlarını yap
  python uyap_watcher.py --test-telegram   # Telegram bağlantısını test et
  python uyap_watcher.py                   # Nöbetçiyi başlat (varsayılan: 30 dk)
  python uyap_watcher.py --interval 15     # 15 dakikalık aralıkla çalıştır
  python uyap_watcher.py --drive-dir "G:\\Drive'ım\\UYAP_Arsiv"  # Google Drive eşitlemesiyle çalıştır
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_DIR = Path(os.environ.get("UYAP_VERI_DIZINI") or BASE_DIR)
CONFIG_FILE = DATA_DIR / "watcher_config.json"
LOG_DIR = DATA_DIR / "loglar"

# Günlük
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (Watcher) %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "watcher.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("uyap.watcher")

# Katalog ve Dönüştürücü İçe Aktarımı
sys.path.insert(0, str(BASE_DIR))
import evrak_katalog as ek
import donusturucu as dn


# ---------------------------------------------------------------------------
# Telegram Bot İstemcisi (Sıfır ek kütüphane bağımlılığı)
# ---------------------------------------------------------------------------
class TelegramClient:
    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = bot_token.strip()
        self.chat_id = chat_id.strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.is_configured:
            return False
        import requests
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                return True
            logger.error("Telegram mesaj hatası: %s - %s", resp.status_code, resp.text)
        except Exception as e:
            logger.error("Telegram bağlantı hatası: %s", e)
        return False

    def send_document(self, file_path: Path | str, caption: str = "") -> bool:
        if not self.is_configured:
            return False
        import requests
        p = Path(file_path)
        if not p.exists() or p.stat().st_size == 0:
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
        data = {"chat_id": self.chat_id, "caption": caption[:1000], "parse_mode": "HTML"}
        try:
            with open(p, "rb") as f:
                files = {"document": (p.name, f)}
                resp = requests.post(url, data=data, files=files, timeout=45)
                if resp.status_code == 200:
                    return True
                logger.error("Telegram evrak gönderme hatası: %s - %s", resp.status_code, resp.text)
        except Exception as e:
            logger.error("Telegram dosya aktarım hatası: %s", e)
        return False


# ---------------------------------------------------------------------------
# Yapılandırma Yönetimi
# ---------------------------------------------------------------------------
DEFAULT_WATCHED_CATEGORIES = [
    "durusma", "bilirkisi", "karar", "tensip", "dilekce",
    "layihalar", "tebligat", "kesinlesme", "iddianame", "tutuklama"
]

def load_config() -> dict:
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    cfg.setdefault("telegram_token", os.environ.get("UYAP_TELEGRAM_TOKEN", ""))
    cfg.setdefault("telegram_chat_id", os.environ.get("UYAP_TELEGRAM_CHAT_ID", ""))
    cfg.setdefault("interval_minutes", 15)
    cfg.setdefault("keepalive_minutes", 3)
    cfg.setdefault("google_drive_dir", "G:\\Drive'ım\\UYAP_Arsiv")
    cfg.setdefault("google_drive_webhook", "")
    cfg.setdefault("check_open_cases", True)
    cfg.setdefault("check_durusma", True)
    cfg.setdefault("check_tebligat", True)
    cfg.setdefault("watched_categories", DEFAULT_WATCHED_CATEGORIES)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Yapılandırma kaydedildi: %s", CONFIG_FILE)


# ---------------------------------------------------------------------------
# Hukuki Süre Analizcisi
# ---------------------------------------------------------------------------
def calculate_legal_deadline(tur: str, aciklama: str) -> str:
    """Evrak türü ve açıklamasına göre yasal itiraz/cevap süresini hesaplar."""
    t_lower = (tur + " " + aciklama).lower()
    bugun = dt.date.today()

    if "bilirkişi" in t_lower or "bilirkisi" in t_lower or "ek rapor" in t_lower:
        son_gun = bugun + dt.timedelta(days=14)
        return (f"⚖️ <b>Yasal Süre:</b> Bilirkişi Raporuna İtiraz (HMK m. 281)\n"
                f"Tebliğden itibaren <b>2 hafta</b>. (Tahmini son gün: {son_gun.strftime('%d.%m.%Y')})")

    if "tebligat" in t_lower or "uets" in t_lower or "mazbata" in t_lower:
        teblig_tarihi = bugun + dt.timedelta(days=5)  # TK 7/a elektronik tebligat 5 gün
        cevap_son = teblig_tarihi + dt.timedelta(days=14)
        return (f"📬 <b>Tebligat Kanunu m. 7/a:</b> Elektronik tebligat 5 gün sonra ({teblig_tarihi.strftime('%d.%m.%Y')}) tebliğ sayılır.\n"
                f"2 haftalık yasal cevap/istinaf süresi için son gün: <b>{cevap_son.strftime('%d.%m.%Y')}</b>")

    if "tensip" in t_lower:
        return ("📋 <b>Tensip Zaptı:</b> Mahkemenin tensip ara kararlarını ve delil/avans "
                "kesin sürelerini zaptı açarak kontrol ediniz.")

    if "gerekçeli karar" in t_lower or "hüküm" in t_lower:
        istinaf_son = bugun + dt.timedelta(days=14)
        return (f"📜 <b>Gerekçeli Karar:</b> HMK istinaf süresi tebliğden itibaren <b>2 hafta</b> "
                f"(Tahmini son gün: {istinaf_son.strftime('%d.%m.%Y')}). Ceza davalarında 7 gün.")

    if "dava dilekçesi" in t_lower or "cevap dilekçesi" in t_lower:
        return "📝 <b>Dilekçe Değişimi:</b> Karşı tarafın sunduğu dilekçeye karşı 2 haftalık cevap/itiraz süresi başlayabilir."

    return ""


# ---------------------------------------------------------------------------
# UYAP Nöbetçi Motoru (Watcher Engine)
# ---------------------------------------------------------------------------
class UyapWatcher:
    def __init__(self, config: dict):
        self.config = config
        self.katalog = ek.Katalog()
        self.tg = TelegramClient(
            config.get("telegram_token", ""),
            config.get("telegram_chat_id", "")
        )
        from google_drive_entegrasyonu import GoogleDriveCloudClient
        self.cloud = GoogleDriveCloudClient(
            webhook_url=config.get("google_drive_webhook", ""),
            local_drive_dir=config.get("google_drive_dir", "")
        )
        self.bridge = None
        self.last_keepalive = 0
        self.notified_durusmalar = set()

    def connect_bridge(self) -> bool:
        """Açık olan UYAP Chrome oturumuna bağlanır."""
        try:
            import uyap_indirici as ui
            self.bridge = ui.UyapBrowserBridge(port=ui.CDP_PORT)
            ready, msg = self.bridge.ensure_ready("uyap")
            if not ready:
                logger.warning("UYAP tarayıcısına bağlanılamadı: %s", msg)
                return False
            logger.info("UYAP tarayıcı köprüsüne başarıyla bağlandı (Port: %d).", self.bridge.port)
            return True
        except Exception as e:
            logger.error("Köprü bağlantı hatası: %s", e)
            return False

    def heartbeat(self) -> None:
        """Oturumun düşmemesi için 3 dakikada bir UYAP'a hafif bir AJAX isteği gönderir."""
        now = time.time()
        keepalive_s = self.config.get("keepalive_minutes", 3) * 60
        if now - self.last_keepalive < keepalive_s:
            return
        if not self.bridge:
            return
        try:
            js = "fetch('/kullanici_bilgileri.ajx').then(r=>r.status).catch(e=>0)"
            res = self.bridge.eval_js(js, timeout=10.0)
            self.last_keepalive = now
            logger.debug("Oturum nabzı (Heartbeat) gönderildi: durum=%s", res)
        except Exception as e:
            logger.debug("Heartbeat hatası: %s", e)

    def scan_open_cases(self) -> int:
        """Açık (derdest) 149 dosyayı nazikçe sorgular; yeni evrakları yakalayıp indirir."""
        if not self.bridge:
            return 0

        # Sadece 'Açık' durumdaki dosyaları çek
        cur = self.katalog.db.execute(
            "SELECT dkey, birim, dosya_no, dosya_id, evrak_sayisi FROM dosya WHERE durum='Açık' ORDER BY taranma ASC"
        )
        open_cases = cur.fetchall()
        logger.info("Derdest (Açık) Dosya Nöbetçisi: %d dosya kontrol ediliyor...", len(open_cases))

        new_doc_count = 0
        drive_dir = Path(self.config.get("google_drive_dir")) if self.config.get("google_drive_dir") else None
        download_dir = drive_dir or Path(os.environ.get("UYAP_INDIRME_DIZINI") or DATA_DIR / "UYAP_Indirilenler")

        for case in open_cases:
            self.heartbeat()
            dkey = case["dkey"]
            dosya_id = case["dosya_id"]
            if not dosya_id:
                continue

            try:
                # UYAP'tan dosyanın güncel evrak sayfalarını al
                evrak_pages = self.bridge.query_dosya_evraklar(dosya_id)
                if not evrak_pages:
                    time.sleep(1.0)
                    continue

                # Mevcut evrak kimlikleri (skey)
                existing_skeys = {
                    r[0] for r in self.katalog.db.execute(
                        "SELECT skey FROM evrak WHERE dkey=?", (dkey,)
                    ).fetchall()
                }

                # Sayfaları kataloğa işle (tüm ekler ve gruplar dahil)
                self.katalog.ingest_pages(dkey, evrak_pages, oturum="watcher")

                # Sadece bu işlemle YENİ eklenen veya henüz indirilmemiş evrakları filtrele
                cur = self.katalog.db.execute(
                    "SELECT * FROM evrak WHERE dkey=? ORDER BY id ASC", (dkey,)
                )
                all_case_docs = cur.fetchall()

                truly_fresh = []
                watched_cats = set(self.config.get("watched_categories") or DEFAULT_WATCHED_CATEGORIES)
                allow_all = "tumu" in watched_cats or "*" in watched_cats

                for row in all_case_docs:
                    skey = row["skey"]
                    # 1. Zaten indirilmişse veya mükerrerse atla
                    if row["durum"] in ("indirildi", "mukerrer"):
                        continue

                    # 2. Kategori Filtresi: Yalnızca kullanıcının ilgilendiği hayati evraklar!
                    kat = str(row["kategori"] or row["ust_kategori"] or "").lower()
                    if not allow_all and kat and kat not in watched_cats:
                        continue

                    # 3. Google Drive / diskte hedef PDF zaten var mı kontrol et!
                    target_pdf = ek.target_path_for(row, download_dir / "DAVA_EVRAKLARI", ".pdf")
                    if target_pdf.exists() and target_pdf.stat().st_size > 0:
                        # Drive'da zaten var! 'indirildi' olarak işaretle ve sessizce geç
                        self.katalog.set_result(row["id"], durum="indirildi", pdf_path=str(target_pdf))
                        continue

                    # 4. Daha önce mevcut skey'ler arasındaysa da atla (eski evrak)
                    if skey in existing_skeys:
                        continue

                    truly_fresh.append((row, target_pdf))

                if truly_fresh:
                    logger.info("🔔 [%s - %s] %d GERÇEKTEN YENİ EVRAK BULUNDU!", case["birim"], case["dosya_no"], len(truly_fresh))

                    for row, target_pdf in truly_fresh:
                        tur = str(row["tur"] or "Evrak")
                        acik = str(row["aciklama"] or "")
                        gonderen = str(row["gonderen"] or "")
                        tarih = str(row["tarih"] or "")

                        # Evrakı UYAP'tan indir
                        target_pdf.parent.mkdir(parents=True, exist_ok=True)
                        doc_params = {
                            "evrakId": row["evrak_id"],
                            "dosyaId": dosya_id,
                            "tur": tur,
                        }
                        res = self.bridge.download_document(doc_params)
                        pdf_path = None
                        if res and res.get("data"):
                            raw_bytes = res["data"]
                            ext = res.get("ext", ".udf")
                            target_raw = target_pdf.with_suffix(ext)
                            target_raw.write_bytes(raw_bytes)

                            # PDF'e dönüştür
                            if ext == ".pdf":
                                pdf_path = target_raw
                            elif ext == ".udf":
                                pdf_path = dn.donustur(target_raw)
                            elif ext in (".tif", ".tiff"):
                                pdf_path = ek.tiff_to_pdf(target_raw)

                            # Veritabanında güncelle
                            self.katalog.set_result(row["id"], durum="indirildi", path=str(target_raw), pdf_path=str(pdf_path or ""))

                            # Google Drive / Bulut yüklemesi (Yerel G:\ veya Doğrudan Webhook)
                            if pdf_path and pdf_path.exists():
                                rel_folder = f"DAVA_EVRAKLARI/{case['birim']}/{case['dosya_no']}"
                                self.cloud.upload_file(pdf_path, remote_folder_path=rel_folder)

                        # Yasal süre hesabı
                        sure_bilgisi = calculate_legal_deadline(tur, acik)

                        # Telegram Bildirimi Hazırla
                        msg_lines = [
                            f"🔔 <b>UYAP: Yeni Evrak Geldi!</b>",
                            f"🏢 <b>Birim:</b> {case['birim']}",
                            f"📁 <b>Dosya:</b> {case['dosya_no']}",
                            f"📄 <b>Evrak Türü:</b> {tur}",
                            f"📅 <b>Tarih:</b> {tarih}",
                        ]
                        if gonderen:
                            msg_lines.append(f"👤 <b>Gönderen:</b> {gonderen}")
                        if acik:
                            msg_lines.append(f"📝 <b>Açıklama:</b> {acik}")
                        if sure_bilgisi:
                            msg_lines.append(f"\n{sure_bilgisi}")

                        msg_text = "\n".join(msg_lines)

                        # Telegram'a gönder (PDF varsa iliştir)
                        if pdf_path and pdf_path.exists() and pdf_path.stat().st_size <= 45 * 1024 * 1024:
                            self.tg.send_document(pdf_path, caption=msg_text)
                        else:
                            self.tg.send_message(msg_text)

                        new_doc_count += 1

                # Taranma zamanını tazele
                now_str = dt.datetime.now().isoformat()[:19]
                self.katalog.db.execute("UPDATE dosya SET taranma=? WHERE dkey=?", (now_str, dkey))
                self.katalog.db.commit()

            except Exception as e:
                logger.debug("[%s] Evrak tarama atlandı: %s", case["dosya_no"], e)

            # WAF'a takılmamak için nazik bekleme
            time.sleep(1.5)

        # Google Drive varsa veritabanı anlık snapshot'ını güncelle
        if drive_dir:
            self.katalog.cloud_backup(drive_dir / "uyap_katalog_yedek.db")

        return new_doc_count

    def check_upcoming_hearings(self) -> None:
        """Önümüzdeki 7 günün duruşmalarını çeker, yaklaşanları Telegram'dan bildirir."""
        if not self.bridge or not self.config.get("check_durusma", True):
            return
        try:
            today = dt.date.today()
            end_d = today + dt.timedelta(days=7)
            durusmalar = self.bridge.query_durusmalar(today.strftime("%d/%m/%Y"), end_d.strftime("%d/%m/%Y"))
            if not durusmalar:
                return

            for d in durusmalar:
                dt_str = str(d.get("tarihSaat") or "")
                birim = str(d.get("yerelBirimAd") or "")
                dno = str(d.get("dosyaNo") or "")
                key = f"{dt_str}|{birim}|{dno}"

                if key not in self.notified_durusmalar:
                    # Kataloğa duruşmayı kaydet
                    t_part = dt_str.split()[0] if " " in dt_str else dt_str
                    s_part = dt_str.split()[1] if " " in dt_str else ""
                    self.katalog.upsert_durusma({
                        "birim": birim,
                        "dosya_no": dno,
                        "tarih": t_part,
                        "saat": s_part,
                        "yer": birim,
                        "aciklama": d.get("islemTuruAciklama", ""),
                    })

                    # Telegram Hatırlatması
                    tg_msg = (
                        f"⚖️ <b>DURUŞMA HATIRLATICI</b>\n"
                        f"📅 <b>Tarih & Saat:</b> {dt_str}\n"
                        f"🏢 <b>Mahkeme:</b> {birim}\n"
                        f"📁 <b>Dosya No:</b> {dno}\n"
                        f"📌 <b>Açıklama:</b> {d.get('islemTuruAciklama', '')}"
                    )
                    self.tg.send_message(tg_msg)
                    self.notified_durusmalar.add(key)
        except Exception as e:
            logger.debug("Duruşma sorgulama atlandı: %s", e)

    def run_loop(self) -> None:
        """Sürekli arka plan nöbet döngüsü."""
        logger.info("=" * 65)
        logger.info("  UYAP Nöbetçi Bot Devrede")
        logger.info("  Döngü Aralığı: %d dakika", self.config.get("interval_minutes", 30))
        if self.tg.is_configured:
            logger.info("  Telegram Bildirimleri: AKTİF")
            self.tg.send_message("🛡️ <b>UYAP Nöbetçi Bot Başlatıldı!</b>\nAçık dosyalar ve yeni evraklar izleniyor.")
        else:
            logger.warning("  Telegram Bildirimleri: YAPILANDIRILMAMIŞ (Terminal loglanacak)")
        logger.info("=" * 65)

        interval_s = self.config.get("interval_minutes", 30) * 60

        while True:
            try:
                if not self.bridge or not self.bridge.is_browser_alive():
                    logger.info("UYAP tarayıcısı aranıyor / bağlanılıyor...")
                    if not self.connect_bridge():
                        logger.warning("UYAP bağlantısı kurulamadı. 60 sn sonra tekrar denenecek.")
                        time.sleep(60)
                        continue

                # 1. Oturum nabzı
                self.heartbeat()

                # 2. Duruşma kontrolü
                self.check_upcoming_hearings()

                # 3. Derdest açık dosyaların evrak kontrolü
                if self.config.get("check_open_cases", True):
                    yeni_sayisi = self.scan_open_cases()
                    if yeni_sayisi > 0:
                        logger.info("Döngü tamamlandı: %d yeni evrak işlendi.", yeni_sayisi)

            except KeyboardInterrupt:
                logger.info("Nöbetçi kullanıcı tarafından durduruldu.")
                break
            except Exception as e:
                logger.error("Nöbetçi döngü hatası: %s", e)

            logger.info("Sonraki kontrol için bekleniyor (%d dakika)...", self.config.get("interval_minutes", 30))
            time.sleep(interval_s)


# ---------------------------------------------------------------------------
# CLI & Kurulum Menüsü
# ---------------------------------------------------------------------------
def interactive_setup() -> None:
    print("=" * 65)
    print("  UYAP Watcher / Telegram Yapılandırma Sihirbazı")
    print("=" * 65)
    cfg = load_config()

    print("\n1) Telegram Bot Token:")
    print("   (Telegram'da @BotFather üzerinden oluşturulan bot token'ı)")
    t = input(f"   Token [{cfg.get('telegram_token', '')}]: ").strip()
    if t:
        cfg["telegram_token"] = t

    print("\n2) Telegram Chat ID:")
    print("   (Kendi kullanıcı ID'niz veya mesajların gideceği grup ID'si)")
    print("   (Öğrenmek için Telegram'da @userinfobot botuna /start yazabilirsiniz)")
    c = input(f"   Chat ID [{cfg.get('telegram_chat_id', '')}]: ").strip()
    if c:
        cfg["telegram_chat_id"] = c

    print("\n3) Kontrol Sıklığı (Dakika):")
    inv = input(f"   Dakika [{cfg.get('interval_minutes', 30)}]: ").strip()
    if inv and inv.isdigit():
        cfg["interval_minutes"] = int(inv)

    print("\n4) Google Drive Yedekleme Klasörü (Opsiyonel):")
    print("   (Örn: G:\\Drive'ım\\UYAP_Arsiv veya boş bırakabilirsiniz)")
    drv = input(f"   Klasör [{cfg.get('google_drive_dir', '')}]: ").strip()
    if drv:
        cfg["google_drive_dir"] = drv

    save_config(cfg)
    print("\n[✓] Ayarlar kaydedildi!")

    if cfg.get("telegram_token") and cfg.get("telegram_chat_id"):
        print("\n[*] Telegram test mesajı gönderiliyor...")
        tg = TelegramClient(cfg["telegram_token"], cfg["telegram_chat_id"])
        if tg.send_message("🔔 <b>Tebrikler!</b> UYAP Nöbetçi Bot bağlantısı başarıyla kuruldu."):
            print("[✓] Test mesajı Telegram'a ulaştı!")
        else:
            print("[!] Test mesajı gönderilemedi. Token ve Chat ID'yi kontrol ediniz.")


def main():
    parser = argparse.ArgumentParser(description="UYAP Nöbetçi Bot & Arka Plan İzleyici")
    parser.add_argument("--setup", action="store_true", help="Telegram ve nöbetçi ayarlarını yap")
    parser.add_argument("--test-telegram", action="store_true", help="Telegram bağlantısını test et")
    parser.add_argument("--interval", type=int, default=None, help="Kontrol aralığı (dakika)")
    parser.add_argument("--drive-dir", type=str, default=None, help="Google Drive hedef klasörü")
    args = parser.parse_args()

    if args.setup:
        interactive_setup()
        return

    cfg = load_config()
    if args.interval:
        cfg["interval_minutes"] = args.interval
    if args.drive_dir:
        cfg["google_drive_dir"] = args.drive_dir

    if args.test_telegram:
        tg = TelegramClient(cfg.get("telegram_token", ""), cfg.get("telegram_chat_id", ""))
        if not tg.is_configured:
            print("[!] Telegram henüz yapılandırılmamış. Lütfen 'python uyap_watcher.py --setup' çalıştırın.")
            return
        if tg.send_message("🔔 <b>UYAP Test Bildirimi:</b> Telegram bağlantısı aktiftir."):
            print("[✓] Test mesajı başarıyla iletildi!")
        else:
            print("[!] Test mesajı gönderilemedi.")
        return

    watcher = UyapWatcher(cfg)
    watcher.run_loop()


if __name__ == "__main__":
    main()
