# Yerel randevu veritabanı — ilk sürüm

SQLite, Python standart kütüphanesiyle çalışır; ayrı veritabanı sunucusu gerekmez.
Bu bir geliştirme prototipidir. Gerçek hasta bilgileri girmeyin.

## Oluşturma ve test

```powershell
python database/init_db.py
python -m unittest discover -s database -p "test_*.py" -v
```

Varsayılan dosya: `%LOCALAPPDATA%/OzYilmazClinic/clinic.sqlite3`.
Mevcut dosya korunur; oluşturucu aynı dosyanın üzerine yazmaz.
Veritabanı özellikle web sitesinin klasörünün DIŞINDA tutulur. Statik sunucuya,
Git deposuna veya yayın paketine eklenmemelidir. Her yeni SQLite bağlantısında
`PRAGMA foreign_keys = ON` etkinleştirilmelidir.

## Tablolar

| Tablo | İçerik |
| --- | --- |
| doctors | Hekim ve aktiflik bilgisi |
| services | Hizmet ve planlanan süre |
| doctor_services | Hekimin sunabildiği hizmetler |
| working_hours | Haftalık çalışma aralıkları; öğle arası ayrı aralıklarla tanımlanabilir |
| closures | Hekim veya tüm klinik için kapalı zaman aralıkları |
| appointments | İletişim adı/telefonu, hizmet, atanmış hekim, saat ve durum |

Randevu durumları: `pending` (bekliyor), `confirmed` (onaylandı), `cancelled`
(iptal), `completed` (tamamlandı), `no_show` (gelmedi). Bekleyen talebin hekimi
ve saati boş olabilir. Onay için hekim, başlangıç ve bitiş gerekir.
Bekleyen ve onaylı randevularda aynı hekimin çakışan saatleri INSERT ve UPDATE tetikleyicileriyle
engellenir; art arda randevulara izin verilir. Bekleyen talepler de zaman aralığını ayırır.

Randevu ve kapanış zamanları UTC Unix saniyesidir. Çalışma saatleri
`Europe/Istanbul` yerel saatinde, gece yarısından itibaren dakika olarak saklanır.

## Yerel servis eklendi

`python server.py` ile 127.0.0.1:4180 üzerinde çalışan demo artık formu SQLite'a
bağlar. Ayrı `clinic-demo.sqlite3` dosyası kullanılır; orijinal `clinic.sqlite3`
değiştirilmez. Demo dosyasında `demo_metadata` ve tekrar gönderim engeli için
`submission_keys` tabloları da bulunur.

Çalışma saatleri, kapanışlar, aktif hekim/hizmet eşleşmesi, görüşme süresi ve
geçmiş tarih kontrolleri artık yerel API'de uygulanır. Gönderim sırasında uygunluk
SQLite yazma işlemi içinde tekrar kontrol edilir. Bekleyen talepler de saat ayırır;
onaylama ve takvim yönetimi /yonetim adresindeki oturum korumalı panelden yapılır.

API: GET /api/catalog, GET /api/availability, POST /api/appointments.
Hasta/talep listesi sunan açık API yoktur. POST aynı Origin ve JSON gerektirir;
Host yalnızca yerel adres olabilir. Statik servis Python kaynaklarını, şemayı,
veritabanını ve testleri sunmaz. Üretim kimlik doğrulaması, hız sınırı, TLS,
yedekleme ve veri saklama kuralları henüz tamamlanmadığından internete açmayın.

Testler geçici SQLite dosyaları ve sentetik bilgiler de kullanır; çalışma sonunda
bu geçici test dosyaları temizlenir. Tarayıcı uçtan uca deneme kaydı ayrı demo
veritabanında kalabilir.



## Yerel yönetim paneli

Demo başlangıcında admin_users, appointment_events tabloları ve appointments.revision
alanı eklenir. Kullanıcı parolası scrypt ile özetlenir. Başlangıç giriş bilgileri
veritabanının yanındaki local-admin.txt dosyasındadır; statik servis bunu sunmaz.

/api/admin/appointments yalnızca oturum açmış yöneticiye liste döndürür. İşlemler
aynı kaynak kontrolü, oturum çerezi ve CSRF belirteci gerektirir. Her değişiklik
revision kontrolüyle yapılır; çakışmalar ve geçersiz durum geçişleri reddedilir.
İşlem geçmişi aynı veritabanı işlemi içinde kaydedilir. Demo kayıtları silinmez.

Oturumlar bellekte tutulur ve bir saatte sona erer. Servis yeniden başlayınca
tekrar giriş gerekir. Personel rolleri, parola değiştirme/kurtarma, üretim HTTPS
ve veri saklama politikaları henüz kapsam dışındadır. Gerçek hasta verisi kullanmayın.
