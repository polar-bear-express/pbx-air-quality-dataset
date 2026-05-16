# Air Quality Dataset — TOR / NYC / CHI

Hourly PM2.5 readings for **Toronto, New York City, and Chicago**, aggregated from public sources via [OpenAQ](https://openaq.org/).

Built for training short-horizon air-quality prediction models. Released under CC-BY-4.0; see [ATTRIBUTION.md](./ATTRIBUTION.md) for required upstream credits.

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

## Quick start

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
