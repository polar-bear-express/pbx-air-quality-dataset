#!/usr/bin/env python3
"""Load a consolidated Parquet file and show a baseline hourly aggregation.

This is the fast path into the dataset. Instead of globbing tens of thousands
of daily CSVs, load one Parquet file per (city, provider):

    data/parquet/{city}_{provider}.parquet

Run scripts/consolidate.py first if the Parquet files are not present locally.

Usage:
    python3 scripts/load.py            # defaults to NYC / airnow
    python3 scripts/load.py CHI airgradient

Requires: pandas, pyarrow  (pip install pandas pyarrow)
"""
from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
PARQUET = REPO / 'data' / 'parquet'


def load(city: str, provider: str) -> pd.DataFrame:
    """One-line load of a city/provider slice as a typed, datetime-indexed DataFrame."""
    path = PARQUET / f'{city}_{provider}.parquet'
    if not path.exists():
        sys.exit(f'{path} not found — run: python3 scripts/consolidate.py')
    return pd.read_parquet(path)


def main() -> None:
    city = sys.argv[1] if len(sys.argv) > 1 else 'NYC'
    provider = sys.argv[2] if len(sys.argv) > 2 else 'airnow'

    df = load(city, provider)
    print(f'{city}/{provider}: {len(df):,} rows, '
          f'{df["location_id"].nunique()} sensors, '
          f'{df["datetime"].min()} -> {df["datetime"].max()}')

    # Baseline: hourly city-wide median PM2.5 across all sensors.
    hourly = (df.set_index('datetime')
                .groupby(pd.Grouper(freq='1h'))['value']
                .median()
                .dropna())
    print(f'\nhourly city median PM2.5 (last 5 hours):')
    print(hourly.tail())
    print(f'\noverall median: {hourly.median():.2f} ug/m3')


if __name__ == '__main__':
    main()
