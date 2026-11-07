#!/usr/bin/env python3
"""
Garden weather analyzer for Wenatchee, WA (or any US weather station).

Pulls daily history from NOAA station records (via the free RCC-ACIS API) and,
optionally, reference evapotranspiration from the Open-Meteo history archive.
Then turns it into garden-planning numbers: frost-date odds, heat stress,
rainfall, irrigation need, growing degree days, planting windows and trends.

Only the Python standard library is used, so it runs anywhere with Python 3.8+.

Examples
--------
  python3 garden_weather.py                       # full report, default station
  python3 garden_weather.py --window 05-15 07-15  # add a custom date window (e.g. a trip)
  python3 garden_weather.py --station USW00094239 # Pangborn airport instead of town
  python3 garden_weather.py --years 20            # base the climatology on the last 20 years
  python3 garden_weather.py --refresh             # ignore the local cache and re-download
"""

import argparse
import csv
import datetime as dt
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import chill
import pws_import

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
REPORT_DIR = os.path.join(HERE, "reports")

DEFAULT_STATION = "USC00459074"  # WENATCHEE (in town, 626 ft, records since 1931)
DEFAULT_WIND_STATION = "USW00094239"  # WENATCHEE PANGBORN AIRPORT (daily wind since 2000)
NCEI_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
ACIS_URL = "https://data.rcc-acis.org"
OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
USER_AGENT = "garden-weather-tool/1.0"

GAL_PER_INCH_PER_100SQFT = 62.3  # 1 inch of water over 100 sq ft
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
MPH_TO_MS = 0.44704
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# --------------------------------------------------------------------------- #
# Data access
# --------------------------------------------------------------------------- #

def _http_json(url, payload=None, timeout=120):
    data = None
    headers = {"User-Agent": USER_AGENT}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def station_meta(sid):
    res = _http_json(ACIS_URL + "/StnMeta", {
        "sids": sid, "meta": "name,sids,ll,elev,valid_daterange", "elems": "maxt,mint,pcpn"})
    if not res.get("meta"):
        sys.exit(f"Station {sid} not found in ACIS.")
    m = res["meta"][0]
    return {"sid": sid, "name": m["name"], "lon": m["ll"][0], "lat": m["ll"][1],
            "elev_ft": m.get("elev"), "start": m["valid_daterange"][0][0]}


def _num(v):
    """ACIS values: number, 'M' missing, 'T' trace, '0.50A' accumulated, 'S' subsequent."""
    if v in ("M", "S", "", None):
        return None
    if v == "T":
        return 0.0
    v = v.rstrip("A")
    try:
        return float(v)
    except ValueError:
        return None


def load_station_daily(sid, refresh=False):
    """Return list of dicts {date, tmax, tmin, pcpn}, cached as CSV in data/."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{sid}_daily.csv")
    today = dt.date.today()
    if not refresh and os.path.exists(path):
        age_days = (dt.datetime.now() - dt.datetime.fromtimestamp(os.path.getmtime(path))).days
        if age_days < 7:
            return _read_daily_csv(path)
    meta = station_meta(sid)
    res = _http_json(ACIS_URL + "/StnData", {
        "sid": sid, "sdate": meta["start"], "edate": today.isoformat(),
        "elems": "maxt,mint,pcpn"})
    if "error" in res:
        sys.exit(f"ACIS error: {res['error']}")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "tmax_f", "tmin_f", "precip_in"])
        for d, tmax, tmin, p in res["data"]:
            w.writerow([d, *("" if _num(x) is None else _num(x) for x in (tmax, tmin, p))])
    return _read_daily_csv(path)


def _read_daily_csv(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append({
                "date": dt.date.fromisoformat(r["date"]),
                "tmax": float(r["tmax_f"]) if r["tmax_f"] else None,
                "tmin": float(r["tmin_f"]) if r["tmin_f"] else None,
                "pcpn": float(r["precip_in"]) if r["precip_in"] else None,
            })
    return rows


def load_et0(lat, lon, start_year, end_year, refresh=False):
    """Daily FAO-56 reference evapotranspiration (inches) from Open-Meteo. {date: et0}."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"et0_{lat:.3f}_{lon:.3f}_{start_year}_{end_year}.csv")
    if not refresh and os.path.exists(path):
        with open(path) as f:
            return {dt.date.fromisoformat(r["date"]): float(r["et0_in"])
                    for r in csv.DictReader(f) if r["et0_in"]}
    times, vals = [], []
    for y0 in range(start_year, end_year + 1, 5):  # small chunks keep under the free-tier rate limit
        y1 = min(y0 + 4, end_year)
        q = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon,
            "start_date": f"{y0}-01-01", "end_date": f"{y1}-12-31",
            "daily": "et0_fao_evapotranspiration", "precipitation_unit": "inch",
            "timezone": "America/Los_Angeles"})
        res = None
        for attempt in range(4):
            try:
                res = _http_json(OPEN_METEO_URL + "?" + q, timeout=180)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    time.sleep(15 * (attempt + 1))
                    continue
                print(f"  (Open-Meteo unavailable: {e}; falling back to Hargreaves estimate)", file=sys.stderr)
                return {}
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"  (Open-Meteo unavailable: {e}; falling back to Hargreaves estimate)", file=sys.stderr)
                return {}
        times += res["daily"]["time"]
        vals += res["daily"]["et0_fao_evapotranspiration"]
        time.sleep(2)
    out = {}
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "et0_in"])
        for d, v in zip(times, vals):
            w.writerow([d, "" if v is None else v])
            if v is not None:
                out[dt.date.fromisoformat(d)] = float(v)
    return out


