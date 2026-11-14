package uyap;

import java.io.*;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * UYAP UDF -> PDF Toplu Dönüştürücü (Official Havelsan Engine)
 * Av. Can Kızılyar - naps-uyap-thunar-tools & udemobile motorlarından uyarlanmıştır.
 */
public class UdfBatchPdfConverter {

    private static final PrintStream CONSOLE = System.out;

    public static void main(String[] args) throws Exception {
        String rootDir = "/sdcard/Download/UYAP_Indirilenler";
        if (args.length > 0) {
            rootDir = args[0];
        }

        File targetDir = new File(rootDir);
        if (!targetDir.exists()) {
            CONSOLE.println("[HATA] Belirtilen dosya veya dizin bulunamadı: " + rootDir);
            System.exit(1);
        }

        List<File> allUdfs = new ArrayList<>();
        if (targetDir.isFile()) {
            if (targetDir.getName().toLowerCase().endsWith(".udf")) {
                allUdfs.add(targetDir);
            }
        } else {
            CONSOLE.println("==================================================================");
            CONSOLE.println("    UYAP UDF -> PDF Toplu Vektörel Dönüştürücü (Havelsan Motoru)");
            CONSOLE.println("==================================================================");
            CONSOLE.println("[*] Dizin taranıyor: " + targetDir.getAbsolutePath());
            scanDirectory(targetDir, allUdfs);
            CONSOLE.println("[✓] Toplam bulunan UDF sayısı: " + allUdfs.size() + " adet");
        }

        List<File> toConvert = new ArrayList<>();
        int alreadyExistsCount = 0;
        for (File udf : allUdfs) {
            String p = udf.getAbsolutePath();
            String pdfPath = p.substring(0, p.lastIndexOf('.')) + ".pdf";
            File pdfFile = new File(pdfPath);
            if (pdfFile.exists() && pdfFile.length() > 0) {
                alreadyExistsCount++;
            } else {
                toConvert.add(udf);
            }
        }

        CONSOLE.println("[✓] Zaten mevcut PDF sayısı   : " + alreadyExistsCount + " adet (atlandı)");
        CONSOLE.println("[*] Dönüştürülecek yeni UDF  : " + toConvert.size() + " adet");
        CONSOLE.println("------------------------------------------------------------------");

        if (toConvert.isEmpty()) {
            CONSOLE.println("[✓] Bütün UDF dosyalarının PDF kopyaları zaten mevcut! İşlem tamamlandı.");
            return;
        }

        // Havelsan debug loglarını /dev/null'a yönlendir
        PrintStream devNull = new PrintStream(new OutputStream() {
            @Override public void write(int b) {}
            @Override public void write(byte[] b, int off, int len) {}
        });
        System.setOut(devNull);
        System.setErr(devNull);

        // Havelsan motoru sınıfı ve metodu
        Class<?> bClass = Class.forName("tr.com.havelsan.uyap.system.editor.b.b");
        Method mConvert = bClass.getMethod("a", OutputStream.class, ByteArrayInputStream.class);

        int threads = Math.min(6, Math.max(2, Runtime.getRuntime().availableProcessors()));
        CONSOLE.println("[*] " + threads + " iş parçacığı (threads) ile dönüştürme başlatılıyor...");

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
                    if (done % 50 == 0 || done == totalToConvert) {
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

        CONSOLE.println("------------------------------------------------------------------");
        CONSOLE.printf("[✓] TAMAMLANDI: %d PDF başarıyla oluşturuldu, %d hata (Süre: %.1f sn)\n",
                successCounter.get(), failCounter.get(), totalSec);
        CONSOLE.println("==================================================================");
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
