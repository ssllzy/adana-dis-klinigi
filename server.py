"""Local-only booking demo. Not a production server or administration API."""
import argparse
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import parse_qs, unquote, urlsplit
from database.init_db import DEFAULT_PATH, SITE_ROOT, initialize
from database.migrate import upgrade
from admin_backend import AdminSessions, prepare_admin, handle_admin

# Keep the same exception/types when this file is launched as a script.
if __name__ == '__main__':
    sys.modules['server'] = sys.modules[__name__]

LOCAL_TIME = timezone(timedelta(hours=3), 'Europe/Istanbul')
DEMO_PATH = DEFAULT_PATH.with_name('clinic-demo.sqlite3')


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def prepare_demo(path):
    path = Path(path).resolve()
    if SITE_ROOT == path or SITE_ROOT in path.parents or path == DEFAULT_PATH.resolve():
        raise ValueError('Use a separate demo database outside the website directory.')
    if not path.exists():
        initialize(path)
    with closing(connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS demo_metadata (version INTEGER NOT NULL)')
        if not db.execute('SELECT 1 FROM demo_metadata').fetchone():
            if db.execute('SELECT 1 FROM doctors UNION ALL SELECT 1 FROM appointments').fetchone():
                raise ValueError('Refusing to add demo data to a populated database.')
            db.executemany('INSERT INTO doctors(id,name,specialty) VALUES (?,?,?)', [
                (1,'Demo Hekim 1','Deneme kaydı'),(2,'Demo Hekim 2','Deneme kaydı')])
            db.executemany('INSERT INTO services(id,name,duration_minutes) VALUES (?,?,?)', [
                (1,'Genel Muayene',30),(2,'İmplant',60),(3,'Ortodonti',45),(4,'Gülüş Estetiği',45)])
            db.executemany('INSERT INTO doctor_services VALUES (?,?)',[(1,1),(1,2),(1,4),(2,1),(2,3)])
            db.executemany('INSERT INTO working_hours(doctor_id,weekday,start_minute,end_minute) VALUES (?,?,?,?)',
                           [(doc,day,start,end) for doc in (1,2) for day in range(1,7)
                            for start,end in ((540,720),(780,1020))])
            db.execute('INSERT INTO demo_metadata VALUES (1)')
        db.execute('''CREATE TABLE IF NOT EXISTS submission_keys (
            token TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
            appointment_id INTEGER NOT NULL REFERENCES appointments(id))''')
        if 'active' not in [r['name'] for r in db.execute('PRAGMA table_info(doctor_services)')]:
            db.execute('ALTER TABLE doctor_services ADD COLUMN active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))')
    with closing(connect(path)) as db:
        upgrade(db)


class InvalidRequest(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def positive_id(value):
    if not re.fullmatch(r'[1-9][0-9]{0,8}', str(value)):
        raise InvalidRequest('Hekim ve görüşme konusu seçimini kontrol edin.')
    return int(value)


def available_slots(db, doctor, service, day_text, now=None, exclude_id=0):
    from clinic_settings import get_settings
    now = now or datetime.now(LOCAL_TIME)
    booking_window_days=get_settings(db)['booking_window_days']
    try:
        day = date.fromisoformat(day_text)
        if day.isoformat() != day_text or not now.date() <= day <= now.date()+timedelta(days=booking_window_days):
            raise ValueError()
    except (ValueError, TypeError):
        raise InvalidRequest(f'Bugün ile önümüzdeki {booking_window_days} gün arasında bir tarih seçin.')
    row = db.execute('''SELECT s.duration_minutes FROM doctor_services ds
        JOIN doctors d ON d.id=ds.doctor_id JOIN services s ON s.id=ds.service_id
        WHERE d.id=? AND s.id=? AND d.active=1 AND s.active=1 AND ds.active=1''',(doctor,service)).fetchone()
    if not row:
        raise InvalidRequest('Bu hekim seçilen görüşme konusu için uygun değil.')
    duration = row['duration_minutes']
    intervals = db.execute('SELECT start_minute,end_minute FROM working_hours WHERE doctor_id=? AND weekday=?',
                          (doctor,day.isoweekday())).fetchall()
    midnight = int(datetime.combine(day,time(),LOCAL_TIME).timestamp())
    blocked = db.execute('''SELECT starts_at,ends_at FROM closures
        WHERE (doctor_id IS NULL OR doctor_id=?) AND starts_at<? AND ends_at>?
        UNION ALL SELECT starts_at,ends_at FROM appointments
        WHERE doctor_id=? AND id<>? AND status IN ('pending','confirmed') AND starts_at<? AND ends_at>?''',
        (doctor,midnight+86400,midnight,doctor,exclude_id,midnight+86400,midnight)).fetchall()
    slots = set()
    for interval in intervals:
        for minute in range(interval['start_minute'],interval['end_minute']-duration+1,15):
            start = midnight + minute*60
            end = start + duration*60
            if start <= now.timestamp() or any(a < end and b > start for a,b in blocked):
                continue
            slots.add(f'{minute//60:02d}:{minute%60:02d}')
    return sorted(slots), duration, midnight


def create_request(db, data, *, actor=None):
    if not isinstance(data,dict):
        raise InvalidRequest('Geçersiz form verisi.')
    doctor, service = positive_id(data.get('doctor_id')),positive_id(data.get('service_id'))
    name,phone = data.get('name'),data.get('phone')
    if not isinstance(name,str) or not 2 <= len(name.strip()) <= 100 or any(ord(c)<32 for c in name):
        raise InvalidRequest('Ad soyad alanını kontrol edin.')
    if not isinstance(phone,str) or not re.fullmatch(r'\+?[0-9 ()-]{7,25}',phone) or not 10 <= len(re.sub(r'\D','',phone)) <= 15:
        raise InvalidRequest('Telefon numarası 10–15 rakam içermeli.')
    token=data.get('request_id','')
    if not isinstance(token,str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}',token):
        raise InvalidRequest('Sayfayı yenileyip tekrar deneyin.')
    normalized={k:data.get(k) for k in ('doctor_id','service_id','name','phone','date','time')}
    if actor is not None:
        token='admin:'+actor+':'+token
    digest=hashlib.sha256(json.dumps(normalized,sort_keys=True).encode()).hexdigest()
    with db:
        db.execute('BEGIN IMMEDIATE')
        previous=db.execute('SELECT * FROM submission_keys WHERE token=?',(token,)).fetchone()
        if previous:
            if previous['payload_hash'] != digest:
                raise InvalidRequest('Bu gönderim anahtarı farklı bir formda kullanıldı.',409)
            return previous['appointment_id']
        slots,duration,midnight=available_slots(db,doctor,service,data.get('date'))
        if data.get('time') not in slots:
            raise InvalidRequest('Seçilen saat uygun değil. Saat listesini yenileyip tekrar seçin.',409)
        hour,minute=map(int,data['time'].split(':'))
        start=midnight+(hour*60+minute)*60
        status='confirmed' if actor is not None else 'pending'
        result=db.execute('''INSERT INTO appointments
            (contact_name,contact_phone,doctor_id,service_id,starts_at,ends_at,status)
            VALUES (?,?,?,?,?,?,?)''',(name.strip(),phone.strip(),doctor,service,start,start+duration*60,status))
        db.execute('INSERT INTO submission_keys VALUES (?,?,?)',(token,digest,result.lastrowid))
        if actor is not None:
            after=dict(status=status,starts_at=start,ends_at=start+duration*60,doctor_id=doctor,service_id=service,revision=0)
            db.execute('INSERT INTO appointment_events(appointment_id,actor,action,before_json,after_json) VALUES (?,?,?,?,?)',
                       (result.lastrowid,actor,'create','{}',json.dumps(after)))
        return result.lastrowid


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, db_path):
        self.db_path = Path(db_path)
        prepare_admin(self.db_path)
        self.admin_sessions = AdminSessions()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not log submitted contact details or request URLs.

    def allowed_host(self):
        return self.headers.get('Host') in (f'localhost:{self.server.server_port}',f'127.0.0.1:{self.server.server_port}')

    def security_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','strict-origin-when-cross-origin')
        self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=(), payment=(), usb=()')
        self.send_header('Cross-Origin-Opener-Policy','same-origin')
        self.send_header('Cross-Origin-Resource-Policy','same-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'")

    def respond(self,status,data,headers=None):
        payload=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store')
        self.security_headers()
        for name,value in (headers or {}).items():
            self.send_header(name,value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if not self.allowed_host():
            return self.respond(403,{'error':'Yerel erişim gerekli.'})
        url=urlsplit(self.path)
        try:
            if url.path.startswith('/api/admin/'):
                return handle_admin(self)
            if url.path == '/api/catalog':
                with closing(connect(self.server.db_path)) as db:
                    from clinic_settings import get_settings
                    settings=get_settings(db)
                    services=[dict(r) for r in db.execute('SELECT id,name,duration_minutes FROM services WHERE active=1')]
                    doctors=[dict(r) for r in db.execute('SELECT id,name FROM doctors WHERE active=1')]
                    links=[dict(r) for r in db.execute('SELECT doctor_id,service_id FROM doctor_services WHERE active=1')]
                today=datetime.now(LOCAL_TIME).date()
                return self.respond(200,{'services':services,'doctors':doctors,'links':links,'demo':True,
                    'today':today.isoformat(),'max_date':(today+timedelta(days=settings['booking_window_days'])).isoformat()})
            if url.path == '/api/settings':
                from clinic_settings import get_settings
                with closing(connect(self.server.db_path)) as db: settings=get_settings(db)
                return self.respond(200,settings)
            if url.path == '/api/availability':
                query=parse_qs(url.query)
                doctor=positive_id(query.get('doctor_id',[''])[0])
                service=positive_id(query.get('service_id',[''])[0])
                with closing(connect(self.server.db_path)) as db:
                    slots,duration,_=available_slots(db,doctor,service,query.get('date',[''])[0])
                return self.respond(200,{'slots':slots,'duration_minutes':duration})
            if url.path.startswith('/api/'):
                return self.respond(404,{'error':'Bulunamadı.'})
            relative=unquote(url.path).lstrip('/') or 'index.html'
            if relative in ('yonetim','yonetim/'):
                relative='admin.html'
            target=(SITE_ROOT/relative).resolve()
            static={'index.html','style.css','hero.css','clinic.css','script.js','hero.js','booking.js','intro-bootstrap.js','vendor/motion.js','admin.html','admin.css','admin.js','doctors.js','closures.js','patients.js','settings.js','robots.txt','sitemap.xml','site.webmanifest','favicon.svg'}
            image_allowed=target.parent == (SITE_ROOT/'assets').resolve() and target.suffix in ('.png','.jpg','.webp')
            if (relative not in static and not image_allowed) or SITE_ROOT not in target.parents or not target.is_file():
                return self.respond(404,{'error':'Bulunamadı.'})
            payload=target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type',mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-cache')
            self.security_headers()
            self.end_headers()
            self.wfile.write(payload)
        except InvalidRequest as exc:
            self.respond(exc.status,{'error':str(exc)})
        except sqlite3.Error as exc:
            print(f'Local SQLite error: {type(exc).__name__}: {exc}', file=sys.stderr)
            self.respond(503,{'error':'Veritabanına ulaşılamadı. Lütfen tekrar deneyin.'})

    def do_POST(self):
        expected=f'http://{self.headers.get("Host")}'
        if not self.allowed_host() or self.headers.get('Origin') != expected:
            return self.respond(403,{'error':'İstek aynı yerel siteden gelmeli.'})
        if self.path != '/api/appointments' and not self.path.startswith('/api/admin/'):
            return self.respond(404,{'error':'Bulunamadı.'})
        if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
            return self.respond(415,{'error':'JSON gerekli.'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 4096:
                return self.respond(413,{'error':'Geçersiz istek boyutu.'})
            data=json.loads(self.rfile.read(length))
            if self.path.startswith('/api/admin/'):
                return handle_admin(self,data)
            with closing(connect(self.server.db_path)) as db:
                appointment_id=create_request(db,data)
            self.respond(201,{'id':appointment_id,'status':'pending','demo':True})
        except InvalidRequest as exc:
            self.respond(exc.status,{'error':str(exc)})
        except (ValueError,UnicodeError):
            self.respond(400,{'error':'Geçersiz istek.'})
        except sqlite3.IntegrityError as exc:
            if 'doctor_slot_conflict' in str(exc):
                self.respond(409,{'error':'Bu saat başka bir talep tarafından ayrıldı. Lütfen başka saat seçin.'})
            else:
                self.respond(400,{'error':'Randevu bilgilerini kontrol edip tekrar deneyin.'})
        except sqlite3.Error as exc:
            self.respond(503,{'error':'Kayıt tamamlanamadı. Aynı formu tekrar deneyebilirsiniz.'})


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=4180)
    parser.add_argument('--database',type=Path,default=DEMO_PATH)
    args=parser.parse_args()
    prepare_demo(args.database)
    with LocalServer(('127.0.0.1',args.port),args.database) as server:
        print(f'Local demo: http://127.0.0.1:{server.server_port}\nDatabase: {args.database}',flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
