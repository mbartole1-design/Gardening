#!/usr/bin/env python3
"""Bundle Weather Underground exports for Pitcher Canyon (KWAWENAT11) into pitcher-canyon.js.

Put the monthly CSV exports ("weather-history-YYYY-MM-01.csv", from
https://www.wunderground.com/dashboard/pws/KWAWENAT11 > History > Monthly > Export) in
pitcher-canyon-exports/, then run:  python3 build_pitcher.py

The gauge pages load pitcher-canyon.js with a plain <script> tag, which works when the
page is opened straight from disk (a browser won't let a local page read a CSV itself).
"""
import csv, glob, json, os, re, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "pitcher-canyon-exports")
OUT = os.path.join(HERE, "pitcher-canyon.js")


def inches(cell):
    m = re.match(r"\s*([-\d.]+)\s*(mm|in)?", cell or "")
    if not m:
        return None
    v = float(m.group(1))
    return v / 25.4 if (m.group(2) or "mm") == "mm" else v


def main():
    days = {}
    for path in sorted(glob.glob(os.path.join(SRC, "*.csv"))):
        ym = re.search(r"(\d{4})-(\d{2})", os.path.basename(path))
        if not ym:
            continue
        year = int(ym.group(1))
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        head = [h.strip().lower() for h in rows[0]]
        pi = next(i for i, h in enumerate(head) if "precip" in h and "accum" in h)
        for r in rows[1:]:
            if len(r) <= pi:
                continue
            parts = r[0].strip().split("/")
            try:
                mo, d = int(parts[0]), int(parts[1])
                y = int(parts[2]) if len(parts) > 2 else year
                y = y + 2000 if y < 100 else y
                date = dt.date(y, mo, d)
            except (ValueError, IndexError):
                continue
            v = inches(r[pi])
            if v is not None:
                days[date.isoformat()] = round(v, 3)
    days = dict(sorted(days.items()))
    data = {
        "sid": "KWAWENAT11", "name": "Pitcher Canyon", "elev": "about 1,800 ft",
        "source": "Weather Underground exports", "first": next(iter(days)), "last": next(reversed(days)),
        "built": dt.date.today().isoformat(), "days": days,
    }
    with open(OUT, "w") as f:
        f.write("// Built by build_pitcher.py from Weather Underground exports. Daily rain in inches.\n")
        f.write("window.PITCHER_CANYON = " + json.dumps(data, separators=(",", ":")) + ";\n")
    print(f"{len(days)} days, {data['first']} to {data['last']} -> {OUT}")


if __name__ == "__main__":
    main()
