---
name: garden-weather
description: Fetch historical daily weather and wind for Wenatchee WA (or any US station) and analyze frost dates, heat, wind, rain, irrigation need and planting windows for garden planning.
---

# Garden weather

Use this when the user asks about frost dates, heat, wind, rainfall, watering needs, growing degree days,
planting windows, or "what will the weather be like" for a date or period in their garden.

The garden is in Wenatchee, WA (Zone 7a-7b, about 9 in of rain a year, very dry hot summers).

## Steps

1. Run the analyzer bundled with this skill:
   `python3 garden_weather.py [--window MM-DD MM-DD] [--station ID] [--years N] --json`
   - Default station `USC00459074` (Wenatchee, in town). `USW00094239` is Pangborn airport.
   - Use `--window` for any period the user asks about (a trip, a month, a planting window).
   - It needs network access to `data.rcc-acis.org` (temperature, rain) and `www.ncei.noaa.gov`
     (airport wind). Without wind it falls back to a temperature-only water estimate.
2. Read the Markdown report it writes in `reports/` (and the JSON for exact numbers).
3. Answer the question directly with the relevant numbers. Frame anything about the future as odds based on
   the last 30 years, not a forecast; day-to-day forecasts beyond about two weeks are not possible.
4. Turn the numbers into garden advice: transplant dates (frost odds plus the 60°F warm-season date),
   lettuce windows (the 80°F-high dates), heat-stress periods for fruit set, irrigation in gallons
   per 100 sq ft (1 in over 100 sq ft = 62.3 gal), and wind: which months and directions to shelter
   against and how much a windbreak would save.
