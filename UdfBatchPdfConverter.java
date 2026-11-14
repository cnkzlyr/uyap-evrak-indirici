package uyap;

import java.io.*;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * UYAP UDF -> PDF Toplu Vektörel Dönüştürücü (Official Havelsan Engine)
 * Av. Can Kızılyar - naps-uyap-thunar-tools & udemobile motorlarından uyarlanmıştır.
 * TrueType Font Gömme (Embedded Times New Roman, Arial vb.) desteklidir.
 */
public class UdfBatchPdfConverter {

    private static final PrintStream CONSOLE = System.out;

    public static void main(String[] args) throws Exception {
        String targetPathStr = "/sdcard/Download/UYAP_Indirilenler";
        boolean forceOverwrite = false;

        for (String arg : args) {
            if ("--force".equalsIgnoreCase(arg) || "-f".equalsIgnoreCase(arg)) {
                forceOverwrite = true;
            } else if (!arg.startsWith("-")) {
                targetPathStr = arg;
            }
        }

        File target = new File(targetPathStr);
        if (!target.exists()) {
            CONSOLE.println("[HATA] Belirtilen dosya veya dizin bulunamadı: " + targetPathStr);
            System.exit(1);
        }

        // 1. Havelsan FontMapper'ını Termux & Android sistem fontlarıyla başlat
        initHavelsanFonts();

        List<File> allUdfs = new ArrayList<>();
        if (target.isFile()) {
            if (target.getName().toLowerCase().endsWith(".udf")) {
                allUdfs.add(target);
            }
        } else {
            CONSOLE.println("==================================================================");
            CONSOLE.println("    UYAP UDF -> PDF Vektörel Dönüştürücü (Havelsan + TrueType)");
            CONSOLE.println("==================================================================");
            CONSOLE.println("[*] Dizin taranıyor: " + target.getAbsolutePath());
            scanDirectory(target, allUdfs);
            CONSOLE.println("[✓] Toplam bulunan UDF sayısı: " + allUdfs.size() + " adet");
        }

        List<File> toConvert = new ArrayList<>();
        int alreadyExistsCount = 0;
        for (File udf : allUdfs) {
            String p = udf.getAbsolutePath();
            String pdfPath = p.substring(0, p.lastIndexOf('.')) + ".pdf";
            File pdfFile = new File(pdfPath);
            if (!forceOverwrite && pdfFile.exists() && pdfFile.length() > 0) {
                alreadyExistsCount++;
            } else {
                toConvert.add(udf);
            }
        }

        if (allUdfs.size() > 1) {
            CONSOLE.println("[✓] Zaten mevcut PDF sayısı   : " + alreadyExistsCount + " adet (atlandı)");
            CONSOLE.println("[*] Dönüştürülecek yeni UDF  : " + toConvert.size() + " adet");
            if (forceOverwrite) {
                CONSOLE.println("[!] Zorunlu mod (--force) aktif: Mevcut PDF'ler Times New Roman ile yenileniyor.");
            }
            CONSOLE.println("------------------------------------------------------------------");
        }

        if (toConvert.isEmpty()) {
            CONSOLE.println("[✓] Bütün UDF dosyalarının PDF kopyaları zaten mevcut! İşlem tamamlandı.");
            return;
        }

        // Havelsan debug loglarını sustur
        PrintStream devNull = new PrintStream(new OutputStream() {
            @Override public void write(int b) {}
            @Override public void write(byte[] b, int off, int len) {}
        });
        System.setOut(devNull);
        System.setErr(devNull);

        Class<?> bClass = Class.forName("tr.com.havelsan.uyap.system.editor.b.b");
        Method mConvert = bClass.getMethod("a", OutputStream.class, ByteArrayInputStream.class);

        int threads = Math.min(6, Math.max(2, Runtime.getRuntime().availableProcessors()));
        if (allUdfs.size() > 1) {
            CONSOLE.println("[*] " + threads + " iş parçacığı (threads) ile vektörel çevrim başlatılıyor...");
        }

        ExecutorService executor = Executors.newFixedThreadPool(threads);
        AtomicInteger successCounter = new AtomicInteger(0);
        AtomicInteger failCounter = new AtomicInteger(0);
        AtomicInteger processedCounter = new AtomicInteger(0);

        int totalToConvert = toConvert.size();
        long startTime = System.currentTimeMillis();

        List<Future<?>> futures = new ArrayList<>(totalToConvert);

        for (File udf : toConvert) {
            futures.add(executor.submit(() -> {
                try {
                    String p = udf.getAbsolutePath();
                    String pdfPath = p.substring(0, p.lastIndexOf('.')) + ".pdf";
                    File pdfFile = new File(pdfPath);

                    byte[] bytes = Files.readAllBytes(udf.toPath());
                    if (bytes.length == 0) {
                        failCounter.incrementAndGet();
                        return;
                    }

                    Object bObj = bClass.getDeclaredConstructor().newInstance();
                    try (FileOutputStream fos = new FileOutputStream(pdfFile);
                         ByteArrayInputStream bais = new ByteArrayInputStream(bytes)) {
                        mConvert.invoke(bObj, fos, bais);
                    }

                    if (pdfFile.exists() && pdfFile.length() > 0) {
                        successCounter.incrementAndGet();
                    } else {
                        failCounter.incrementAndGet();
                    }
                } catch (Throwable t) {
                    failCounter.incrementAndGet();
                } finally {
                    int done = processedCounter.incrementAndGet();
                    if (allUdfs.size() > 1 && (done % 50 == 0 || done == totalToConvert)) {
                        long elapsed = System.currentTimeMillis() - startTime;
                        double sec = Math.max(0.1, elapsed / 1000.0);
                        double rate = done / sec;
                        double pct = (done * 100.0) / totalToConvert;
                        CONSOLE.printf("  [%d / %d] (%%%.1f) - Başarılı: %d | Hata: %d | Hız: %.1f evrak/sn\n",
                                done, totalToConvert, pct, successCounter.get(), failCounter.get(), rate);
                    }
                }
            }));
        }

        for (Future<?> f : futures) {
            try {
                f.get();
            } catch (Exception ignored) {}
        }

        executor.shutdown();
        long totalElapsed = System.currentTimeMillis() - startTime;
        double totalSec = totalElapsed / 1000.0;

        if (allUdfs.size() > 1) {
            CONSOLE.println("------------------------------------------------------------------");
            CONSOLE.printf("[✓] TAMAMLANDI: %d PDF başarıyla oluşturuldu, %d hata (Süre: %.1f sn)\n",
                    successCounter.get(), failCounter.get(), totalSec);
            CONSOLE.println("==================================================================");
        } else if (successCounter.get() > 0) {
            String p = allUdfs.get(0).getAbsolutePath();
            CONSOLE.println("[✓] PDF oluşturuldu: " + p.substring(0, p.lastIndexOf('.')) + ".pdf");
        }
    }

