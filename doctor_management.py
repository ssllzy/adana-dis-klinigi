"""Transactional doctor and recurring weekly schedule administration."""
from datetime import datetime
import hashlib
import json
import re


def list_doctors(db):
    items=[]
    for row in db.execute('SELECT * FROM doctors ORDER BY active DESC,id'):
        item=dict(row)
        item['service_ids']=[r[0] for r in db.execute('SELECT service_id FROM doctor_services WHERE doctor_id=? AND active=1',(row['id'],))]
        item['hours']=[dict(r) for r in db.execute('SELECT weekday,start_minute,end_minute FROM working_hours WHERE doctor_id=? ORDER BY weekday,start_minute',(row['id'],))]
        items.append(item)
    return {'doctors':items,'services':[dict(r) for r in db.execute('SELECT * FROM services WHERE active=1')]}


def save_doctor(db, doctor_id, data, actor):
    from server import InvalidRequest, LOCAL_TIME
    if not isinstance(data,dict):
        raise InvalidRequest('Geçersiz hekim bilgisi.')
    name,specialty=data.get('name'),data.get('specialty','')
    if not isinstance(name,str) or not 2<=len(name.strip())<=120 or any(ord(c)<32 for c in name):
        raise InvalidRequest('Hekim adı 2–120 karakter olmalı.')
    if not isinstance(specialty,str) or len(specialty)>160 or any(ord(c)<32 for c in specialty):
        raise InvalidRequest('Uzmanlık bilgisi en fazla 160 karakter olmalı.')
    active=data.get('active')
    services=data.get('service_ids')
    hours=data.get('hours')
    if type(active) is not bool or not isinstance(services,list) or len(services)>100 or any(type(s) is not int for s in services):
        raise InvalidRequest('Durum ve hizmet seçimini kontrol edin.')
    services=sorted(set(services))
    if active and not services:
        raise InvalidRequest('Aktif hekim için en az bir hizmet seçin.')
    if not isinstance(hours,list) or len(hours)>28:
        raise InvalidRequest('En fazla 28 çalışma aralığı ekleyebilirsiniz.')
    normalized=[]
    for h in hours:
        if not isinstance(h,dict) or any(type(h.get(k)) is not int for k in ('weekday','start_minute','end_minute')):
            raise InvalidRequest('Çalışma aralığını kontrol edin.')
        day,start,end=h['weekday'],h['start_minute'],h['end_minute']
        if not (1<=day<=7 and 0<=start<end<=1440) or start%15 or end%15:
            raise InvalidRequest('Saatler 15 dakikalık aralıklarla, aynı gün içinde ve başlangıç bitişten önce olmalı.')
        normalized.append(dict(weekday=day,start_minute=start,end_minute=end))
    normalized.sort(key=lambda h:(h['weekday'],h['start_minute']))
    if any(sum(h['weekday']==day for h in normalized)>4 for day in range(1,8)):
        raise InvalidRequest('Her gün için en fazla dört çalışma aralığı ekleyebilirsiniz.')
    for a,b in zip(normalized,normalized[1:]):
        if a['weekday']==b['weekday'] and a['end_minute']>b['start_minute']:
            raise InvalidRequest('Aynı gündeki çalışma aralıkları çakışıyor.')
    after=dict(name=name.strip(),specialty=specialty.strip(),active=active,service_ids=services,hours=normalized)
    digest=hashlib.sha256(json.dumps(after,sort_keys=True).encode()).hexdigest()
    with db:
        db.execute('BEGIN IMMEDIATE')
        before=next((d for d in list_doctors(db)['doctors'] if d['id']==doctor_id),None)
        if doctor_id is not None:
            if before is None:
                raise InvalidRequest('Hekim bulunamadı.',404)
            if type(data.get('revision')) is not int or data['revision']!=before['revision']:
                raise InvalidRequest('Hekim bilgisi değişmiş. Pencereyi kapatıp listeyi yenileyin.',409)
        else:
            token=data.get('request_id')
            if not isinstance(token,str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}',token):
                raise InvalidRequest('Formu yeniden açın.')
            token=actor+':'+token
            previous=db.execute('SELECT * FROM doctor_requests WHERE token=?',(token,)).fetchone()
            if previous:
                if previous['digest']!=digest:
                    raise InvalidRequest('Gönderim anahtarı farklı bir formda kullanıldı.',409)
                return {'id':previous['doctor_id']}
        valid={r[0] for r in db.execute('SELECT id FROM services WHERE active=1')}
        if not set(services)<=valid:
            raise InvalidRequest('Seçilen hizmet artık aktif değil. Listeyi yenileyin.')
        impacted=[]
        for a in db.execute("SELECT id,service_id,starts_at,ends_at FROM appointments WHERE doctor_id=? AND status IN ('pending','confirmed') AND (ends_at>? OR starts_at IS NULL)",(doctor_id,datetime.now(LOCAL_TIME).timestamp())):
            fits=False
            if a['starts_at'] is not None:
                start=datetime.fromtimestamp(a['starts_at'],LOCAL_TIME)
                midnight=int(start.replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
                fits=any(h['weekday']==start.isoweekday() and midnight+h['start_minute']*60<=a['starts_at'] and midnight+h['end_minute']*60>=a['ends_at'] for h in normalized)
            if not active or a['service_id'] not in services or not fits:
                impacted.append(a['id'])
        if impacted:
            ids=', '.join('#'+str(i) for i in impacted[:10])
            raise InvalidRequest(f'Değişiklik {len(impacted)} açık randevuyu etkiliyor ({ids}). Önce bu randevuları taşıyın veya iptal edin. Hiçbir değişiklik kaydedilmedi.',409)
        if doctor_id is None:
            doctor_id=db.execute('INSERT INTO doctors(name,specialty,active) VALUES (?,?,?)',(after['name'],after['specialty'],int(active))).lastrowid
            db.execute('INSERT INTO doctor_requests VALUES (?,?,?)',(token,digest,doctor_id))
        else:
            db.execute('UPDATE doctors SET name=?,specialty=?,active=?,revision=revision+1 WHERE id=?',(after['name'],after['specialty'],int(active),doctor_id))
        # Keep historical foreign-key relationships, but stop offering removed services.
        db.execute('UPDATE doctor_services SET active=0 WHERE doctor_id=?',(doctor_id,))
        for service in services:
            db.execute('INSERT INTO doctor_services(doctor_id,service_id,active) VALUES (?,?,1) ON CONFLICT(doctor_id,service_id) DO UPDATE SET active=1',(doctor_id,service))
        db.execute('DELETE FROM working_hours WHERE doctor_id=?',(doctor_id,))
        db.executemany('INSERT INTO working_hours(doctor_id,weekday,start_minute,end_minute) VALUES (?,?,?,?)',[(doctor_id,h['weekday'],h['start_minute'],h['end_minute']) for h in normalized])
        db.execute('INSERT INTO doctor_events(doctor_id,actor,before_json,after_json) VALUES (?,?,?,?)',(doctor_id,actor,json.dumps(before),json.dumps(after)))
    return {'id':doctor_id}
