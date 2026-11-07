"""
Import history exported from a personal weather station (for example Weather Underground's
"Export CSV" on a station's dashboard, or a WeatherCat/Davis export), and use the overlap with a
long-record NOAA station to estimate the long-term climate at the personal station's site.

The reader is deliberately forgiving: it looks for a date or time column and either
high/low temperature columns (daily summaries) or a single temperature column (individual
observations, which it rolls up into daily highs and lows).
"""

import csv
import datetime as dt
import glob
import os
import re
import statistics

DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d",
    "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%m/%d/%y %H:%M", "%m/%d/%y",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y",
]


def _parse_dt(s, year=None):
    s = s.strip().strip('"')
    if year and re.fullmatch(r"\d{1,2}/\d{1,2}", s):  # Weather Underground monthly export: "7/14"
        s = f"{s}/{year}"
    for f in DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def _num(s):
    m = re.search(r"-?\d+(\.\d+)?", s or "")
    return float(m.group()) if m else None


def _temp_f(s, header_is_c=False):
    """Temperature in °F from a cell like '28.8°C', '83.8 °F' or '72'."""
    v = _num(s)
    if v is None:
        return None
    cell = (s or "").upper()
    if "°C" in cell or cell.rstrip().endswith("C") or (header_is_c and "F" not in cell):
        return v * 1.8 + 32
    return v


def _is_celsius(header):
    return "°c" in header.lower() or "(c)" in header.lower() or header.lower().endswith(" c")


def _find(headers, *must, avoid=()):
    for i, h in enumerate(headers):
        hl = h.lower()
        if all(m in hl for m in must) and not any(a in hl for a in avoid):
            return i
    return None


def read_pws(folder):
    """Return {date: {"tmax", "tmin", "pcpn"}} from every .csv in folder.

    Weather Underground's monthly export ("weather-history-YYYY-MM-01.csv") has one row per day with a
    single Temperature column that holds the day's HIGH; the year comes from the file name. Those
    days get tmax only (tmin None).
    """
    daily = {}
    obs = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.csv"))):
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        if not rows:
            continue
        # header = first row containing a date/time-ish column name
        hi = next((i for i, r in enumerate(rows[:10])
                   if any(("date" in c.lower() or "time" in c.lower()) for c in r)), 0)
        headers = [c.strip() for c in rows[hi]]
        di = _find(headers, "date") if _find(headers, "date") is not None else _find(headers, "time")
        hi_i = (_find(headers, "temp", "high") or _find(headers, "temp", "max") or _find(headers, "high", avoid=("dew", "wind", "gust", "humid", "pressure")))
        lo_i = (_find(headers, "temp", "low") or _find(headers, "temp", "min") or _find(headers, "low", avoid=("dew", "wind", "gust", "humid", "pressure")))
        t_i = _find(headers, "temp", avoid=("dew", "high", "low", "max", "min", "avg", "feels", "heat", "chill", "soil", "indoor", "inside"))
        p_i = (_find(headers, "precip", "accum") or _find(headers, "precip", "total") or _find(headers, "rain", "day")
               or _find(headers, "precip", avoid=("rate",)))
        ym = re.search(r"(\d{4})-\d{2}", os.path.basename(path))
        file_year = int(ym.group(1)) if ym else None
        if di is None:
            print(f"  skipped {os.path.basename(path)}: no date column", flush=True)
            continue
        daily_mode = hi_i is not None and lo_i is not None
        for r in rows[hi + 1:]:
            if len(r) <= di:
                continue
            ts = _parse_dt(r[di], file_year)
            if ts is None:
                continue
            if daily_mode:
                hc = _is_celsius(headers[hi_i])
                tmax, tmin = _temp_f(r[hi_i], hc), _temp_f(r[lo_i], hc)
                if tmax is None or tmin is None:
                    continue
                pr = _num(r[p_i]) if p_i is not None and p_i < len(r) else None
                if pr is not None and "mm" in r[p_i].lower():
                    pr /= 25.4
                daily[ts.date()] = {"tmax": tmax, "tmin": tmin, "pcpn": pr}
            elif t_i is not None and t_i < len(r):
                t = _temp_f(r[t_i], _is_celsius(headers[t_i]))
                if t is not None:
                    o = obs.setdefault(ts.date(), {"t": [], "p": None, "times": set()})
                    o["times"].add(ts.time())
                    o["t"].append(t)
                    if p_i is not None and p_i < len(r):
                        p = _num(r[p_i])
                        if p is not None:
                            if "mm" in r[p_i].lower():
                                p /= 25.4
                            o["p"] = max(o["p"] or 0, p)  # daily accumulated rain column
    one_per_day = obs and all(len(o["t"]) == 1 and o["times"] == {dt.time()} for o in obs.values())
    for d, o in obs.items():
        if d in daily:
            continue
        if one_per_day:  # daily summary with one temperature: Weather Underground's daily high
            daily[d] = {"tmax": o["t"][0], "tmin": None, "pcpn": o["p"], "high_only": True}
        elif len(o["t"]) >= 12:  # individual observations with decent coverage of the day
            daily[d] = {"tmax": max(o["t"]), "tmin": min(o["t"]), "pcpn": o["p"]}
    return daily


