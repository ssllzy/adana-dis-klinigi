import sqlite3
import unittest
from pathlib import Path
from init_db import initialize, SITE_ROOT


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
        self.db.executescript("""
          INSERT INTO doctors(id,name) VALUES (1,'Test Doctor A'),(2,'Test Doctor B');
          INSERT INTO services(id,name,duration_minutes) VALUES (1,'Test Service',30),(2,'Other',30);
          INSERT INTO doctor_services VALUES (1,1),(2,1);
        """)

    def tearDown(self):
        self.db.close()

    def book(self, start=1000, end=2800, doctor=1, status='confirmed', service=1):
        return self.db.execute('''INSERT INTO appointments
          (contact_name,contact_phone,service_id,doctor_id,starts_at,ends_at,status)
          VALUES ('Synthetic Test','0000000000',?,?,?,?,?)''', (service,doctor,start,end,status)).lastrowid

    def test_overlaps_rejected_but_adjacent_and_other_doctors_allowed(self):
        self.book()
        for start,end in [(1000,2800),(1500,2000),(500,3000),(500,1500),(2000,3500)]:
            with self.assertRaisesRegex(sqlite3.IntegrityError,'doctor_slot_conflict'):
                self.book(start,end)
        self.book(2800,4600)
        self.book(doctor=2)

    def test_pending_confirmation_and_rescheduling_check_conflicts(self):
        self.book()
        pending = self.book(None,None,None,status='pending')
        with self.assertRaisesRegex(sqlite3.IntegrityError,'doctor_slot_conflict'):
            self.db.execute("UPDATE appointments SET status='confirmed',doctor_id=1,starts_at=1000,ends_at=2800 WHERE id=?",(pending,))
        other = self.book(3000,4800)
        with self.assertRaisesRegex(sqlite3.IntegrityError,'doctor_slot_conflict'):
            self.db.execute('UPDATE appointments SET starts_at=2000 WHERE id=?',(other,))

    def test_cancellation_releases_slot(self):
        old = self.book()
        self.db.execute("UPDATE appointments SET status='cancelled' WHERE id=?",(old,))
        self.book()

    def test_pending_overlap_insert_and_update_rejected(self):
        pending=self.book(status='pending')
        for status in ('pending','confirmed'):
            with self.assertRaisesRegex(sqlite3.IntegrityError,'doctor_slot_conflict'):
                self.book(status=status)
        other=self.book(2800,4600,status='pending')
        with self.assertRaisesRegex(sqlite3.IntegrityError,'doctor_slot_conflict'):
            self.db.execute('UPDATE appointments SET starts_at=2000 WHERE id=?',(other,))
        self.db.execute("UPDATE appointments SET status='confirmed' WHERE id=?",(pending,))

    def test_pending_can_be_unassigned_but_confirmed_cannot(self):
        self.book(None,None,None,'pending')
        with self.assertRaises(sqlite3.IntegrityError):
            self.book(None,None,None)

    def test_invalid_references_times_and_status_rejected(self):
        for values in [dict(service=999),dict(service=2),dict(doctor=999),dict(start=2800,end=1000),dict(status='invalid')]:
            with self.assertRaises(sqlite3.IntegrityError):
                self.book(**values)

    def test_public_database_path_rejected(self):
        with self.assertRaisesRegex(ValueError,'outside'):
            initialize(SITE_ROOT / 'unsafe.sqlite3')


if __name__ == '__main__':
    unittest.main()
