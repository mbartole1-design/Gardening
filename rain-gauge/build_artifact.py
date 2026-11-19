#!/usr/bin/env python3
"""Build artifact.html (the claude.ai rain gauge page) from cartoon.html.

The artifact can't reach NOAA itself, so it embeds the history used for the averages
(rain 1996-2025, snowfall for the 1995-96 to 2024-25 seasons) and reads this year's numbers (doc rain/current, written daily by
refresh_artifact_data.py) and Pitcher Canyon (doc rain/pitcher) from its database.
"""
import json, os, re, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

def fetch(sid, elems):
    req = urllib.request.Request("https://data.rcc-acis.org/StnData", method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"sid": sid, "sdate": "1995-07-01", "edate": "2025-12-31", "elems": [{"name": e} for e in elems]}).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["data"]

def val(x):
    if x in ("M", "S", ""): return None
    if x == "T": return 0.0
    return float(x.rstrip("A"))

town, pang = fetch("USC00459074", ["pcpn", "snow"]), fetch("USW00094239", ["pcpn"])
pmap = {r[0]: val(r[1]) for r in pang}
hist, hist_snow = [], []
for d, x, sn in town:
    v = val(x)
    if v is None: v = pmap.get(d)
    hist.append("" if v is None else str(round(v * 100)))
    v = val(sn)  # snowfall: town only, Pangborn doesn't measure it
    hist_snow.append("" if v is None else str(round(v * 10)))
assert town[0][0] == "1995-07-01" and len(hist) == 11142, (town[0], len(hist))

s = open(os.path.join(HERE, "cartoon.html")).read()
def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)

# page contract: no doctype/html/head/body of our own; title + viewport-fit meta first
head, body = s.split("</head>", 1)
head = head[head.index("<title>"):]
body = body.replace("<body>", "", 1).replace("</body>", "").replace("</html>", "")
s = head + body
rep("<title>Rain Gauge Cartoon</title>", '<title>Wenatchee Rain Gauge</title>\n<meta name="claude:viewport-fit-sides" content="cover">')
rep("  padding: 24px 16px 40px;", "  padding-block: 24px 40px;\n  padding-left: max(16px, env(safe-area-inset-left, 0px));\n  padding-right: max(16px, env(safe-area-inset-right, 0px));")
s = s.replace("--cloud: #e8ecff; --shadow: #0c0f22; --tick: #e8ecff;", "--cloud: #e8ecff; --shadow: #0c0f22; --tick: #e8ecff; color-scheme: dark;")
rep('<script src="pitcher-canyon.js"></script>\n', "")

# data: embedded history + database docs
rep("  async function loadData() {", '''  const HIST = "%s".split(",");
  const HIST_SNOW = "%s".split(",");
  async function loadData() {
    const use = window.claude && window.claude.use;
    const db = use ? await window.claude.use("db") : null;
    if (!db) throw new Error("open this page on claude.ai to see the rain data");
    const [cur, pc] = await Promise.all([db.doc("rain/current").get(), db.doc("rain/pitcher").get()]);
    if (!cur.exists) throw new Error("this year's numbers haven't arrived yet; they come with the daily refresh");
    const c = cur.data();
    const rowsFrom = (start, vals) => vals.map((v, i) => [iso(addDays(parse(start), i)), v]);
    const primary = rowsFrom("1995-07-01", HIST.map(v => v === "" ? "M" : (Number(v) / 100).toFixed(2)))
      .map((r, i) => [r[0], r[1], HIST_SNOW[i] === "" ? "M" : (Number(HIST_SNOW[i]) / 10).toFixed(1)]);
    const current = {};
    for (const sid in c.stations) {
      const sn = c.snow && c.snow[sid] ? String(c.snow[sid]).split(",") : [];
      const sd = c.snwd && c.snwd[sid] ? String(c.snwd[sid]).split(",") : [];
      current[sid] = rowsFrom(c.start, String(c.stations[sid]).split(",")).map((r, i) => [r[0], r[1], sn[i] ?? "M", sd[i] ?? "M"]);
    }
    if (pc.exists) { window.PITCHER_CANYON = pc.data(); setupCompare(); }
    const when = new Date(c.updated);
    return { primary, backup: [], current, note: "", refreshed: when };
  }
  async function loadDataOld() {''' % (",".join(hist), ",".join(hist_snow)))
rep('''  const cmpBox = document.getElementById("compare");
  if (window.PITCHER_CANYON) {''', '''  const cmpBox = document.getElementById("compare");
  function setupCompare() {''')
rep('''    cmpBox.addEventListener("change", () => { try { localStorage.setItem("rain-gauge-compare", cmpBox.checked ? "1" : "0"); } catch (e) {} if (DATA) show(); });
  }''', '''  }
  cmpBox.addEventListener("change", () => { try { localStorage.setItem("rain-gauge-compare", cmpBox.checked ? "1" : "0"); } catch (e) {} if (DATA) show(); });''')
rep("Check the internet connection and reload.", "")
rep("To update it, export new months into the pitcher-canyon-exports folder and ask Claude to rebuild, or run build_pitcher.py.",
    "To update it, export new months from Weather Underground and ask Claude to add them.")
rep("Station records via NOAA's ACIS service (data.rcc-acis.org).",
    "Station records via NOAA's ACIS service (data.rcc-acis.org), refreshed every morning${data.refreshed ? `, last at ${data.refreshed.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}` : ''}.")
open(os.path.join(HERE, "artifact.html"), "w").write(s)
print("artifact.html", len(s))
