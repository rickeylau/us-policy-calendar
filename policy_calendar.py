#!/usr/bin/env python3
"""Selected public US policy dates. No forecast, trading or private schedule data."""
import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo('America/New_York')
UTC = timezone.utc
API = 'https://www.federalregister.gov/api/v1/documents.json'
SCHEDULE_PAGE = 'https://rollcall.com/factbase/trump/calendar/'
CACHE = ROOT / 'source_cache.json'
OUTPUT = ROOT / 'us-policy.ics'
STATUS = ROOT / 'source_status.json'


def fetch(url):
    request = Request(url, headers={'User-Agent': 'USPolicyCalendar/1.0 (public calendar)', 'Accept': '*/*'})
    with urlopen(request, timeout=35) as response:
        data = response.read(30_000_001)
    if len(data) > 30_000_000:
        raise ValueError('response too large')
    return data


def load(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))


def write(path, data):
    if not path.exists() or path.read_bytes() != data:
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_bytes(data)
        tmp.replace(path)


def json_bytes(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def key(*parts):
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:32]


def start_value(s):
    if len(s) == 10:
        return date.fromisoformat(s)
    v = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if v.tzinfo is None:
        raise ValueError('timezone required')
    return v.astimezone(UTC)


def day(s):
    v = start_value(s)
    return v.astimezone(ET).date() if isinstance(v, datetime) else v


def in_window(s, today, cfg):
    return today - timedelta(days=cfg['history_days']) <= day(s) <= today + timedelta(days=cfg['future_days'])


def matches(s, words):
    s = s.casefold()
    return any(re.search(r'(?<!\w)' + re.escape(word.casefold()) + r'(?!\w)', s) for word in words)


def unescape(s):
    return re.sub(r'\\([nN,;\\])', lambda m: '\n' if m[1].lower() == 'n' else m[1], s)


def ics_time(value, params):
    if re.fullmatch(r'\d{8}', value):
        return datetime.strptime(value, '%Y%m%d').date().isoformat()
    if value.endswith('Z'):
        return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=UTC).isoformat()
    m = re.search(r'(?:^|;)TZID=([^;]+)', params)
    if not m:
        raise ValueError('floating time is not supported; no timezone guessing')
    tz = ZoneInfo(m[1].strip('"'))
    return datetime.strptime(value, '%Y%m%dT%H%M%S').replace(tzinfo=tz).isoformat()


def parse_schedule(raw, today, cfg):
    text = re.sub(r'\r?\n[ \t]', '', raw.decode('utf-8-sig'))
    if not text.startswith('BEGIN:VCALENDAR') or 'END:VCALENDAR' not in text:
        raise ValueError('not a complete ICS calendar')
    blocks = re.findall(r'^BEGIN:VEVENT\r?\n(.*?)^END:VEVENT\r?$', text, re.M | re.S)
    if not blocks:
        raise ValueError('empty source calendar')
    out = {}
    supported = 0
    for block in blocks:
        p = {}
        for line in block.splitlines():
            if ':' not in line:
                continue
            name, value = line.split(':', 1)
            prop, _, params = name.partition(';')
            p[prop] = (value, params)
        if not all(x in p for x in ('UID', 'DTSTART', 'SUMMARY')):
            continue
        if 'RRULE' in p or 'RECURRENCE-ID' in p:
            continue  # no speculative recurrence expansion
        try:
            start = ics_time(*p['DTSTART'])
        except (ValueError, KeyError):
            continue
        supported += 1
        title = unescape(p['SUMMARY'][0])
        desc = unescape(p.get('DESCRIPTION', ('', ''))[0])
        if p.get('STATUS', ('', ''))[0].upper() == 'CANCELLED':
            continue
        if not in_window(start, today, cfg):
            continue
        if matches(title + ' ' + desc, cfg['schedule_exclusions']):
            continue
        if not matches(title, cfg['schedule_keywords']):
            continue
        uid = 'schedule-' + key(p['UID'][0])
        out[uid] = {'id': uid, 'start': start, 'title': 'White House - ' + title,
                    'description': 'Public schedule via Roll Call / Factba.se. Time can change; may be added after the event. Market relevance is keyword-filtered.', 'url': SCHEDULE_PAGE}
    if not supported:
        raise ValueError('no supported events in calendar')
    return list(out.values())