    private static void initHavelsanFonts() {
        try {
            Class<?> bClass = Class.forName("tr.com.havelsan.uyap.system.editor.b.b");
            Method getMapper = bClass.getDeclaredMethod("a");
            getMapper.setAccessible(true);
            Object cObj = getMapper.invoke(null);

            Class<?> cClass = Class.forName("tr.com.havelsan.uyap.system.editor.b.c");
            Method regDir = cClass.getMethod("a", String.class);

            String[] fontDirs = {
                "/data/data/com.termux/files/home/.fonts/microsoft",
                "/data/data/com.termux/files/home/.fonts",
                "/system/fonts",
                "/data/data/com.termux/files/usr/share/fonts"
            };

            int totalLoaded = 0;
            for (String dir : fontDirs) {
                try {
                    File d = new File(dir);
                    if (d.exists() && d.isDirectory()) {
                        totalLoaded += (int) regDir.invoke(cObj, dir);
                    }
                } catch (Exception ignored) {}
            }

            // Bütün fontlarda TrueType embedded = true bayrağını aktif et
            Method getA = cClass.getMethod("a");
            HashMap<?, ?> map = (HashMap<?, ?>) getA.invoke(cObj);

            Class<?> dClass = Class.forName("tr.com.havelsan.uyap.system.editor.b.d");
            Field embeddedField = null;
            for (Field f : dClass.getFields()) {
                if (f.getType() == boolean.class) {
                    embeddedField = f;
                    break;
                }
            }

            if (embeddedField != null && map != null) {
                for (Object dObj : map.values()) {
                    try {
                        embeddedField.setBoolean(dObj, true);
                    } catch (Exception ignored) {}
                }
            }

            // Alias tanımları: Serif / Times / Roman -> Times New Roman
            Method aliasMethod = cClass.getMethod("a", String.class, String.class);
            aliasMethod.invoke(cObj, "Serif", "Times New Roman");
            aliasMethod.invoke(cObj, "TimesRoman", "Times New Roman");
            aliasMethod.invoke(cObj, "Times", "Times New Roman");
            aliasMethod.invoke(cObj, "SansSerif", "Arial");
            aliasMethod.invoke(cObj, "Dialog", "Arial");
            aliasMethod.invoke(cObj, "Calibri", "Arial");

        } catch (Throwable t) {
            // Font yüklenemezse orijinal akış bozulmasın
        }
    }

    private static void scanDirectory(File dir, List<File> list) {
        File[] files = dir.listFiles();
        if (files == null) return;
        for (File f : files) {
            if (f.isDirectory()) {
                scanDirectory(f, list);
            } else if (f.getName().toLowerCase().endsWith(".udf")) {
                list.add(f);
            }
        }
    }
}
