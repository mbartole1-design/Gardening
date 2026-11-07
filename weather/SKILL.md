---
name: garden-weather
description: Fetch historical weather and wind for Wenatchee WA (or any US station) and analyze frost dates, heat, wind, rain, irrigation need, planting windows and fruit-tree chill for garden planning.
---

# Garden weather

Use this when the user asks about frost dates, heat, wind, rainfall, chill hours for fruit trees, watering needs, growing degree days,
planting windows, or "what will the weather be like" for a date or period in their garden.

The garden is at about 1,800 ft up Squilchuck Road above Wenatchee, WA (Zone 7a-7b, about 9 in of rain a year,
very dry hot summers). The nearest station is Pitcher Canyon (Weather Underground KWAWENAT11, 1,440 ft). If CSV
exports from it are in `pws/Pitcher Canyon/`, always run with `--pws "pws/Pitcher Canyon"`.
Fruit trees and their chill needs and cold limits are listed in `fruit_trees.json`.

## Steps

1. Run the analyzer bundled with this skill:
   `python3 garden_weather.py [--window MM-DD MM-DD] [--station ID] [--years N] --json`
   - Default station `USC00459074` (Wenatchee, in town). `USW00094239` is Pangborn airport.
   - Use `--pws FOLDER` to re-base on a personal station's exported history.
   - Use `--window` for any period the user asks about (a trip, a month, a planting window).
   - It needs network access to `data.rcc-acis.org` (temperature, rain) and `www.ncei.noaa.gov`
     (airport wind). Without wind it falls back to a temperature-only water estimate.
2. Read the Markdown report it writes in `reports/` (and the JSON for exact numbers).
3. Answer the question directly with the relevant numbers. Frame anything about the future as odds based on
   the last 30 years, not a forecast; day-to-day forecasts beyond about two weeks are not possible.
4. Turn the numbers into garden advice: transplant dates (frost odds plus the 60°F warm-season date),
   lettuce windows (the 80°F-high dates), heat-stress periods for fruit set, irrigation in gallons
   per 100 sq ft (1 in over 100 sq ft = 62.3 gal), and wind: which months and directions to shelter
   against and how much a windbreak would save. For fruit trees, compare each winter's chill with
   each tree's need, and flag winter cold below its limits and bloom-time freezes.
