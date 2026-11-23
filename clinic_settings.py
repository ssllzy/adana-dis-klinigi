"""Validated clinic settings shared by the public site and local admin."""
import re

DEFAULTS = {
    'clinic_name': 'Adana Diş',
    'phone': '',
    'whatsapp': '',
    'address': '',
    'working_hours_note': '',
    'booking_window_days': '90',
}


def ensure_settings(db):
    db.execute('''CREATE TABLE IF NOT EXISTS clinic_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_by TEXT,
        updated_at INTEGER NOT NULL DEFAULT (unixepoch()))''')
    db.executemany('INSERT OR IGNORE INTO clinic_settings(key,value) VALUES (?,?)', DEFAULTS.items())


def get_settings(db):
    values=DEFAULTS.copy()
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='clinic_settings'").fetchone():
        values.update({row['key']:row['value'] for row in db.execute('SELECT key,value FROM clinic_settings')})
    values['booking_window_days']=int(values['booking_window_days'])
    return values


def save_settings(db, data, actor):
    from server import InvalidRequest
    if not isinstance(data,dict): raise InvalidRequest('Geçersiz ayar bilgisi.')
    required=('clinic_name','phone','whatsapp','address','working_hours_note','booking_window_days')
    if any(key not in data for key in required): raise InvalidRequest('Tüm ayar alanlarını gönderin.')
    text={key:data[key].strip() if isinstance(data[key],str) else None for key in required[:-1]}
    if text['clinic_name'] is None or not 2<=len(text['clinic_name'])<=100: raise InvalidRequest('Klinik adı 2–100 karakter olmalı.')
    if any(text[key] is None or len(text[key])>limit for key,limit in [('phone',30),('whatsapp',30),('address',300),('working_hours_note',200)]): raise InvalidRequest('İletişim bilgilerini kontrol edin.')
    for key in ('phone','whatsapp'):
        if text[key] and (not re.fullmatch(r'\+?[0-9 ()-]{7,30}',text[key]) or not 10<=len(re.sub(r'\D','',text[key]))<=15): raise InvalidRequest('Telefon numaraları 10–15 rakam içermeli.')
    if any(any(ord(char)<32 and char not in '\n\t' for char in value) for value in text.values()): raise InvalidRequest('Ayar metinlerinde geçersiz karakter var.')
    try: days=int(data['booking_window_days'])
    except (TypeError,ValueError): raise InvalidRequest('Randevu aralığını kontrol edin.')
    if not 7<=days<=365: raise InvalidRequest('Randevu aralığı 7–365 gün olmalı.')
    values={**text,'booking_window_days':str(days)}
    with db:
        ensure_settings(db)
        db.executemany('''INSERT INTO clinic_settings(key,value,updated_by,updated_at) VALUES (?,?,?,unixepoch())
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
            [(key,value,actor) for key,value in values.items()])
    return get_settings(db)

