#!/usr/bin/env python3
"""Baseline next-hour PM2.5 forecast -- a starting point for modelling this dataset.

Loads the ML-ready hourly tables (data/parquet/{CITY}_hourly.parquet), builds a
PM2.5 target H hours ahead, trains a histogram gradient-boosting regressor, and
benchmarks it against the naive *persistence* baseline (the forecast = the last
observed value). The split is chronological -- no shuffling; this is a time
series and shuffling would leak the future into training.

This is a reference baseline, not a tuned model. It exists to show the dataset
is trainable end-to-end and to give every feature a number to beat.

Run after scripts/build_dataset.py.
Requires: pandas, numpy, scikit-learn, pyarrow.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance

REPO = Path(__file__).resolve().parent.parent
PARQUET = REPO / 'data' / 'parquet'
CITIES = ('TOR', 'NYC', 'CHI')
HORIZONS = (1, 6)  # forecast lead times, hours ahead

# Features known at prediction time t (no lookahead).
FEATURES = [
    'pm25', 'pm10', 'no2', 'so2', 'co', 'o3',
    'met_pblh', 'met_wind_speed', 'met_wind_dir', 'met_temp', 'met_rh',
    'met_pressure',
    'smoke_overhead', 'smoke_density_rank', 'smoke_within_100km',
    'smoke_within_300km', 'smoke_within_500km', 'smoke_nearest_km',
    'smoke_nearest_bearing', 'smoke_upwind',
    'pm25_lag1', 'pm25_lag2', 'pm25_lag3', 'pm25_lag6', 'pm25_lag12',
    'pm25_lag24', 'pm25_roll6', 'pm25_roll24',
    'hour', 'dayofweek', 'month', 'is_weekend', 'city_code',
]


def load() -> pd.DataFrame:
    frames = []
    for code, city in enumerate(CITIES):
        f = PARQUET / f'{city}_hourly.parquet'
        if f.exists():
            d = pd.read_parquet(f)
            d['city_code'] = code
            frames.append(d)
    if not frames:
        raise SystemExit('No hourly tables -- run scripts/build_dataset.py first.')
    df = pd.concat(frames, ignore_index=True)
    for col in ('smoke_overhead', 'smoke_within_100km', 'smoke_within_300km',
                'smoke_within_500km', 'is_weekend'):
        df[col] = df[col].astype(float)
    return df


def rmse(a, b) -> float:
    return float(np.sqrt(np.nanmean((np.asarray(a) - np.asarray(b)) ** 2)))


def mae(a, b) -> float:
    return float(np.nanmean(np.abs(np.asarray(a) - np.asarray(b))))


def evaluate(df: pd.DataFrame, horizon: int) -> None:
    # Target = PM2.5 `horizon` hours ahead, shifted within each city.
    parts = []
    for _, g in df.groupby('city_code'):
        g = g.sort_values('datetime').copy()
        g['target'] = g['pm25'].shift(-horizon)
        parts.append(g)
    d = pd.concat(parts, ignore_index=True)
    # Keep rows with a target, a current reading, and meteorology present.
    d = d.dropna(subset=['target', 'pm25', 'met_pblh'])
    d = d.sort_values('datetime').reset_index(drop=True)

    split = int(len(d) * 0.8)
    train, test = d.iloc[:split], d.iloc[split:]

    model = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.06,
                                          max_depth=8, random_state=0)
    model.fit(train[FEATURES], train['target'])
    pred = model.predict(test[FEATURES])
    persist = test['pm25'].to_numpy()  # naive baseline: next = now

    r_p, r_m = rmse(test['target'], persist), rmse(test['target'], pred)
    m_p, m_m = mae(test['target'], persist), mae(test['target'], pred)
    skill = 1 - r_m / r_p

    print(f'\n=== +{horizon} h horizon  '
          f'({len(train):,} train / {len(test):,} test rows) ===')
    print(f'  persistence   RMSE {r_p:6.2f}   MAE {m_p:6.2f}   ug/m3')
    print(f'  GBM model     RMSE {r_m:6.2f}   MAE {m_m:6.2f}   ug/m3')
    print(f'  skill score   {skill:+.3f}   ({skill * 100:+.1f}% RMSE reduction '
          f'vs persistence)')

    # Permutation importance on a test sample -- which features earn their keep.
    sample = test.sample(min(6000, len(test)), random_state=0)
    imp = permutation_importance(model, sample[FEATURES], sample['target'],
                                 n_repeats=5, random_state=0)
    order = np.argsort(imp.importances_mean)[::-1][:8]
    print('  top features:',
          ', '.join(f'{FEATURES[i]}' for i in order))


def main() -> None:
    df = load()
    print(f'loaded {len(df):,} city-hours across {df.city_code.nunique()} cities')
    for h in HORIZONS:
        evaluate(df, h)
    print('\nNote: a 1-hour horizon is a hard baseline -- persistence is very '
          'strong there.\nSkill widens at longer lead times; tune from here.')


if __name__ == '__main__':
    main()