def load_wind(sid, refresh=False):
    """Daily wind from NOAA GHCN-Daily (NCEI): {date: {"avg", "gust", "dir"}} in mph / degrees.

    AWND = average daily wind speed, WSF5 = fastest 5-second gust, WDF2 = direction of the
    fastest 2-minute wind. Airports (USW...) carry these; most co-op stations do not.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{sid}_wind.csv")
    fresh = os.path.exists(path) and (
        dt.datetime.now() - dt.datetime.fromtimestamp(os.path.getmtime(path))).days < 7
    if refresh or not fresh:
        q = urllib.parse.urlencode({
            "dataset": "daily-summaries", "stations": sid, "startDate": "1980-01-01",
            "endDate": dt.date.today().isoformat(), "dataTypes": "AWND,WSF2,WSF5,WDF2",
            "units": "standard", "format": "csv"})
        try:
            req = urllib.request.Request(NCEI_URL + "?" + q, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = resp.read().decode()
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  (NOAA wind data unavailable: {e}; water loss will ignore wind)", file=sys.stderr)
            if not os.path.exists(path):
                return {}
        else:
            with open(path, "w") as f:
                f.write(body)
    out = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            g = lambda k: float(r[k]) if r.get(k, "").strip() else None
            if g("AWND") is None:
                continue
            out[dt.date.fromisoformat(r["DATE"])] = {
                "avg": g("AWND"), "gust": g("WSF5") or g("WSF2"), "dir": g("WDF2")}
    return out


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def doy_to_str(doy, year=2027):
    """Day-of-year (1-based, non-leap reference) -> 'May 03'."""
    if doy is None:
        return "n/a"
    d = dt.date(2027, 1, 1) + dt.timedelta(days=round(doy) - 1)
    return d.strftime("%b %d")


def doy(d):
    """Day of year on a non-leap calendar so dates line up across years."""
    return (dt.date(2027, d.month, min(d.day, 28 if d.month == 2 else d.day)) - dt.date(2027, 1, 1)).days + 1


def percentile(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def mean(vals):
    vals = [v for v in vals if v is not None]
    return statistics.mean(vals) if vals else None


def slope_per_decade(pairs):
    """Least-squares slope of (year, value) pairs, per 10 years."""
    pairs = [(x, y) for x, y in pairs if y is not None]
    if len(pairs) < 10:
        return None
    mx = statistics.mean(p[0] for p in pairs)
    my = statistics.mean(p[1] for p in pairs)
    num = sum((x - mx) * (y - my) for x, y in pairs)
    den = sum((x - mx) ** 2 for x, _ in pairs)
    return 10 * num / den if den else None


def fmt(v, nd=1, suffix=""):
    return "n/a" if v is None else f"{v:.{nd}f}{suffix}"


def hargreaves_et0(tmax_f, tmin_f, date, lat_deg):
    """Hargreaves-Samani reference ET (inches/day) from temperature only."""
    if tmax_f is None or tmin_f is None or tmax_f < tmin_f:
        return None
    tmax, tmin = (tmax_f - 32) / 1.8, (tmin_f - 32) / 1.8
    j = date.timetuple().tm_yday
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * j / 365)
    delta = 0.409 * math.sin(2 * math.pi * j / 365 - 1.39)
    ws = math.acos(max(-1, min(1, -math.tan(phi) * math.tan(delta))))
    ra = (24 * 60 / math.pi) * 0.0820 * dr * (
        ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws))
    et_mm = 0.0023 * 0.408 * ra * ((tmax + tmin) / 2 + 17.8) * math.sqrt(tmax - tmin)
    return max(0.0, et_mm / 25.4)


def _ra(date, lat_deg):
    """Extraterrestrial radiation, MJ/m2/day (FAO-56 eq. 21)."""
    j = date.timetuple().tm_yday
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * j / 365)
    delta = 0.409 * math.sin(2 * math.pi * j / 365 - 1.39)
    ws = math.acos(max(-1, min(1, -math.tan(phi) * math.tan(delta))))
    return (24 * 60 / math.pi) * 0.0820 * dr * (
        ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws))


def penman_et0(tmax_f, tmin_f, date, lat_deg, elev_ft, wind_mph_10m):
    """FAO-56 Penman-Monteith reference ET (inches/day) using measured wind.

    Uses the FAO-56 procedures for missing humidity and radiation data: dew point is taken as
    Tmin - 2°C (the arid-region adjustment) and solar radiation is estimated from the daily
    temperature range (kRs = 0.16, interior location). Wind is the station's 24-hour average,
    converted from the 10 m anemometer height to the 2 m standard.
    """
    if tmax_f is None or tmin_f is None or tmax_f < tmin_f or wind_mph_10m is None:
        return None
    tx, tn = (tmax_f - 32) / 1.8, (tmin_f - 32) / 1.8
    t = (tx + tn) / 2
    z = (elev_ft or 0) * 0.3048
    e0 = lambda T: 0.6108 * math.exp(17.27 * T / (T + 237.3))
    es = (e0(tx) + e0(tn)) / 2
    ea = e0(tn - 2)
    delta = 4098 * e0(t) / (t + 237.3) ** 2
    gamma = 0.000665 * 101.3 * ((293 - 0.0065 * z) / 293) ** 5.26
    ra = _ra(date, lat_deg)
    rs = 0.16 * math.sqrt(tx - tn) * ra
    rso = (0.75 + 2e-5 * z) * ra
    rnl = 4.903e-9 * ((tx + 273.16) ** 4 + (tn + 273.16) ** 4) / 2 * (0.34 - 0.14 * math.sqrt(ea)) * (
        1.35 * min(1.0, rs / rso if rso else 1) - 0.35)
    rn = 0.77 * rs - rnl
    u2 = wind_mph_10m * MPH_TO_MS * 4.87 / math.log(67.8 * 10 - 5.42)
    et_mm = (0.408 * delta * rn + gamma * 900 / (t + 273) * u2 * (es - ea)) / (delta + gamma * (1 + 0.34 * u2))
    return max(0.0, et_mm / 25.4)


CALM_MPH = 1.5  # roughly the lightest breeze an exposed garden ever sees


def build_et0(rows, wind, lat, elev_ft):
    """Daily ET0 with wind, plus the same with calm air and with wind halved (a windbreak).

    Days without a wind reading use that month's average wind, so years before the wind record
    still get a wind-aware estimate.
    """
    by_month = {}
    for d, w in wind.items():
        by_month.setdefault(d.month, []).append(w["avg"])
    month_avg = {m: statistics.mean(v) for m, v in by_month.items()}
    et, calm, half = {}, {}, {}
    for r in rows:
        w = wind.get(r["date"], {}).get("avg") or month_avg.get(r["date"].month)
        e = penman_et0(r["tmax"], r["tmin"], r["date"], lat, elev_ft, w)
        if e is None:
            continue
        et[r["date"]] = e
        calm[r["date"]] = penman_et0(r["tmax"], r["tmin"], r["date"], lat, elev_ft, CALM_MPH)
        half[r["date"]] = penman_et0(r["tmax"], r["tmin"], r["date"], lat, elev_ft, w / 2)
    return et, calm, half


def compass(deg):
    return None if deg is None else COMPASS[int(((deg % 360) + 22.5) // 45) % 8]


def by_year(rows):
    out = {}
    for r in rows:
        out.setdefault(r["date"].year, []).append(r)
    return out


def complete_enough(days, start_md, end_md, max_missing=10):
    sel = [r for r in days if start_md <= (r["date"].month, r["date"].day) <= end_md]
    missing = sum(1 for r in sel if r["tmin"] is None)
    return len(sel) > 0 and missing <= max_missing


# --------------------------------------------------------------------------- #
# Analyses
# --------------------------------------------------------------------------- #

def frost_dates(years, threshold):
    """Per-year last spring and first fall date with tmin <= threshold (as DOY)."""
    out = {}
    for y, days in years.items():
        spring_ok = complete_enough(days, (3, 1), (6, 15))
        fall_ok = complete_enough(days, (8, 15), (11, 30))
        last = first = None
        if spring_ok:
            hits = [r["date"] for r in days if r["date"].month <= 7 and r["tmin"] is not None and r["tmin"] <= threshold]
            last = doy(max(hits)) if hits else None
        if fall_ok:
            hits = [r["date"] for r in days if r["date"].month >= 8 and r["tmin"] is not None and r["tmin"] <= threshold]
            first = doy(min(hits)) if hits else None
        out[y] = (last if spring_ok else None, first if fall_ok else None)
    return out


def rolling_first_last(days, key, threshold, window=7, above=True, after_doy=1):
    """First and last date when the trailing `window`-day mean of key crosses threshold."""
    vals = []
    first = last = None
    for r in days:
        v = key(r)
        vals.append(v)
        recent = [x for x in vals[-window:] if x is not None]
        if len(recent) < window - 2:
            continue
        m = statistics.mean(recent)
        hit = m >= threshold if above else m <= threshold
        if hit and doy(r["date"]) >= after_doy:
            if first is None:
                first = doy(r["date"])
            last = doy(r["date"])
    return first, last


def analyze(rows, ets, lat, clim_years, window=None, wind=None):
    """ets: {"et": {date: in}, optionally "calm" and "half"}; wind: from load_wind()."""
    et0_daily = ets.get("et", {})
    wind = wind or {}
    today = dt.date.today()
    last_full_year = today.year - 1
    years_all = {y: d for y, d in by_year(rows).items() if y <= last_full_year}
    first_year = min(years_all)
    clim_start = last_full_year - clim_years + 1
    clim = {y: d for y, d in years_all.items() if y >= clim_start}
    clim_rows = [r for y in clim for r in clim[y]]

    res = {"clim_start": clim_start, "clim_end": last_full_year,
           "record_start": first_year, "n_clim_years": len(clim)}

    # --- frost --------------------------------------------------------------
    res["frost"] = {}
    for thr, label in ((36, "36°F (light frost possible at ground level)"),
                       (32, "32°F (frost / tender plants damaged)"),
                       (28, "28°F (hard freeze)")):
        fd = frost_dates(years_all, thr)
        springs = [fd[y][0] for y in clim if fd[y][0] is not None]
        falls = [fd[y][1] for y in clim if fd[y][1] is not None]
        seasons = [fd[y][1] - fd[y][0] for y in clim if fd[y][0] and fd[y][1]]
        res["frost"][thr] = {
            "label": label,
            "spring_50": percentile(springs, 0.50),
            "spring_10": percentile(springs, 0.90),  # 10% risk of frost after this date
            "spring_latest": max(springs) if springs else None,
            "fall_10": percentile(falls, 0.10),      # 10% risk of frost before this date
            "fall_50": percentile(falls, 0.50),
            "fall_earliest": min(falls) if falls else None,
            "season_median": percentile(seasons, 0.5),
            "spring_trend": slope_per_decade([(y, fd[y][0]) for y in years_all]),
            "fall_trend": slope_per_decade([(y, fd[y][1]) for y in years_all]),
            "by_year": {y: fd[y] for y in years_all},
        }

    # --- monthly climate ------------------------------------------------------
    monthly = []
    for m in range(1, 13):
        mrows = [r for r in clim_rows if r["date"].month == m]
        per_year = {}
        for r in mrows:
            per_year.setdefault(r["date"].year, []).append(r)
        rain_tot, d90, d95, d100, d32, et_tot, et_h_tot, gdd_tot = [], [], [], [], [], [], [], []
        extra_tot = {}
        for y, rs in per_year.items():
            if sum(1 for r in rs if r["tmax"] is None) > 5:
                continue
            rain_tot.append(sum(r["pcpn"] or 0 for r in rs))
            d90.append(sum(1 for r in rs if (r["tmax"] or 0) >= 90))
            d95.append(sum(1 for r in rs if (r["tmax"] or 0) >= 95))
            d100.append(sum(1 for r in rs if (r["tmax"] or 0) >= 100))
            d32.append(sum(1 for r in rs if r["tmin"] is not None and r["tmin"] <= 32))
            gdd_tot.append(sum(gdd50(r) for r in rs))
            et_h = [hargreaves_et0(r["tmax"], r["tmin"], r["date"], lat) for r in rs]
            et_h = [e for e in et_h if e is not None]
            if et_h:
                et_h_tot.append(statistics.mean(et_h) * len(rs))
            et = [et0_daily[r["date"]] for r in rs if r["date"] in et0_daily]
            if len(et) >= len(rs) - 2:
                et_tot.append(sum(et))
                for k in ("calm", "half"):
                    if k in ets:
                        extra_tot.setdefault(k, []).append(sum(ets[k].get(r["date"], 0) for r in rs))
        # wind, from whatever wind years fall inside the climatology period
        wd = [(d, v) for d, v in wind.items() if d.month == m and d.year in clim]
        wyears = {}
        for d, v in wd:
            wyears.setdefault(d.year, []).append(v)
        wyears = {y: v for y, v in wyears.items() if len(v) >= 25}
        dirs = [compass(v["dir"]) for _, v in wd if v["gust"] and v["gust"] >= 25 and v["dir"] is not None]
        wstats = {
            "wind_avg": mean([v["avg"] for _, v in wd]),
            "gust_avg": mean([v["gust"] for _, v in wd]),
            "windy_days": mean([sum(1 for v in vs if v["avg"] >= 12) for vs in wyears.values()]),
            "gust35_days": mean([sum(1 for v in vs if (v["gust"] or 0) >= 35) for vs in wyears.values()]),
            "gust45_days": mean([sum(1 for v in vs if (v["gust"] or 0) >= 45) for vs in wyears.values()]),
            "gust_max": max((v["gust"] or 0 for _, v in wd), default=None),
            "strong_dir": max(set(dirs), key=dirs.count) if dirs else None,
        }
        monthly.append({
            **wstats,
            "month": MONTHS[m - 1],
            "hi": mean([r["tmax"] for r in mrows]), "lo": mean([r["tmin"] for r in mrows]),
            "rain": mean(rain_tot), "d90": mean(d90), "d95": mean(d95), "d100": mean(d100),
            "frost_nights": mean(d32), "gdd": mean(gdd_tot),
            "et0": mean(et_tot) if et_tot else mean(et_h_tot),
            "et0_calm": mean(extra_tot.get("calm", [])), "et0_half": mean(extra_tot.get("half", [])),
        })
    res["monthly"] = monthly
    res["annual_rain"] = sum(m["rain"] or 0 for m in monthly)
    res["summer_rain"] = sum(m["rain"] or 0 for m in monthly[5:9])
    wind_years = sorted({d.year for d in wind if d.year in clim})
    res["wind_period"] = (wind_years[0], wind_years[-1]) if wind_years else None

    # --- heat ---------------------------------------------------------------------
    heat_years = {}
    for y, days in years_all.items():
        if sum(1 for r in days if 5 <= r["date"].month <= 9 and r["tmax"] is None) > 15:
            continue
        hot = [r for r in days if r["tmax"] is not None]
        first90 = next((doy(r["date"]) for r in hot if r["tmax"] >= 90), None)
        last90 = next((doy(r["date"]) for r in reversed(hot) if r["tmax"] >= 90), None)
        streak = best = 0
        for r in days:
            streak = streak + 1 if (r["tmax"] or 0) >= 95 else 0
            best = max(best, streak)
        heat_years[y] = {
            "d90": sum(1 for r in hot if r["tmax"] >= 90),
            "d95": sum(1 for r in hot if r["tmax"] >= 95),
            "d100": sum(1 for r in hot if r["tmax"] >= 100),
            "warm_nights": sum(1 for r in days if r["tmin"] is not None and r["tmin"] >= 65),
            "first90": first90, "last90": last90, "longest95": best,
            "max": max((r["tmax"] for r in hot), default=None),
        }
    hc = [heat_years[y] for y in clim if y in heat_years]
    res["heat"] = {
        "d90": mean([h["d90"] for h in hc]), "d95": mean([h["d95"] for h in hc]),
        "d100": mean([h["d100"] for h in hc]), "warm_nights": mean([h["warm_nights"] for h in hc]),
        "first90_med": percentile([h["first90"] for h in hc], 0.5),
        "first90_early": percentile([h["first90"] for h in hc], 0.1),
        "last90_med": percentile([h["last90"] for h in hc], 0.5),
        "longest95_med": percentile([h["longest95"] for h in hc], 0.5),
        "max_record": max(((h["max"], y) for y, h in heat_years.items() if h["max"]), default=None),
        "d95_trend": slope_per_decade([(y, h["d95"]) for y, h in heat_years.items()]),
        "d90_early": mean([heat_years[y]["d90"] for y in years_all if y in heat_years and y < first_year + 30]),
        "early_label": f"{first_year}-{first_year + 29}",
        "by_year": heat_years,
    }

    # --- planting windows (rolling 7-day means) ---------------------------------
    warm_soil, lettuce_stop, lettuce_restart, cool_fall = [], [], [], []
    for y in clim:
        days = clim[y]
        f, _ = rolling_first_last(days, lambda r: None if r["tmax"] is None or r["tmin"] is None
                                  else (r["tmax"] + r["tmin"]) / 2, 60)
        warm_soil.append(f)
        f, l = rolling_first_last(days, lambda r: r["tmax"], 80)
        lettuce_stop.append(f)
        lettuce_restart.append(l)
        _, l = rolling_first_last(days, lambda r: None if r["tmax"] is None or r["tmin"] is None
                                  else (r["tmax"] + r["tmin"]) / 2, 50)
        cool_fall.append(l)
    res["windows"] = {
        "warm_season_med": percentile(warm_soil, 0.5), "warm_season_late": percentile(warm_soil, 0.8),
        "lettuce_stop_med": percentile(lettuce_stop, 0.5), "lettuce_stop_early": percentile(lettuce_stop, 0.2),
        "lettuce_restart_med": percentile(lettuce_restart, 0.5),
        "cool_fall_med": percentile(cool_fall, 0.5),
    }

    # --- dry spells -------------------------------------------------------------
    spells = []
    for y in clim:
        best = cur = 0
        for r in clim[y]:
            if 4 <= r["date"].month <= 10:
                cur = cur + 1 if (r["pcpn"] or 0) < 0.10 else 0
                best = max(best, cur)
        spells.append(best)
    res["dry_spell_med"] = percentile(spells, 0.5)
    res["dry_spell_max"] = max(spells) if spells else None

    # --- custom window ------------------------------------------------------------
    if window:
        res["window"] = window_stats(clim, ets, lat, window, wind)

    # --- recent years table -----------------------------------------------------
    recent = []
    fd32 = res["frost"][32]["by_year"]
    for y in sorted(years_all)[-10:]:
        h = heat_years.get(y, {})
        recent.append({"year": y, "last32": fd32[y][0], "first32": fd32[y][1],
                       "d90": h.get("d90"), "d100": h.get("d100"), "max": h.get("max"),
                       "rain": sum(r["pcpn"] or 0 for r in years_all[y])})
    res["recent"] = recent

    # --- fruit-tree chill and winter risk -----------------------------------------
    trees = chill.load_trees()
    seasons = chill.chill_by_winter(chill.hourly_from_daily(rows, lat), trees)
    extras = chill.winter_extras(rows)
    winters = []
    for k in sorted(seasons):
        sdat = seasons[k]
        if not sdat["complete"]:
            continue
        e = extras.get(k, {})
        winters.append({"winter": k, "start_year": int(k[:4]), "ch": sdat["ch"], "utah": sdat["utah"],
                        "cp": sdat["cp"], "met": sdat["met"], "coldest": e.get("coldest"),
                        "bloom_freezes": e.get("bloom_freezes"), "last28": e.get("last28")})
    cw = [w for w in winters if clim_start - 1 <= w["start_year"] < last_full_year]
    tree_sum = []
    for t in trees:
        n = t["name"]
        met_doys = [((w["met"][n] - dt.date(w["start_year"], 10, 1)).days) for w in cw if n in w["met"]]
        tree_sum.append({
            **t,
            "pct_min": 100 * sum(1 for w in cw if w["ch"] >= t["chill_hours_min"]) / len(cw) if cw else None,
            "pct_max": 100 * sum(1 for w in cw if w["ch"] >= t["chill_hours_max"]) / len(cw) if cw else None,
            "met_med": (dt.date(2026, 10, 1) + dt.timedelta(days=percentile(met_doys, 0.5))) if met_doys else None,
            "met_late": (dt.date(2026, 10, 1) + dt.timedelta(days=percentile(met_doys, 0.9))) if met_doys else None,
            "damage_winters": sum(1 for w in cw if w["coldest"] is not None and w["coldest"] <= t["cold_damage_f"]),
            "kill_winters": sum(1 for w in cw if w["coldest"] is not None and w["coldest"] <= t["cold_kill_f"]),
        })
    res["chill"] = {
        "winters": winters, "trees": tree_sum, "n": len(cw),
        "ch_med": percentile([w["ch"] for w in cw], 0.5), "ch_low": min((w["ch"] for w in cw), default=None),
        "ch_high": max((w["ch"] for w in cw), default=None),
        "cp_med": percentile([w["cp"] for w in cw], 0.5), "utah_med": percentile([w["utah"] for w in cw], 0.5),
        "bloom_freeze_avg": mean([w["bloom_freezes"] for w in cw]),
        "bloom_freeze_pct": 100 * sum(1 for w in cw if (w["bloom_freezes"] or 0) > 0) / len(cw) if cw else None,
        "coldest_med": percentile([w["coldest"] for w in cw], 0.5),
        "ch_trend": slope_per_decade([(w["start_year"], w["ch"]) for w in winters]),
    }
    return res


def gdd50(r):
    """Growing degree days, base 50°F with 86°F cap (standard for tomatoes, peppers, corn)."""
    if r["tmax"] is None or r["tmin"] is None:
        return 0
    hi, lo = min(r["tmax"], 86), max(r["tmin"], 50)
    return max(0.0, (hi + max(lo, 50)) / 2 - 50) if hi > 50 else 0.0


def window_stats(clim, ets, lat, window, wind):
    et0_daily = ets.get("et", {})
    (sm, sd), (em, ed) = window
    per = []
    for y, days in clim.items():
        sel = [r for r in days if (sm, sd) <= (r["date"].month, r["date"].day) <= (em, ed)]
        if not sel or sum(1 for r in sel if r["tmax"] is None) > 5:
            continue
        et = [et0_daily.get(r["date"]) or hargreaves_et0(r["tmax"], r["tmin"], r["date"], lat) or 0 for r in sel]
        rain = sum(r["pcpn"] or 0 for r in sel)
        wsel = [wind[r["date"]] for r in sel if r["date"] in wind]
        per.append({
            "wind": mean([v["avg"] for v in wsel]) if len(wsel) > 0.8 * len(sel) else None,
            "gust35": sum(1 for v in wsel if (v["gust"] or 0) >= 35) if len(wsel) > 0.8 * len(sel) else None,
            "wind_et": (sum(et) - sum(ets["calm"].get(r["date"], 0) for r in sel)) if "calm" in ets else None,
            "half_save": (sum(et) - sum(ets["half"].get(r["date"], 0) for r in sel)) if "half" in ets else None,
            "hi": mean([r["tmax"] for r in sel]), "lo": mean([r["tmin"] for r in sel]),
            "d90": sum(1 for r in sel if (r["tmax"] or 0) >= 90),
            "d95": sum(1 for r in sel if (r["tmax"] or 0) >= 95),
            "d100": sum(1 for r in sel if (r["tmax"] or 0) >= 100),
            "frost": sum(1 for r in sel if r["tmin"] is not None and r["tmin"] <= 32),
            "rain": rain, "rain_days": sum(1 for r in sel if (r["pcpn"] or 0) >= 0.10),
            "et0": sum(et), "deficit": max(0.0, sum(et) - rain),
            "gdd": sum(gdd50(r) for r in sel), "max": max((r["tmax"] or 0) for r in sel),
            "days": len(sel),
        })
    agg = lambda k, p=None: percentile([x[k] for x in per], p) if p is not None else mean([x[k] for x in per])
    return {
        "label": f"{MONTHS[sm-1]} {sd} – {MONTHS[em-1]} {ed}", "days": per[0]["days"] if per else 0,
        "hi": agg("hi"), "lo": agg("lo"), "d90": agg("d90"), "d95": agg("d95"), "d100": agg("d100"),
        "d100_pct": 100 * sum(1 for x in per if x["d100"] > 0) / len(per) if per else None,
        "frost_pct": 100 * sum(1 for x in per if x["frost"] > 0) / len(per) if per else None,
        "rain": agg("rain"), "rain_dry": agg("rain", 0.1), "rain_days": agg("rain_days"),
        "et0": agg("et0"), "deficit": agg("deficit"), "deficit_hot": agg("deficit", 0.9),
        "gdd": agg("gdd"), "max_med": agg("max", 0.5), "n": len(per),
        "wind": agg("wind"), "gust35": agg("gust35"), "wind_et": agg("wind_et"), "half_save": agg("half_save"),
    }


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def render(meta, res, et_src):
    L = []
    w = L.append
    cs, ce = res["clim_start"], res["clim_end"]
    w(f"# Garden weather report: {meta.get('site', {}).get('name') or meta['name'].title()}")
    w("")
    w(f"Station {meta['sid']} at {meta['lat']:.3f}, {meta['lon']:.3f}, elevation {meta['elev_ft']:.0f} ft. "
      f"Records since {res['record_start']}. Unless noted, numbers are based on the last "
      f"{res['n_clim_years']} complete years ({cs}–{ce}), which reflect today's climate better than the full record. "
      f"Generated {dt.date.today().isoformat()}.")
    w("")
    w("> Weather can't be forecast day-by-day more than about two weeks out. What this report gives you is the "
      "odds, based on what actually happened here in recent decades, which is the best guide for a plan a season ahead.")
    w("")

    f32, f28, f36 = res["frost"][32], res["frost"][28], res["frost"][36]
    win = res["windows"]
    h = res["heat"]
    w("## The short version")
    w("")
    w(f"- **Last spring frost (32°F):** typically around **{doy_to_str(f32['spring_50'])}**. "
      f"Only a 1-in-10 chance of frost after **{doy_to_str(f32['spring_10'])}**. "
      f"Latest seen: {doy_to_str(f32['spring_latest'])}.")
    w(f"- **First fall frost (32°F):** typically around **{doy_to_str(f32['fall_50'])}**, "
      f"with a 1-in-10 chance it comes before **{doy_to_str(f32['fall_10'])}**.")
    w(f"- **Frost-free season:** about **{fmt(f32['season_median'], 0)} days** in a typical year.")
    w(f"- **Warm-season crops** (tomatoes, peppers, cucumbers, squash) do best once the 7-day average temperature "
      f"holds above 60°F, typically **{doy_to_str(win['warm_season_med'])}** "
      f"(in a late year {doy_to_str(win['warm_season_late'])}).")
    w(f"- **Lettuce and greens window closes** when daytime highs average 80°F+, typically "
      f"**{doy_to_str(win['lettuce_stop_med'])}** (early years {doy_to_str(win['lettuce_stop_early'])}). "
      f"It reopens around **{doy_to_str(win['lettuce_restart_med'])}**, so sow fall greens from late "
      f"{doy_to_str(win['lettuce_restart_med'] - 30)[:3]} onward.")
    w(f"- **Heat:** about **{fmt(h['d90'], 0)} days ≥ 90°F**, **{fmt(h['d95'], 0)} days ≥ 95°F** and "
      f"**{fmt(h['d100'], 0)} days ≥ 100°F** a year. First 90°F day is typically {doy_to_str(h['first90_med'])}.")
    w(f"- **Rain:** about **{res['annual_rain']:.1f} in a year**, but only **{res['summer_rain']:.1f} in from June "
      f"through September**. The typical longest dry spell (no day with 0.10 in or more, Apr–Oct) is "
      f"**{fmt(res['dry_spell_med'], 0)} days**.")
    summer_et = sum(m["et0"] or 0 for m in res["monthly"][5:9])
    w(f"- **Water:** plants lose roughly **{summer_et:.0f} in of water from June through September** "
      f"(reference evapotranspiration), so almost all of it has to come from irrigation.")
    mo = res["monthly"]
    if res.get("wind_period") and mo[6]["et0_calm"]:
        summer_wind = sum((m["et0"] or 0) - (m["et0_calm"] or 0) for m in mo[5:9])
        summer_half = sum((m["et0"] or 0) - (m["et0_half"] or 0) for m in mo[5:9])
        windiest = sorted(mo, key=lambda m: m["wind_avg"] or 0, reverse=True)[:3]
        w(f"- **Wind:** windiest months are {', '.join(m['month'] for m in windiest)} "
          f"(about {windiest[0]['wind_avg']:.0f} mph average, with gusts of 35 mph or more on "
          f"{sum(m['gust35_days'] or 0 for m in mo):.0f} days a year). Wind adds about **{summer_wind:.1f} in** "
          f"({100 * summer_wind / summer_et:.0f}%) to June–September water loss compared with still air. "
          f"A windbreak that halves wind speed would save roughly **{summer_half:.1f} in, about "
          f"{summer_half * GAL_PER_INCH_PER_100SQFT:.0f} gallons per 100 sq ft** over the summer.")
    w("")

    w("## Frost dates")
    w("")
    w("The date by which the last spring frost is behind you, and the first fall frost, at three temperatures. "
      "Station thermometers are at about 5 ft, so ground level can frost when the station reads 34–36°F.")
    w("")
    w("| Threshold | Last spring: typical | Last spring: 1-in-10 later than | First fall: 1-in-10 earlier than | First fall: typical | Season (days) |")
    w("|---|---|---|---|---|---|")
    for thr in (36, 32, 28):
        f = res["frost"][thr]
        w(f"| {f['label']} | {doy_to_str(f['spring_50'])} | {doy_to_str(f['spring_10'])} | "
          f"{doy_to_str(f['fall_10'])} | {doy_to_str(f['fall_50'])} | {fmt(f['season_median'], 0)} |")
    w("")
    st, ft = f32["spring_trend"], f32["fall_trend"]
    if st is not None and ft is not None:
        w(f"**Trend since {res['record_start']}:** the last spring frost has moved "
          f"{'earlier' if st < 0 else 'later'} by about {abs(st):.1f} days per decade and the first fall frost "
          f"{'later' if ft > 0 else 'earlier'} by about {abs(ft):.1f} days per decade.")
        w("")

    w("## Month by month")
    w("")
    w("| Month | Avg high °F | Avg low °F | Rain (in) | Days ≥90° | Days ≥100° | Frost nights | Water loss ET₀ (in) | Irrigation need (in) | Gal per 100 sq ft | Growing degree days |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for m in res["monthly"]:
        need = max(0.0, (m["et0"] or 0) - 0.7 * (m["rain"] or 0))
        w(f"| {m['month']} | {fmt(m['hi'], 0)} | {fmt(m['lo'], 0)} | {fmt(m['rain'], 2)} | {fmt(m['d90'], 1)} | "
          f"{fmt(m['d100'], 1)} | {fmt(m['frost_nights'], 0)} | {fmt(m['et0'], 1)} | {need:.1f} | "
          f"{need * GAL_PER_INCH_PER_100SQFT:.0f} | {fmt(m['gdd'], 0)} |")
    w("")
    w(f"Water loss is reference evapotranspiration (ET₀) for a well-watered short grass, from {et_src}. "
      "Irrigation need is ET₀ minus the 70% of rain that is usefully absorbed. A full-grown tomato or squash "
      "uses about 1.0–1.1× ET₀ at peak; young transplants, garlic or onions near harvest, and mulched or "
      "drip-irrigated beds use noticeably less. Growing degree days use a 50°F base with an 86°F cap, the usual "
      "measure for tomatoes, peppers, squash and corn.")
    w("")

    if res.get("wind_period"):
        wp = res["wind_period"]
        w("## Wind")
        w("")
        w(f"Daily wind at Pangborn airport, {wp[0]}–{wp[1]}. The airport sits on the open plateau, so a "
          "sheltered valley yard will feel less, but the pattern by month and the direction of the strong winds hold.")
        w("")
        cal = bool(mo[6]["et0_calm"])
        hdr = "| Month | Avg wind (mph) | Avg daily peak gust (mph) | Windy days (avg ≥12 mph) | Gusts ≥35 mph (days) | Gusts ≥45 mph (days) | Strong winds from |"
        sep = "|---|---|---|---|---|---|---|"
        if cal:
            hdr += " Water loss from wind (in) | Saved by windbreak (gal per 100 sq ft) |"
            sep += "---|---|"
        w(hdr)
        w(sep)
        for m in mo:
            line = (f"| {m['month']} | {fmt(m['wind_avg'], 1)} | {fmt(m['gust_avg'], 0)} | {fmt(m['windy_days'], 1)} | "
                    f"{fmt(m['gust35_days'], 1)} | {fmt(m['gust45_days'], 1)} | {m['strong_dir'] or 'n/a'} |")
            if cal:
                we = (m["et0"] or 0) - (m["et0_calm"] or 0)
                hs = (m["et0"] or 0) - (m["et0_half"] or 0)
                line += f" {we:.1f} | {hs * GAL_PER_INCH_PER_100SQFT:.0f} |"
            w(line)
        w("")
        if cal:
            w("\"Water loss from wind\" is how much more water a fully planted bed loses than it would in near-still air. "
              "\"Saved by windbreak\" is what you would save if a fence, hedge or shade-cloth screen cut the wind in half. "
              "A windbreak protects a strip about 5 to 10 times its height downwind, so a 6 ft screen covers 30 to 60 ft. "
              "Put it on the side the strong winds come from.")
            w("")
        w("Wind also dries the soil surface and sprinkler spray long before roots get it, so drip or soaker lines "
          "under mulch and watering in the early morning, before the afternoon wind picks up, waste far less.")
        w("")

    w("## Heat stress")
    w("")
    w(f"- Days ≥ 90°F: {fmt(h['d90'], 0)} a year now, vs {fmt(h['d90_early'], 0)} in {h['early_label']}.")
    if h["d95_trend"] is not None:
        w(f"- Days ≥ 95°F are {'rising' if h['d95_trend'] > 0 else 'falling'} by about "
          f"{abs(h['d95_trend']):.1f} days per decade.")
    w(f"- Nights that stay ≥ 65°F (these hurt tomato and pepper fruit set): {fmt(h['warm_nights'], 0)} a year.")
    w(f"- First 90°F day: typically {doy_to_str(h['first90_med'])}, early years {doy_to_str(h['first90_early'])}. "
      f"Last 90°F day: typically {doy_to_str(h['last90_med'])}.")
    w(f"- Longest run of 95°F+ days in a typical summer: {fmt(h['longest95_med'], 0)} days.")
    if h["max_record"]:
        w(f"- Hottest day on record: {h['max_record'][0]:.0f}°F ({h['max_record'][1]}).")
    w("")
    w("Tomatoes drop blossoms when days run above about 90–95°F with nights above about 70–75°F, so most "
      "fruit sets before early July and again from mid-August. Peppers set best from 70–85°F days.")
    w("")

    if "window" in res:
        x = res["window"]
        w(f"## Your window: {x['label']} ({x['days']} days)")
        w("")
        w(f"Based on {x['n']} years ({cs}–{ce}).")
        w("")
        w(f"- Average high / low: {fmt(x['hi'], 0)}°F / {fmt(x['lo'], 0)}°F. Hottest day in a typical year: {fmt(x['max_med'], 0)}°F.")
        w(f"- Days ≥ 90°F: {fmt(x['d90'], 1)}. Days ≥ 95°F: {fmt(x['d95'], 1)}. Days ≥ 100°F: {fmt(x['d100'], 1)} "
          f"(at least one 100°F day in {fmt(x['d100_pct'], 0)}% of years).")
        w(f"- Chance of a 32°F night in this window: {fmt(x['frost_pct'], 0)}%.")
        w(f"- Rain: {fmt(x['rain'], 2)} in on average over {fmt(x['rain_days'], 1)} days, and only "
          f"{fmt(x['rain_dry'], 2)} in in a dry year (1-in-10).")
        w(f"- Water loss (ET₀): {fmt(x['et0'], 1)} in. Water deficit to make up by irrigation: "
          f"{fmt(x['deficit'], 1)} in typical, {fmt(x['deficit_hot'], 1)} in in a hot dry year. That is about "
          f"**{(x['deficit'] or 0) * GAL_PER_INCH_PER_100SQFT:.0f} gallons per 100 sq ft** of fully planted bed "
          f"({(x['deficit_hot'] or 0) * GAL_PER_INCH_PER_100SQFT:.0f} in a hot year).")
        w(f"- Growing degree days accumulated: {fmt(x['gdd'], 0)}.")
        if x.get("wind") is not None:
            w(f"- Wind: {fmt(x['wind'], 1)} mph average, gusts of 35 mph or more on {fmt(x['gust35'], 1)} days.")
        if x.get("wind_et") is not None:
            w(f"- Wind's share of that water loss: {fmt(x['wind_et'], 1)} in. A windbreak that halves the wind would "
              f"save about {(x['half_save'] or 0) * GAL_PER_INCH_PER_100SQFT:.0f} gallons per 100 sq ft in this window.")
        w("")

    c = res.get("chill")
    if c and c["n"]:
        w("## Fruit trees: chill and winter risk")
        w("")
        w(f"Chill is counted from Oct 1 to Mar 31. A typical winter here gives **{fmt(c['ch_med'], 0)} chill hours** "
          f"(32–45°F; lowest {fmt(c['ch_low'], 0)}, highest {fmt(c['ch_high'], 0)} over {c['n']} winters), "
          f"{fmt(c['utah_med'], 0)} Utah chill units and {fmt(c['cp_med'], 0)} chill portions. "
          "That is far more than any of your trees need, so chill is not the problem here. The real risks are "
          "trees finishing their chill early and then blooming into a spring freeze, and hard winter cold.")
        if c["ch_trend"] is not None:
            w(f"Chill hours have changed by about {c['ch_trend']:+.0f} hours per decade over the full record.")
        w("")
        w("| Tree | Chill hours needed | Winters with enough chill | Need typically met by | Met by (late winter) | Winters cold enough to injure | Winters cold enough to kill back |")
        w("|---|---|---|---|---|---|---|")
        for t in c["trees"]:
            w(f"| {t['name']} | {t['chill_hours_min']}–{t['chill_hours_max']} | {fmt(t['pct_max'], 0)}% | "
              f"{t['met_med'].strftime('%b %d') if t['met_med'] else 'n/a'} | "
              f"{t['met_late'].strftime('%b %d') if t['met_late'] else 'n/a'} | "
              f"{t['damage_winters']} of {c['n']} (≤{t['cold_damage_f']}°F) | {t['kill_winters']} of {c['n']} (≤{t['cold_kill_f']}°F) |")
        w("")
        for t in c["trees"]:
            w(f"- **{t['name']}:** {t['notes']}")
        w("")
        w(f"**Bloom-time freezes:** {fmt(c['bloom_freeze_pct'], 0)}% of springs have at least one night at 28°F or "
          f"colder between Mar 15 and Apr 30 (about {fmt(c['bloom_freeze_avg'], 1)} such nights a year). "
          f"The typical coldest winter night is {fmt(c['coldest_med'], 0)}°F.")
        w("")
        w("### Each winter")
        w("")
        names = [t["name"] for t in c["trees"]]
        short = [n.split(" (")[0] for n in names]
        w("| Winter | Chill hours | Utah units | Chill portions | " + " | ".join(f"{s} need met" for s in short) +
          " | Coldest night °F | Freezes ≤28°F Mar 15–Apr 30 | Last ≤28°F |")
        w("|---" * (7 + len(names)) + "|")
        for x in c["winters"][-15:]:
            w(f"| {x['winter']} | {x['ch']:.0f} | {x['utah']:.0f} | {x['cp']:.0f} | " +
              " | ".join(x["met"][n].strftime("%b %d") if n in x["met"] else "not met" for n in names) +
              f" | {fmt(x['coldest'], 0)} | {x['bloom_freezes']} | {x['last28'].strftime('%b %d') if x['last28'] else 'none'} |")
        w("")
        w("Every winter on record is in `reports/<station>_chill_by_winter.csv`. Hourly temperatures are rebuilt from daily highs "
          "and lows; checked against real hourly readings at Pangborn airport for 2015–2025, chill hours came out within "
          "about 5% in most winters. Tree needs and cold limits are in `fruit_trees.json`, which you can edit.")
        w("")

    site = meta.get("site")
    if site:
        w(f"## How {site['name']} compares with the {site['base_name'].title()} station")
        w("")
        w(f"Based on {site['days']} days of your station's records ({site['first']} to {site['last']}). The long "
          f"{site['base_name'].title()} record has been shifted by these monthly differences to estimate {site['name']}'s "
          "long-term climate, and your station's own readings are used directly where they exist.")
        rr = site["offsets"].get("rain_ratio")
        if rr:
            w(f"Rain: {site['name']} caught {rr:.2f}× the town station's rain over the same days, so the long-term rain "
              "totals are scaled by that.")
        if site.get("high_only"):
            w("")
            w("**Only daily highs were in these files**, so nights, frost dates and chill still come from the town "
              "station's lows. Heat, growing degree days and water loss reflect your station's highs.")
        w("")
        w("| Month | Days compared | Highs differ by °F | Lows differ by °F |")
        w("|---|---|---|---|")
        for m in range(1, 13):
            o = site["offsets"][m]
            hi = o["hi"] if o["hi"] is not None else o.get("hi_fill")
            lo = o["lo"] if o["lo"] is not None else o.get("lo_fill")
            sh = "" if o["hi"] is not None or hi is None else " (from nearest month)"
            sl = "" if o["lo"] is not None or lo is None else " (from nearest month)"
            w(f"| {MONTHS[m-1]} | {o['n']} | {'n/a' if hi is None else f'{hi:+.1f}'}{sh} | "
              f"{'n/a' if lo is None else f'{lo:+.1f}'}{sl} |")
        w("")

    w("## Recent years")
    w("")
    w("| Year | Last 32°F | First 32°F | Days ≥90° | Days ≥100° | Hottest °F | Rain (in) |")
    w("|---|---|---|---|---|---|---|")
    for r in res["recent"]:
        w(f"| {r['year']} | {doy_to_str(r['last32'])} | {doy_to_str(r['first32'])} | {r['d90'] if r['d90'] is not None else 'n/a'} | "
          f"{r['d100'] if r['d100'] is not None else 'n/a'} | {fmt(r['max'], 0)} | {r['rain']:.1f} |")
    w("")
    w("Rain totals in years with gaps in the record will read a little low.")
    w("")
    w("## Sources")
    w("")
    w("- Daily temperature and precipitation: NOAA cooperative and airport station records, via the "
      "Regional Climate Centers' ACIS service (data.rcc-acis.org).")
    w(f"- Evapotranspiration: {et_src}.")
    w("- Wind: NOAA GHCN-Daily average wind speed, peak gust and gust direction for Pangborn airport "
      "(USW00094239), via NCEI (www.ncei.noaa.gov).")
    return "\n".join(L) + "\n"


def parse_md(s):
    m, d = s.split("-")
    return int(m), int(d)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--station", default=DEFAULT_STATION,
                    help="GHCN station id (default USC00459074 Wenatchee town; USW00094239 = Pangborn airport)")
    ap.add_argument("--years", type=int, default=30, help="Years of recent record for the climatology (default 30)")
    ap.add_argument("--window", nargs=2, metavar=("MM-DD", "MM-DD"),
                    help="Also summarize a date window, e.g. --window 05-15 07-15")
    ap.add_argument("--lat", type=float, help="Latitude for evapotranspiration (default: station)")
    ap.add_argument("--lon", type=float, help="Longitude for evapotranspiration (default: station)")
    ap.add_argument("--wind-station", default=DEFAULT_WIND_STATION,
                    help="Station with daily wind (default USW00094239 Pangborn airport)")
    ap.add_argument("--no-wind", action="store_true", help="Skip wind; estimate ET from temperature only")
    ap.add_argument("--open-meteo", action="store_true",
                    help="Use Open-Meteo's gridded ET0 instead of the station-based wind-aware estimate")
    ap.add_argument("--refresh", action="store_true", help="Re-download even if cached data is fresh")
    ap.add_argument("--out", help="Report path (default weather/reports/<station>_report.md)")
    ap.add_argument("--json", action="store_true", help="Also write a JSON summary next to the report")
    ap.add_argument("--pws", metavar="FOLDER",
                    help="Folder of CSVs exported from your own/nearby personal weather station")
    ap.add_argument("--pws-name", default="Pitcher Canyon", help="Name for the personal station")
    ap.add_argument("--pws-elev", type=float, default=1440, help="Personal station elevation in ft")
    a = ap.parse_args()

    print(f"Fetching station {a.station} ...", file=sys.stderr)
    meta = station_meta(a.station)
    rows = load_station_daily(a.station, a.refresh)
    if a.pws:
        pws = pws_import.read_pws(a.pws)
        if not pws:
            sys.exit(f"No usable temperature data found in {a.pws}")
        offsets = pws_import.monthly_offsets(pws, rows)
        rows = pws_import.adjust_rows(rows, offsets)
        for r in rows:  # use the personal station's own readings where it has them
            p = pws.get(r["date"])
            if p:
                r["tmax"] = p["tmax"]
                if p.get("tmin") is not None:
                    r["tmin"] = p["tmin"]
                if p.get("pcpn") is not None:
                    r["pcpn"] = p["pcpn"]
        days = sorted(pws)
        meta["site"] = {"name": a.pws_name, "base_name": meta["name"], "offsets": offsets,
                        "high_only": all(p.get("tmin") is None for p in pws.values()),
                        "days": len(days), "first": days[0], "last": days[-1]}
        meta["elev_ft"] = a.pws_elev
        print(f"Using {len(days)} days from {a.pws_name} ({days[0]} to {days[-1]})", file=sys.stderr)
    lat = a.lat if a.lat is not None else meta["lat"]
    lon = a.lon if a.lon is not None else meta["lon"]
    last_full = dt.date.today().year - 1
    wind, ets = {}, {}
    if not a.no_wind:
        print(f"Fetching wind from NOAA ({a.wind_station}) ...", file=sys.stderr)
        wind = load_wind(a.wind_station, a.refresh)
    et_src = "the Hargreaves temperature-based estimate (wind not included)"
    if a.open_meteo:
        print("Fetching evapotranspiration from Open-Meteo ...", file=sys.stderr)
        om = load_et0(lat, lon, last_full - a.years + 1, last_full, a.refresh)
        if om:
            ets = {"et": om}
            et_src = "the Open-Meteo history archive (FAO-56 Penman-Monteith from ERA5 reanalysis)"
    if not ets and wind:
        e, c, h = build_et0(rows, wind, lat, meta["elev_ft"])
        ets = {"et": e, "calm": c, "half": h}
        et_src = ("FAO-56 Penman-Monteith using this station's temperatures and measured airport wind "
                  "(humidity and sunshine estimated from the daily temperature range)")
    window = (parse_md(a.window[0]), parse_md(a.window[1])) if a.window else None

    print("Analyzing ...", file=sys.stderr)
    res = analyze(rows, ets, lat, a.years, window, wind)
    os.makedirs(REPORT_DIR, exist_ok=True)
    tag = a.pws_name.replace(" ", "_") if a.pws else a.station
    out = a.out or os.path.join(REPORT_DIR, f"{tag}_report.md")
    with open(os.path.join(os.path.dirname(out), f"{tag}_chill_by_winter.csv"), "w", newline="") as f:
        cwr = csv.writer(f)
        tnames = [t["name"] for t in res["chill"]["trees"]]
        cwr.writerow(["winter", "chill_hours", "utah_units", "chill_portions"] +
                     [f"{n}: need met" for n in tnames] + ["coldest_night_f", "freezes_le28_mar15_apr30", "last_le28"])
        for x in res["chill"]["winters"]:
            cwr.writerow([x["winter"], round(x["ch"]), round(x["utah"]), round(x["cp"], 1)] +
                         [x["met"].get(n, "") for n in tnames] + [x["coldest"], x["bloom_freezes"], x["last28"] or ""])
    with open(out, "w") as f:
        f.write(render(meta, res, et_src))
    if a.json:
        slim = {k: v for k, v in res.items() if k not in ("frost", "heat", "chill")}
        slim["chill"] = {k: v for k, v in res["chill"].items() if k != "winters"}
        slim["frost"] = {t: {k: v for k, v in f.items() if k != "by_year"} for t, f in res["frost"].items()}
        slim["heat"] = {k: v for k, v in res["heat"].items() if k != "by_year"}
        slim["station"] = meta
        with open(os.path.splitext(out)[0] + ".json", "w") as f:
            json.dump(slim, f, indent=2, default=str)
    print(f"Report written to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
