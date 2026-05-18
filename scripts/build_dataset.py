#!/usr/bin/env python3
"""Build the ML-ready tables from the raw daily CSVs + HRRR meteorology.

The raw archive under data/hourly/ is tens of thousands of tiny gzipped CSVs --
correct, but not what you train on. This script joins air quality with the HRRR
meteorology under data/met/ and writes two committed Parquet products per city:

  data/parquet/{CITY}.parquet         per-sensor long table -- one row per
                                      (sensor, hour): the source-of-truth table
                                      for spatial / graph models. Each row
                                      carries one pollutant reading plus the
                                      meteorology at that sensor's location.

  data/parquet/{CITY}_hourly.parquet  city-hour wide table -- one row per hour
                                      with city-median pm25/pm10/no2/so2/co/o3
                                      and city-mean meteorology. The ready-to-go
                                      baseline table for LightGBM / LSTM models.

Wind speed and direction are derived from the HRRR 10 m u/v components
(direction is meteorological -- the compass bearing the wind blows *from*).

Re-run any time after pull.py / pull_hrrr.py refresh the raw inputs. Idempotent:
it overwrites the Parquet outputs from whatever is currently on disk.

Requires: pandas, pyarrow, numpy
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
HOURLY = REPO / 'data' / 'hourly'
MET = REPO / 'data' / 'met'
OUT = REPO / 'data' / 'parquet'

CITIES = ('TOR', 'NYC', 'CHI')
POLLUTANTS = ('pm25', 'pm10', 'no2', 'so2', 'co', 'o3')
MET_COLS = ('met_pblh', 'met_wind_speed', 'met_wind_dir',
            'met_temp', 'met_rh', 'met_pressure')


def load_air_quality(city: str) -> pd.DataFrame:
    """All raw daily CSVs for a city, tagged with provider, floored to the hour."""
    paths = sorted(glob.glob(str(HOURLY / city / '**' / '*.csv.gz'), recursive=True))
    frames = []
    for p in paths:
        try:
            df = pd.read_csv(p, compression='gzip')
        except Exception as e:  # noqa: BLE001
            print(f'  WARN: skipped {p}: {e}', file=sys.stderr)
            continue
        # data/hourly/{city}/{provider}/{locationid}/{day}.csv.gz
        df['provider'] = Path(p).parts[-3]
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df['datetime'] = pd.to_datetime(df['datetime'], utc=True, errors='coerce')
    df = df.dropna(subset=['datetime', 'value'])
    df['datetime'] = df['datetime'].dt.floor('h')
    df['city'] = city
    return df


def load_met(city: str) -> pd.DataFrame:
    """HRRR meteorology for a city; derives wind speed + direction from u/v."""
    paths = sorted(glob.glob(str(MET / city / '*.csv.gz')))
    if not paths:
        return pd.DataFrame(columns=['location_id', 'datetime', *MET_COLS])
    df = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
    df['datetime'] = pd.to_datetime(df['datetime'], utc=True, errors='coerce')
    df['datetime'] = df['datetime'].dt.floor('h')
    u, v = df['met_wind_u'], df['met_wind_v']
    df['met_wind_speed'] = np.hypot(u, v).round(3)
    # Meteorological direction: the compass bearing the wind blows *from*.
    df['met_wind_dir'] = ((270.0 - np.degrees(np.arctan2(v, u))) % 360).round(1)
    keep = ['location_id', 'datetime', 'met_pblh', 'met_wind_speed',
            'met_wind_dir', 'met_temp', 'met_rh', 'met_pressure']
    return (df[keep].drop_duplicates(['location_id', 'datetime'], keep='last'))


def build_city(city: str) -> None:
    print(f'building {city} ...', flush=True)
    aq = load_air_quality(city)
    if aq.empty:
        print(f'  no air-quality data for {city} -- skipped', flush=True)
        return
    met = load_met(city)

    # --- per-sensor long table -------------------------------------------------
    long = aq.merge(met, on=['location_id', 'datetime'], how='left')
    long_cols = ['datetime', 'city', 'location_id', 'sensors_id', 'location',
                 'provider', 'lat', 'lon', 'parameter', 'units', 'value',
                 *MET_COLS]
    long = (long.reindex(columns=long_cols)
            .sort_values(['location_id', 'parameter', 'datetime'])
            .reset_index(drop=True))
    dest = OUT / f'{city}.parquet'
    long.to_parquet(dest, engine='pyarrow', compression='snappy', index=False)
    print(f'  wrote {dest.relative_to(REPO)}  rows={len(long):,}  '
          f'{dest.stat().st_size / 1e6:.2f} MB', flush=True)

    # --- city-hour wide table --------------------------------------------------
    pollutants = (aq[aq.parameter.isin(POLLUTANTS)]
                  .groupby(['datetime', 'parameter'])['value'].median()
                  .unstack('parameter')
                  .reindex(columns=POLLUTANTS))
    met_hourly = (met.drop(columns='location_id')
                  .groupby('datetime').mean()) if not met.empty else \
        pd.DataFrame(columns=MET_COLS)
    wide = (pollutants.join(met_hourly, how='left')
            .reset_index())
    wide.insert(1, 'city', city)
    wide = wide.reindex(columns=['datetime', 'city', *POLLUTANTS, *MET_COLS])
    dest = OUT / f'{city}_hourly.parquet'
    wide.to_parquet(dest, engine='pyarrow', compression='snappy', index=False)
    print(f'  wrote {dest.relative_to(REPO)}  rows={len(wide):,}  '
          f'{dest.stat().st_size / 1e6:.2f} MB', flush=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for city in CITIES:
        build_city(city)
    print('done.', flush=True)


if __name__ == '__main__':
    main()
