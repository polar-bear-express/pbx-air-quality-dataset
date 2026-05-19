#!/usr/bin/env python3
"""Load an ML-ready Parquet table and show a quick summary.

This is the fast path into the dataset. Instead of globbing tens of thousands
of daily CSVs, load one of the tables built by scripts/build_dataset.py:

    data/parquet/{CITY}.parquet         per-sensor long table (graph models)
    data/parquet/{CITY}_hourly.parquet  city-hour wide table (baseline models)

The older per-provider files (data/parquet/{city}_{provider}.parquet, written
by scripts/consolidate.py) are still produced for backward compatibility.

Run scripts/build_dataset.py first if the tables are not present locally.

Usage:
    python3 scripts/load.py                 # NYC, hourly wide table
    python3 scripts/load.py CHI long        # CHI, per-sensor long table
    python3 scripts/load.py TOR hourly

Requires: pandas, pyarrow  (pip install pandas pyarrow)
"""
from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
PARQUET = REPO / 'data' / 'parquet'


def load(city: str, kind: str) -> pd.DataFrame:
    """Load the hourly wide table (kind='hourly') or per-sensor long table
    (kind='long') for a city."""
    name = f'{city}.parquet' if kind == 'long' else f'{city}_hourly.parquet'
    path = PARQUET / name
    if not path.exists():
        sys.exit(f'{path} not found — run: python3 scripts/build_dataset.py')
    return pd.read_parquet(path)


def main() -> None:
    city = sys.argv[1] if len(sys.argv) > 1 else 'NYC'
    kind = sys.argv[2] if len(sys.argv) > 2 else 'hourly'

    df = load(city, kind)
    print(f'{city} ({kind}): {len(df):,} rows, '
          f'{df["datetime"].min()} -> {df["datetime"].max()}')
    print(f'columns: {", ".join(df.columns)}')

    if kind == 'hourly':
        # Coverage: how much of each column is populated.
        cov = (df.notna().mean() * 100).round(1)
        print('\ncolumn coverage (%):')
        print(cov.to_string())
        print('\nlast 5 hours:')
        print(df.tail().to_string(index=False))
    else:
        print(f'\n{df["location_id"].nunique()} sensors, '
              f'parameters: {", ".join(sorted(df["parameter"].dropna().unique()))}')
        print('\nrows per parameter:')
        print(df["parameter"].value_counts().to_string())


if __name__ == '__main__':
    main()
