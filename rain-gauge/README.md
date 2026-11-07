# Rain gauge

`index.html` is a single self-contained page: three animated rain gauges for this year, this month and this week (Sunday to Saturday), each filling up to the current total when the page opens, when you come back to the tab, or when you click a gauge or **Refill**.

Each gauge marks:
- **Average for the whole period** (dashed orange): the average year, the average for this month, the average for this week of the year.
- **Normal by this date** (solid green): the average total for the same days so far in past years. The percentage under the gauge compares against this.

## Two looks

- `index.html`: plain, clean glass gauges.
- `cartoon.html`: flat cartoon-explainer style with thick outlines, a rain cloud that drips into each gauge, a bouncy fill, and tags that pop in. Same data and controls.

## Data

Free NOAA station records through the ACIS service (`data.rcc-acis.org`), fetched live by the browser; no key needed. Results are kept for 3 hours, and the last download is shown if the internet is down.

- This year's totals: pick a station in the menu. Default is the CoCoRaHS gauge **Wenatchee 1.5 WSW** (US1WACH0051, 1,050 ft), the closest reporting gauge toward Squilchuck Rd. Days it skips are filled from Wenatchee 1.0 WSW, then Wenatchee town, then Pangborn.
- Averages: Wenatchee town (USC00459074), 1996–2025, with gaps filled from Pangborn airport (USW00094239). The CoCoRaHS gauges only go back to 2008 and 2023.

The Weather Underground station Pitcher Canyon (KWAWENAT11) is not used: its data is only available through a Weather Underground API key, which is issued to people who own a station.

## Pitcher Canyon alongside

Tick **Pitcher Canyon alongside** to show a second tube in each card for Pitcher Canyon (KWAWENAT11, about 1,800 ft, the closest station to the garden), on the same scale. Weather Underground only gives its data out as exports, so it's bundled:

1. Export months from https://www.wunderground.com/dashboard/pws/KWAWENAT11 (History > Monthly > Export) into `pitcher-canyon-exports/`.
2. Run `python3 build_pitcher.py` (or ask Claude), which rewrites `pitcher-canyon.js`.

Days missing from the exports, and days after the last export, use the internet station picked in the menu. Keep `pitcher-canyon.js` in the same folder as the pages.

## On claude.ai (desktop and iPhone)

`artifact.html` is the cartoon page published as a Claude artifact: https://claude.ai/artifact/X1vWGNtjJXTxaDoeTH8euP

Artifact pages can't reach NOAA themselves, so `build_artifact.py` embeds the 1996–2025 history, and the page reads two documents from its database: `rain/current` (this year, written every morning by a scheduled task running `refresh_artifact_data.py`) and `rain/pitcher` (the Pitcher Canyon bundle; Claude updates it after new exports).

## Open it

Double-click `index.html` or `cartoon.html` (`Rain gauge.html` / `Rain gauge (cartoon).html` in the Mac folder). It opens in the default browser.
