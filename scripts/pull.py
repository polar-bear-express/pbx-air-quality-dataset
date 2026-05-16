#!/usr/bin/env python3
"""Idempotent pull of hourly PM2.5 from OpenAQ S3 archive.

Re-run to fetch new daily files. Existing files are skipped if same size.

Requires: env var OPENAQ_API_KEY (only for the initial location-discovery step;
the S3 archive itself is keyless).
"""
from __future__ import annotations
import csv, datetime as dt, json, os, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / 'data' / 'hourly'
MANIFEST = REPO / 'manifest.csv'

CITY_BBOX = {
    'TOR': '-79.64,43.58,-79.12,43.85',
    'NYC': '-74.26,40.55,-73.68,40.92',
    'CHI': '-87.94,41.64,-87.52,42.02',
}
WANT_PROVIDERS = {'AirGradient', 'AirNow'}
ACTIVE_CUTOFF = (dt.datetime.utcnow() - dt.timedelta(days=14)).replace(tzinfo=dt.timezone.utc)


def discover_targets():
    api_key = os.environ.get('OPENAQ_API_KEY')
    if not api_key:
        sys.exit('Set OPENAQ_API_KEY (sign up at https://explore.openaq.org/register)')
    targets = []
    for city, bbox in CITY_BBOX.items():
        url = f'https://api.openaq.org/v3/locations?bbox={bbox}&parameters_id=2&limit=200'
        req = urllib.request.Request(url, headers={'X-API-Key': api_key})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        for loc in data.get('results') or []:
            prov = (loc.get('provider') or {}).get('name')
            if prov not in WANT_PROVIDERS: continue
            last = (loc.get('datetimeLast') or {}).get('utc') or ''
            try:
                if last and dt.datetime.fromisoformat(last.replace('Z', '+00:00')) >= ACTIVE_CUTOFF:
                    coords = loc.get('coordinates') or {}
                    targets.append({
                        'city': city, 'provider': prov, 'locationid': loc['id'],
                        'name': loc.get('name'), 'lat': coords.get('latitude'), 'lng': coords.get('longitude'),
                    })
            except ValueError: pass
    return targets


def list_keys(loc_id):
    keys = []
    for year in range(2018, dt.date.today().year + 1):
        url = f'https://openaq-data-archive.s3.amazonaws.com/?prefix=records/csv.gz/locationid={loc_id}/year={year}/'
        marker = ''
        while True:
            full = url + (f'&marker={marker}' if marker else '')
            try:
                with urllib.request.urlopen(full, timeout=15) as r:
                    txt = r.read().decode()
            except Exception:
                break
            page = re.findall(r'<Key>([^<]+\.csv\.gz)</Key>', txt)
            if not page: break
            keys.extend(page)
            if '<IsTruncated>true</IsTruncated>' in txt:
                marker = page[-1]
            else:
                break
    return loc_id, keys


def fetch_one(target, key):
    m = re.search(r'-(\d{8})\.csv\.gz$', key)
    if not m: return 'skip'
    day = m.group(1)
    dest = DATA / target['city'] / target['provider'].lower() / str(target['locationid']) / f'{day}.csv.gz'
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        # HEAD first to compare size (skip if local matches)
        req = urllib.request.Request(f'https://openaq-data-archive.s3.amazonaws.com/{key}', method='HEAD')
        with urllib.request.urlopen(req, timeout=15) as r:
            remote_size = int(r.headers.get('Content-Length', 0))
        if dest.exists() and dest.stat().st_size == remote_size:
            return 'skip'
        with urllib.request.urlopen(f'https://openaq-data-archive.s3.amazonaws.com/{key}', timeout=20) as r:
            dest.write_bytes(r.read())
        return 'ok'
    except Exception as e:
        return f'err:{e}'


def main():
    print('1/3 discovering active sensors via OpenAQ v3...', flush=True)
    targets = discover_targets()
    print(f'    found {len(targets)} active sensors', flush=True)

    print('2/3 listing S3 keys per sensor...', flush=True)
    all_keys = []
    with ThreadPoolExecutor(max_workers=24) as ex:
        futs = {ex.submit(list_keys, t['locationid']): t for t in targets}
        for fut in as_completed(futs):
            t = futs[fut]
            _, keys = fut.result()
            for k in keys:
                all_keys.append((t, k))
    print(f'    {len(all_keys)} CSV keys', flush=True)

    print('3/3 fetching (skipping existing)...', flush=True)
    counts = {'ok': 0, 'skip': 0, 'err': 0}
    with ThreadPoolExecutor(max_workers=32) as ex:
        futs = [ex.submit(fetch_one, t, k) for t, k in all_keys]
        for i, fut in enumerate(as_completed(futs)):
            r = fut.result()
            counts['ok' if r == 'ok' else 'skip' if r == 'skip' else 'err'] += 1
            if (i+1) % 500 == 0:
                print(f'    {i+1}/{len(all_keys)}  ok={counts["ok"]} skip={counts["skip"]} err={counts["err"]}', flush=True)
    print(f'done: {counts}', flush=True)

    # Rebuild manifest
    rows = []
    for t in targets:
        d = DATA / t['city'] / t['provider'].lower() / str(t['locationid'])
        days = sorted(p.stem for p in d.glob('*.csv.gz')) if d.exists() else []
        rows.append([t['city'], t['provider'], t['locationid'], t['name'],
                     t['lat'], t['lng'], len(days),
                     days[0] if days else '', days[-1] if days else ''])
    with open(MANIFEST, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['city','provider','locationid','name','lat','lng','n_days','first_day','last_day'])
        w.writerows(sorted(rows))
    print(f'wrote manifest: {MANIFEST}', flush=True)


if __name__ == '__main__':
    main()