def api_pages(query, today, cfg):
    params = [('per_page', '100'), ('order', 'newest'),
              ('conditions[publication_date][gte]', (today - timedelta(days=cfg['future_days'])).isoformat())]
    params += [('fields[]', f) for f in ('document_number', 'title', 'publication_date', 'effective_on', 'html_url', 'type', 'agencies')]
    params += query
    out = []
    for page in range(1, 21):
        data = json.loads(fetch(API + '?' + urlencode(params + [('page', str(page))])))
        results = data.get('results')
        if not isinstance(results, list) or not isinstance(data.get('count'), int):
            raise ValueError('unexpected API response')
        out.extend(results)
        if len(out) >= data['count']:
            return out
        if not results:
            raise ValueError('incomplete pagination')
    raise ValueError('pagination exceeded safety limit')


def parse_documents(documents, today, cfg):
    events = {}
    for d in documents:
        title = d.get('title') or ''
        if not matches(title, cfg['policy_keywords']):
            continue
        if matches(title, ['request for comments', 'proposed rule', 'request for nominations', 'advisory committee', 'meeting notice']):
            continue
        num = d['document_number']
        url = d.get('html_url') or 'https://www.federalregister.gov/d/' + num
        pub = date.fromisoformat(d['publication_date'])
        if cfg['include_publication_dates'] and in_window(pub.isoformat(), today, cfg):
            uid = 'fr-publication-' + num
            events[uid] = {'id':uid, 'start':pub.isoformat(), 'title':'Policy Published - '+title,
                           'description':'Federal Register publication date, not the time of the original announcement or legal effective date.', 'url':url}
        effective = d.get('effective_on')
        if cfg['include_effective_dates'] and effective and d.get('type') in ('Rule', 'RULE', 'Presidential Document', 'PRESDOCU', 'Notice', 'NOTICE'):
            eff = date.fromisoformat(effective)
            if eff > pub and in_window(effective, today, cfg):
                uid = 'fr-effective-' + num
                events[uid] = {'id':uid, 'start':effective, 'title':'Policy Effective (FR metadata) - '+title,
                               'description':'Date supplied by Federal Register API. All-day reminder; verify scope, amendments and exact effective time in the legal document.', 'url':url}
    return list(events.values())


def fetch_documents(today, cfg):
    docs = api_pages([('conditions[type][]', 'PRESDOCU')], today, cfg)
    # Focused agency search, not all federal notices or all economic news.
    docs += api_pages([('conditions[agencies][]', 'trade-representative-office-of-united-states')], today, cfg)
    return parse_documents(docs, today, cfg)


def escape(s):
    return str(s).replace('\\', '\\\\').replace('\r', '').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def fold(s):
    parts = []
    line = ''
    for ch in s:
        if len((line + ch).encode()) > 75:
            parts.append(line)
            line = ' ' + ch
        else:
            line += ch
    return '\r\n'.join(parts + [line])


def render(events, cfg):
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//US Policy Calendar//EN', 'CALSCALE:GREGORIAN',
             'X-WR-CALNAME:' + escape(cfg['calendar_name']), 'X-WR-TIMEZONE:America/New_York',
             'X-WR-CALDESC:Public schedule and policy dates. Not real-time alerts. Verify all legal deadlines.']
    seen = set()
    for e in sorted(events, key=lambda e: (day(e['start']), e['start'], e['id'])):
        if e['id'] in seen:
            raise ValueError('duplicate UID: ' + e['id'])
        seen.add(e['id'])
        v = start_value(e['start'])
        # Deterministic DTSTAMP: event record version, changed when content changes.
        stamp = e.get('stamp', '20261115T000000Z')
        timing = (['DTSTART:' + v.strftime('%Y%m%dT%H%M%SZ'),
                   'DTEND:' + (v + timedelta(minutes=5)).strftime('%Y%m%dT%H%M%SZ')]
                  if isinstance(v, datetime) else
                  ['DTSTART;VALUE=DATE:' + v.strftime('%Y%m%d'), 'DTEND;VALUE=DATE:' + (v + timedelta(days=1)).strftime('%Y%m%d')])
        lines += ['BEGIN:VEVENT', 'UID:' + e['id'] + '@us-policy-calendar', 'DTSTAMP:' + stamp, *timing,
                  'SUMMARY:' + escape(e['title']), 'DESCRIPTION:' + escape(e['description']), 'URL:' + e['url'],
                  'TRANSP:TRANSPARENT', 'END:VEVENT']
    return ('\r\n'.join(fold(s) for s in lines + ['END:VCALENDAR']) + '\r\n').encode()


