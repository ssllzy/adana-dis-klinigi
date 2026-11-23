"""Clinic and doctor-specific closure administration."""
from datetime import date, datetime, timedelta
import hashlib
import json
import re


def _item(row, local_time):
    item=dict(row)
    item['starts_at_local']=datetime.fromtimestamp(row['starts_at'],local_time).strftime('%Y-%m-%dT%H:%M')
    item['ends_at_local']=datetime.fromtimestamp(row['ends_at'],local_time).strftime('%Y-%m-%dT%H:%M')
    return item


def list_closures(db):
    from server import LOCAL_TIME
    rows=db.execute('''SELECT c.*,d.name AS doctor FROM closures c LEFT JOIN doctors d ON d.id=c.doctor_id
        WHERE c.ends_at>? ORDER BY c.starts_at,c.id''',(int(datetime.now(LOCAL_TIME).timestamp()),)).fetchall()
    doctors=[dict(r) for r in db.execute('SELECT id,name,active FROM doctors ORDER BY active DESC,name')]
    return {'closures':[_item(r,LOCAL_TIME) for r in rows],'doctors':doctors,
            'today':datetime.now(LOCAL_TIME).date().isoformat(),
            'max_date':(datetime.now(LOCAL_TIME).date()+timedelta(days=730)).isoformat()}


def create_closure(db, data, actor):
    from server import InvalidRequest, LOCAL_TIME
    if not isinstance(data,dict):raise InvalidRequest('Geçersiz izin bilgisi.')
    doctor=data.get('doctor_id')
    if doctor is not None and type(doctor) is not int:raise InvalidRequest('Hekim seçimini kontrol edin.')
    label=data.get('label')
    if not isinstance(label,str) or not 2<=len(label.strip())<=120 or any(ord(c)<32 for c in label):
        raise InvalidRequest('Açıklama 2–120 karakter olmalı.')
    def parse(value):
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}',value):raise ValueError()
        result=datetime.fromisoformat(value).replace(tzinfo=LOCAL_TIME)
        if result.strftime('%Y-%m-%dT%H:%M')!=value or result.minute%15:raise ValueError()
        return result
    try:start,end=parse(data.get('starts_at')),parse(data.get('ends_at'))
    except ValueError:raise InvalidRequest('Başlangıç ve bitişi 15 dakikalık aralıklarla seçin.')
    today=datetime.now(LOCAL_TIME).date()
    if start.date()<today or start.date()>today+timedelta(days=730):
        raise InvalidRequest('Bugün ile önümüzdeki iki yıl arasında bir başlangıç seçin.')
    if not start<end or end-start>timedelta(days=366):raise InvalidRequest('Bitiş başlangıçtan sonra ve en fazla bir yıl içinde olmalı.')
    start_ts,end_ts=int(start.timestamp()),int(end.timestamp())
    normalized={'doctor_id':doctor,'label':label.strip(),'starts_at':start_ts,'ends_at':end_ts}
    digest=hashlib.sha256(json.dumps(normalized,sort_keys=True).encode()).hexdigest()
    token=data.get('request_id')
    if not isinstance(token,str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}',token):raise InvalidRequest('Formu yeniden açın.')
    token=actor+':'+token
    with db:
        db.execute('BEGIN IMMEDIATE')
        previous=db.execute('SELECT * FROM closure_requests WHERE token=?',(token,)).fetchone()
        if previous:
            if previous['digest']!=digest:raise InvalidRequest('Gönderim anahtarı farklı bir formda kullanıldı.',409)
            return {'id':previous['closure_id']}
        if doctor is not None and not db.execute('SELECT 1 FROM doctors WHERE id=? AND active=1',(doctor,)).fetchone():
            raise InvalidRequest('Seçilen hekim aktif değil.')
        appointments=db.execute('''SELECT id FROM appointments WHERE status IN ('pending','confirmed')
            AND starts_at<? AND ends_at>? AND (? IS NULL OR doctor_id=?) ORDER BY starts_at,id''',
            (end_ts,start_ts,doctor,doctor)).fetchall()
        if appointments:
            ids=', '.join('#'+str(r['id']) for r in appointments[:10])
            raise InvalidRequest(f'Bu kapanış {len(appointments)} açık randevuyla çakışıyor ({ids}). Önce randevuları taşıyın veya iptal edin. Hiçbir değişiklik kaydedilmedi.',409)
        result=db.execute('INSERT INTO closures(doctor_id,starts_at,ends_at,label) VALUES (?,?,?,?)',(doctor,start_ts,end_ts,normalized['label']))
        closure_id=result.lastrowid
        db.execute('INSERT INTO closure_requests VALUES (?,?,?)',(token,digest,closure_id))
        db.execute('INSERT INTO closure_events(closure_id,actor,action,payload_json) VALUES (?,?,?,?)',(closure_id,actor,'create',json.dumps(normalized)))
    return {'id':closure_id}


def delete_closure(db, closure_id, data, actor):
    from server import InvalidRequest
    if not isinstance(data,dict) or data.get('action')!='delete':raise InvalidRequest('Geçersiz işlem.')
    with db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM closures WHERE id=?',(closure_id,)).fetchone()
        if not row:raise InvalidRequest('İzin veya kapanış bulunamadı.',404)
        payload={k:row[k] for k in ('doctor_id','starts_at','ends_at','label')}
        db.execute('INSERT INTO closure_events(closure_id,actor,action,payload_json) VALUES (?,?,?,?)',(closure_id,actor,'delete',json.dumps(payload)))
        db.execute('DELETE FROM closure_requests WHERE closure_id=?',(closure_id,))
        db.execute('DELETE FROM closures WHERE id=?',(closure_id,))
    return {'ok':True}
