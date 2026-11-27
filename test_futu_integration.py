import unittest
import json
import tempfile
from pathlib import Path
from datetime import date, datetime, timezone
from unittest.mock import patch
from futu_calendar import FutuError
from futu_integration import (publication_enabled, query_window, refresh_snapshot,
                              merge_events, update_source)
import policy_calendar
from policy_calendar import render

TODAY = date(2026, 11, 26)
BEGIN = date(2026, 11, 24)
END = date(2026, 12, 4)
NOW = datetime(2026, 11, 26, 23, 50, tzinfo=timezone.utc)
CFG = dict(history_days=45, future_days=365, calendar_name='Test')


def row(id='futu-test', start='2026-11-26T23:45:00+00:00', title='Futu - U.S. President Trump delivered a speech.'):
    return dict(id=id, start=start, title=title, description='Synthetic test event', url='https://example.com')


class IntegrationTests(unittest.TestCase):
    def test_default_off(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertFalse(publication_enabled())
        with patch.dict('os.environ', {'FUTU_PUBLISH_ENABLED': 'true'}):
            self.assertTrue(publication_enabled())

    def test_query_china_date(self):
        self.assertEqual(query_window(NOW), (BEGIN, END))

    def test_snapshot_retains_old_history(self):
        older = row('futu-older', '2026-11-20T00:00:00+00:00')
        expired = row('futu-expired', '2026-09-01T00:00:00+00:00')
        gone = row('futu-gone', '2026-11-30T00:00:00+00:00')
        result = refresh_snapshot([older, expired, gone], [row()], BEGIN, END, TODAY, CFG)
        self.assertEqual({e['id'] for e in result}, {'futu-older', 'futu-test'})

    def test_empty_snapshot_is_valid(self):
        self.assertEqual(refresh_snapshot([row()], [], BEGIN, END, TODAY, CFG), [])

    def test_time_change_same_id(self):
        old = dict(row(), stamp='20261125T000000Z')
        updated = row(start='2026-11-27T00:00:00+00:00')
        result = refresh_snapshot([old], [updated], BEGIN, END, TODAY, CFG)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['id'], old['id'])
        self.assertEqual(result[0]['start'], updated['start'])

    def test_unchanged_stamp(self):
        old = dict(row(), stamp='20261125T000000Z')
        result = refresh_snapshot([old], [row()], BEGIN, END, TODAY, CFG)
        self.assertEqual(result[0]['stamp'], old['stamp'])

    def test_exact_match_keeps_existing_uid(self):
        official = row('schedule-test', title='White House - The President delivers remarks')
        merged, added, dup = merge_events([official], [row()], TODAY, CFG)
        self.assertEqual((merged, added, dup), ([official], 0, 1))

    def test_different_times_not_merged(self):
        official = row('schedule-test', '2026-11-26T22:17:00+00:00', 'White House - The President announces disaster relief')
        merged, added, dup = merge_events([official], [row()], TODAY, CFG)
        self.assertEqual((len(merged), added, dup), (2, 1, 0))
        self.assertIn(b'DTSTART:20261126T234500Z', render(merged, CFG))

    def test_different_topics_same_minute_not_merged(self):
        official = row('schedule-test', title='White House - The President announces disaster relief')
        self.assertEqual(merge_events([official], [row()], TODAY, CFG)[1:], (1, 0))

    def test_policy_unchanged(self):
        original = row('fr-publication-x', '2026-11-26', 'Policy Published - Tariffs')
        merged, added, dup = merge_events([original], [row()], TODAY, CFG)
        self.assertEqual(merged[0], original)
        self.assertEqual((added, dup), (1, 0))

    def test_failure_preserves_cache(self):
        old = dict(row(), stamp='20261125T000000Z')
        cache = {'schedule': [], 'federal_register': [], 'futu': [old]}
        def fail(*args):
            raise FutuError('HTTP 403')
        status = update_source(cache, TODAY, CFG, NOW, fail)
        self.assertEqual(status['state'], 'error')
        self.assertEqual(cache['futu'], [old])

    def test_success_reports_counts(self):
        cache = {'futu': []}
        status = update_source(cache, TODAY, CFG, NOW, lambda *_: (9, [row()]))
        self.assertEqual((status['state'], status['raw_events'], status['events']), ('ok', 9, 1))
        self.assertEqual(len(cache['futu']), 1)

    def test_ambiguous_match_not_suppressed(self):
        official = row('schedule-test', title='White House - The President delivers remarks')
        merged, added, dup = merge_events([official], [row(), row('futu-second')], TODAY, CFG)
        self.assertEqual((len(merged), added, dup), (3, 2, 0))

    def test_offline_publication_gate(self):
        for enabled in ('false', 'true'):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cfg = {**CFG, 'presidential_schedule': True, 'federal_register': True}
                old = row()
                (root/'config.json').write_text(json.dumps(cfg))
                (root/'manual_events.json').write_text('[]')
                (root/'source_cache.json').write_text(json.dumps({'schedule': [], 'federal_register': [], 'futu': [old]}))
                with patch.multiple(policy_calendar, ROOT=root, CACHE=root/'source_cache.json', OUTPUT=root/'us-policy.ics', STATUS=root/'source_status.json'), patch.dict('os.environ', {'FUTU_PUBLISH_ENABLED': enabled}), patch('sys.argv', ['policy_calendar.py', '--offline', '--as-of', TODAY.isoformat()]):
                    self.assertEqual(policy_calendar.main(), 0)
                self.assertEqual(b'futu-test@us-policy-calendar' in (root/'us-policy.ics').read_bytes(), enabled == 'true')
                self.assertEqual('futu' in json.loads((root/'source_cache.json').read_text()), enabled == 'true')
