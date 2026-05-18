# Air Quality Dataset — TOR / NYC / CHI

Hourly air-quality readings for **Toronto, New York City, and Chicago** — PM2.5
plus co-pollutants (PM10, NO2, SO2, CO, O3) — joined with **HRRR meteorology**
(boundary-layer height, wind, temperature, humidity, pressure) and **NOAA HMS
wildfire-smoke** coverage. Aggregated from public sources via [OpenAQ](https://openaq.org/),
[NOAA HRRR](https://rapidrefresh.noaa.gov/hrrr/), and [NOAA HMS](https://www.ospo.noaa.gov/products/land/hms.html).

Built for training short-horizon air-quality prediction models. The pollutant
drivers, meteorology, and smoke are included precisely because PM2.5 history
alone only gets you a persistence baseline — boundary-layer height, wind,
co-pollutants, and upwind smoke are what let a model actually beat it. Released
under CC-BY-4.0; see [ATTRIBUTION.md](./ATTRIBUTION.md) for required upstream credits.

Maintained by Polar Bear Express.

## Reproducible — verify it yourself

This is the trust anchor for the dataset: **nothing here is hand-curated or fabricated.** Every reading is rebuilt from public, independently accessible sources by the scripts checked into this repo. You can re-run them yourself and confirm the output matches, byte for byte, what is committed.

The raw measurements come from four public archives:

- **OpenAQ S3 archive** (`https://openaq-data-archive.s3.amazonaws.com/`) — the keyless public mirror that hosts the daily air-quality CSVs.
- **OpenAQ v3 API** (`https://api.openaq.org/v3`) — used only to discover which sensor locations are active in each city's bounding box (a free API key, available at <https://explore.openaq.org/register>, is needed for this discovery step).
- **NOAA HRRR** on the AWS Open Data bucket (`s3://noaa-hrrr-bdp-pds/`) — the keyless public mirror of the High-Resolution Rapid Refresh model, source of the meteorology.
- **NOAA HMS** smoke-polygon archive (`https://satepsanone.nesdis.noaa.gov/.../HMS/Smoke_Polygons/`) — keyless daily shapefiles of observed wildfire smoke, source of the smoke layer.

The pipeline is seven real scripts, each doing exactly one job:

| Script | What it does |
|---|---|
| [`scripts/pull.py`](./scripts/pull.py) | Discovers active AirNow + AirGradient monitors in the TOR/NYC/CHI bounding boxes via the OpenAQ v3 API — every location measuring PM2.5, PM10, NO2, SO2, CO, or O3 — then downloads every daily CSV from the OpenAQ S3 archive into `data/hourly/`. Idempotent — skips files already present at the same size — and rebuilds `manifest.csv` from what is on disk. |
| [`scripts/pull_hrrr.py`](./scripts/pull_hrrr.py) | Downloads NOAA HRRR surface analysis (GRIB byte-range subset) and extracts boundary-layer height, 10 m wind, 2 m temperature/humidity, and surface pressure at every monitor location into `data/met/`. Multiprocessing, idempotent; takes a `--start/--end` window for backfilling. |
| [`scripts/pull_hms.py`](./scripts/pull_hms.py) | Downloads NOAA HMS daily smoke-plume shapefiles and computes, per city per day, smoke overhead + density and whether smoke lies within 100/300/500 km (plus distance + bearing to the nearest plume) into `data/smoke/`. Idempotent. |
| [`scripts/build_dataset.py`](./scripts/build_dataset.py) | Joins air quality + meteorology + smoke and engineers lag/rolling/calendar/upwind features, writing the two ML-ready Parquet tables per city — `{CITY}.parquet` (per-sensor long) and `{CITY}_hourly.parquet` (city-hour wide) — into `data/parquet/`. |
| [`scripts/consolidate.py`](./scripts/consolidate.py) | (Legacy) Reads all daily gzipped CSVs and writes one typed, datetime-sorted Parquet file per city per provider into `data/parquet/`. Kept for backward compatibility. |
| [`scripts/correlate.py`](./scripts/correlate.py) | Fits per-city OLS regressions between AirGradient and AirNow hourly medians and writes the coefficients, Pearson r, RMSE, and per-concentration-bucket residuals to `harmonization/ag-vs-airnow.json`. |
| [`scripts/load.py`](./scripts/load.py) | Convenience loader: one-line `pd.read_parquet` of an ML-ready table plus a coverage summary. |

To re-derive the whole dataset from scratch:

```bash
export OPENAQ_API_KEY=...          # free at https://explore.openaq.org/register
python3 scripts/pull.py            # rebuilds data/hourly/ + manifest.csv from public sources
python3 scripts/pull_hrrr.py --start 2018-01-01 --end <today>   # HRRR meteorology -> data/met/
python3 scripts/pull_hms.py        # HMS wildfire smoke -> data/smoke/
python3 scripts/build_dataset.py   # builds ML-ready data/parquet/{CITY}*.parquet tables
python3 scripts/consolidate.py     # (legacy) per-provider parquet files
python3 scripts/correlate.py       # rebuilds harmonization/ag-vs-airnow.json
```

Because the upstream archives are public and the scripts are short, anyone can independently reproduce this dataset and confirm it was not altered or invented.

> **Note on the HRRR backfill.** The full 2018→present meteorology backfill
> streams a few hundred GB (deleted as it goes — peak disk stays small) and runs
> for several hours. `pull_hrrr.py` is idempotent and multiprocessing: completed
> days are skipped, so it safely resumes after an interruption. Without `--start`
> it pulls only the most recent week.

## What's in here

Two kinds of data, joined into the ML-ready tables described below.

**Air quality** — hourly observations from OpenAQ:

| Source | Hardware | Pollutants | Earliest data |
|---|---|---|---|
| **AirNow** (U.S. EPA + Environment Canada) | regulatory BAM, FRM, FEM | PM2.5, PM10, NO2, SO2, CO, O3 | Jan 2018 (US) / Apr 2020 (CA) |
| **AirGradient** | Plantower PMS5003 (low-cost optical) | PM2.5, PM10 | Nov 2023 |

**Meteorology** — hourly, extracted at each monitor location from the NOAA HRRR
3 km surface analysis: planetary boundary-layer height, 10 m wind (speed +
direction), 2 m temperature, 2 m relative humidity, surface pressure. These are
the drivers short-horizon PM2.5 forecasters need — boundary-layer height and
wind in particular.

**Wildfire smoke** — daily, from the NOAA HMS smoke-plume analysis: for each
city, whether smoke is overhead and its density, whether smoke lies within
100/300/500 km, and the distance + bearing to the nearest plume. Because smoke
is wind-transported, *upwind* smoke is what predicts a city's air quality before
it arrives — the ring/distance fields exist to capture that.

`manifest.csv` lists every monitor with its coordinates, the pollutants it
measures, and its date range.

The two air-quality source types are intentionally kept separate. AirNow is regulatory ground truth (sparse but trustworthy). AirGradient is the dense low-cost network. Don't blindly average them — they're calibrated differently and diverge non-linearly above ~10 μg/m³. Use `scripts/correlate.py` to fit your own per-city harmonization if your model needs them on the same scale.

## Layout

```
data/hourly/{city}/{provider}/{locationid}/{YYYYMMDD}.csv.gz   air quality (raw)
data/met/{city}/{YYYYMMDD}.csv.gz                              meteorology (raw)
data/smoke/{city}.csv                                          wildfire smoke (raw, daily)
data/parquet/{CITY}.parquet            ML-ready — per-sensor long table
data/parquet/{CITY}_hourly.parquet     ML-ready — city-hour wide table
```

Each air-quality daily CSV follows the OpenAQ schema:

```
location_id,sensors_id,location,datetime,lat,lon,parameter,units,value
```

`datetime` is ISO-8601 with the monitor's local timezone offset. `parameter` is one of `pm25,pm10,no2,so2,co,o3`. `units`/`value` vary by pollutant (μg/m³ for particulates; ppm/ppb for gases — read the `units` column).

Each meteorology daily CSV:

```
location_id,datetime,lat,lon,met_pblh,met_wind_u,met_wind_v,met_temp,met_rh,met_pressure
```

`datetime` is hourly UTC. Units: PBLH metres, wind m/s, temperature °C, RH %, pressure hPa.

## The ML-ready tables — start here

[`scripts/build_dataset.py`](./scripts/build_dataset.py) joins air quality +
meteorology + smoke and engineers the derived features a forecaster needs,
writing two committed Parquet tables per city. **These are what you train on.**

**`data/parquet/{CITY}_hourly.parquet` — city-hour wide.** One row per hour on a
complete, gap-free hourly index. The ready-to-go table for LightGBM / LSTM
forecasters. Columns:

| Group | Columns |
|---|---|
| keys | `datetime`, `city` |
| pollutants (city-median) | `pm25, pm10, no2, so2, co, o3` |
| meteorology (city-mean) | `met_pblh, met_wind_speed, met_wind_dir, met_temp, met_rh, met_pressure` |
| smoke | `smoke_overhead, smoke_density, smoke_density_rank, smoke_within_100km, smoke_within_300km, smoke_within_500km, smoke_nearest_km, smoke_nearest_bearing` |
| smoke × wind | `smoke_upwind` — cos(wind direction − bearing to nearest smoke): +1 = smoke directly upwind (incoming), −1 = downwind |
| PM2.5 lags | `pm25_lag1, pm25_lag2, pm25_lag3, pm25_lag6, pm25_lag12, pm25_lag24` |
| PM2.5 rolling means | `pm25_roll6, pm25_roll24` |
| calendar | `hour, dayofweek, month, is_weekend` |

```python
import pandas as pd
df = pd.read_parquet('data/parquet/CHI_hourly.parquet')
```

**`data/parquet/{CITY}.parquet` — per-sensor long.** One row per (sensor, hour):
a single pollutant reading plus the meteorology and smoke at that monitor's
location. The source-of-truth table for spatial / graph models, where each
monitor is a node. Columns: `datetime, city, location_id, sensors_id, location,
provider, lat, lon, parameter, units, value`, the six `met_*` fields, the eight
`smoke_*` fields, and `hour, dayofweek, month`.

[`scripts/load.py`](./scripts/load.py) loads either and prints a summary:
`python3 scripts/load.py CHI hourly` or `python3 scripts/load.py NYC long`.
Wind direction is meteorological — the compass bearing the wind blows *from*.

**Baseline.** [`examples/baseline_forecast.py`](./examples/baseline_forecast.py)
trains a gradient-boosted PM2.5 forecaster on the hourly table and benchmarks it
against the naive persistence baseline (forecast = last observed value) at +1 h
and +6 h lead times — a reference number to beat. At +6 h it cuts RMSE ~23%
below persistence; +1 h is the genuinely hard regime where persistence is very
strong. Autoregressive lags and meteorology dominate the feature importance, as
the literature predicts.

> The older per-provider files (`data/parquet/{city}_{provider}.parquet`, from
> [`scripts/consolidate.py`](./scripts/consolidate.py)) are still produced for
> backward compatibility, but the two tables above supersede them.

## Quick start (raw CSVs)

```python
import pandas as pd, glob

# Load all NYC AirNow data
paths = glob.glob('data/hourly/NYC/airnow/**/*.csv.gz', recursive=True)
df = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
df['datetime'] = pd.to_datetime(df['datetime'], utc=True)

# Hourly city median across sensors
city_hourly = df.groupby(pd.Grouper(key='datetime', freq='1h'))['value'].median()
```

## Manifest

`manifest.csv` indexes every (city, provider, locationid) tuple with coordinates, the pollutants it measures, day count, and date range:

```
city,provider,locationid,name,lat,lng,parameters,n_days,first_day,last_day
NYC,AirNow,857,Fort Lee Near Road,40.85194,-73.96528,pm25,2961,20180102,20260515
```

## Harmonizing AirGradient ↔ AirNow

The two networks measure the same air but report different μg/m³. AirNow uses regulatory-grade Beta Attenuation Monitors (BAM) that physically weigh dried particles. AirGradient uses optical particle counters (Plantower PMS5003) that estimate mass from laser-scatter and apply firmware corrections. They diverge in predictable ways:

- **Low PM (<5 μg/m³)**: AirGradient under-reads by 1–2 μg/m³ (optical noise floor)
- **High humidity (RH>80%)**: AirGradient over-reads (water droplets scatter laser light)
- **Elevated PM (>10 μg/m³)**: divergence grows non-linearly; magnitude varies by site

Run `python scripts/correlate.py` to compute fresh per-city AG↔AirNow regression coefficients on this dataset. Results land in `harmonization/ag-vs-airnow.json`.

## Refreshing

```bash
export OPENAQ_API_KEY=...        # free at https://explore.openaq.org/register
python3 scripts/pull.py          # idempotent — only fetches new daily files
python3 scripts/pull_hrrr.py     # idempotent — appends recent HRRR meteorology
python3 scripts/build_dataset.py # rebuilds the ML-ready tables
```

Re-run as needed. The scripts skip files already present at the same size.

## Caveats for ML training

1. **TOR AG↔AirNow correlation is weak** (r≈0.47, vs r≈0.85–0.91 for NYC/CHI). The Toronto AirGradient sensors and AirNow stations are placed in geographically distinct parts of the city, so they're measuring different microenvironments. Don't expect a single TOR harmonization to fit well — consider per-station fits, or include neighborhood as a feature.
2. **AirNow density is low for cross-validation** — TOR has only 4 stations. Geographic-holdout CV gets thin there; fall back to time-slice CV or pool with AirGradient.
3. **Local timezone offsets in `datetime`** — the raw air-quality CSVs carry the monitor's local offset; the meteorology CSVs and all Parquet tables are UTC. Normalize to UTC before joining or aggregating raw CSVs yourself.
4. **Sparse offline windows** — sensors drop out for days/weeks at a time. Forward-fill carefully; better to drop than fabricate.
5. **Occasional sensor malfunctions** (e.g., AirGradient values >500 μg/m³ in clean air) — `scripts/correlate.py` filters >500 μg/m³ by default; you may want stricter limits.
6. **No row-level contributor attribution** — OpenAQ strips AirGradient's `publicContributorName` field in the S3 export. Network-level attribution to AirGradient is the best we can preserve.
7. **A small fraction of meteorology hours are missing** — HRRR has occasional archive gaps and a few fetches fail; those individual hours are left null in `data/met/` and the `met_*` columns rather than fabricated. Expect ~4% of hours null. Check coverage with `python3 scripts/load.py <CITY> hourly`.
8. **Co-pollutant monitors are separate locations** — each OpenAQ location measures one pollutant, so NO2/O3/etc. come from different sites than PM2.5. The per-sensor table keeps them as distinct rows; the city-hour table aggregates each pollutant across that city's monitors. There is no single co-located multi-pollutant station.
9. **Smoke is a daily product** — NOAA HMS publishes one smoke analysis per day, so every `smoke_*` field is constant across the 24 hours of a date. It is a city-scale signal, not a per-sensor or sub-daily one.

## Disclaimer

This dataset is provided **for research and modeling only**. The AirNow
portion (U.S. EPA / Environment Canada) consists of **preliminary data
that has not been fully verified or validated and is subject to change**.

Do **not** use this dataset for regulatory, legal, health, or safety
decisions. The U.S. EPA, the AirNow program, Environment Canada, NOAA,
AirGradient, and OpenAQ do **not** endorse this dataset or any product
derived from it. The data is provided "as is", without warranty.

## License & attribution

Aggregated dataset: **CC-BY-4.0**. Upstream sources retain their own terms — see [ATTRIBUTION.md](./ATTRIBUTION.md).

If you publish work using this dataset, please cite:

> Hourly air-quality dataset (PM2.5 + co-pollutants, meteorology, and wildfire smoke) for Toronto, New York City, and Chicago. Sourced via OpenAQ, NOAA HRRR, and NOAA HMS. Original providers: U.S. EPA AirNow, Environment Canada, AirGradient, NOAA/NCEP, NOAA/NESDIS.
