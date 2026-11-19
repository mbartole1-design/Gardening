#!/usr/bin/env python3
"""Fetch this year's daily rain and snow for the gauge stations from NOAA ACIS and write the
JSON document the rain gauge artifact reads (database doc rain/current).

  python3 refresh_artifact_data.py out.json
"""
import json, sys, urllib.request, datetime as dt

STATIONS = ["US1WACH0051", "US1WACH0013", "USC00459074", "USW00094239"]
today = dt.date.today()
start = f"{today.year - 1}-06-20"  # covers this calendar year and this snow season
out = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "start": start, "stations": {}, "snow": {}, "snwd": {}}
for sid in STATIONS:
    req = urllib.request.Request("https://data.rcc-acis.org/StnData", method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"sid": sid, "sdate": start, "edate": today.isoformat(), "elems": [{"name": "pcpn"}, {"name": "snow"}, {"name": "snwd"}]}).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        rows = json.load(r).get("data", [])
    # one value per day from `start`, comma-separated, ACIS codes kept (M missing, T trace)
    out["stations"][sid] = ",".join(str(r[1]) for r in rows)  # rain + melted snow
    out["snow"][sid] = ",".join(str(r[2]) for r in rows)      # new snowfall
    out["snwd"][sid] = ",".join(str(r[3]) for r in rows)      # snow depth
json.dump(out, open(sys.argv[1], "w"))
print({k: len(v.split(",")) for k, v in out["stations"].items()}, out["updated"])
