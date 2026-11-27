import unittest
from datetime import date, datetime, timezone
from futu_calendar import fetch_items, select_events, FutuError

D = date(2026, 11, 27)
T = int(datetime(2026, 11, 26, 23, 45, tzinfo=timezone.utc).timestamp())

def event(**kw):
    return dict(dict(article_id='synthetic-1', title='U.S. President delivers remarks', country='United States', item_type='ECONOMIC_EVENT', star=3, timestamp=T), **kw)

class FutuTests(unittest.TestCase):
    def test_filter_and_timezone(self):
        rows = select_events([event(), event(country='France'), event(title='Fed Chair Powell speaks'), event(star=1), event(item_type='ECONOMIC_DATA')], D, D)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['start'], '2026-11-26T23:45:00+00:00')
        self.assertEqual(rows[0]['id'], 'futu-synthetic-1')
    def test_stable_id(self):
        self.assertEqual(select_events([event(timestamp=T+60)],D,D)[0]['id'], select_events([event()],D,D)[0]['id'])
    def test_duplicate(self):
        self.assertEqual(len(select_events([event(),event()],D,D)),1)
        with self.assertRaises(FutuError): select_events([event(),event(timestamp=T+60)],D,D)
    def test_invalid_time(self):
        with self.assertRaises(FutuError): select_events([event(timestamp=0)],D,D)
    def test_pages(self):
        calls=[]
        def request(p):
            calls.append(p)
            return {'ret_code':0,'data':{'items':[event()]},'pagination':{'completed':len(calls)==2,'page_flag':'next'}}
        self.assertEqual(len(fetch_items(request,D,D)),2)
        self.assertEqual(calls[1]['page_flag'],'next')
    def test_bad_page(self):
        with self.assertRaises(FutuError): fetch_items(lambda p:{'ret_code':0,'data':{'items':[]},'pagination':{'completed':False,'page_flag':'same'}},D,D)
    def test_permission(self):
        with self.assertRaises(FutuError): fetch_items(lambda p:{'ret_code':-9},D,D)
    def test_no_data(self):
        self.assertEqual(fetch_items(lambda p:{'ret_code':-10},D,D),[])