def versioned(new, old):
    prev = {e['id']:e for e in old}
    out = []
    for e in new:
        old_e = prev.get(e['id'], {})
        same = {k:v for k,v in old_e.items() if k != 'stamp'} == e
        out.append({**e, 'stamp':old_e.get('stamp', '20261115T000000Z') if same else datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')})
    return sorted(out, key=lambda e:e['id'])


def combine(cache, manual, today, cfg):
    records = {}
    for source, enabled in [('schedule', cfg['presidential_schedule']), ('federal_register', cfg['federal_register'])]:
        if enabled:
            for e in cache[source]:
                if in_window(e['start'], today, cfg):
                    records[e['id']] = e
    for e in manual:
        if not e.get('active', True) or not in_window(e['start'], today, cfg):
            continue
        # Do not duplicate an exact official-document effective-date reminder.
        for uid, other in list(records.items()):
            if uid.startswith('fr-effective-') and day(other['start']) == day(e['start']) and other['url'] == e['url']:
                del records[uid]
        records[e['id']] = {**e, 'stamp':e['reviewed'].replace('-', '')+'T000000Z',
                            'description':e['description'] + ' Manually reviewed: ' + e['reviewed'] + '. Verify later amendments.'}
    return list(records.values())


def main():
    p = argparse.ArgumentParser(); p.add_argument('--offline', action='store_true'); p.add_argument('--as-of'); args = p.parse_args()
    today = date.fromisoformat(args.as_of) if args.as_of else datetime.now(ET).date()
    cfg = load('config.json'); manual = load('manual_events.json'); cache = load('source_cache.json')
    status = {}; failed = False
    if not args.offline:
        for name, enabled, fn in [('schedule', cfg['presidential_schedule'], lambda: parse_schedule(fetch(cfg['schedule_feed']), today, cfg)),
                                  ('federal_register', cfg['federal_register'], lambda: fetch_documents(today, cfg))]:
            if not enabled:
                status[name] = {'state':'disabled'}; continue
            try:
                fresh = fn()
                # Source snapshot replaced only after successful retrieval; preserve past schedule history,
                # but remove future entries absent from the new source (cancellations/changes).
                if name == 'schedule':
                    present = {e['id'] for e in fresh}
                    prior = [e for e in cache[name] if day(e['start']) < today and e['id'] not in present and in_window(e['start'], today, cfg)]
                    cache[name] = versioned(fresh, cache[name]) + prior
                else:
                    cache[name] = versioned(fresh, cache[name])
                status[name] = {'state':'ok', 'events':len(fresh)}
                print(f'{name}: OK ({len(fresh)} events)')
            except Exception as exc:
                failed = True
                status[name] = {'state':'error', 'message':str(exc)[:300], 'cached_events':len(cache[name])}
                print(f'::warning::{name}: {exc}; retaining previous source snapshot', file=sys.stderr)
    events = combine(cache, manual, today, cfg)
    write(CACHE, json_bytes(cache)); write(OUTPUT, render(events, cfg))
    if not args.offline:
        write(STATUS, json_bytes(status))
    print(f'{len(events)} events; calendar generated')
    # Publish valid successful-source changes; workflow marks partial failure after publishing.
    return 2 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
