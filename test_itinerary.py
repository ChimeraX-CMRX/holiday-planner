import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import itinerary
import server


class ItineraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=server.DB_PATH
        server.DB_PATH=Path(self.tmp.name)/'test.sqlite3'
        with server.db() as c:
            for ident in (1,2):
                c.execute("INSERT INTO holidays(id,name,start_date,end_date,created_at) VALUES(?,?,'2027-05-01','2027-05-04',0)",(ident,'Test holiday '+str(ident)))
            c.execute("INSERT INTO destinations(id,holiday_id,slug,name,country,country_code,start_date,end_date,latitude,longitude) VALUES(1,1,'paris','Paris','France','FR','2027-05-02','2027-05-04',48.8,2.3)")
            c.execute("INSERT INTO places(id,destination_id,category,name,created_at,stay_start_date,stay_end_date) VALUES(1,1,'hotel','Example Hotel',0,'2027-05-02','2027-05-04')")
            c.execute("INSERT INTO places(id,destination_id,category,name,created_at,schedule_date,schedule_period) VALUES(2,1,'activity','Sightseeing secret',0,'2027-05-02','full-day')")
            c.execute("INSERT INTO travel(id,holiday_id,travel_type,service_number,from_name,to_name,departure_at,arrival_at,status,booking_reference,created_at) VALUES(1,1,'flight','AF123','London','Paris','2027-05-01T23:00','2027-05-02T01:15','booked','PRIVATE-REF',0)")

    def tearDown(self):
        server.DB_PATH=self.old
        self.tmp.cleanup()

    def test_complete_calendar_and_overnight_arrival(self):
        with server.db() as c:d=itinerary.get(c,1)
        self.assertEqual(len(d['calendar']),4)
        self.assertIn('23:00 Depart',d['calendar'][0]['travel'][0])
        self.assertIn('01:15 Arrive',d['calendar'][1]['travel'][0])
        self.assertIn('Check-in:',d['calendar'][1]['accommodation'][0])
        self.assertIn('Check-out:',d['calendar'][3]['accommodation'][0])
        self.assertNotIn('Sightseeing secret',json.dumps(d))
        self.assertNotIn('PRIVATE-REF',json.dumps(d))

    def test_contacts_persist_without_changing_stays_or_journey_times(self):
        with server.db() as c:
            before=c.execute('SELECT * FROM places').fetchall()
            itinerary.save(c,1,dict(kind='hotel',id=1,details=dict(address='1 Example Street',phone='+33 1 00 00 00 00')))
            self.assertEqual(before,c.execute('SELECT * FROM places').fetchall())
        with server.db() as c:self.assertEqual(itinerary.get(c,1)['hotels'][0]['details']['address'],'1 Example Street')

    def test_isolation_and_validation(self):
        with server.db() as c:
            self.assertEqual(itinerary.get(c,2)['hotels'],[])
            for payload in [dict(kind='hotel',id=1,details={}),dict(kind='hotel',id=2,details={})]:
                with self.assertRaises(ValueError):itinerary.save(c,2,payload)
            with self.assertRaises(ValueError):itinerary.save(c,1,dict(kind='hotel',id=1,details={'sources':'javascript:alert(1)'}))

    def test_out_of_range_stays_not_in_calendar(self):
        with server.db() as c:
            c.execute("UPDATE destinations SET start_date='2027-05-03' WHERE id=1")
            d=itinerary.get(c,1)
        self.assertFalse(d['hotels'][0]['dates_valid'])
        self.assertTrue(all(not day['accommodation'] for day in d['calendar']))

    def test_lookup_is_cached_and_does_not_save_suggestions(self):
        response=io.BytesIO(json.dumps([dict(osm_type='way',osm_id=12,name='Example Hotel',display_name='1 Example Street',extratags={})]).encode())
        with patch('urllib.request.urlopen',return_value=response) as fetch:
            first=itinerary.lookup(server.db,1,dict(kind='hotel',id=1))
            self.assertEqual(first,itinerary.lookup(server.db,1,dict(kind='hotel',id=1)))
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(first[0]['phone'],'')
        with server.db() as c:self.assertEqual(itinerary.details(c,'hotel',1),{})

    def test_pdf(self):
        try:
            import reportlab
        except ImportError:
            self.skipTest('PDF dependency not installed')
        with server.db() as c:d=itinerary.get(c,1)
        content=itinerary.pdf(d)
        self.assertTrue(content.startswith(b'%PDF-'))
        self.assertGreater(len(content),10000)


if __name__=='__main__':unittest.main()
