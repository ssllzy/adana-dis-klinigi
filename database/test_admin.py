from contextlib import closing
from datetime import datetime, timedelta
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import subprocess
import sys
import queue
import unittest
from server import prepare_demo, connect, create_request, available_slots, InvalidRequest, LOCAL_TIME, LocalServer
from admin_backend import prepare_admin, update_appointment


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'demo.sqlite3'
        prepare_demo(self.path)
        prepare_admin(self.path)
        self.password=self.path.with_name('local-admin.txt').read_text(encoding='utf-8').split('Parola: ')[1].splitlines()[0]
        self.db=connect(self.path)
        day=datetime.now(LOCAL_TIME).date()+timedelta(days=1)
        while day.isoweekday()==7:
            day+=timedelta(days=1)
        self.day=day.isoformat()
        self.payload=dict(name='Admin Synthetic Test',phone='0000000000',doctor_id=1,service_id=1,
                          date=self.day,time='09:00',request_id='admin-test-request-01')
        self.id=create_request(self.db,self.payload)
        self.server=LocalServer(('127.0.0.1',0),self.path)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.cookie=''
        self.csrf=''

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.db.close();self.temp.cleanup()

    def request(self,path,body=None,csrf=True):
        c=HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        headers={'Cookie':self.cookie,'Origin':f'http://127.0.0.1:{self.server.server_port}'}
        if body is not None:
            headers['Content-Type']='application/json'
            if csrf:headers['X-CSRF-Token']=self.csrf
        c.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),headers)
        r=c.getresponse();code=r.status;cookie=r.getheader('Set-Cookie');data=json.loads(r.read());c.close()
        return code,data,cookie

    def login(self):
        code,data,cookie=self.request('/api/admin/login',{'username':'admin','password':self.password})
        self.assertEqual(code,200)
        self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
        self.cookie=cookie.split(';')[0];self.csrf=data['csrf']

    def test_protected_api_login_logout_and_csrf(self):
        self.assertEqual(self.request('/api/admin/appointments')[0],401)
        self.assertEqual(self.request('/api/admin/appointments/'+str(self.id),{'action':'cancel','revision':0})[0],401)
        self.login()
        code,data,_=self.request('/api/admin/appointments')
        self.assertEqual(code,200);self.assertEqual(len(data['appointments']),1)
        path='/api/admin/appointments/'+str(self.id)
        self.assertEqual(self.request(path,{'action':'cancel','revision':0},csrf=False)[0],403)
        self.assertEqual(self.request('/api/admin/logout',{})[0],200)
        self.assertEqual(self.request('/api/admin/appointments')[0],401)

    def test_clinic_settings_are_validated_saved_and_change_booking_window(self):
        self.login()
        self.assertEqual(self.request('/api/admin/settings')[1]['booking_window_days'],90)
        payload={'clinic_name':'Adana Diş','phone':'0322 000 00 00',
                 'whatsapp':'0555 000 00 00','address':'Yüreğir / Adana',
                 'working_hours_note':'Pazartesi–Cumartesi · 09.00–18.00','booking_window_days':'30'}
        code,saved,_=self.request('/api/admin/settings',payload)
        self.assertEqual(code,200);self.assertEqual(saved['booking_window_days'],30)
        self.assertEqual(self.request('/api/admin/settings',{**payload,'booking_window_days':'2'})[0],400)
        code,catalog,_=self.request('/api/catalog')
        self.assertEqual(code,200)
        self.assertEqual((datetime.fromisoformat(catalog['max_date'])-datetime.fromisoformat(catalog['today'])).days,30)

    def test_patient_profiles_group_by_phone_and_save_private_notes(self):
        self.db.execute("UPDATE appointments SET contact_phone='0555 111 22 33' WHERE id=?",(self.id,))
        self.db.commit()
        second={**self.payload,'phone':'0555 111 22 33','time':'10:00','request_id':'patient-profile-second'}
        second_id=create_request(self.db,second)
        self.login()
        self.assertEqual(self.request('/api/admin/patients')[0],200)
        result=self.request('/api/admin/patients')[1]
        profile=next(p for p in result['patients'] if p['key']=='05551112233')
        self.assertEqual(profile['appointment_count'],2)
        self.assertEqual({a['id'] for a in profile['appointments']},{self.id,second_id})
        path='/api/admin/patients/05551112233'
        self.assertEqual(self.request(path,{'note':'Telefonla aranmayı tercih ediyor.','revision':0},csrf=False)[0],403)
        code,saved,_=self.request(path,{'note':'Telefonla aranmayı tercih ediyor.','revision':0})
        self.assertEqual(code,200);self.assertEqual(saved['revision'],1)
        profile=next(p for p in self.request('/api/admin/patients')[1]['patients'] if p['key']=='05551112233')
        self.assertEqual(profile['note'],'Telefonla aranmayı tercih ediyor.')
        self.assertEqual(profile['note_revision'],1)
        self.assertEqual(self.request(path,{'note':'Eski ekran','revision':0})[0],409)
        self.assertEqual(self.request(path,{'note':'x'*2001,'revision':1})[0],400)
        self.assertEqual(self.request('/api/admin/patients/9999999999',{'note':'Bulunmaz','revision':0})[0],404)
        treatment={'title':'Sentetik dolgu','tooth':'26','status':'planned','fee':'2500.50','note':'Test kaydı'}
        code,created,_=self.request(path+'/treatments',treatment)
        self.assertEqual(code,201)
        profile=next(p for p in self.request('/api/admin/patients')[1]['patients'] if p['key']=='05551112233')
        item=profile['treatments'][0]
        self.assertEqual(item['title'],'Sentetik dolgu');self.assertEqual(item['fee_cents'],250050)
        code,_,_=self.request(path+'/treatments/'+str(created['id']),{**treatment,'status':'completed','revision':0})
        self.assertEqual(code,200)
        self.assertEqual(self.request(path+'/treatments/'+str(created['id']),{**treatment,'revision':0})[0],409)
        code,payment,_=self.request(path+'/payments',{'amount':'1250.25','method':'card','note':'Sentetik ödeme'})
        self.assertEqual(code,201)
        profile=next(p for p in self.request('/api/admin/patients')[1]['patients'] if p['key']=='05551112233')
        self.assertEqual(profile['payments'][0]['amount_cents'],125025)
        self.assertEqual(self.request(path+'/payments/'+str(payment['id']),{'action':'void'})[0],200)
        self.assertEqual(self.request(path+'/payments/'+str(payment['id']),{'action':'void'})[0],409)

    def test_appointment_history_is_protected_ordered_and_private(self):
        path='/api/admin/appointments/'+str(self.id)+'/events'
        self.assertEqual(self.request(path)[0],401)
        update_appointment(self.db,self.id,{'action':'confirm','revision':0},'admin')
        update_appointment(self.db,self.id,{'action':'reschedule','revision':1,'date':self.day,'time':'10:00'},'admin')
        self.login()
        code,result,_=self.request(path)
        self.assertEqual(code,200)
        self.assertEqual([e['action'] for e in result['events']],['request','confirm','reschedule'])
        self.assertEqual(result['events'][0]['actor'],'Web sitesi')
        self.assertEqual(result['events'][1]['actor'],'admin')
        self.assertLessEqual(result['events'][0]['created_at'],result['events'][1]['created_at'])
        payload=json.dumps(result,ensure_ascii=False)
        self.assertNotIn(self.payload['name'],payload)
        self.assertNotIn(self.payload['phone'],payload)
        self.assertEqual(result['events'][2]['before']['starts_at'],result['events'][1]['after']['starts_at'])
        self.assertEqual(self.request('/api/admin/appointments/999999/events')[0],404)

    def test_admin_created_history_has_single_create_event(self):
        self.login()
        payload={**self.payload,'time':'10:00','request_id':'history-admin-create-01'}
        appointment_id=self.request('/api/admin/appointments',payload)[1]['id']
        events=self.request('/api/admin/appointments/'+str(appointment_id)+'/events')[1]['events']
        self.assertEqual([e['action'] for e in events],['create'])
        self.assertEqual(events[0]['actor'],'admin')

    def test_patient_arrival_flow_is_persisted_audited_and_cleared(self):
        now=int(datetime.now(LOCAL_TIME).timestamp())
        self.db.execute("UPDATE appointments SET status='confirmed',starts_at=?,ends_at=? WHERE id=?",(now-1800,now+1800,self.id))
        self.db.commit()
        update_appointment(self.db,self.id,{'action':'arrive','revision':0},'admin')
        self.assertEqual(self.db.execute('SELECT stage FROM appointment_flow WHERE appointment_id=?',(self.id,)).fetchone()['stage'],'arrived')
        update_appointment(self.db,self.id,{'action':'start','revision':1},'admin')
        self.assertEqual(self.db.execute('SELECT stage FROM appointment_flow WHERE appointment_id=?',(self.id,)).fetchone()['stage'],'in_progress')
        self.login()
        item=self.request('/api/admin/appointments')[1]['appointments'][0]
        self.assertEqual(item['arrival_status'],'in_progress')
        self.assertEqual(self.request('/api/admin/appointments/'+str(self.id),{'action':'complete','revision':2})[0],200)
        self.assertIsNone(self.db.execute('SELECT stage FROM appointment_flow WHERE appointment_id=?',(self.id,)).fetchone())
        events=self.request('/api/admin/appointments/'+str(self.id)+'/events')[1]['events']
        self.assertEqual([e['action'] for e in events[-3:]],['arrive','start','complete'])

    def test_wrong_password_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.request('/api/admin/login',{'username':'admin','password':'wrong'})[0],401)
        self.assertEqual(self.request('/api/admin/login',{'username':'admin','password':self.password})[0],429)

    def test_week_calendar_boundaries_auth_and_no_recent_limit(self):
        path='/api/admin/appointments?week=2026-12-31'
        self.assertEqual(self.request(path)[0],401)
        self.login()
        def stamp(day):
            return int(datetime.fromisoformat(day).replace(tzinfo=LOCAL_TIME).timestamp())
        # Closed synthetic history can share times; ensure a week is not truncated at 500.
        with self.db:
            for day,count in [('2026-12-30T23:59:00',1),('2026-12-31T00:00:00',501),
                              ('2027-01-03T23:45:00',1),('2027-01-04T00:00:00',1),('2027-01-07T00:00:00',1)]:
                for _ in range(count):
                    start=stamp(day)
                    self.db.execute("INSERT INTO appointments(contact_name,contact_phone,doctor_id,service_id,starts_at,ends_at,status) VALUES ('Synthetic Week','0000000000',1,1,?,?,'cancelled')",(start,start+900))
        code,data,_=self.request(path)
        self.assertEqual(code,200)
        self.assertEqual(len(data['appointments']),503)
        self.assertEqual({a['date'] for a in data['appointments']},{'2026-12-31','2027-01-03','2027-01-04'})
        for invalid in ('not-a-date','2026-02-30','2100-01-01'):
            self.assertEqual(self.request('/api/admin/appointments?week='+invalid)[0],400)

    def closure_payload(self, doctor_id=2):
        return {'doctor_id':doctor_id,'label':'Synthetic Leave','starts_at':self.day+'T09:00',
                'ends_at':self.day+'T12:00','request_id':'closure-test-0001'}

    def test_closure_auth_create_idempotency_availability_and_delete(self):
        path='/api/admin/closures'
        self.assertEqual(self.request(path)[0],401)
        self.login()
        payload=self.closure_payload()
        self.assertEqual(self.request(path,payload,csrf=False)[0],403)
        code,result,_=self.request(path,payload)
        self.assertEqual(code,201)
        self.assertEqual(self.request(path,payload)[1]['id'],result['id'])
        self.assertNotIn('09:00',available_slots(self.db,2,1,self.day)[0])
        listed=self.request(path)[1]
        self.assertEqual(len(listed['closures']),1)
        self.assertEqual(listed['closures'][0]['doctor'],'Demo Hekim 2')
        self.assertEqual(self.db.execute("SELECT count(*) FROM closure_events WHERE action='create'").fetchone()[0],1)
        delete='/api/admin/closures/'+str(result['id'])
        self.assertEqual(self.request(delete,{'action':'delete'},csrf=False)[0],403)
        self.assertEqual(self.request(delete,{'action':'delete'})[0],200)
        self.assertIn('09:00',available_slots(self.db,2,1,self.day)[0])
        self.assertEqual(self.db.execute("SELECT count(*) FROM closure_events WHERE action='delete'").fetchone()[0],1)

    def test_closure_conflict_validation_and_atomicity(self):
        self.login()
        path='/api/admin/closures'
        self.assertEqual(self.request(path,self.closure_payload(1))[0],409)
        self.assertEqual(self.request(path,{**self.closure_payload(None),'request_id':'closure-test-0002'})[0],409)
        invalid=[{'doctor_id':999},{'label':'x'},{'starts_at':self.day+'T09:01'},
                 {'ends_at':self.day+'T08:00'},{'ends_at':self.day+'T09:00'}]
        for i,changes in enumerate(invalid):
            payload={**self.closure_payload(2),**changes,'request_id':'closure-invalid-%04d'%i}
            self.assertEqual(self.request(path,payload)[0],400)
        self.assertEqual(self.db.execute('SELECT count(*) FROM closures').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM closure_events').fetchone()[0],0)

    def test_clinic_full_day_closure_blocks_all_doctors(self):
        self.login()
        update_appointment(self.db,self.id,{'action':'cancel','revision':0},'admin')
        payload={**self.closure_payload(None),'starts_at':self.day+'T00:00','ends_at':
                 (datetime.fromisoformat(self.day)+timedelta(days=1)).date().isoformat()+'T00:00'}
        self.assertEqual(self.request('/api/admin/closures',payload)[0],201)
        for doctor in (1,2):
            self.assertEqual(available_slots(self.db,doctor,1,self.day)[0],[])

    def doctor_payload(self):
        from doctor_management import list_doctors
        d=list_doctors(self.db)['doctors'][0]
        return {**d,'active':bool(d['active'])}

    def test_doctor_create_hours_catalog_and_idempotency(self):
        self.assertEqual(self.request('/api/admin/doctors')[0],401)
        self.login()
        payload={**self.doctor_payload(),'name':'Synthetic Doctor','request_id':'new-doctor-test-01'}
        self.assertEqual(self.request('/api/admin/doctors',payload,csrf=False)[0],403)
        code,result,_=self.request('/api/admin/doctors',payload)
        self.assertEqual(code,200)
        self.assertEqual(self.request('/api/admin/doctors',payload)[1]['id'],result['id'])
        self.assertIn('09:00',available_slots(self.db,result['id'],1,self.day)[0])
        self.assertEqual(self.db.execute('SELECT count(*) FROM doctor_events').fetchone()[0],1)

    def test_doctor_changes_protect_appointments_and_validate_hours(self):
        self.login()
        payload=self.doctor_payload()
        path='/api/admin/doctors/1'
        for changes in ({'hours':[]},{'active':False},{'service_ids':[2]}):
            self.assertEqual(self.request(path,{**payload,**changes})[0],409)
        for changes in ({'hours':[{'weekday':1,'start_minute':600,'end_minute':540}]},
                        {'hours':[payload['hours'][0],payload['hours'][0]]}, {'service_ids':[999]}):
            self.assertEqual(self.request(path,{**payload,**changes})[0],400)
        self.assertEqual(self.db.execute('SELECT revision FROM doctors WHERE id=1').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM doctor_events').fetchone()[0],0)
        self.assertEqual(self.request(path,{**payload,'name':'Updated Demo'})[0],200)
        self.assertEqual(self.request(path,payload)[0],409)

    def test_doctor_service_removal_preserves_history_and_blocks_booking(self):
        self.login()
        update_appointment(self.db,self.id,{'action':'cancel','revision':0},'admin')
        payload={**self.doctor_payload(),'service_ids':[2]}
        self.assertEqual(self.request('/api/admin/doctors/1',payload)[0],200)
        self.assertEqual(self.db.execute('SELECT count(*) FROM appointments').fetchone()[0],1)
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(),[])
        with self.assertRaises(InvalidRequest):available_slots(self.db,1,1,self.day)
        catalog=self.request('/api/catalog')[1]
        self.assertNotIn({'doctor_id':1,'service_id':1},catalog['links'])
        payload['revision']=1
        payload['active']=False
        self.assertEqual(self.request('/api/admin/doctors/1',payload)[0],200)
        self.assertNotIn(1,[d['id'] for d in self.request('/api/catalog')[1]['doctors']])

    def test_doctor_hours_change_updates_available_slots(self):
        self.login()
        update_appointment(self.db,self.id,{'action':'cancel','revision':0},'admin')
        weekday=datetime.fromisoformat(self.day).isoweekday()
        payload={**self.doctor_payload(),'hours':[{'weekday':weekday,'start_minute':600,'end_minute':720},
                                                  {'weekday':weekday,'start_minute':780,'end_minute':840}]}
        self.assertEqual(self.request('/api/admin/doctors/1',payload)[0],200)
        slots=available_slots(self.db,1,1,self.day)[0]
        self.assertNotIn('09:00',slots)
        self.assertNotIn('12:00',slots)
        self.assertIn('10:00',slots)
        self.assertIn('13:30',slots)
        self.assertNotIn('13:45',slots)

    def test_create_confirmed_protected_idempotent_and_audited(self):
        path='/api/admin/appointments'
        payload={**self.payload,'time':'10:00','request_id':'admin-create-test-01'}
        self.assertEqual(self.request(path,payload)[0],401)
        self.login()
        self.assertEqual(self.request(path,payload,csrf=False)[0],403)
        code,result,_=self.request(path,payload)
        self.assertEqual(code,201)
        self.assertEqual(result['status'],'confirmed')
        self.assertEqual(self.request(path,payload)[1]['id'],result['id'])
        row=self.db.execute('SELECT * FROM appointments WHERE id=?',(result['id'],)).fetchone()
        self.assertEqual(row['status'],'confirmed')
        self.assertNotIn('10:15',available_slots(self.db,1,1,self.day)[0])
        events=self.db.execute('SELECT * FROM appointment_events WHERE appointment_id=?',(result['id'],)).fetchall()
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['action'],'create')
        self.assertEqual(events[0]['actor'],'admin')
        self.assertNotIn(payload['phone'],events[0]['after_json'])
        self.assertEqual(self.request(path,{**payload,'name':'Changed Name'})[0],409)

    def test_create_rejects_conflicts_invalid_pair_and_bad_contact(self):
        self.login()
        path='/api/admin/appointments'
        for changes,expected in [({},409),({'time':'09:15'},409),({'time':'12:00'},409),
                                 ({'service_id':3},400),({'phone':'abc'},400),({'name':'x'},400)]:
            self.assertEqual(self.request(path,{**self.payload,**changes})[0],expected)
        self.assertEqual(self.db.execute('SELECT count(*) FROM appointments').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM appointment_events').fetchone()[0],0)

    def test_public_cannot_confirm_or_replay_admin_submission(self):
        payload={**self.payload,'time':'11:00','request_id':'namespace-test-0001','actor':'admin','status':'confirmed'}
        appointment_id=create_request(self.db,payload)
        self.assertEqual(self.db.execute('SELECT status FROM appointments WHERE id=?',(appointment_id,)).fetchone()[0],'pending')
        self.login()
        self.assertEqual(self.request('/api/admin/appointments',payload)[0],409)

    def test_expired_session_rejected(self):
        self.login()
        with self.server.admin_sessions.lock:
            for session in self.server.admin_sessions.sessions.values():session['expires']=0
        self.assertEqual(self.request('/api/admin/session')[0],401)

    def test_confirm_reschedule_cancel_and_audit(self):
        update_appointment(self.db,self.id,{'action':'confirm','revision':0},'admin')
        update_appointment(self.db,self.id,{'action':'reschedule','revision':1,'date':self.day,'time':'10:00'},'admin')
        self.assertIn('09:00',available_slots(self.db,1,1,self.day)[0])
        self.assertNotIn('10:00',available_slots(self.db,1,1,self.day)[0])
        update_appointment(self.db,self.id,{'action':'cancel','revision':2},'admin')
        self.assertIn('10:00',available_slots(self.db,1,1,self.day)[0])
        self.assertEqual(self.db.execute('SELECT count(*) FROM appointment_events').fetchone()[0],3)
        self.assertEqual(self.db.execute('SELECT revision FROM appointments WHERE id=?',(self.id,)).fetchone()[0],3)

    def test_conflicting_move_and_stale_revision_rejected(self):
        create_request(self.db,{**self.payload,'time':'10:00','request_id':'admin-test-request-02'})
        with self.assertRaises(InvalidRequest):
            update_appointment(self.db,self.id,{'action':'reschedule','revision':0,'date':self.day,'time':'10:00'},'admin')
        update_appointment(self.db,self.id,{'action':'confirm','revision':0},'admin')
        with self.assertRaises(InvalidRequest):
            update_appointment(self.db,self.id,{'action':'cancel','revision':0},'admin')
        self.assertEqual(self.db.execute('SELECT count(*) FROM appointment_events').fetchone()[0],1)

    def test_future_completion_and_closed_record_changes_rejected(self):
        update_appointment(self.db,self.id,{'action':'confirm','revision':0},'admin')
        for action in ('complete','no_show'):
            with self.assertRaises(InvalidRequest):
                update_appointment(self.db,self.id,{'action':action,'revision':1},'admin')
        update_appointment(self.db,self.id,{'action':'cancel','revision':1},'admin')
        with self.assertRaises(InvalidRequest):
            update_appointment(self.db,self.id,{'action':'confirm','revision':2},'admin')

    def test_http_action_and_private_files(self):
        self.login()
        for path in ('/local-admin.txt','/admin_backend.py','/database/schema.sql'):
            self.assertEqual(self.request(path)[0],404)
        self.assertEqual(self.request('/api/admin/appointments/'+str(self.id),{'action':'confirm','revision':0})[0],200)
        self.assertEqual(self.request('/api/admin/appointments/'+str(self.id),{'action':'cancel','revision':0})[0],409)

    def test_script_entrypoint_handles_unauthenticated_request(self):
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve().parents[1]/'server.py'),
            '--port','0','--database',str(self.path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        output=queue.Queue()
        reader=threading.Thread(target=lambda:output.put(process.stdout.readline()),daemon=True)
        reader.start()
        try:
            line=output.get(timeout=8)
            self.assertIn('Local demo: http://127.0.0.1:',line)
            port=int(line.strip().rsplit(':',1)[-1])
            c=HTTPConnection('127.0.0.1',port,timeout=3)
            c.request('GET','/api/admin/session')
            r=c.getresponse();self.assertEqual(r.status,401);r.read();c.close()
        finally:
            process.terminate();process.wait(timeout=5)
            process.stdout.close();process.stderr.close();reader.join(timeout=1)


if __name__=='__main__':unittest.main()


