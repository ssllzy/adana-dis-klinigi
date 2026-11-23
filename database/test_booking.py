from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import sqlite3
import threading
import unittest
from server import prepare_demo, connect, available_slots, create_request, InvalidRequest, LOCAL_TIME, LocalServer
from database.migrate import upgrade


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'demo.sqlite3'
        prepare_demo(self.path)
        self.db = connect(self.path)
        day=datetime.now(LOCAL_TIME).date()+timedelta(days=1)
        while day.isoweekday()==7:
            day+=timedelta(days=1)
        self.day=day.isoformat()
        self.payload=dict(name='Synthetic Test',phone='0000000000',doctor_id=1,service_id=1,
                          date=self.day,time='09:00',request_id='test-request-00000001')

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_doctor_service_and_inactive_checks(self):
        with self.assertRaises(InvalidRequest):
            available_slots(self.db,1,3,self.day)
        self.db.execute('UPDATE doctors SET active=0 WHERE id=1')
        self.db.commit()
        with self.assertRaises(InvalidRequest):
            create_request(self.db,self.payload)

    def test_breaks_duration_closed_days_and_past(self):
        slots,_,_=available_slots(self.db,1,2,self.day)
        self.assertIn('11:00',slots)
        self.assertNotIn('11:15',slots)
        self.assertNotIn('12:00',slots)
        self.assertNotIn('16:15',slots)
        sunday=datetime.fromisoformat(self.day).date()
        while sunday.isoweekday()!=7:
            sunday+=timedelta(days=1)
        self.assertEqual(available_slots(self.db,1,1,sunday.isoformat())[0],[])
        with self.assertRaises(InvalidRequest):
            available_slots(self.db,1,1,'2000-01-01')

    def test_clinic_closure_and_confirmed_conflicts(self):
        _,_,midnight=available_slots(self.db,1,1,self.day)
        self.db.execute('INSERT INTO closures(starts_at,ends_at) VALUES (?,?)',(midnight+9*3600,midnight+10*3600))
        self.db.commit()
        with self.assertRaises(InvalidRequest):
            create_request(self.db,self.payload)
        self.payload['time']='10:00'
        appointment=create_request(self.db,self.payload)
        self.db.execute("UPDATE appointments SET status='confirmed' WHERE id=?",(appointment,))
        self.db.commit()
        self.assertNotIn('10:00',available_slots(self.db,1,1,self.day)[0])

    def test_request_persists_and_duplicate_retry_is_idempotent(self):
        appointment=create_request(self.db,self.payload)
        self.assertEqual(create_request(self.db,self.payload),appointment)
        with closing(connect(self.path)) as reopened:
            self.assertEqual(reopened.execute('SELECT count(*) FROM appointments').fetchone()[0],1)
            self.assertEqual(reopened.execute('SELECT status FROM appointments').fetchone()[0],'pending')
        self.payload['time']='10:00'
        with self.assertRaises(InvalidRequest):
            create_request(self.db,self.payload)

    def test_invalid_form_and_slot_rejected(self):
        for change in [dict(name=' '),dict(phone='abc'),dict(time='12:30'),dict(doctor_id=999),dict(date='bad')]:
            with self.assertRaises(InvalidRequest):
                create_request(self.db,{**self.payload,**change})
        self.assertEqual(self.db.execute('SELECT count(*) FROM appointments').fetchone()[0],0)

    def test_pending_reserves_entire_interval_and_cancel_releases_it(self):
        appointment=create_request(self.db,self.payload)
        slots=available_slots(self.db,1,1,self.day)[0]
        self.assertNotIn('09:00',slots)
        self.assertNotIn('09:15',slots)
        self.assertIn('09:30',slots)
        self.assertIn('09:00',available_slots(self.db,2,1,self.day)[0])
        for hour in ('09:00','09:15'):
            with self.assertRaises(InvalidRequest):
                create_request(self.db,{**self.payload,'time':hour,'request_id':'new-request-0000001'})
        self.db.execute("UPDATE appointments SET status='cancelled' WHERE id=?",(appointment,))
        self.db.commit()
        self.assertIn('09:00',available_slots(self.db,1,1,self.day)[0])

    def test_simultaneous_requests_only_one_wins(self):
        barrier=threading.Barrier(2)
        def submit(number):
            with closing(connect(self.path)) as db:
                barrier.wait(timeout=3)
                try:
                    create_request(db,{**self.payload,'request_id':f'parallel-request-{number}'})
                    return 'accepted'
                except InvalidRequest as exc:
                    return exc.status
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(submit,(1,2)))
        self.assertCountEqual(results,['accepted',409])
        self.assertEqual(self.db.execute('SELECT count(*) FROM appointments').fetchone()[0],1)

    def test_v1_migration_preserves_conflicts_but_blocks_new_ones(self):
        first=create_request(self.db,self.payload)
        self.db.executescript('DROP TRIGGER appointment_overlap_insert; DROP TRIGGER appointment_overlap_update; PRAGMA user_version=1;')
        self.db.execute('''INSERT INTO appointments(contact_name,contact_phone,service_id,doctor_id,starts_at,ends_at,status)
            SELECT contact_name,contact_phone,service_id,doctor_id,starts_at,ends_at,status FROM appointments WHERE id=?''',(first,))
        self.db.commit()
        upgrade(self.db)
        upgrade(self.db)
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT count(*) FROM appointments').fetchone()[0],2)
        with self.assertRaisesRegex(sqlite3.IntegrityError,'doctor_slot_conflict'):
            self.db.execute('''INSERT INTO appointments(contact_name,contact_phone,service_id,doctor_id,starts_at,ends_at,status)
                SELECT contact_name,contact_phone,service_id,doctor_id,starts_at,ends_at,status FROM appointments WHERE id=?''',(first,))

    def test_demo_initialization_preserves_existing_records(self):
        create_request(self.db,self.payload)
        prepare_demo(self.path)
        self.assertEqual(self.db.execute('SELECT count(*) FROM appointments').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM doctors').fetchone()[0],2)

    def test_http_catalog_post_and_security_boundaries(self):
        server=LocalServer(('127.0.0.1',0),self.path)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        def request(method,path,body=None,headers=None):
            connection=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            connection.request(method,path,body=body,headers=headers or {})
            response=connection.getresponse()
            code,data=response.status,response.read()
            connection.close()
            return code,json.loads(data)
        try:
            code,catalog=request('GET','/api/catalog')
            self.assertEqual(code,200)
            self.assertEqual(len(catalog['doctors']),2)
            self.assertEqual(request('GET','/database/schema.sql')[0],404)
            self.assertEqual(request('GET','/api/appointments')[0],404)
            self.assertEqual(request('GET','/api/catalog',headers={'Host':'evil.example'})[0],403)
            body=json.dumps(self.payload)
            self.assertEqual(request('POST','/api/appointments',body,{'Content-Type':'application/json','Origin':'https://other.example'})[0],403)
            code,result=request('POST','/api/appointments',body,{'Content-Type':'application/json','Origin':origin})
            self.assertEqual(code,201)
            self.assertEqual(result['status'],'pending')
            self.assertNotIn('phone',result)
            stale_body=json.dumps({**self.payload,'request_id':'another-request-00001'})
            self.assertEqual(request('POST','/api/appointments',stale_body,
                {'Content-Type':'application/json','Origin':origin})[0],409)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
