"""
Winter chill for fruit trees: chill hours, Utah chill units and Dynamic-model chill portions.

Hourly temperatures are reconstructed from daily highs and lows with the standard idealized
daily curve (sine rise from sunrise to the afternoon high, logarithmic fall overnight), which lets
any station with daily records, back to 1931 here, get a chill estimate.
"""

import datetime as dt
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TREES_FILE = os.path.join(HERE, "fruit_trees.json")

SEASON_START = (10, 1)   # chill counted from Oct 1 ...
SEASON_END = (3, 31)     # ... through Mar 31


def load_trees():
    with open(TREES_FILE) as f:
        return json.load(f)["trees"]


# --------------------------------------------------------------------------- #
# Hourly reconstruction
# --------------------------------------------------------------------------- #

def _daylength(date, lat):
    j = date.timetuple().tm_yday
    decl = 0.409 * math.sin(2 * math.pi * j / 365 - 1.39)
    x = max(-1.0, min(1.0, -math.tan(math.radians(lat)) * math.tan(decl)))
    return 24 * math.acos(x) / math.pi


def hourly_from_daily(rows, lat):
    """[(datetime, temp_f)] hourly, built day by day from sunrise to the next sunrise.

    Each day rises on a sine curve from its low at sunrise to its high in mid-afternoon, then
    cools logarithmically through the night to the next day's low at the next sunrise.
    """
    out = []
    for i, r in enumerate(rows[:-1]):
        nxt = rows[i + 1]
        if r["tmax"] is None or r["tmin"] is None or nxt["tmin"] is None or r["tmax"] < r["tmin"]:
            continue
        dl = _daylength(r["date"], lat)
        sunrise, sunset = 12 - dl / 2, 12 + dl / 2
        next_sunrise = 24 + 12 - _daylength(nxt["date"], lat) / 2
        tmin, tmax = r["tmin"], r["tmax"]
        t_sunset = tmin + (tmax - tmin) * math.sin(math.pi * dl / (dl + 4))
        night = next_sunrise - sunset
        base = dt.datetime.combine(r["date"], dt.time())
        h = math.ceil(sunrise)
        while h < next_sunrise:
            if h <= sunset:
                t = tmin + (tmax - tmin) * math.sin(math.pi * (h - sunrise) / (dl + 4))
            else:
                t = t_sunset - (t_sunset - nxt["tmin"]) * math.log(h - sunset + 1) / math.log(night + 1)
            out.append((base + dt.timedelta(hours=h), t))
            h += 1
    return out


# --------------------------------------------------------------------------- #
# Chill models
# --------------------------------------------------------------------------- #

def _utah(tf):
    tc = (tf - 32) / 1.8
    if tc <= 1.4:
        return 0.0
    if tc <= 2.4:
        return 0.5
    if tc <= 9.1:
        return 1.0
    if tc <= 12.4:
        return 0.5
    if tc <= 15.9:
        return 0.0
    if tc <= 18.0:
        return -0.5
    return -1.0


class _Dynamic:
    """Fishman/Erez Dynamic model, hour by hour (as implemented in the chillR package)."""
    e0, e1, a0, a1, slp, tetmlt = 4153.5, 12888.8, 139500.0, 2.567e18, 1.6, 277.0

    def __init__(self):
        self.inter_s = 0.0

    def step(self, tf):
        tk = (tf - 32) / 1.8 + 273.0
        aa, ee = self.a0 / self.a1, self.e1 - self.e0
        ftmprt = self.slp * self.tetmlt * (tk - self.tetmlt) / tk
        sr = math.exp(ftmprt)
        xi = sr / (1 + sr)
        xs = aa * math.exp(ee / tk)
        ak1 = self.a1 * math.exp(-self.e1 / tk)
        inter_e = xs - (xs - self.inter_s) * math.exp(-ak1)
        if inter_e >= 1:
            delt = inter_e * xi
            self.inter_s = inter_e - inter_e * xi
        else:
            delt = 0.0
            self.inter_s = inter_e
        return delt


def winter_label(d):
    y = d.year if d.month >= SEASON_START[0] else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def chill_by_winter(hourly, trees):
    """Per winter: totals and the date each tree's minimum chill-hour need was met."""
    seasons = {}
    for ts, tf in hourly:
        md = (ts.month, ts.day)
        if not (md >= SEASON_START or md <= SEASON_END):
            continue
        key = winter_label(ts)
        s = seasons.get(key)
        if s is None:
            s = seasons[key] = {"ch": 0.0, "utah": 0.0, "cp": 0.0, "dyn": _Dynamic(), "hours": 0,
                                "met": {}, "ch_feb": None}
        s["hours"] += 1
        if 32 < tf <= 45:
            s["ch"] += 1
        s["utah"] = max(0.0, s["utah"] + _utah(tf))
        s["cp"] += s["dyn"].step(tf)
        if md == (3, 1) and ts.hour == 0:
            s["ch_feb"] = s["ch"]
        for t in trees:
            if t["name"] not in s["met"] and s["ch"] >= t["chill_hours_min"]:
                s["met"][t["name"]] = ts.date()
    full = 183 * 24  # Oct 1 - Mar 31
    for s in seasons.values():
        s.pop("dyn")
        s["complete"] = s["hours"] >= 0.9 * full
    return seasons


def winter_extras(rows):
    """Per winter: coldest night, and hard freezes during the bloom period."""
    out = {}
    for r in rows:
        if r["tmin"] is None:
            continue
        d = r["date"]
        if d.month >= 10 or d.month <= 4:
            k = winter_label(d) if d.month >= 10 or d.month <= 3 else winter_label(d.replace(month=3))
            e = out.setdefault(k, {"coldest": None, "bloom_freezes": 0, "last28": None})
            if d.month <= 2 or d.month >= 11:
                if e["coldest"] is None or r["tmin"] < e["coldest"]:
                    e["coldest"] = r["tmin"]
            if (3, 15) <= (d.month, d.day) <= (4, 30) and r["tmin"] <= 28:
                e["bloom_freezes"] += 1
            if d.month in (3, 4) and r["tmin"] <= 28:
                e["last28"] = d
    return out
