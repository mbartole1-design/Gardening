#!/usr/bin/env python3
"""Fetch this year's daily rain for the gauge stations from NOAA ACIS and write the
JSON document the rain gauge artifact reads (database doc rain/current).

  python3 refresh_artifact_data.py out.json
"""
import json, sys, urllib.request, datetime as dt

STATIONS = ["US1WACH0051", "US1WACH0013", "USC00459074", "USW00094239"]
today = dt.date.today()
start = f"{today.year - 1}-12-20"
out = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "start": start, "stations": {}}
for sid in STATIONS:
    req = urllib.request.Request("https://data.rcc-acis.org/StnData", method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"sid": sid, "sdate": start, "edate": today.isoformat(), "elems": [{"name": "pcpn"}]}).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        rows = json.load(r).get("data", [])
    # one value per day from `start`, comma-separated, ACIS codes kept (M missing, T trace)
    out["stations"][sid] = ",".join(str(v) for _, v in rows)
json.dump(out, open(sys.argv[1], "w"))
print({k: len(v.split(",")) for k, v in out["stations"].items()}, out["updated"])
