#!/usr/bin/env python3
"""Build the ML-ready tables from the raw layers.

The dataset is stored as three raw layers -- air quality (data/hourly/),
HRRR meteorology (data/met/), and HMS wildfire smoke (data/smoke/). They are
correct but not trainable as-is. This script joins all three and engineers the
derived features a forecaster actually needs, writing two Parquet products per
city into data/parquet/:

  {CITY}_hourly.parquet  -- THE modeling table. One row per hour on a complete
                            hourly index, with city-median pollutants, city-mean
                            meteorology, smoke coverage, and engineered features:
                            PM2.5 lags (1/2/3/6/12/24 h), 6 h & 24 h rolling
                            means, calendar fields, and a smoke-upwind term.
                            Load this and train.

  {CITY}.parquet         -- per-sensor long table. One row per (sensor, hour):
                            a single pollutant reading plus the meteorology and
                            smoke at that location, for spatial / graph models.

Engineered features (on the hourly table):
  pm25_lag{1,2,3,6,12,24}  PM2.5 shifted back N hours -- lag-1 is the single
                           strongest predictor in the literature.
  pm25_roll6 / pm25_roll24 trailing 6 h / 24 h mean PM2.5.
  hour/dayofweek/month/is_weekend  calendar / diurnal cycle.
  smoke_upwind             cos of (wind direction - bearing to nearest smoke):
                           +1 = smoke is directly upwind (incoming), -1 = down-
                           wind. Combined with smoke_nearest_km this is the
                           "approaching smoke" signal.

Re-run any time after the raw layers refresh. Idempotent.

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
SMOKE = REPO / 'data' / 'smoke'
OUT = REPO / 'data' / 'parquet'

CITIES = ('TOR', 'NYC', 'CHI')
POLLUTANTS = ('pm25', 'pm10', 'no2', 'so2', 'co', 'o3')
MET_COLS = ('met_pblh', 'met_wind_speed', 'met_wind_dir',
            'met_temp', 'met_rh', 'met_pressure')
SMOKE_COLS = ('smoke_overhead', 'smoke_density', 'smoke_density_rank',
              'smoke_within_100km', 'smoke_within_300km', 'smoke_within_500km',
              'smoke_nearest_km', 'smoke_nearest_bearing')
LAGS = (1, 2, 3, 6, 12, 24)
DENSITY_RANK = {'': 0, 'light': 1, 'medium': 2, 'heavy': 3}


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
        df['provider'] = Path(p).parts[-3]  # data/hourly/{city}/{provider}/...
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
    keep = ['location_id', 'datetime', *MET_COLS]
    return df[keep].drop_duplicates(['location_id', 'datetime'], keep='last')


def load_smoke(city: str) -> pd.DataFrame:
    """HMS wildfire-smoke coverage for a city, keyed by date (one row per day)."""
    f = SMOKE / f'{city}.csv'
    if not f.exists():
        return pd.DataFrame(columns=['date', *SMOKE_COLS])
    s = pd.read_csv(f)
    s['date'] = pd.to_datetime(s['date'], utc=True).dt.normalize()
    s['smoke_density'] = s['smoke_density'].fillna('').astype(str)
    s['smoke_density_rank'] = (s['smoke_density'].str.lower()
                               .map(DENSITY_RANK).fillna(0).astype(int))
    s = s.rename(columns={'within_100km': 'smoke_within_100km',
                          'within_300km': 'smoke_within_300km',
                          'within_500km': 'smoke_within_500km',
                          'nearest_km': 'smoke_nearest_km',
                          'nearest_bearing': 'smoke_nearest_bearing'})
    return s[['date', *SMOKE_COLS]]


def build_city(city: str) -> None:
    print(f'building {city} ...', flush=True)
    aq = load_air_quality(city)
    if aq.empty:
        print(f'  no air-quality data for {city} -- skipped', flush=True)
        return
    met = load_met(city)
    smoke = load_smoke(city)

    # --- per-sensor long table -------------------------------------------------
    long = aq.merge(met, on=['location_id', 'datetime'], how='left')
    long['date'] = long['datetime'].dt.normalize()
    if not smoke.empty:
        long = long.merge(smoke, on='date', how='left')
    long['hour'] = long['datetime'].dt.hour
    long['dayofweek'] = long['datetime'].dt.dayofweek
    long['month'] = long['datetime'].dt.month
    long_cols = ['datetime', 'city', 'location_id', 'sensors_id', 'location',
                 'provider', 'lat', 'lon', 'parameter', 'units', 'value',
                 *MET_COLS, *SMOKE_COLS, 'hour', 'dayofweek', 'month']
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
    met_hourly = (met.drop(columns='location_id').groupby('datetime').mean()
                  if not met.empty else pd.DataFrame(columns=MET_COLS))
    wide = pollutants.join(met_hourly, how='outer')

    # Put it on a complete, gap-free hourly index so lag features are correct
    # (a plain .shift() is only a true lag if no hours are missing).
    full = pd.date_range(wide.index.min(), wide.index.max(), freq='h', tz='UTC')
    wide = wide.reindex(full).rename_axis('datetime').reset_index()

    # Smoke (daily) broadcast to every hour of its day.
    wide['date'] = wide['datetime'].dt.normalize()
    if not smoke.empty:
        wide = wide.merge(smoke, on='date', how='left')
    else:
        for c in SMOKE_COLS:
            wide[c] = np.nan

    # Smoke-upwind: cos(wind direction - bearing to nearest smoke). +1 means the
    # wind is blowing from where the smoke is -> smoke is heading for the city.
    wide['smoke_upwind'] = np.cos(np.radians(
        wide['met_wind_dir'] - wide['smoke_nearest_bearing'])).round(3)

    # Engineered PM2.5 features.
    for lag in LAGS:
        wide[f'pm25_lag{lag}'] = wide['pm25'].shift(lag)
    wide['pm25_roll6'] = wide['pm25'].rolling(6, min_periods=1).mean().round(2)
    wide['pm25_roll24'] = wide['pm25'].rolling(24, min_periods=1).mean().round(2)

    # Calendar / diurnal features.
    wide['hour'] = wide['datetime'].dt.hour
    wide['dayofweek'] = wide['datetime'].dt.dayofweek
    wide['month'] = wide['datetime'].dt.month
    wide['is_weekend'] = (wide['datetime'].dt.dayofweek >= 5).astype(int)

    wide['city'] = city
    lag_cols = [f'pm25_lag{lag}' for lag in LAGS]
    wide = wide.reindex(columns=[
        'datetime', 'city', *POLLUTANTS, *MET_COLS, *SMOKE_COLS, 'smoke_upwind',
        *lag_cols, 'pm25_roll6', 'pm25_roll24',
        'hour', 'dayofweek', 'month', 'is_weekend'])
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
