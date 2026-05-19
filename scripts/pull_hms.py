#!/usr/bin/env python3
"""Pull NOAA HMS wildfire-smoke coverage for each city.

Downloads the daily NOAA Hazard Mapping System (HMS) smoke-plume shapefile --
polygons of smoke observed over North America, classified Light/Medium/Heavy --
and computes, per city per day:

  smoke_overhead    a smoke polygon covers the city centroid (bool)
  smoke_density     densest overhead smoke (light/medium/heavy, or empty)
  within_100km      a smoke plume lies within 100 km of the city (bool)
  within_300km      ... within 300 km
  within_500km      ... within 500 km
  nearest_km        distance to the nearest smoke plume (0 if overhead)
  nearest_bearing   compass bearing (deg) to the nearest plume

The ring / distance fields are the point of this layer: smoke is wind-transported,
so smoke *upwind* of a city predicts its air quality before it arrives overhead.
Smoke directly overhead is largely already visible in the PM2.5 reading itself.

Output: data/smoke/{city}.csv  -- one row per city per day.

HMS is U.S. federal public domain (NOAA/NESDIS). Daily, 2005 -> present.

Usage:
  python3 scripts/pull_hms.py                                  # 2018-01-01 -> today
  python3 scripts/pull_hms.py --start 2023-01-01 --end 2023-12-31

Idempotent: days already present in every city's CSV are skipped on re-run.

Requires: shapely, pyshp, pandas  (pip install shapely pyshp)
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import math
import sys
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import shapefile  # pyshp
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / 'manifest.csv'
SMOKE = REPO / 'data' / 'smoke'
BASE = ('https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/'
        'Smoke_Polygons/Shapefile')

DENSITY_RANK = {'light': 1, 'medium': 2, 'heavy': 3}
RANK_NAME = {0: '', 1: 'light', 2: 'medium', 3: 'heavy'}
OUT_COLS = ['city', 'date', 'smoke_overhead', 'smoke_density',
            'within_100km', 'within_300km', 'within_500km',
            'nearest_km', 'nearest_bearing']


def city_centroids() -> dict[str, tuple[float, float]]:
    """(lat, lon) centroid per city, from the mean of its monitor coordinates."""
    m = pd.read_csv(MANIFEST).dropna(subset=['lat', 'lng'])
    return {c: (float(g.lat.mean()), float(g.lng.mean()))
            for c, g in m.groupby('city')}


def fetch_polygons(day: dt.date):
    """Download one day's HMS smoke shapefile -> list of (density, [(lon,lat)..]).
    Returns None if that day's file is unavailable."""
    url = f'{BASE}/{day:%Y}/{day:%m}/hms_smoke{day:%Y%m%d}.zip'
    try:
        raw = urllib.request.urlopen(url, timeout=30).read()
        z = zipfile.ZipFile(io.BytesIO(raw))
        base = next(n[:-4] for n in z.namelist() if n.lower().endswith('.shp'))
        r = shapefile.Reader(
            shp=io.BytesIO(z.read(base + '.shp')),
            dbf=io.BytesIO(z.read(base + '.dbf')),
            shx=io.BytesIO(z.read(base + '.shx')))
        fields = [f[0] for f in r.fields[1:]]
        di = fields.index('Density') if 'Density' in fields else None
        out = []
        for sr in r.shapeRecords():
            pts = sr.shape.points
            if len(pts) >= 3:
                dens = str(sr.record[di]).strip().lower() if di is not None else ''
                out.append((dens, pts))
        return out
    except Exception:  # noqa: BLE001 -- a missing/bad day shouldn't sink the run
        return None


def smoke_for_city(clat: float, clon: float, polygons: list) -> dict:
    """Project every plume to a local km plane centred on the city and measure."""
    kx = 111.320 * math.cos(math.radians(clat))   # km per degree longitude
    ky = 110.574                                   # km per degree latitude
    city = Point(0.0, 0.0)
    overhead_rank = 0
    nearest = None  # (distance_km, polygon)
    for dens, pts in polygons:
        ring = [((lon - clon) * kx, (lat - clat) * ky) for lon, lat in pts]
        try:
            poly = Polygon(ring)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue
        except Exception:  # noqa: BLE001
            continue
        if poly.contains(city):
            overhead_rank = max(overhead_rank, DENSITY_RANK.get(dens, 1))
            d = 0.0
        else:
            d = city.distance(poly)
        if nearest is None or d < nearest[0]:
            nearest = (d, poly)
    if nearest is None:
        return dict(smoke_overhead=False, smoke_density='', within_100km=False,
                    within_300km=False, within_500km=False,
                    nearest_km='', nearest_bearing='')
    d, poly = nearest
    _, np_poly = nearest_points(city, poly)
    bearing = math.degrees(math.atan2(np_poly.x, np_poly.y)) % 360
    return dict(
        smoke_overhead=overhead_rank > 0,
        smoke_density=RANK_NAME[overhead_rank],
        within_100km=d <= 100, within_300km=d <= 300, within_500km=d <= 500,
        nearest_km=round(d, 1), nearest_bearing=round(bearing, 1))


def process_day(day: dt.date, centroids: dict):
    """All cities' smoke metrics for one day, or None if HMS lacks that day."""
    polys = fetch_polygons(day)
    if polys is None:
        return None
    return [{'city': c, 'date': day.isoformat(),
             **smoke_for_city(lat, lon, polys)}
            for c, (lat, lon) in centroids.items()]


def main() -> None:
    ap = argparse.ArgumentParser(description='Pull NOAA HMS smoke coverage.')
    ap.add_argument('--start', default='2018-01-01', help='YYYY-MM-DD')
    ap.add_argument('--end', help='YYYY-MM-DD (default: today)')
    ap.add_argument('--workers', type=int, default=16)
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    centroids = city_centroids()
    SMOKE.mkdir(parents=True, exist_ok=True)

    existing = {c: (set(pd.read_csv(SMOKE / f'{c}.csv')['date'].astype(str))
                    if (SMOKE / f'{c}.csv').exists() else set())
                for c in centroids}
    days, d = [], start
    while d <= end:
        if not all(d.isoformat() in existing[c] for c in centroids):
            days.append(d)
        d += dt.timedelta(days=1)
    print(f'{len(centroids)} cities; {len(days)} days to fetch', flush=True)

    rows, done = [], 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for res in ex.map(lambda dd: process_day(dd, centroids), days):
            done += 1
            if res:
                rows.extend(res)
            if done % 200 == 0:
                print(f'  {done}/{len(days)} days', flush=True)

    if not rows:
        print('no new smoke data', flush=True)
        return
    new = pd.DataFrame(rows)
    for c in centroids:
        f = SMOKE / f'{c}.csv'
        part = new[new.city == c]
        if f.exists():
            part = pd.concat([pd.read_csv(f), part], ignore_index=True)
        part = part.drop_duplicates('date', keep='last').sort_values('date')
        part[OUT_COLS].to_csv(f, index=False)
        print(f'  wrote {f.relative_to(REPO)}  {len(part)} days', flush=True)
    print('done.', flush=True)


if __name__ == '__main__':
    main()
