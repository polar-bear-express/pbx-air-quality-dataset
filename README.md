# Air Quality Dataset — TOR / NYC / CHI

Hourly air-quality readings for **Toronto, New York City, and Chicago** — PM2.5
plus co-pollutants (PM10, NO2, SO2, CO, O3) — joined with **HRRR meteorology**
(boundary-layer height, wind, temperature, humidity, pressure). Aggregated from
public sources via [OpenAQ](https://openaq.org/) and [NOAA HRRR](https://rapidrefresh.noaa.gov/hrrr/).

Built for training short-horizon air-quality prediction models. The pollutant
drivers and meteorology are included precisely because PM2.5 history alone only
gets you a persistence baseline — boundary-layer height, wind, and co-pollutants
are what let a model actually beat it. Released under CC-BY-4.0; see
[ATTRIBUTION.md](./ATTRIBUTION.md) for required upstream credits.

Maintained by Polar Bear Express.

## Reproducible — verify it yourself

This is the trust anchor for the dataset: **nothing here is hand-curated or fabricated.** Every reading is rebuilt from public, independently accessible sources by the scripts checked into this repo. You can re-run them yourself and confirm the output matches, byte for byte, what is committed.

The raw measurements come from three public archives:

- **OpenAQ S3 archive** (`https://openaq-data-archive.s3.amazonaws.com/`) — the keyless public mirror that hosts the daily air-quality CSVs.
- **OpenAQ v3 API** (`https://api.openaq.org/v3`) — used only to discover which sensor locations are active in each city's bounding box (a free API key, available at <https://explore.openaq.org/register>, is needed for this discovery step).
- **NOAA HRRR** on the AWS Open Data bucket (`s3://noaa-hrrr-bdp-pds/`) — the keyless public mirror of the High-Resolution Rapid Refresh model, source of the meteorology.

The pipeline is six real scripts, each doing exactly one job:

| Script | What it does |
|---|---|
| [`scripts/pull.py`](./scripts/pull.py) | Discovers active AirNow + AirGradient monitors in the TOR/NYC/CHI bounding boxes via the OpenAQ v3 API — every location measuring PM2.5, PM10, NO2, SO2, CO, or O3 — then downloads every daily CSV from the OpenAQ S3 archive into `data/hourly/`. Idempotent — skips files already present at the same size — and rebuilds `manifest.csv` from what is on disk. |
| [`scripts/pull_hrrr.py`](./scripts/pull_hrrr.py) | Downloads NOAA HRRR surface analysis (GRIB byte-range subset) and extracts boundary-layer height, 10 m wind, 2 m temperature/humidity, and surface pressure at every monitor location into `data/met/`. Idempotent; takes a `--start/--end` window for backfilling. |
| [`scripts/build_dataset.py`](./scripts/build_dataset.py) | Joins air quality with meteorology and writes the two ML-ready Parquet tables per city — `{CITY}.parquet` (per-sensor long) and `{CITY}_hourly.parquet` (city-hour wide) — into `data/parquet/`. |
| [`scripts/consolidate.py`](./scripts/consolidate.py) | (Legacy) Reads all daily gzipped CSVs and writes one typed, datetime-sorted Parquet file per city per provider into `data/parquet/`. Kept for backward compatibility. |
| [`scripts/correlate.py`](./scripts/correlate.py) | Fits per-city OLS regressions between AirGradient and AirNow hourly medians and writes the coefficients, Pearson r, RMSE, and per-concentration-bucket residuals to `harmonization/ag-vs-airnow.json`. |
| [`scripts/load.py`](./scripts/load.py) | Convenience loader: one-line `pd.read_parquet` of an ML-ready table plus a coverage summary. |

To re-derive the whole dataset from scratch:

```bash
export OPENAQ_API_KEY=...          # free at https://explore.openaq.org/register
python3 scripts/pull.py            # rebuilds data/hourly/ + manifest.csv from public sources
python3 scripts/pull_hrrr.py       # pulls HRRR meteorology into data/met/ (see note below)
python3 scripts/build_dataset.py   # builds ML-ready data/parquet/{CITY}*.parquet tables
python3 scripts/consolidate.py     # (legacy) per-provider parquet files
python3 scripts/correlate.py       # rebuilds harmonization/ag-vs-airnow.json
```

Because the upstream archives are public and the scripts are short, anyone can independently reproduce this dataset and confirm it was not altered or invented.

> **Note on meteorology coverage.** `pull_hrrr.py` defaults to the most recent
> 7 days — a fast, verifiable sample. The committed `data/met/` is that sample.
> The full 2018→present HRRR backfill is hundreds of GB of downloads and runs
> for many hours; run `python3 scripts/pull_hrrr.py --start 2018-01-01 --end <today>`
> to build it. The meteorology columns in the ML-ready tables are populated
> wherever `data/met/` has coverage and left null elsewhere.

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

`manifest.csv` lists every monitor with its coordinates, the pollutants it
measures, and its date range.

The two air-quality source types are intentionally kept separate. AirNow is regulatory ground truth (sparse but trustworthy). AirGradient is the dense low-cost network. Don't blindly average them — they're calibrated differently and diverge non-linearly above ~10 μg/m³. Use `scripts/correlate.py` to fit your own per-city harmonization if your model needs them on the same scale.

## Layout

```
data/hourly/{city}/{provider}/{locationid}/{YYYYMMDD}.csv.gz   air quality (raw)
data/met/{city}/{YYYYMMDD}.csv.gz                              meteorology (raw)
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

[`scripts/build_dataset.py`](./scripts/build_dataset.py) joins air quality with
meteorology into two committed Parquet tables per city. **These are what you
train on.**

**`data/parquet/{CITY}_hourly.parquet` — city-hour wide.** One row per hour:
city-median `pm25, pm10, no2, so2, co, o3` and city-mean meteorology. The
ready-to-go baseline table for LightGBM / LSTM forecasters.

```python
import pandas as pd
df = pd.read_parquet('data/parquet/CHI_hourly.parquet')
# columns: datetime, city, pm25, pm10, no2, so2, co, o3,
#          met_pblh, met_wind_speed, met_wind_dir, met_temp, met_rh, met_pressure
```

**`data/parquet/{CITY}.parquet` — per-sensor long.** One row per (sensor, hour):
a single pollutant reading plus the meteorology at that sensor's location. The
source-of-truth table for spatial / graph models, where each monitor is a node.

```python
df = pd.read_parquet('data/parquet/NYC.parquet')
# columns: datetime, city, location_id, sensors_id, location, provider,
#          lat, lon, parameter, units, value, met_pblh, met_wind_speed,
#          met_wind_dir, met_temp, met_rh, met_pressure
```

[`scripts/load.py`](./scripts/load.py) loads either and prints a summary:
`python3 scripts/load.py CHI hourly` or `python3 scripts/load.py NYC long`.
Wind direction is meteorological — the compass bearing the wind blows *from*.

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
7. **Meteorology coverage is partial** — `data/met/` ships a recent HRRR sample (see the note in *Reproducible*). The `met_*` columns are null outside that window until you run the full backfill. Check coverage with `python3 scripts/load.py <CITY> hourly` before relying on them.
8. **Co-pollutant monitors are separate locations** — each OpenAQ location measures one pollutant, so NO2/O3/etc. come from different sites than PM2.5. The per-sensor table keeps them as distinct rows; the city-hour table aggregates each pollutant across that city's monitors. There is no single co-located multi-pollutant station.

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

> Hourly PM2.5 dataset for Toronto, New York City, and Chicago. Sourced via OpenAQ. Original providers: U.S. EPA AirNow, Environment Canada, AirGradient.
