#!/usr/bin/env python3
"""Pull HRRR surface meteorology and extract point values at each sensor.

For every hour in the requested window this fetches the NOAA HRRR surface
analysis (forecast f00) from the AWS open-data bucket -- using Herbie's GRIB
byte-range subsetting so only the six fields we need are downloaded, not the
full ~120 MB file -- then extracts the nearest-grid-point value of each field
at every monitor location in manifest.csv.

It works one day at a time: the 24 hours of a day are fetched in parallel,
written to one gzipped CSV per city, and the downloaded GRIB chunks are then
deleted. Peak disk stays in the megabytes no matter how long the backfill is.
Days whose city files already exist are skipped, so an interrupted run resumes.

Output: one gzipped CSV per city per day at

    data/met/{city}/{YYYYMMDD}.csv.gz

columns: location_id,datetime,lat,lon,met_pblh,met_wind_u,met_wind_v,
met_temp,met_rh,met_pressure  (datetime hourly UTC).

Fields / units:
  met_pblh      planetary boundary layer height   metres
  met_wind_u    10 m U wind component             m/s
  met_wind_v    10 m V wind component             m/s
  met_temp      2 m temperature                   degrees Celsius
  met_rh        2 m relative humidity             percent
  met_pressure  surface pressure                  hPa

HRRR is U.S. federal public domain (NOAA). CONUS 3 km, hourly, 2014 -> present.

Usage:
  python3 scripts/pull_hrrr.py                                  # last 7 days
  python3 scripts/pull_hrrr.py --days 30
  python3 scripts/pull_hrrr.py --start 2018-01-01 --end 2026-05-18
  python3 scripts/pull_hrrr.py --start 2018-01-01 --end 2026-05-18 --workers 16

Requires: herbie-data, xarray, cfgrib, numpy, pandas  (pip install herbie-data)
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / 'manifest.csv'
MET = REPO / 'data' / 'met'
CACHE = Path.home() / 'data' / 'hrrr'   # Herbie's download cache (cleaned as we go)

# Combined GRIB search -- one byte-range fetch per hour covers all six fields.
SEARCH = '|'.join((
    ':HPBL:surface:anl',
    ':UGRD:10 m above ground:anl',
    ':VGRD:10 m above ground:anl',
    ':TMP:2 m above ground:anl',
    ':RH:2 m above ground:anl',
    ':PRES:surface:anl',
))
# cfgrib short name -> our output column (covers the naming variants cfgrib
# emits for these HRRR fields).
VARMAP = {
    'hpbl': 'met_pblh', 'blh': 'met_pblh',
    'u10': 'met_wind_u', '10u': 'met_wind_u',
    'v10': 'met_wind_v', '10v': 'met_wind_v',
    't2m': 'met_temp', '2t': 'met_temp',
    'r2': 'met_rh', '2r': 'met_rh',
    'sp': 'met_pressure', 'pres': 'met_pressure',
}
FIELD_COLS = ('met_pblh', 'met_wind_u', 'met_wind_v',
              'met_temp', 'met_rh', 'met_pressure')
OUT_COLS = ['location_id', 'datetime', 'lat', 'lon', *FIELD_COLS]


def load_sensors() -> pd.DataFrame:
    """Unique (city, location_id, lat, lon) rows from the manifest."""
    if not MANIFEST.exists():
        sys.exit('manifest.csv not found -- run scripts/pull.py first.')
    m = pd.read_csv(MANIFEST)
    s = (m[['city', 'locationid', 'lat', 'lng']]
         .dropna(subset=['lat', 'lng'])
         .drop_duplicates('locationid')
         .rename(columns={'locationid': 'location_id', 'lng': 'lon'}))
    return s.reset_index(drop=True)


def nearest_indices(grid_lat: np.ndarray, grid_lon: np.ndarray,
                    sensors: pd.DataFrame) -> list[tuple[int, int]]:
    """Nearest HRRR grid cell (iy, ix) for each sensor. The grid is static, so
    this is computed once and reused for every hour."""
    lon = np.where(grid_lon > 180, grid_lon - 360, grid_lon)
    idx = []
    for _, s in sensors.iterrows():
        d2 = (grid_lat - s.lat) ** 2 + (lon - s.lon) ** 2
        idx.append(tuple(int(i) for i in np.unravel_index(np.argmin(d2), d2.shape)))
    return idx


def clean_cache_hour(when: dt.datetime) -> None:
    """Delete the GRIB chunks Herbie cached for one run hour."""
    day_dir = CACHE / when.strftime('%Y%m%d')
    if day_dir.exists():
        for f in day_dir.glob(f'*t{when:%H}z*'):
            try:
                f.unlink()
            except OSError:
                pass


def build_grid_index(sensors: pd.DataFrame, end: dt.datetime) -> list:
    """Build the static nearest-grid-point index from a recent reference hour."""
    from herbie import Herbie
    for back in range(3, 72):
        ref = (end - dt.timedelta(hours=back)).replace(
            minute=0, second=0, microsecond=0)
        try:
            H = Herbie(ref.strftime('%Y-%m-%d %H:00'), model='hrrr',
                       product='sfc', fxx=0, verbose=False)
            ds = H.xarray(':HPBL:surface:anl', verbose=False)
            if isinstance(ds, list):
                ds = ds[0]
            idx = nearest_indices(ds.latitude.values, ds.longitude.values, sensors)
            clean_cache_hour(ref)
            return idx
        except Exception:  # noqa: BLE001
            clean_cache_hour(ref)
    sys.exit('could not fetch a reference HRRR file to build the grid index.')


def fetch_hour(when: dt.datetime, sensors: pd.DataFrame,
               idx: list) -> pd.DataFrame | None:
    """All six fields at every sensor for one UTC hour, or None if unavailable."""
    from herbie import Herbie
    out = {c: np.full(len(sensors), np.nan) for c in FIELD_COLS}
    try:
        H = Herbie(when.strftime('%Y-%m-%d %H:00'), model='hrrr',
                   product='sfc', fxx=0, verbose=False)
        result = H.xarray(SEARCH, verbose=False)
        datasets = result if isinstance(result, list) else [result]
        for ds in datasets:
            for vname in ds.data_vars:
                col = VARMAP.get(str(vname))
                if not col:
                    continue
                vals = ds[vname].values
                for n, (iy, ix) in enumerate(idx):
                    out[col][n] = float(vals[iy, ix])
    except Exception as e:  # noqa: BLE001 -- a bad hour shouldn't sink the run
        print(f'    WARN {when:%Y-%m-%d %H}Z: {e}', file=sys.stderr)
        return None
    finally:
        clean_cache_hour(when)

    if all(np.all(np.isnan(out[c])) for c in FIELD_COLS):
        return None  # nothing matched -- treat as unavailable
    df = pd.DataFrame({
        'location_id': sensors.location_id.values,
        'datetime': when.replace(tzinfo=dt.timezone.utc).isoformat(),
        'lat': sensors.lat.values, 'lon': sensors.lon.values, **out,
    })
    df['met_temp'] = df['met_temp'] - 273.15         # K -> degC
    df['met_pressure'] = df['met_pressure'] / 100.0  # Pa -> hPa
    return df.round(3)


def write_day(day_str: str, allday: pd.DataFrame, sensors: pd.DataFrame) -> None:
    """Write/merge one gzipped CSV per city for a day (idempotent)."""
    tagged = allday.merge(sensors[['location_id', 'city']], on='location_id')
    for city, grp in tagged.groupby('city'):
        dest = MET / city / f'{day_str}.csv.gz'
        dest.parent.mkdir(parents=True, exist_ok=True)
        rows = grp.drop(columns='city')
        if dest.exists():
            old = pd.read_csv(dest)
            rows = (pd.concat([old, rows], ignore_index=True)
                    .drop_duplicates(['location_id', 'datetime'], keep='last'))
        rows = rows.sort_values(['location_id', 'datetime'])[OUT_COLS]
        with gzip.open(dest, 'wt', newline='') as f:
            rows.to_csv(f, index=False)


def main() -> None:
    ap = argparse.ArgumentParser(description='Pull HRRR meteorology per sensor.')
    ap.add_argument('--days', type=int, default=7,
                    help='pull the most recent N days (default 7)')
    ap.add_argument('--start', help='window start YYYY-MM-DD (overrides --days)')
    ap.add_argument('--end', help='window end YYYY-MM-DD (default: now)')
    ap.add_argument('--workers', type=int, default=16,
                    help='parallel hour fetches (default 16)')
    args = ap.parse_args()

    end = (dt.datetime.strptime(args.end, '%Y-%m-%d') if args.end
           else dt.datetime.utcnow()).replace(minute=0, second=0, microsecond=0)
    start = (dt.datetime.strptime(args.start, '%Y-%m-%d') if args.start
             else end - dt.timedelta(days=args.days)).replace(
        minute=0, second=0, microsecond=0)

    sensors = load_sensors()
    cities = sorted(sensors.city.unique())
    print(f'{len(sensors)} sensors; window {start:%Y-%m-%d} -> {end:%Y-%m-%d}; '
          f'{args.workers} workers', flush=True)

    idx = build_grid_index(sensors, end)
    print('grid index built; starting day-by-day pull', flush=True)

    n_days = (end.date() - start.date()).days + 1
    written = skipped = empty = 0
    for d in range(n_days):
        day = start.date() + dt.timedelta(days=d)
        day_str = day.strftime('%Y%m%d')
        if all((MET / c / f'{day_str}.csv.gz').exists() for c in cities):
            skipped += 1
        else:
            hours = [dt.datetime(day.year, day.month, day.day, h)
                     for h in range(24)
                     if start <= dt.datetime(day.year, day.month, day.day, h) <= end]
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                frames = [f for f in ex.map(
                    lambda w: fetch_hour(w, sensors, idx), hours) if f is not None]
            if frames:
                write_day(day_str, pd.concat(frames, ignore_index=True), sensors)
                written += 1
            else:
                empty += 1
        shutil.rmtree(CACHE / day_str, ignore_errors=True)  # belt-and-suspenders
        seen = written + skipped + empty
        if seen % 10 == 0 or d == n_days - 1:
            print(f'    {day_str}: {written} written, {skipped} skipped, '
                  f'{empty} empty, {n_days - seen} remaining', flush=True)
    print(f'done: {written} day-batches written, {skipped} skipped, '
          f'{empty} empty (no HRRR data)', flush=True)


if __name__ == '__main__':
    main()
