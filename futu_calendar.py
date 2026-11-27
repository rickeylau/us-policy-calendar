"""Read-only Futu calendar adapter. No persistent storage or publication."""
import argparse
import base64
import json
import os
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

BASE = 'https://webapi.futunn.com'
PATH = '/api/v1.0/quote/economic-calendar/list'
CN = ZoneInfo('Asia/Shanghai')

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

class FutuError(Exception):
    pass


def make_client():
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    app = os.environ.get('FUTU_APP_KEY', '').strip()
    raw = os.environ.get('FUTU_PRIVATE_KEY', '').strip()
    if not app or not raw:
        raise FutuError('Missing GitHub Secrets')
    body = re.sub(r'\s+', '', raw.replace('-----BEGIN PRIVATE KEY-----', '').replace('-----END PRIVATE KEY-----', ''))
    try:
        private = load_pem_private_key(('-----BEGIN PRIVATE KEY-----\n' + body + '\n-----END PRIVATE KEY-----\n').encode(), None)
    except (ValueError, TypeError):
        raise FutuError('Invalid private key format') from None
    if not isinstance(private, Ed25519PrivateKey):
        raise FutuError('Expected Ed25519 private key')
    opener = build_opener(NoRedirect())

    def request(params):
        query = urlencode(params)
        for attempt in range(3):
            ts = str(int(time.time() * 1000))
            payload = '\n'.join([ts, 'GET', PATH, query, ''])
            sig = base64.b64encode(private.sign(payload.encode())).decode()
            req = Request(BASE + PATH + '?' + query, headers={
                'X-Api-Key': app, 'X-Timestamp': ts, 'X-Nonce': uuid.uuid4().hex,
                'Authorization': sig, 'Accept': 'application/json'})
            try:
                with opener.open(req, timeout=20) as res:
                    raw = res.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise FutuError('Response too large')
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise FutuError('Unexpected response schema')
                if result.get('ret_code') == -11 and attempt < 2:
                    time.sleep(2 ** attempt * 5)
                    continue
                return result
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise FutuError(f'HTTP {exc.code}') from None
            except (URLError, TimeoutError):
                if attempt == 2:
                    raise FutuError('Network failure') from None
            except (ValueError, UnicodeError):
                raise FutuError('Invalid JSON response') from None
            time.sleep(2 ** attempt * 5)
    return request


def fetch_items(request, begin, end):
    if not 0 <= (end - begin).days <= 10:
        raise FutuError('Date range must be 1 to 11 days inclusive')
    params = dict(begin_date=begin.isoformat(), end_date=end.isoformat(), tab_type='ECONOMIC_EVENT', market='US', star=0, only_focus='false', page_size=100)
    items, seen = [], set()
    for _ in range(20):
        result = request(params.copy())
        code = result.get('ret_code')
        if code == -10 and not items and not seen:
            return []
        if code != 0:
            raise FutuError(f'API failure (code {code if isinstance(code, int) else "unknown"})')
        data = result.get('data')
        page = result.get('pagination')
        if not isinstance(data, dict) or not isinstance(data.get('items'), list) or not isinstance(page, dict):
            raise FutuError('Invalid response schema')
        if not all(isinstance(x, dict) for x in data['items']):
            raise FutuError('Invalid event record')
        items.extend(data['items'])
        if page.get('completed') is True:
            return items
        flag = page.get('page_flag')
        if not isinstance(flag, str) or not flag or flag in seen:
            raise FutuError('Invalid pagination cursor')
        seen.add(flag)
        params['page_flag'] = flag
    raise FutuError('Pagination limit exceeded')

# Require policy actor/topic AND an event action; exclude central-bank events.
ACTOR = re.compile(r'\b(?:president trump|u\.?s\.? president|white house|treasury secretary|secretary of the treasury|u\.?s\.? trade representative|commerce secretary)\b|特朗普|美国总统|美國總統|白宫|白宮|美国财长|美國財長|美国贸易代表|美國貿易代表', re.I)
ACTION = re.compile(r'\b(?:speech|speaks?|remarks|address(?:es)?|interview|press conference|briefing|statement|announce(?:s|d|ment)?|testif(?:y|ies|ied)|testimony|sign(?:s|ed|ing)?)\b|讲话|講話|演讲|演講|发表|發表|记者会|記者會|宣布|听证|聽證|签署|簽署', re.I)
FED = re.compile(r'\b(?:federal reserve|fomc|fed chair|fed governor|fed president|powell)\b|美联储|美聯儲|鲍威尔|鮑威爾', re.I)


def select_events(items, begin, end):
    out = {}
    for e in items:
        title = e.get('title')
        if e.get('country') not in ('United States', 'US', 'USA', '美国', '美國') or e.get('item_type') != 'ECONOMIC_EVENT':
            continue
        if not isinstance(title, str) or not ACTOR.search(title) or not ACTION.search(title) or FED.search(title):
            continue
        star, ts, uid = e.get('star'), e.get('timestamp'), e.get('article_id')
        if type(star) is not int or not 2 <= star <= 5:
            continue
        if type(ts) is not int or ts <= 0 or not isinstance(uid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', uid):
            raise FutuError('Matched policy event has invalid time or ID')
        try:
            dt = datetime.fromtimestamp(ts, timezone.utc)
        except (ValueError, OverflowError, OSError):
            raise FutuError('Invalid event timestamp') from None
        if not begin <= dt.astimezone(CN).date() <= end:
            continue
        row = dict(id='futu-' + uid, start=dt.isoformat(), title='Futu - ' + title,
                   description='Futu calendar listed time; not independently confirmed actual speech time.',
                   url='https://www.futunn.com/')
        if row['id'] in out and out[row['id']] != row:
            raise FutuError('Conflicting records for same event ID')
        out[row['id']] = row
    return sorted(out.values(), key=lambda e: (e['start'], e['id']))


def main():
    p = argparse.ArgumentParser(description='Read-only cloud smoke test, counts only')
    p.add_argument('--begin', type=date.fromisoformat)
    p.add_argument('--end', type=date.fromisoformat)
    args = p.parse_args()
    today = datetime.now(CN).date()
    begin = args.begin or today - timedelta(days=3)
    end = args.end or (begin if args.begin else today + timedelta(days=7))
    try:
        items = fetch_items(make_client(), begin, end)
        selected = select_events(items, begin, end)
        # Exercise the existing renderer in memory, never save or print feed data.
        from policy_calendar import render, load
        rendered = render(selected, load('config.json'))
        if rendered.count(b'BEGIN:VEVENT') != len(selected):
            raise FutuError('ICS preview count mismatch')
        print('Futu API: OK')
        print('Fetched events:', len(items))
        print('Matched US policy events:', len(selected))
        print('In-memory ICS rendering: OK')
        print('Publication: DISABLED (no files written)')
    except Exception as exc:
        print('Futu test failed:', str(exc) if isinstance(exc, FutuError) else type(exc).__name__)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
