# Step 4: turn buildings into SITES (one lead = one decision-maker), apply exclusions, and rank.
# Inputs: merged.csv from steps 1-3. Output: sites.csv (one row per site) and buildings_final.csv.
import re
import pandas as pd

b = pd.read_csv("merged.csv")
b = b.fillna({"osm_name": "", "osm_operator": "", "osm_address": "", "site_name": "", "site_kind": "",
              "repd_rooftop_nearby": "", "heritage_osm": ""})
b["solar_now"] = b["has_mapped_solar"] | (b["solar_recheck"] == True)

# --- 1. Which named areas count as ONE site (one owner)? -------------------------------------
# Schools, hospitals, universities, colleges, sports centres: yes.
# Named landuse areas: yes, unless the name says it holds many occupiers (business park, estate...).
MULTI = ["park", "estate", "centre", "center", "court", "quarter", "north", "trading", "business", "science", "retail"]
def groups_as_one(name, kind):
    if not name:
        return False
    if kind.split("=")[0] in ("amenity", "healthcare", "leisure"):
        return True
    if kind.startswith("landuse="):
        return not any(w in name.lower() for w in MULTI)
    return False

b["site_key"] = [("site:" + n) if groups_as_one(n, k) else ("bld:" + o)
                 for n, k, o in zip(b["site_name"], b["site_kind"], b["oid"])]

# --- 2. Registered rooftop solar (REPD): same site only if the names share a real word ---------
STOP = set("""road way lane drive street solar panels panel photovoltaic system the and ltd limited uk
              oxford building buildings units unit hall house centre center park of for pv
              school college academy university hospital sports sport leisure""".split())
ALIAS = {"bmw": "mini"}   # BMW's REPD record is the Mini Plant Oxford site
def words(s):
    raw = re.findall(r"[a-z]+", s.lower())
    w = set(x for x in raw if len(x) >= 4 and x not in STOP)
    return w | set(ALIAS[x] for x in raw if x in ALIAS)
def repd_records(row):
    # split "name | MW | status [123 m] || name | ..." into (text, distance) pairs
    out = []
    for part in [x for x in row["repd_rooftop_nearby"].split(" || ") if x]:
        d = int(re.search(r"\[(\d+) m\]$", part).group(1))
        out.append((part, d))
    return out

def repd_org(text):
    # "David Lloyd Leisure, John Smith Drive - Solar Panels | 0.38 MW | ..." -> "David Lloyd Leisure"
    name = text.split("|")[0]
    return re.split(r",| - ", name)[0]

def repd_name_match(row, text, dist):
    org = words(repd_org(text))
    if row["site_key"].startswith("site:"):          # one-owner site: the site name counts
        here = words(" ".join([row["osm_name"], row["osm_operator"], row["site_name"]]))
    else:                                            # estate with many occupiers: own name only
        here = words(" ".join([row["osm_name"], row["osm_operator"]]))
    shared = org & here
    if not shared:
        return False
    if any(a in shared for a in ALIAS.values()):     # e.g. BMW -> Mini
        return True
    if len(shared) >= min(2, len(org)):              # all of a one-word name, or two words
        return True
    return dist <= 150                               # one shared word only counts when very close

def repd_same_site(row):
    for text, d in repd_records(row):
        if repd_name_match(row, text, d):
            return text
    return ""

def repd_nearby_only(row):
    for text, d in repd_records(row):
        if d <= 150 and not repd_name_match(row, text, d):
            return text
    return ""

b["repd_same_site"] = b.apply(repd_same_site, axis=1)
b["repd_nearby_only"] = b.apply(repd_nearby_only, axis=1)

# --- 3. Build the site table -------------------------------------------------------------------
def best_name(g):
    top = g.iloc[0]
    if top["site_key"].startswith("site:"):
        return top["site_name"]
    for col in ["osm_name", "osm_operator", "site_name"]:
        if top[col]:
            return top[col] + ("" if col != "site_name" else " (unit on site)")
    return "Unnamed " + top["sector"].replace("_", " ")

rows = []
for key, g in b.groupby("site_key", sort=False):
    live = g[~g["solar_now"]].sort_values(["pts", "area", "oid"], ascending=[False, False, True])
    if live.empty:
        continue                                   # every building here already has panels mapped
    top = live.iloc[0]
    repd = g[g["repd_same_site"] != ""]["repd_same_site"]
    near = g[g["repd_nearby_only"] != ""]["repd_nearby_only"]
    rows.append({
        "site_key": key,
        "site": best_name(live),
        "sector": top["sector"],
        "buildings": len(live),
        "points_best": int(top["pts"]),
        "largest_roof_m2": round(live["area"].max()),
        "total_roof_m2": round(live["area"].sum()),
        "site_gap_m": round(top["gap_m"], 1),
        "address_osm": next((a for a in live["osm_address"] if a), ""),
        "lat": top["lat"], "lon": top["lon"],
        "lead_oid": top["oid"],
        "lead_building_name": top["osm_name"],
        "pv_elsewhere_on_site": int(g["solar_now"].sum()),
        "repd_same_site": repd.iloc[0] if len(repd) else "",
        "repd_nearby": near.iloc[0] if len(near) else "",
        "heritage_osm": next((h for h in live["heritage_osm"] if h), ""),
    })

s = pd.DataFrame(rows)
# Rank sites the way the method ranks buildings: best points, then bigger roof, then id
s = s.sort_values(["points_best", "largest_roof_m2", "lead_oid"], ascending=[False, False, True]).reset_index(drop=True)
s.to_csv("sites.csv", index=False)
b.to_csv("buildings_final.csv", index=False)

print("buildings:", len(b), "| with panels mapped:", int(b.solar_now.sum()))
print("sites with at least one building without mapped panels:", len(s))
print("sites already in the REPD solar pipeline (same site):", int((s.repd_same_site != "").sum()))
print(s[s.repd_same_site != ""][["site", "points_best", "largest_roof_m2", "repd_same_site"]].to_string(index=False))
