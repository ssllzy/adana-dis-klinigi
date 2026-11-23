"""Authenticated local administration; never expose this demo to the internet."""
from contextlib import closing
from datetime import datetime, date, time as daytime, timedelta
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import json
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def prepare_admin(path):
    from server import connect
    from clinic_settings import ensure_settings
    credentials = path.with_name('local-admin.txt')
    with closing(connect(path)) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS admin_users (
            username TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS appointment_events (
            id INTEGER PRIMARY KEY, appointment_id INTEGER NOT NULL REFERENCES appointments(id),
            actor TEXT NOT NULL, action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (unixepoch()))''')
        db.execute('''CREATE TABLE IF NOT EXISTS appointment_flow (
            appointment_id INTEGER PRIMARY KEY REFERENCES appointments(id) ON DELETE CASCADE,
            stage TEXT NOT NULL CHECK(stage IN ('arrived','in_progress')),
            updated_by TEXT NOT NULL, updated_at INTEGER NOT NULL DEFAULT (unixepoch()))''')
        if 'revision' not in [r['name'] for r in db.execute('PRAGMA table_info(appointments)')]:
            db.execute('ALTER TABLE appointments ADD COLUMN revision INTEGER NOT NULL DEFAULT 0')
        if 'revision' not in [r['name'] for r in db.execute('PRAGMA table_info(doctors)')]:
            db.execute('ALTER TABLE doctors ADD COLUMN revision INTEGER NOT NULL DEFAULT 0')
        if 'active' not in [r['name'] for r in db.execute('PRAGMA table_info(doctor_services)')]:
            db.execute('ALTER TABLE doctor_services ADD COLUMN active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))')
        db.execute('''CREATE TABLE IF NOT EXISTS doctor_events (
            id INTEGER PRIMARY KEY, doctor_id INTEGER NOT NULL REFERENCES doctors(id), actor TEXT NOT NULL,
            before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at INTEGER NOT NULL DEFAULT (unixepoch()))''')
        db.execute('''CREATE TABLE IF NOT EXISTS doctor_requests (
            token TEXT PRIMARY KEY, digest TEXT NOT NULL, doctor_id INTEGER NOT NULL REFERENCES doctors(id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS closure_events (
            id INTEGER PRIMARY KEY, closure_id INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
            payload_json TEXT NOT NULL, created_at INTEGER NOT NULL DEFAULT (unixepoch()))''')
        db.execute('''CREATE TABLE IF NOT EXISTS closure_requests (
            token TEXT PRIMARY KEY, digest TEXT NOT NULL, closure_id INTEGER NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS patient_notes (
            patient_key TEXT PRIMARY KEY, note TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
            updated_by TEXT NOT NULL, updated_at INTEGER NOT NULL DEFAULT (unixepoch()))''')
        db.execute('''CREATE TABLE IF NOT EXISTS treatment_items (
            id INTEGER PRIMARY KEY, patient_key TEXT NOT NULL, title TEXT NOT NULL, tooth TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK(status IN ('planned','active','completed','cancelled')) DEFAULT 'planned',
            fee_cents INTEGER, note TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
            created_by TEXT NOT NULL, updated_by TEXT NOT NULL, created_at INTEGER NOT NULL DEFAULT (unixepoch()),
            updated_at INTEGER NOT NULL DEFAULT (unixepoch()))''')
        db.execute('''CREATE TABLE IF NOT EXISTS patient_payments (
            id INTEGER PRIMARY KEY, patient_key TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
            method TEXT NOT NULL CHECK(method IN ('cash','card','transfer')), note TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL CHECK(state IN ('active','void')) DEFAULT 'active', actor TEXT NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (unixepoch()), voided_by TEXT, voided_at INTEGER)''')
        ensure_settings(db)
        if not db.execute('SELECT 1 FROM admin_users').fetchone():
            password = secrets.token_urlsafe(18)
            salt = secrets.token_hex(16)
            # Saved outside the static root. Never print credentials into request logs.
            with credentials.open('x', encoding='utf-8') as output:
                output.write(f'Yerel demo yönetim paneli\nAdres: http://127.0.0.1:4180/yonetim\nKullanıcı: admin\nParola: {password}\n\nBu dosyayı paylaşmayın. Yalnızca yerel deneme içindir.\n')
            db.execute('INSERT INTO admin_users VALUES (?,?,?)',('admin',salt,password_hash(password,salt)))
    return credentials


class AdminSessions:
    def __init__(self):
        self.sessions = {}
        self.failures = []
        self.lock = threading.Lock()

    def login(self, db, username, password):
        from server import InvalidRequest
        with self.lock:
            now=time.monotonic()
            self.failures=[t for t in self.failures if t > now-300]
            if len(self.failures) >= 5:
                raise InvalidRequest('Çok fazla hatalı deneme. 5 dakika sonra tekrar deneyin.',429)
            if not isinstance(username,str) or not isinstance(password,str) or len(password)>256:
                self.failures.append(now)
                raise InvalidRequest('Kullanıcı adı veya parola yanlış.',401)
            row=db.execute('SELECT * FROM admin_users WHERE username=?',(username,)).fetchone()
            salt=row['salt'] if row else '0'*32
            digest=password_hash(password,salt)
            if not row or not hmac.compare_digest(digest,row['password_hash']):
                self.failures.append(now)
                raise InvalidRequest('Kullanıcı adı veya parola yanlış.',401)
            self.failures=[]
            self.sessions={k:v for k,v in self.sessions.items() if v['expires']>now}
            token=secrets.token_urlsafe(32)
            session={'username':username,'csrf':secrets.token_urlsafe(32),'expires':now+3600}
            self.sessions[token]=session
            return token,session

    def get(self, handler):
        try:
            cookie=SimpleCookie(handler.headers.get('Cookie',''))
            token=cookie['clinic_admin'].value if 'clinic_admin' in cookie else ''
        except CookieError:
            return None,None
        with self.lock:
            session=self.sessions.get(token)
            if session and session['expires']<=time.monotonic():
                self.sessions.pop(token,None)
                session=None
        return token,session


def update_appointment(db, appointment_id, data, actor):
    from server import InvalidRequest, available_slots, LOCAL_TIME
    if not isinstance(data,dict):
        raise InvalidRequest('Geçersiz işlem.')
    action=data.get('action')
    with db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('''SELECT a.*,COALESCE(f.stage,'expected') AS arrival_status
            FROM appointments a LEFT JOIN appointment_flow f ON f.appointment_id=a.id WHERE a.id=?''',(appointment_id,)).fetchone()
        if not current:
            raise InvalidRequest('Randevu bulunamadı.',404)
        if type(data.get('revision')) is not int or data['revision'] != current['revision']:
            raise InvalidRequest('Bu kayıt değişmiş. Listeyi yenileyip tekrar deneyin.',409)
        before={k:current[k] for k in ('status','starts_at','ends_at','doctor_id','revision','arrival_status')}
        status=current['status']
        start,end=current['starts_at'],current['ends_at']
        if action == 'arrive' and status == 'confirmed':
            if datetime.fromtimestamp(start,LOCAL_TIME).date() != datetime.now(LOCAL_TIME).date():
                raise InvalidRequest('Hasta yalnızca randevu gününde geldi olarak işaretlenebilir.',409)
            db.execute('''INSERT INTO appointment_flow(appointment_id,stage,updated_by) VALUES (?,'arrived',?)
                ON CONFLICT(appointment_id) DO UPDATE SET stage='arrived',updated_by=excluded.updated_by,updated_at=unixepoch()''',(appointment_id,actor))
        elif action == 'start' and status == 'confirmed' and current['arrival_status'] == 'arrived':
            db.execute("UPDATE appointment_flow SET stage='in_progress',updated_by=?,updated_at=unixepoch() WHERE appointment_id=?",(actor,appointment_id))
        elif action == 'cancel' and status in ('pending','confirmed'):
            status='cancelled'
            db.execute('DELETE FROM appointment_flow WHERE appointment_id=?',(appointment_id,))
        elif action == 'confirm' and status == 'pending':
            if start is None or current['doctor_id'] is None:
                raise InvalidRequest('Onaylamak için atanmış hekim ve saat gerekli.')
            day=datetime.fromtimestamp(start,LOCAL_TIME).strftime('%Y-%m-%d')
            clock=datetime.fromtimestamp(start,LOCAL_TIME).strftime('%H:%M')
            slots,_,_=available_slots(db,current['doctor_id'],current['service_id'],day,exclude_id=appointment_id)
            if clock not in slots:
                raise InvalidRequest('Saat artık uygun değil. Önce saati değiştirin veya çakışan talebi çözün.',409)
            status='confirmed'
        elif action == 'reschedule' and status in ('pending','confirmed'):
            slots,duration,midnight=available_slots(db,current['doctor_id'],current['service_id'],data.get('date'),exclude_id=appointment_id)
            if data.get('time') not in slots:
                raise InvalidRequest('Seçilen saat dolu veya uygun değil.',409)
            hour,minute=map(int,data['time'].split(':'))
            start=midnight+(hour*60+minute)*60
            end=start+duration*60
            db.execute('DELETE FROM appointment_flow WHERE appointment_id=?',(appointment_id,))
        elif action in ('complete','no_show') and status == 'confirmed':
            if start is None or start > datetime.now(LOCAL_TIME).timestamp():
                raise InvalidRequest('Gelecekteki randevu tamamlandı veya gelmedi olarak işaretlenemez.')
            status='completed' if action=='complete' else 'no_show'
            db.execute('DELETE FROM appointment_flow WHERE appointment_id=?',(appointment_id,))
        else:
            raise InvalidRequest('Bu randevu durumunda seçilen işlem yapılamaz.',409)
        db.execute('UPDATE appointments SET status=?,starts_at=?,ends_at=?,revision=revision+1 WHERE id=?',
                   (status,start,end,appointment_id))
        flow=db.execute('SELECT stage FROM appointment_flow WHERE appointment_id=?',(appointment_id,)).fetchone()
        after={**before,'status':status,'starts_at':start,'ends_at':end,'revision':current['revision']+1,
               'arrival_status':flow['stage'] if flow else 'expected'}
        db.execute('INSERT INTO appointment_events(appointment_id,actor,action,before_json,after_json) VALUES (?,?,?,?,?)',
                   (appointment_id,actor,action,json.dumps(before),json.dumps(after)))
    return {'ok':True}


def handle_admin(handler, data=None):
    from server import connect, InvalidRequest, available_slots, positive_id, LOCAL_TIME
    url=urlsplit(handler.path)
    method=handler.command
    sessions=handler.server.admin_sessions
    if method=='POST' and url.path=='/api/admin/login':
        if not isinstance(data,dict):
            raise InvalidRequest('Geçersiz giriş isteği.')
        with closing(connect(handler.server.db_path)) as db:
            token,session=sessions.login(db,data.get('username'),data.get('password'))
        handler.respond(200,{'username':session['username'],'csrf':session['csrf']},
            {'Set-Cookie':f'clinic_admin={token}; HttpOnly; SameSite=Strict; Path=/api/admin; Max-Age=3600'})
        return
    token,session=sessions.get(handler)
    if not session:
        raise InvalidRequest('Oturum açmanız gerekiyor.',401)
    if method=='POST' and not hmac.compare_digest(handler.headers.get('X-CSRF-Token',''),session['csrf']):
        raise InvalidRequest('Güvenlik doğrulaması başarısız. Yeniden giriş yapın.',403)
    if method=='GET' and url.path=='/api/admin/session':
        return handler.respond(200,{'username':session['username'],'csrf':session['csrf']})
    if url.path=='/api/admin/settings':
        from clinic_settings import get_settings, save_settings
        with closing(connect(handler.server.db_path)) as db:
            if method=='GET': return handler.respond(200,get_settings(db))
            if method=='POST': return handler.respond(200,save_settings(db,data,session['username']))
        raise InvalidRequest('Bulunamadı.',404)
    if url.path=='/api/admin/doctors' or url.path.startswith('/api/admin/doctors/'):
        from doctor_management import list_doctors, save_doctor
        with closing(connect(handler.server.db_path)) as db:
            if method=='GET' and url.path=='/api/admin/doctors':
                return handler.respond(200,list_doctors(db))
            if method=='POST':
                doctor_id=None if url.path=='/api/admin/doctors' else positive_id(url.path.rsplit('/',1)[-1])
                return handler.respond(200,save_doctor(db,doctor_id,data,session['username']))
        raise InvalidRequest('Bulunamadı.',404)
    if url.path=='/api/admin/closures' or url.path.startswith('/api/admin/closures/'):
        from closure_management import list_closures, create_closure, delete_closure
        with closing(connect(handler.server.db_path)) as db:
            if method=='GET' and url.path=='/api/admin/closures':
                return handler.respond(200,list_closures(db))
            if method=='POST' and url.path=='/api/admin/closures':
                return handler.respond(201,create_closure(db,data,session['username']))
            if method=='POST':
                closure_id=positive_id(url.path.rsplit('/',1)[-1])
                return handler.respond(200,delete_closure(db,closure_id,data,session['username']))
        raise InvalidRequest('Bulunamadı.',404)
    if url.path=='/api/admin/patients' or url.path.startswith('/api/admin/patients/'):
        from patient_management import list_patients, save_patient_note, save_treatment, save_payment, void_payment
        with closing(connect(handler.server.db_path)) as db:
            if method=='GET' and url.path=='/api/admin/patients':
                return handler.respond(200,list_patients(db,LOCAL_TIME))
            if method=='POST' and url.path.startswith('/api/admin/patients/'):
                parts=url.path.strip('/').split('/')
                if len(parts)==5 and parts[-1]=='treatments':
                    return handler.respond(201,save_treatment(db,parts[-2],None,data,session['username']))
                if len(parts)==6 and parts[-2]=='treatments':
                    return handler.respond(200,save_treatment(db,parts[-3],positive_id(parts[-1]),data,session['username']))
                if len(parts)==5 and parts[-1]=='payments':
                    return handler.respond(201,save_payment(db,parts[-2],data,session['username']))
                if len(parts)==6 and parts[-2]=='payments':
                    return handler.respond(200,void_payment(db,parts[-3],positive_id(parts[-1]),data,session['username']))
                return handler.respond(200,save_patient_note(db,parts[-1],data,session['username']))
        raise InvalidRequest('Bulunamadı.',404)
    if method=='POST' and url.path=='/api/admin/logout':
        with sessions.lock:
            sessions.sessions.pop(token,None)
        return handler.respond(200,{'ok':True},{'Set-Cookie':'clinic_admin=; HttpOnly; SameSite=Strict; Path=/api/admin; Max-Age=0'})
    if method=='GET' and url.path=='/api/admin/appointments':
        query=parse_qs(url.query)
        where=''
        params=()
        limit='LIMIT 500'
        if 'week' in query:
            try:
                start_day=date.fromisoformat(query['week'][0])
                if start_day.isoformat()!=query['week'][0] or not 1900<=start_day.year<=2099:
                    raise ValueError()
            except ValueError:
                raise InvalidRequest('Geçerli bir hafta tarihi seçin.')
            start=int(datetime.combine(start_day,daytime(),LOCAL_TIME).timestamp())
            where='WHERE a.starts_at>=? AND a.starts_at<?'
            params=(start,start+7*86400)
            limit=''
        with closing(connect(handler.server.db_path)) as db:
            rows=db.execute(f'''SELECT a.*,d.name AS doctor,s.name AS service,COALESCE(f.stage,'expected') AS arrival_status,
                EXISTS(SELECT 1 FROM appointments b WHERE b.id<>a.id AND b.doctor_id=a.doctor_id
                    AND b.status IN ('pending','confirmed') AND a.status IN ('pending','confirmed')
                    AND b.starts_at<a.ends_at AND b.ends_at>a.starts_at) AS conflict
                FROM appointments a LEFT JOIN doctors d ON d.id=a.doctor_id
                LEFT JOIN appointment_flow f ON f.appointment_id=a.id
                JOIN services s ON s.id=a.service_id {where} ORDER BY a.created_at DESC,a.id DESC {limit}''',params).fetchall()
            items=[]
            for row in rows:
                item=dict(row)
                item['date']=datetime.fromtimestamp(row['starts_at'],LOCAL_TIME).strftime('%Y-%m-%d') if row['starts_at'] else ''
                item['time']=datetime.fromtimestamp(row['starts_at'],LOCAL_TIME).strftime('%H:%M') if row['starts_at'] else ''
                item['end_time']=datetime.fromtimestamp(row['ends_at'],LOCAL_TIME).strftime('%H:%M') if row['ends_at'] else ''
                items.append(item)
            total=db.execute('SELECT count(*) FROM appointments').fetchone()[0]
            doctors=[dict(r) for r in db.execute('SELECT id,name,active FROM doctors')]
        return handler.respond(200,{'appointments':items,'doctors':doctors,'total':total,
                                   'today':datetime.now(LOCAL_TIME).date().isoformat()})
    if method=='POST' and url.path=='/api/admin/appointments':
        from server import create_request
        with closing(connect(handler.server.db_path)) as db:
            appointment_id=create_request(db,data,actor=session['username'])
        return handler.respond(201,{'id':appointment_id,'status':'confirmed','demo':True})
    if method=='GET' and url.path=='/api/admin/availability':
        query=parse_qs(url.query)
        appointment_id=positive_id(query.get('appointment_id',[''])[0])
        with closing(connect(handler.server.db_path)) as db:
            row=db.execute('SELECT * FROM appointments WHERE id=?',(appointment_id,)).fetchone()
            if not row:
                raise InvalidRequest('Randevu bulunamadı.',404)
            slots,_,_=available_slots(db,row['doctor_id'],row['service_id'],query.get('date',[''])[0],exclude_id=appointment_id)
        return handler.respond(200,{'slots':slots})
    if method=='GET' and url.path.startswith('/api/admin/appointments/') and url.path.endswith('/events'):
        parts=url.path.strip('/').split('/')
        appointment_id=positive_id(parts[-2]) if len(parts)==5 else positive_id('')
        with closing(connect(handler.server.db_path)) as db:
            appointment=db.execute('SELECT id,created_at FROM appointments WHERE id=?',(appointment_id,)).fetchone()
            if not appointment:
                raise InvalidRequest('Randevu bulunamadı.',404)
            rows=db.execute('SELECT id,actor,action,before_json,after_json,created_at FROM appointment_events WHERE appointment_id=? ORDER BY created_at,id',(appointment_id,)).fetchall()
            events=[]
            has_create=any(r['action']=='create' for r in rows)
            if not has_create:
                events.append({'id':0,'actor':'Web sitesi','action':'request','before':{},'after':{},
                    'created_at':appointment['created_at'],
                    'created_at_local':datetime.fromtimestamp(appointment['created_at'],LOCAL_TIME).strftime('%Y-%m-%dT%H:%M:%S')})
            for row in rows:
                try:
                    before=json.loads(row['before_json']);after=json.loads(row['after_json'])
                except (TypeError,json.JSONDecodeError):
                    before={};after={}
                events.append({'id':row['id'],'actor':row['actor'],'action':row['action'],'before':before,'after':after,
                    'created_at':row['created_at'],
                    'created_at_local':datetime.fromtimestamp(row['created_at'],LOCAL_TIME).strftime('%Y-%m-%dT%H:%M:%S')})
        return handler.respond(200,{'events':events})
    if method=='POST' and url.path.startswith('/api/admin/appointments/'):
        appointment_id=positive_id(url.path.rsplit('/',1)[-1])
        with closing(connect(handler.server.db_path)) as db:
            result=update_appointment(db,appointment_id,data,session['username'])
        return handler.respond(200,result)
    raise InvalidRequest('Bulunamadı.',404)
