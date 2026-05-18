# Air Quality Dataset — TOR / NYC / CHI

Hourly PM2.5 readings for **Toronto, New York City, and Chicago**, aggregated from public sources via [OpenAQ](https://openaq.org/).

Built for training short-horizon air-quality prediction models. Released under CC-BY-4.0; see [ATTRIBUTION.md](./ATTRIBUTION.md) for required upstream credits.

Maintained by Polar Bear Express.

## Reproducible — verify it yourself

This is the trust anchor for the dataset: **nothing here is hand-curated or fabricated.** Every reading is rebuilt from public, independently accessible sources by the scripts checked into this repo. You can re-run them yourself and confirm the output matches, byte for byte, what is committed.

The raw measurements come from two public archives:

- **OpenAQ S3 archive** (`https://openaq-data-archive.s3.amazonaws.com/`) — the keyless public mirror that hosts the daily PM2.5 CSVs.
- **OpenAQ v3 API** (`https://api.openaq.org/v3`) — used only to discover which sensor locations are active in each city's bounding box (a free API key, available at <https://explore.openaq.org/register>, is needed for this discovery step).

The pipeline is four real scripts, each doing exactly one job:

| Script | What it does |
|---|---|
| [`scripts/pull.py`](./scripts/pull.py) | Discovers active AirNow + AirGradient sensors in the TOR/NYC/CHI bounding boxes via the OpenAQ v3 API, then downloads every daily PM2.5 CSV from the OpenAQ S3 archive into `data/hourly/`. Idempotent — skips files already present at the same size — and rebuilds `manifest.csv` from what is on disk. |
| [`scripts/consolidate.py`](./scripts/consolidate.py) | Reads all ~60k daily gzipped CSVs and writes one typed, datetime-sorted, snappy-compressed Parquet file per city per provider into `data/parquet/`. |
| [`scripts/correlate.py`](./scripts/correlate.py) | Fits per-city OLS regressions between AirGradient and AirNow hourly medians and writes the coefficients, Pearson r, RMSE, and per-concentration-bucket residuals to `harmonization/ag-vs-airnow.json`. |
| [`scripts/load.py`](./scripts/load.py) | Convenience loader: one-line `pd.read_parquet` of a city/provider slice plus a baseline hourly aggregation example. |

To re-derive the whole dataset from scratch:

```bash
export OPENAQ_API_KEY=...        # free at https://explore.openaq.org/register
python3 scripts/pull.py          # rebuilds data/hourly/ + manifest.csv from public sources
python3 scripts/consolidate.py   # rebuilds data/parquet/ from data/hourly/
python3 scripts/correlate.py     # rebuilds harmonization/ag-vs-airnow.json
```

Because the upstream archives are public and the scripts are short and dependency-light, anyone can independently reproduce this dataset and confirm it was not altered or invented.

## What's in here

| Source | Hardware | Sensors (TOR/NYC/CHI) | Earliest data |
|---|---|---|---|
| **AirNow** (U.S. EPA + Environment Canada) | regulatory BAM, FRM, FEM | 4 / 14 / 9 | Jan 2018 (US) / Apr 2020 (CA) |
| **AirGradient** | Plantower PMS5003 (low-cost optical) | 11 / 18 / 14 | Nov 2023 |

**Total**: 70 active sensors, 60,435 daily CSV files, ~236 MB uncompressed, hourly PM2.5 (μg/m³).

The two source types are intentionally kept separate. AirNow is regulatory ground truth (sparse but trustworthy). AirGradient is the dense low-cost network. Don't blindly average them — they're calibrated differently and diverge non-linearly above ~10 μg/m³. Use `scripts/correlate.py` to fit your own per-city harmonization if your model needs them on the same scale.

## Layout

```
data/hourly/{city}/{provider}/{locationid}/{YYYYMMDD}.csv.gz
```

Each daily CSV follows the OpenAQ schema:

```
location_id,sensors_id,location,datetime,lat,lon,parameter,units,value
```

`datetime` is ISO-8601 with the sensor's local timezone offset. `parameter` is always `pm25`. `value` is μg/m³.

## Quick start (Parquet) — recommended

The raw data is ~60k tiny gzipped daily CSVs, which is slow to load. For
convenience, [`scripts/consolidate.py`](./scripts/consolidate.py) packs them
into one Parquet file per city per provider under `data/parquet/`. These six
files are committed to the repo (~18 MB total), so you can load instantly:

```python
import pandas as pd

# One-line load of a full city/provider slice (typed, datetime in UTC)
df = pd.read_parquet('data/parquet/NYC_airnow.parquet')

# Baseline: hourly city-wide median PM2.5 across all sensors
hourly = (df.set_index('datetime')
            .groupby(pd.Grouper(freq='1h'))['value']
            .median())
```

Available files: `TOR_airnow.parquet`, `TOR_airgradient.parquet`,
`NYC_airnow.parquet`, `NYC_airgradient.parquet`, `CHI_airnow.parquet`,
`CHI_airgradient.parquet`.

[`scripts/load.py`](./scripts/load.py) wraps this load and prints a baseline
aggregation: `python3 scripts/load.py CHI airgradient`. If the Parquet files
are missing locally, regenerate them with `python3 scripts/consolidate.py`
(requires `pandas` and `pyarrow`).

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

`manifest.csv` indexes every (city, provider, locationid) tuple with coordinates, sensor name, day count, and date range:

```
city,provider,locationid,name,lat,lng,n_days,first_day,last_day
NYC,AirNow,857,Fort Lee Near Road,40.85194,-73.96528,2961,20180102,20260515
```

## Harmonizing AirGradient ↔ AirNow

The two networks measure the same air but report different μg/m³. AirNow uses regulatory-grade Beta Attenuation Monitors (BAM) that physically weigh dried particles. AirGradient uses optical particle counters (Plantower PMS5003) that estimate mass from laser-scatter and apply firmware corrections. They diverge in predictable ways:

- **Low PM (<5 μg/m³)**: AirGradient under-reads by 1–2 μg/m³ (optical noise floor)
- **High humidity (RH>80%)**: AirGradient over-reads (water droplets scatter laser light)
- **Elevated PM (>10 μg/m³)**: divergence grows non-linearly; magnitude varies by site

Run `python scripts/correlate.py` to compute fresh per-city AG↔AirNow regression coefficients on this dataset. Results land in `harmonization/ag-vs-airnow.json`.

## Refreshing

```bash
export OPENAQ_API_KEY=...      # free at https://explore.openaq.org/register
python3 scripts/pull.py        # idempotent — only fetches new daily files
```

Re-run as needed. The script skips files already present at the same size.

## Caveats for ML training

1. **TOR AG↔AirNow correlation is weak** (r≈0.47, vs r≈0.85–0.91 for NYC/CHI). The Toronto AirGradient sensors and AirNow stations are placed in geographically distinct parts of the city, so they're measuring different microenvironments. Don't expect a single TOR harmonization to fit well — consider per-station fits, or include neighborhood as a feature.
2. **AirNow density is low for cross-validation** — TOR has only 4 stations. Geographic-holdout CV gets thin there; fall back to time-slice CV or pool with AirGradient.
3. **Local timezone offsets in `datetime`** — always normalize to UTC before joining or aggregating.
4. **Sparse offline windows** — sensors drop out for days/weeks at a time. Forward-fill carefully; better to drop than fabricate.
5. **Occasional sensor malfunctions** (e.g., AirGradient values >500 μg/m³ in clean air) — `scripts/correlate.py` filters >500 μg/m³ by default; you may want stricter limits.
6. **No row-level contributor attribution** — OpenAQ strips AirGradient's `publicContributorName` field in the S3 export. Network-level attribution to AirGradient is the best we can preserve.

## License & attribution

Aggregated dataset: **CC-BY-4.0**. Upstream sources retain their own terms — see [ATTRIBUTION.md](./ATTRIBUTION.md).

If you publish work using this dataset, please cite:

> Hourly PM2.5 dataset for Toronto, New York City, and Chicago. Sourced via OpenAQ. Original providers: U.S. EPA AirNow, Environment Canada, AirGradient.