def read_monthly_summary(folder):
    """Optional monthly_summary.csv in the folder: year,month,avg_low_f[,avg_high_f].

    Used when daily exports lack lows (Weather Underground's monthly export): the station's monthly
    average low, read off its monthly summary, still gives a per-month night-time offset.
    """
    path = os.path.join(folder, "monthly_summary.csv")
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                key = (int(r["year"]), int(r["month"]))
            except (KeyError, ValueError):
                continue
            lo = _temp_f(r.get("avg_low_f", ""))
            hi = _temp_f(r.get("avg_high_f", ""))
            out[key] = {"lo": lo, "hi": hi}
    return out


def monthly_offsets(pws, base_rows, min_days=10, summary=None):
    """Average (personal station - base station) difference in highs and lows, by month,
    plus the ratio of the two stations' rain over the days both reported."""
    base = {r["date"]: r for r in base_rows}
    diffs = {m: {"hi": [], "lo": []} for m in range(1, 13)}
    rain_p = rain_b = 0.0
    for d, p in pws.items():
        b = base.get(d)
        if not b:
            continue
        if p["tmax"] is not None and b["tmax"] is not None and abs(p["tmax"] - b["tmax"]) <= 25:
            diffs[d.month]["hi"].append(p["tmax"] - b["tmax"])
        if p.get("tmin") is not None and b["tmin"] is not None and abs(p["tmin"] - b["tmin"]) <= 25:
            diffs[d.month]["lo"].append(p["tmin"] - b["tmin"])
        if p.get("pcpn") is not None and b["pcpn"] is not None:
            rain_p += p["pcpn"]
            rain_b += b["pcpn"]
    # monthly average lows from the station's summary, compared with the base station's same month
    if summary:
        for (y, m), v in summary.items():
            if v.get("lo") is None or diffs[m]["lo"]:
                continue
            mins = [r["tmin"] for r in base_rows if r["date"].year == y and r["date"].month == m and r["tmin"] is not None]
            if len(mins) >= 20:
                diffs[m]["lo"] = [v["lo"] - statistics.mean(mins)] * min_days  # weight as a full month
    out = {}
    for m, v in diffs.items():
        out[m] = {"n_hi": len(v["hi"]), "n_lo": len(v["lo"]), "n": max(len(v["hi"]), len(v["lo"])),
                  "hi": statistics.mean(v["hi"]) if len(v["hi"]) >= min_days else None,
                  "lo": statistics.mean(v["lo"]) if len(v["lo"]) >= min_days else None}
    for k in ("hi", "lo"):
        have = [m for m in out if out[m][k] is not None]
        for m in out:
            if out[m][k] is None and have:
                near = min(have, key=lambda h: min(abs(h - m), 12 - abs(h - m)))
                out[m][k + "_fill"] = out[near][k]
    out["rain_ratio"] = rain_p / rain_b if rain_b > 2 else None
    return out


def adjust_rows(base_rows, offsets):
    """Long-record series shifted by the personal station's monthly offsets, rain scaled by the rain ratio."""
    out = []
    ratio = offsets.get("rain_ratio") or 1.0
    for r in base_rows:
        o = offsets.get(r["date"].month, {})
        dh = o.get("hi") if o.get("hi") is not None else o.get("hi_fill", 0) or 0
        dl = o.get("lo") if o.get("lo") is not None else o.get("lo_fill", 0) or 0
        out.append({**r,
                    "tmax": None if r["tmax"] is None else r["tmax"] + dh,
                    "tmin": None if r["tmin"] is None else r["tmin"] + dl,
                    "pcpn": None if r["pcpn"] is None else r["pcpn"] * ratio})
    return out
