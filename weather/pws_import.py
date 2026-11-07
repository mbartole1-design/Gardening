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


def _parse_dt(s):
    s = s.strip().strip('"')
    for f in DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def _num(s):
    m = re.search(r"-?\d+(\.\d+)?", s or "")
    return float(m.group()) if m else None


def _is_celsius(header):
    return "°c" in header.lower() or "(c)" in header.lower() or header.lower().endswith(" c")


def _find(headers, *must, avoid=()):
    for i, h in enumerate(headers):
        hl = h.lower()
        if all(m in hl for m in must) and not any(a in hl for a in avoid):
            return i
    return None


def read_pws(folder):
    """Return {date: {"tmax", "tmin", "pcpn"}} from every .csv in folder."""
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
        if di is None:
            print(f"  skipped {os.path.basename(path)}: no date column", flush=True)
            continue
        conv_hi = (lambda v: v * 1.8 + 32) if hi_i is not None and _is_celsius(headers[hi_i]) else (lambda v: v)
        conv_t = (lambda v: v * 1.8 + 32) if t_i is not None and _is_celsius(headers[t_i]) else (lambda v: v)
        daily_mode = hi_i is not None and lo_i is not None
        for r in rows[hi + 1:]:
            if len(r) <= di:
                continue
            ts = _parse_dt(r[di])
            if ts is None:
                continue
            if daily_mode:
                tmax, tmin = _num(r[hi_i]), _num(r[lo_i])
                if tmax is None or tmin is None:
                    continue
                daily[ts.date()] = {"tmax": conv_hi(tmax), "tmin": conv_hi(tmin),
                                    "pcpn": _num(r[p_i]) if p_i is not None and p_i < len(r) else None}
            elif t_i is not None and t_i < len(r):
                t = _num(r[t_i])
                if t is not None:
                    o = obs.setdefault(ts.date(), {"t": [], "p": None})
                    o["t"].append(conv_t(t))
                    if p_i is not None and p_i < len(r):
                        p = _num(r[p_i])
                        if p is not None:
                            o["p"] = max(o["p"] or 0, p)  # daily accumulated rain column
    for d, o in obs.items():
        if len(o["t"]) >= 12 and d not in daily:  # need decent coverage of the day
            daily[d] = {"tmax": max(o["t"]), "tmin": min(o["t"]), "pcpn": o["p"]}
    return daily


def monthly_offsets(pws, base_rows, min_days=10):
    """Average (personal station - base station) difference in highs and lows, by month."""
    base = {r["date"]: r for r in base_rows}
    diffs = {m: {"hi": [], "lo": []} for m in range(1, 13)}
    for d, p in pws.items():
        b = base.get(d)
        if not b or b["tmax"] is None or b["tmin"] is None:
            continue
        if abs(p["tmax"] - b["tmax"]) > 25 or abs(p["tmin"] - b["tmin"]) > 25:
            continue  # obviously bad reading
        diffs[d.month]["hi"].append(p["tmax"] - b["tmax"])
        diffs[d.month]["lo"].append(p["tmin"] - b["tmin"])
    out = {}
    for m, v in diffs.items():
        n = len(v["lo"])
        out[m] = {"n": n,
                  "hi": statistics.mean(v["hi"]) if n >= min_days else None,
                  "lo": statistics.mean(v["lo"]) if n >= min_days else None}
    # fill months without enough overlap from the nearest months that have it
    have = [m for m in out if out[m]["lo"] is not None]
    for m in out:
        if out[m]["lo"] is None and have:
            near = min(have, key=lambda h: min(abs(h - m), 12 - abs(h - m)))
            out[m]["hi_fill"], out[m]["lo_fill"] = out[near]["hi"], out[near]["lo"]
    return out


def adjust_rows(base_rows, offsets):
    """Long-record series shifted by the personal station's monthly offsets (rain left unchanged)."""
    out = []
    for r in base_rows:
        o = offsets.get(r["date"].month, {})
        dh = o.get("hi") if o.get("hi") is not None else o.get("hi_fill", 0) or 0
        dl = o.get("lo") if o.get("lo") is not None else o.get("lo_fill", 0) or 0
        out.append({**r,
                    "tmax": None if r["tmax"] is None else r["tmax"] + dh,
                    "tmin": None if r["tmin"] is None else r["tmin"] + dl})
    return out
