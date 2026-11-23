# Doğrulama — 23 Kasım 2026

- `node build-netlify.mjs`: statik demo paketi üretildi.
- `node --test hero.test.cjs`: 4/4 test geçti.
- Python unittest: toplam 41 test; 19 başarılı, 22 HTTP testi ortamın soket erişim kısıtı (WinError 10013) nedeniyle tamamlanamadı.
- Kişisel SQLite dosyaları, yönetici bilgisi, şifreli yedek ve yedek açma anahtarı dahil edilmedi.
- README görseli mevcut ana sayfa ekran görüntüsüdür; gerçek hasta bilgisi içermez.

Yayınlanacak kaynaklar, mevcut gizli değerlerle eşleşme ve yaygın erişim anahtarı biçimleri bakımından tarandı. Ortam dosyaları, yerel veriler, günlükler, çalışma notları ve yedekler paylaşılmaz.
