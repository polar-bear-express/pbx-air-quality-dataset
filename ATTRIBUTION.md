# Data Attribution

This dataset is aggregated from multiple sources via [OpenAQ](https://openaq.org/), each under its own license.

## Required attribution

When using this dataset, the following attribution must appear in any derived product, publication, or distribution:

> Data provided by **AirGradient** (https://www.airgradient.com/) via **OpenAQ** (https://openaq.org/), licensed under Creative Commons Attribution 4.0 International (CC-BY 4.0) — https://creativecommons.org/licenses/by/4.0/
>
> Regulatory data: **U.S. EPA AirNow** (https://www.airnow.gov/) — U.S. federal public domain. **Environment Canada** (https://weather.gc.ca/) — Open Government Licence Canada 2.0 — https://open.canada.ca/en/open-government-licence-canada

## Sources

### U.S. EPA AirNow
- **Coverage**: NYC + CHI regulatory monitors (Jan 2018 → present)
- **License**: U.S. federal **public domain** (works of the U.S. Government carry no copyright)
- **Attribution**: Not legally required; courtesy citation appreciated
- **Hardware**: BAM-1020 (Beta Attenuation Monitor), Teledyne T640 (FEM), and other Federal Reference / Equivalent Method instruments
- More info: https://www.airnow.gov/

### Environment Canada
- **Coverage**: 4 Toronto regulatory monitors (Apr 2020 → present)
- **License**: [Open Government Licence – Canada 2.0](https://open.canada.ca/en/open-government-licence-canada)
- **Attribution**: Required
- More info: https://weather.gc.ca/

### AirGradient
- **Coverage**: 43 outdoor low-cost monitors across TOR/NYC/CHI (Nov 2023 → present)
- **License**: [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/) when distributed via OpenAQ
- **Attribution**: Required ("Data provided by AirGradient via OpenAQ")
- **Hardware**: Plantower PMS5003-series optical particle counter, AirGradient O-1PP outdoor model
- **Note**: Data accessed directly via AirGradient's own API or Map is licensed CC-BY-SA 4.0 (more restrictive). This dataset was pulled via OpenAQ's S3 archive, so the lighter CC-BY 4.0 terms apply.
- More info: https://www.airgradient.com/

### OpenAQ (aggregator + delivery)
- **Role**: Mirrors all upstream providers and exposes them through a unified API + S3 archive
- **License**: [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/) for aggregated content
- **Attribution**: Required
- More info: https://openaq.org/, https://docs.openaq.org/

## License of the aggregation

The arrangement, filtering, and packaging of this dataset is licensed under **CC-BY 4.0**. Individual data points remain subject to the source-specific licenses above.

## What's allowed

- ✅ **Redistribution** with the attribution above
- ✅ **ML model training** (CC-BY 4.0 does not restrict derivative works)
- ✅ **Commercial use** (all listed licenses permit commercial use)
- ✅ **Modification, filtering, subsetting** with attribution preserved
- ❌ **Building a competing aggregator service** (OpenAQ terms prohibit duplicating their core offering)

If you spot a compliance issue, open an issue on the repo.
