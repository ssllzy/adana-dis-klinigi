"""Patient profiles derived from appointments, with private local admin notes."""
from datetime import datetime
import re


def patient_key(phone):
    return ''.join(re.findall(r'\d', phone or ''))


def list_patients(db, local_time):
    from server import InvalidRequest
    rows=db.execute('''SELECT a.*,d.name AS doctor,s.name AS service
        FROM appointments a LEFT JOIN doctors d ON d.id=a.doctor_id
        JOIN services s ON s.id=a.service_id ORDER BY a.created_at DESC,a.id DESC''').fetchall()
    groups={}
    for row in rows:
        key=patient_key(row['contact_phone'])
        if not key:
            continue
        profile=groups.setdefault(key,{'key':key,'name':row['contact_name'],'phone':row['contact_phone'],
            'appointment_count':0,'completed_count':0,'no_show_count':0,'cancelled_count':0,
            'last_visit':None,'next_appointment':None,'appointments':[]})
        profile['appointment_count']+=1
        if row['status']=='completed': profile['completed_count']+=1
        if row['status']=='no_show': profile['no_show_count']+=1
        if row['status']=='cancelled': profile['cancelled_count']+=1
        at=datetime.fromtimestamp(row['starts_at'],local_time) if row['starts_at'] else None
        now=datetime.now(local_time)
        if at and at<=now and (profile['last_visit'] is None or at>profile['last_visit']): profile['last_visit']=at
        if at and at>now and row['status'] in ('pending','confirmed') and (profile['next_appointment'] is None or at<profile['next_appointment']): profile['next_appointment']=at
        profile['appointments'].append({'id':row['id'],'status':row['status'],'doctor':row['doctor'] or 'Atanmamış',
            'service':row['service'],'date':at.strftime('%Y-%m-%d') if at else '',
            'time':at.strftime('%H:%M') if at else ''})
    notes={r['patient_key']:dict(r) for r in db.execute('SELECT * FROM patient_notes')}
    treatments={}
    for row in db.execute('SELECT * FROM treatment_items ORDER BY created_at DESC,id DESC'):
        treatments.setdefault(row['patient_key'],[]).append(dict(row))
    payments={}
    for row in db.execute('SELECT * FROM patient_payments ORDER BY created_at DESC,id DESC'):
        payments.setdefault(row['patient_key'],[]).append(dict(row))
    items=[]
    for profile in groups.values():
        note=notes.get(profile['key'])
        profile['note']=note['note'] if note else ''
        profile['note_revision']=note['revision'] if note else 0
        profile['treatments']=treatments.get(profile['key'],[])
        profile['payments']=payments.get(profile['key'],[])
        profile['last_visit']=profile['last_visit'].strftime('%Y-%m-%dT%H:%M:%S') if profile['last_visit'] else None
        profile['next_appointment']=profile['next_appointment'].strftime('%Y-%m-%dT%H:%M:%S') if profile['next_appointment'] else None
        items.append(profile)
    items.sort(key=lambda p:(p['next_appointment'] is None,p['next_appointment'] or '',p['name'].casefold()))
    return {'patients':items,'total':len(items)}


