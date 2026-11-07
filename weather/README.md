# Garden weather analyzer

Pulls the full daily weather history for a weather station (default: **Wenatchee, in town, records since 1931**)
and turns it into numbers you can plan a garden with:

- Frost-date odds at 36°F, 32°F and 28°F (typical date, and the 1-in-10 "late" or "early" date), plus how they have shifted since 1931
- Month-by-month highs, lows, rain, 90°F/100°F days, frost nights and growing degree days
- **Wind** by month: average speed, peak gusts, windy days, and the direction strong winds come from
- Water loss (evapotranspiration) and irrigation need per month, in inches and **gallons per 100 sq ft**, with wind built in,
  plus how much of it is caused by wind and how much a windbreak would save
- Heat stress: hot days, warm nights that hurt fruit set, first/last 90°F day, heat streaks
- Planting windows: when warm-season crops can go out, when lettuce stops and when it can start again
- **Fruit-tree chill** for every winter since 1931: chill hours, Utah chill units and Dynamic-model chill portions,
  the date each of your trees (in `fruit_trees.json`) has its chill need met, the coldest winter night against each
  tree's cold limits, and bloom-time freezes. Every winter goes to `reports/<station>_chill_by_winter.csv`
- **Your own site**: import history from a nearby personal weather station (e.g. Pitcher Canyon, KWAWENAT11)
  and the whole report is re-based to that site
- Any date window you choose (for example a trip: `--window 05-15 07-15`)
- The last ten years side by side

It is plain Python using only the standard library (`garden_weather.py` plus `chill.py`, `pws_import.py` and
`fruit_trees.json`), so there is nothing to install.

## Run it

```bash
cd weather
python3 garden_weather.py                        # writes reports/USC00459074_report.md
python3 garden_weather.py --window 05-15 07-15   # add a custom window (e.g. the 2027 Croatia trip)
python3 garden_weather.py --station USW00094239  # Pangborn airport (East Wenatchee plateau) instead
python3 garden_weather.py --years 20 --json      # 20-year climatology, plus a JSON summary
python3 garden_weather.py --no-wind              # temperature-only water estimate
python3 garden_weather.py --pws "pws/Pitcher Canyon"  # re-base everything on Pitcher Canyon exports
python3 garden_weather.py --help
```

Downloads are cached in `weather/data/` for a week; `--refresh` forces a new download.

## Using the Pitcher Canyon station (KWAWENAT11)

Weather Underground doesn't offer a free download feed for other people's stations, but its website lets you
export a station's history yourself:

1. Open https://www.wunderground.com/dashboard/pws/KWAWENAT11 and scroll to the history section.
2. Choose **Table**, then **Monthly** (or **Custom** for a longer range), and click **Export CSV**.
3. Save each file into `weather/pws/Pitcher Canyon/` (any file names). A year or more gives the best result,
   and every month of the year should be covered at least once.
4. Run `python3 garden_weather.py --pws "pws/Pitcher Canyon"`.

The tool compares the station with the long-record town station day by day, works out how much warmer or colder
Pitcher Canyon's highs and lows run in each month, and shifts the 95-year town record by that amount. Where the
station has its own readings, they're used directly. The report then shows frost dates, heat, water loss and chill
for your site, plus a table of the monthly differences. The CSV reader accepts daily summaries (high/low columns) or
individual observations (a single temperature column). Other stations work too: `--pws-name` and `--pws-elev` set
their name and elevation.

## Data sources (all free, no key needed)

- **NOAA station records** via the Regional Climate Centers' ACIS service, `data.rcc-acis.org`.
  Nearby stations: `USC00459074` Wenatchee (626 ft, 1931–now), `USW00094239` Pangborn airport (1,238 ft, 1959–now).
  Any US station id works; find one at https://scacis.rcc-acis.org or with the ACIS `StnMeta` call.
- **NOAA GHCN-Daily wind** via NCEI, `www.ncei.noaa.gov`: daily average wind, peak gust and gust direction
  for Pangborn airport (`USW00094239`, 2000–now). Water loss is computed with the FAO-56 Penman-Monteith
  equation from the station's temperatures plus this measured wind. Use `--no-wind` to skip it
  (temperature-only Hargreaves estimate) or `--wind-station` to pick another airport.
- **Open-Meteo history archive** (optional, `--open-meteo`), `archive-api.open-meteo.com`, for gridded
  FAO-56 evapotranspiration as a cross-check. It rate-limits heavily, so it is off by default.

## What it can and cannot predict

Day-to-day weather cannot be forecast more than about two weeks out. This tool gives the **odds** for any date,
based on the last 30 years here, and shows which way things are trending. For the coming season, pair it with
NOAA's seasonal outlooks (https://www.cpc.ncep.noaa.gov/products/predictions/90day/), which say whether a
season is leaning warmer, cooler, wetter or drier than these normals.

## Using it as a Claude skill

See `SKILL.md` in this folder. To add it to your Claude account, open Settings, then Capabilities (Skills),
upload this `weather` folder as a zip (it must contain `SKILL.md`, the three `.py` files and `fruit_trees.json`), and turn it on.
After that you can ask Claude things like "what are the frost odds for May 1?" in any chat and it will run the tool.
