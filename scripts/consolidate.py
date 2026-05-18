#!/usr/bin/env python3
"""Consolidate the ~60k daily gzipped CSVs into one Parquet file per city per provider.

The raw dataset under data/hourly/{city}/{provider}/{locationid}/{YYYYMMDD}.csv.gz
is correct but slow to load: tens of thousands of tiny files. This script reads
every daily CSV and writes a single columnar Parquet file per (city, provider):

    data/parquet/{city}_{provider}.parquet      (6 files: TOR/NYC/CHI x airnow/airgradient)

Each Parquet file is:
  - sorted by (location_id, datetime)
  - typed (datetime parsed to UTC; numeric columns as float; ids as int)
  - snappy-compressed

Re-run any time after scripts/pull.py refreshes the raw CSVs. Idempotent: it
simply overwrites the Parquet outputs from whatever is currently on disk.

Requires: pandas, pyarrow  (pip install pandas pyarrow)
"""
from __future__ import annotations
import glob
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / 'data' / 'hourly'
OUT = REPO / 'data' / 'parquet'

CITIES = ('TOR', 'NYC', 'CHI')
PROVIDERS = ('airnow', 'airgradient')

DTYPES = {
    'location_id': 'Int64',
    'sensors_id': 'Int64',
    'location': 'string',
    'lat': 'float64',
    'lon': 'float64',
    'parameter': 'string',
    'units': 'string',
    'value': 'float64',
}


def load_pair(city: str, provider: str) -> pd.DataFrame:
    paths = sorted(glob.glob(str(DATA / city / provider / '**' / '*.csv.gz'), recursive=True))
    if not paths:
        return pd.DataFrame()
    frames = []
    for p in paths:
        try:
            frames.append(pd.read_csv(p, compression='gzip'))
        except Exception as e:  # noqa: BLE001
            print(f'  WARN: skipped {p}: {e}', file=sys.stderr)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    # Type the columns explicitly.
    for col, dt in DTYPES.items():
        if col in df.columns:
            df[col] = df[col].astype(dt)
    # Parse datetime to UTC (raw values carry local tz offsets).
    df['datetime'] = pd.to_datetime(df['datetime'], utc=True, errors='coerce')
    df = df.dropna(subset=['datetime'])
    df = df.sort_values(['location_id', 'datetime']).reset_index(drop=True)
    return df


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    total_rows = 0
    for city in CITIES:
        for provider in PROVIDERS:
            print(f'consolidating {city}/{provider} ...', flush=True)
            df = load_pair(city, provider)
            dest = OUT / f'{city}_{provider}.parquet'
            if df.empty:
                print(f'  no data for {city}/{provider} — skipped', flush=True)
                continue
            df.to_parquet(dest, engine='pyarrow', compression='snappy', index=False)
            total_rows += len(df)
            size_mb = dest.stat().st_size / 1e6
            print(f'  wrote {dest.relative_to(REPO)}  rows={len(df):,}  {size_mb:.2f} MB', flush=True)
    print(f'done: {total_rows:,} total rows across Parquet files', flush=True)


if __name__ == '__main__':
    main()
