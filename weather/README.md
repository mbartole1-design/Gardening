# Garden weather analyzer

Pulls the full daily weather history for a weather station (default: **Wenatchee, in town, records since 1931**)
and turns it into numbers you can plan a garden with:

- Frost-date odds at 36°F, 32°F and 28°F (typical date, and the 1-in-10 "late" or "early" date), plus how they have shifted since 1931
- Month-by-month highs, lows, rain, 90°F/100°F days, frost nights and growing degree days
- Water loss (evapotranspiration) and irrigation need per month, in inches and **gallons per 100 sq ft**
- Heat stress: hot days, warm nights that hurt fruit set, first/last 90°F day, heat streaks
- Planting windows: when warm-season crops can go out, when lettuce stops and when it can start again
- Any date window you choose (for example a trip: `--window 05-15 07-15`)
- The last ten years side by side

It is a single Python file using only the standard library, so there is nothing to install.

## Run it

```bash
cd weather
python3 garden_weather.py                        # writes reports/USC00459074_report.md
python3 garden_weather.py --window 05-15 07-15   # add a custom window (e.g. the 2027 Croatia trip)
python3 garden_weather.py --station USW00094239  # Pangborn airport (East Wenatchee plateau) instead
python3 garden_weather.py --years 20 --json      # 20-year climatology, plus a JSON summary
python3 garden_weather.py --help
```

Downloads are cached in `weather/data/` for a week; `--refresh` forces a new download.

## Data sources (both free, no key needed)

- **NOAA station records** via the Regional Climate Centers' ACIS service, `data.rcc-acis.org`.
  Nearby stations: `USC00459074` Wenatchee (626 ft, 1931–now), `USW00094239` Pangborn airport (1,238 ft, 1959–now).
  Any US station id works; find one at https://scacis.rcc-acis.org or with the ACIS `StnMeta` call.
- **Open-Meteo history archive**, `archive-api.open-meteo.com`, for FAO-56 reference evapotranspiration.
  If it is unreachable or rate-limited, the tool falls back to the Hargreaves estimate from the station's
  own temperatures, which is usually within about 10% for this climate.

## What it can and cannot predict

Day-to-day weather cannot be forecast more than about two weeks out. This tool gives the **odds** for any date,
based on the last 30 years here, and shows which way things are trending. For the coming season, pair it with
NOAA's seasonal outlooks (https://www.cpc.ncep.noaa.gov/products/predictions/90day/), which say whether a
season is leaning warmer, cooler, wetter or drier than these normals.

## Using it as a Claude skill

See `SKILL.md` in this folder. To add it to your Claude account, open Settings, then Capabilities (Skills),
upload this `weather` folder as a zip (it must contain `SKILL.md` and `garden_weather.py`), and turn it on.
After that you can ask Claude things like "what are the frost odds for May 1?" in any chat and it will run the tool.
