import unittest
from datetime import date
from policy_calendar import load, parse_schedule, parse_documents, render, combine, day

class TestPolicy(unittest.TestCase):
    cfg = load('config.json')
    today = date(2026,11,15)
    def test_schedule(self):
        raw = b'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:a\r\nDTSTART;TZID=America/New_York:20261116T100000\r\nSUMMARY:The President delivers remarks\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:b\r\nDTSTART:20261116T150000Z\r\nSUMMARY:Executive Time\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:c\r\nDTSTART:20261116T160000Z\r\nSUMMARY:Press briefing\r\nSTATUS:CANCELLED\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'
        rows = parse_schedule(raw, self.today, self.cfg)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['start'], '2026-11-16T10:00:00-05:00')
    def test_bad_feed(self):
        with self.assertRaises(ValueError): parse_schedule(b'<html>error</html>', self.today, self.cfg)
    def test_fr_dates(self):
        d = {'document_number':'test', 'title':'Adjusting Imports of X', 'publication_date':'2026-11-12', 'effective_date':'2026-12-01', 'type':'Presidential Document'}
        rows = parse_documents([d],self.today,self.cfg)
        self.assertEqual(len(rows),2)
        self.assertEqual({r['start'] for r in rows},{'2026-11-12','2026-12-01'})
    def test_exclude_proposal(self):
        d = {'document_number':'test','title':'Request for Comments on Section 301','publication_date':'2026-11-12'}
        self.assertEqual(parse_documents([d],self.today,self.cfg),[])
    def test_manual_and_ics(self):
        rows = combine({'schedule':[],'federal_register':[]},load('manual_events.json'),self.today,self.cfg)
        out = render(rows,self.cfg)
        self.assertEqual(len(rows),3)
        self.assertEqual(out.count(b'BEGIN:VEVENT'),3)
        self.assertIn(b'DTSTART:20261204T050100Z',out)
        self.assertIn(b'DTSTART:20270110T045900Z',out)
        self.assertTrue(all(len(s)<=75 for s in out.split(b'\r\n')))
    def test_local_date(self):
        self.assertEqual(day('2027-01-10T04:59:00Z'),date(2027,1,9))
    def test_no_duplicates(self):
        r = {'id':'x','start':'2026-12-01','title':'x','description':'x','url':'https://example.com'}
        with self.assertRaises(ValueError): render([r,r],self.cfg)

if __name__ == '__main__': unittest.main()