def save_patient_note(db, key, data, actor):
    from server import InvalidRequest
    if not re.fullmatch(r'\d{7,20}',key or ''):
        raise InvalidRequest('Hasta profili bulunamadı.',404)
    if not isinstance(data,dict) or not isinstance(data.get('note'),str) or type(data.get('revision')) is not int:
        raise InvalidRequest('Geçersiz hasta notu.')
    note=data['note'].strip()
    if len(note)>2000 or any(ord(c)<32 and c not in '\n\t' for c in note):
        raise InvalidRequest('Hasta notu en fazla 2000 karakter olabilir.')
    exists=db.execute('SELECT 1 FROM appointments WHERE replace(replace(replace(replace(replace(contact_phone,\' \',\'\'),\'-\',\'\'),\'(\',\'\'),\')\',\'\'),\'+\',\'\')=?',(key,)).fetchone()
    if not exists:
        # Formatting can contain other separators; verify with the same canonicalizer.
        exists=any(patient_key(r['contact_phone'])==key for r in db.execute('SELECT contact_phone FROM appointments'))
    if not exists: raise InvalidRequest('Hasta profili bulunamadı.',404)
    with db:
        current=db.execute('SELECT revision FROM patient_notes WHERE patient_key=?',(key,)).fetchone()
        revision=current['revision'] if current else 0
        if data['revision']!=revision:
            raise InvalidRequest('Bu hasta notu değişmiş. Profili yenileyip tekrar deneyin.',409)
        db.execute('''INSERT INTO patient_notes(patient_key,note,revision,updated_by,updated_at)
            VALUES (?,?,1,?,unixepoch()) ON CONFLICT(patient_key) DO UPDATE SET
            note=excluded.note,revision=patient_notes.revision+1,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
            (key,note,actor))
    return {'ok':True,'revision':revision+1}


def save_treatment(db,key,item_id,data,actor):
    from server import InvalidRequest
    if not re.fullmatch(r'\d{7,20}',key or '') or not isinstance(data,dict): raise InvalidRequest('Geçersiz tedavi kaydı.')
    title=data.get('title');tooth=data.get('tooth','');note=data.get('note','');status=data.get('status','planned');fee=data.get('fee')
    if not isinstance(title,str) or not 2<=len(title.strip())<=120 or not isinstance(tooth,str) or len(tooth)>40 or not isinstance(note,str) or len(note)>1000 or status not in ('planned','active','completed','cancelled'):
        raise InvalidRequest('Tedavi bilgilerini kontrol edin.')
    try: fee_cents=None if fee in (None,'') else round(float(fee)*100)
    except (TypeError,ValueError): raise InvalidRequest('Geçerli bir ücret yazın.')
    if fee_cents is not None and not 0<=fee_cents<=100000000: raise InvalidRequest('Geçerli bir ücret yazın.')
    with db:
        if item_id is None:
            cur=db.execute('''INSERT INTO treatment_items(patient_key,title,tooth,status,fee_cents,note,created_by,updated_by)
                VALUES (?,?,?,?,?,?,?,?)''',(key,title.strip(),tooth.strip(),status,fee_cents,note.strip(),actor,actor))
            item_id=cur.lastrowid
        else:
            row=db.execute('SELECT * FROM treatment_items WHERE id=? AND patient_key=?',(item_id,key)).fetchone()
            if not row: raise InvalidRequest('Tedavi kaydı bulunamadı.',404)
            if type(data.get('revision')) is not int or data['revision']!=row['revision']: raise InvalidRequest('Tedavi kaydı değişmiş. Profili yenileyin.',409)
            db.execute('''UPDATE treatment_items SET title=?,tooth=?,status=?,fee_cents=?,note=?,revision=revision+1,updated_by=?,updated_at=unixepoch() WHERE id=?''',(title.strip(),tooth.strip(),status,fee_cents,note.strip(),actor,item_id))
    return {'ok':True,'id':item_id}


def save_payment(db,key,data,actor):
    from server import InvalidRequest
    if not re.fullmatch(r'\d{7,20}',key or '') or not isinstance(data,dict): raise InvalidRequest('Geçersiz ödeme kaydı.')
    try: amount=round(float(data.get('amount'))*100)
    except (TypeError,ValueError): raise InvalidRequest('Geçerli bir ödeme tutarı yazın.')
    method=data.get('method');note=data.get('note','')
    if not 0<amount<=100000000 or method not in ('cash','card','transfer') or not isinstance(note,str) or len(note)>500: raise InvalidRequest('Ödeme bilgilerini kontrol edin.')
    with db:
        cur=db.execute('INSERT INTO patient_payments(patient_key,amount_cents,method,note,actor) VALUES (?,?,?,?,?)',(key,amount,method,note.strip(),actor))
    return {'ok':True,'id':cur.lastrowid}


def void_payment(db,key,payment_id,data,actor):
    from server import InvalidRequest
    if not isinstance(data,dict) or data.get('action')!='void': raise InvalidRequest('Geçersiz ödeme işlemi.')
    with db:
        row=db.execute('SELECT state FROM patient_payments WHERE id=? AND patient_key=?',(payment_id,key)).fetchone()
        if not row: raise InvalidRequest('Ödeme kaydı bulunamadı.',404)
        if row['state']!='active': raise InvalidRequest('Bu ödeme zaten iptal edilmiş.',409)
        db.execute("UPDATE patient_payments SET state='void',voided_by=?,voided_at=unixepoch() WHERE id=?",(actor,payment_id))
    return {'ok':True}
