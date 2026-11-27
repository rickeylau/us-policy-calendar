"""Production merge for Futu calendar. Publication is explicitly opt-in."""
import argparse
import os
import re
from datetime import date, datetime, timedelta, timezone

from futu_calendar import CN, FutuError, fetch_items, make_client, select_events


def publication_enabled():
    return os.environ.get('FUTU_PUBLISH_ENABLED', '').strip().lower() == 'true'


def query_window(now):
    today = now.astimezone(CN).date()
    return today - timedelta(days=3), today + timedelta(days=7)


def china_day(event):
    from policy_calendar import start_value
    value = start_value(event['start'])
    if not isinstance(value, datetime):
        raise ValueError('Futu event must have a timestamp')
    return value.astimezone(CN).date()


def refresh_snapshot(old, fresh, begin, end, today, cfg):
    """Replace only the queried window after success; retain older history."""
    from policy_calendar import in_window, versioned
    ids = {e['id'] for e in fresh}
    prior = [e for e in old if e['id'] not in ids
             and not begin <= china_day(e) <= end
             and in_window(e['start'], today, cfg)]
    return sorted(versioned(fresh, old) + prior, key=lambda e: e['id'])


def title_key(title):
    """Very conservative aliases, not fuzzy semantic/time-window matching."""
    title = re.sub(r'^(?:White House|Futu)\s*-\s*', '', title, flags=re.I)
    title = re.sub(r'\bU\.S\.(?=\s|$)', 'US', title, flags=re.I)
    title = ' '.join(re.sub(r'[^\w\s]', ' ', title.casefold()).split())
    title = re.sub(r'^(?:us president trump|us president|president trump|the president)\b', 'president', title)
    if re.fullmatch(r'president (?:(?:will )?(?:deliver|delivers|delivered|give|gives|gave) (?:a |the )?(?:speech|remarks|address)|(?:will )?speak(?:s)?|spoke)', title):
        return 'president generic remarks'
    return title


def match_key(event):
    from policy_calendar import start_value
    dt = start_value(event['start'])
    if not isinstance(dt, datetime):
        return None
    title = title_key(event['title'])
    return (dt.replace(second=0, microsecond=0), title) if title else None


def merge_events(base, futu_rows, today, cfg):
    """Keep existing UIDs. Suppress Futu only for a one-to-one exact-minute/title match."""
    from policy_calendar import in_window
    base = list(base)
    schedule_keys = {}
    for e in base:
        if e['id'].startswith('schedule-'):
            k = match_key(e)
            if k is not None:
                schedule_keys[k] = schedule_keys.get(k, 0) + 1
    seen = {e['id'] for e in base}
    eligible = [e for e in futu_rows if in_window(e['start'], today, cfg)]
    futu_keys = {}
    for e in eligible:
        k = match_key(e)
        if k is not None:
            futu_keys[k] = futu_keys.get(k, 0) + 1
    added, duplicates = [], 0
    for e in eligible:
        k = match_key(e)
        if e['id'] in seen or (schedule_keys.get(k) == 1 and futu_keys.get(k) == 1):
            duplicates += 1
            continue
        seen.add(e['id'])
        added.append(e)
    return base + added, len(added), duplicates


def fetch_window(begin, end):
    items = fetch_items(make_client(), begin, end)
    return len(items), select_events(items, begin, end)


def update_source(cache, today, cfg, now=None, fetcher=None):
    """Return independent source status; never log raw provider data or secrets."""
    now = now or datetime.now(timezone.utc)
    fetcher = fetcher or fetch_window
    old = cache.get('futu', [])
    begin, end = query_window(now)
    try:
        raw_count, fresh = fetcher(begin, end)
        cache['futu'] = refresh_snapshot(old, fresh, begin, end, today, cfg)
        return {'state': 'ok', 'events': len(fresh), 'raw_events': raw_count,
                'cached_events': len(cache['futu']), 'query_begin': begin.isoformat(),
                'query_end': end.isoformat()}
    except Exception as exc:
        # FutuError messages from the adapter contain only safe diagnostics.
        message = str(exc) if isinstance(exc, FutuError) else type(exc).__name__
        from policy_calendar import in_window
        cache['futu'] = [e for e in old if in_window(e['start'], today, cfg)]
        return {'state': 'error', 'message': message[:180], 'cached_events': len(cache['futu'])}


def preview(begin, end):
    from policy_calendar import ET, load, combine, render
    today = datetime.now(ET).date()
    cfg, cache, manual = load('config.json'), load('source_cache.json'), load('manual_events.json')
    count, fresh = fetch_window(begin, end)
    snapshot = refresh_snapshot(cache.get('futu', []), fresh, begin, end, today, cfg)
    base = combine(cache, manual, today, cfg)
    merged, added, duplicates = merge_events(base, snapshot, today, cfg)
    content = render(merged, cfg)
    if content.count(b'BEGIN:VEVENT') != len(merged):
        raise FutuError('ICS count mismatch')
    print('Futu API: OK')
    print('Fetched events:', count)
    print('Matched US policy events:', len(fresh))
    print('Existing events:', len(base))
    print('Futu events added:', added)
    print('Duplicates suppressed:', duplicates)
    print('Merged calendar events:', len(merged))
    print('In-memory production merge: OK')
    print('Publication: DISABLED (no files written)')


def main():
    p = argparse.ArgumentParser(description='Read-only Stage 2 production merge preview')
    p.add_argument('--begin', required=True, type=date.fromisoformat)
    p.add_argument('--end', required=True, type=date.fromisoformat)
    args = p.parse_args()
    try:
        preview(args.begin, args.end)
    except Exception as exc:
        print('Preview failed:', str(exc) if isinstance(exc, FutuError) else type(exc).__name__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
