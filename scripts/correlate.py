#!/usr/bin/env python3
"""Fit AirGradient → AirNow harmonization per region.

Loads hourly CSVs from data/hourly/{city}/{provider}/..., medians per city per UTC hour,
fits OLS regression with AirNow as the regulatory reference.

Writes harmonization/ag-vs-airnow.json with:
  - per-city Pearson r, OLS slope/intercept, RMSE
  - per-concentration-bucket residuals
  - formula AG_airnow_equiv ≈ AG × multiplier + offset
"""
from __future__ import annotations
import csv, datetime as dt, glob, gzip, json, statistics, sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / 'data' / 'hourly'
OUT = REPO / 'harmonization' / 'ag-vs-airnow.json'


def to_utc_hour(ts: str) -> dt.datetime:
    s = ts.replace(' ', 'T')
    if s.endswith('+00'): s = s + ':00'
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None: d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)


def load_provider(city: str, provider: str) -> dict:
    """Returns {(city, hour_utc): [values]}."""
    out = defaultdict(list)
    for p in glob.glob(str(DATA / city / provider / '**' / '*.csv.gz'), recursive=True):
        with gzip.open(p, 'rt') as f:
            for row in csv.DictReader(f):
                if row.get('parameter') != 'pm25': continue
                try:
                    v = float(row['value'])
                except (TypeError, ValueError):
                    continue
                if not (0 <= v < 500): continue  # filter sensor malfunctions; real wildfire PM2.5 peaks ~300
                out[(city, to_utc_hour(row['datetime']))].append(v)
    return out


def regress(pairs):
    n = len(pairs)
    if n < 5: return None
    x = [p[0] for p in pairs]; y = [p[1] for p in pairs]
    mx = sum(x)/n; my = sum(y)/n
    sxx = sum((xi-mx)**2 for xi in x); syy = sum((yi-my)**2 for yi in y)
    sxy = sum((x[i]-mx)*(y[i]-my) for i in range(n))
    if sxx == 0 or syy == 0: return None
    slope = sxy / sxx
    intercept = my - slope*mx
    r = sxy / (sxx*syy)**0.5
    rmse = (sum((y[i] - (slope*x[i]+intercept))**2 for i in range(n))/n)**0.5
    return n, mx, my, r, slope, intercept, rmse


def main():
    print('loading AirGradient + AirNow hourly readings...', file=sys.stderr)
    out = {'fit_run_at': dt.datetime.utcnow().isoformat() + 'Z',
           'reference': 'AirNow (regulatory BAM/FEM)',
           'compared': 'AirGradient (Plantower PMS5003 optical)',
           'per_region': {}}

    for city in ('TOR', 'NYC', 'CHI'):
        ag = load_provider(city, 'airgradient')
        an = load_provider(city, 'airnow')
        ag_med = {k: statistics.median(vs) for k, vs in ag.items()}
        an_med = {k: statistics.median(vs) for k, vs in an.items()}
        pairs = [(an_med[k], ag_med[k]) for k in an_med if k in ag_med]
        r = regress(pairs)
        if not r:
            out['per_region'][city] = {'n': len(pairs), 'note': 'insufficient overlap'}
            continue
        n, an_mean, ag_mean, pearson, slope, intercept, rmse = r
        # Map AG → AirNow-equivalent: AN ≈ slope*AG + intercept after inverting?
        # We regressed y=AG on x=AN, so AG ≈ slope*AN + intercept.
        # Inverse: AN ≈ (AG - intercept) / slope = AG * (1/slope) + (-intercept/slope)
        out['per_region'][city] = {
            'n_hours': n,
            'airnow_mean': round(an_mean, 2),
            'airgradient_mean': round(ag_mean, 2),
            'pearson_r': round(pearson, 3),
            'ols_slope_ag_on_airnow': round(slope, 3),
            'ols_intercept': round(intercept, 3),
            'rmse': round(rmse, 2),
            'harmonization_ag_to_airnow': {
                'multiplier': round(1/slope, 3),
                'offset': round(-intercept/slope, 3),
                'formula': f'AN_equiv ≈ AG × {1/slope:.3f} + {-intercept/slope:.3f}',
            },
        }
        # Residual by AirNow concentration bucket
        buckets = [(0,1),(1,2),(2,5),(5,10),(10,20),(20,1000)]
        resb = {}
        for lo, hi in buckets:
            bp = [(an_v, ag_v) for an_v, ag_v in pairs if lo <= an_v < hi]
            if not bp: continue
            res = [ag_v - an_v for an_v, ag_v in bp]
            resb[f'{lo}-{hi if hi<1000 else "inf"}'] = {
                'n': len(bp),
                'mean_residual_ag_minus_airnow': round(sum(res)/len(res), 2),
            }
        out['per_region'][city]['residual_by_airnow_bucket'] = resb

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
