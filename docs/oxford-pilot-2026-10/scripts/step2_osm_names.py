# Step 2: read the OpenStreetMap extract and collect, for each candidate building:
#   - its own name / operator / address tags
#   - the name of any named site area (business park, hospital, college...) that contains it
#   - a fresh recheck for solar objects on or inside the building outline
# Everything stays in longitude/latitude, so no map projection is needed.
import sys
import osmium
import pandas as pd
import shapely
from shapely import wkb as shp_wkb

pbf, cand_csv, out_csv = sys.argv[1], sys.argv[2], sys.argv[3]

cand = pd.read_csv(cand_csv)
wanted_ways = set(int(o[1:]) for o in cand["oid"] if o.startswith("w"))
wanted_rels = set(int(o[1:]) for o in cand["oid"] if o.startswith("r"))

KEEP = ["name", "operator", "brand", "addr:housenumber", "addr:housename", "addr:street", "addr:postcode", "website"]
SITE_KEYS = ["amenity", "landuse", "industrial", "office", "healthcare", "leisure", "shop", "man_made"]

own_tags = {}      # candidate oid -> its tags
own_geom = {}      # candidate oid -> outline (lon/lat)
sites = []         # (name, kind, outline) for named non-building areas
solar_pts = []     # (lon, lat) of solar PV objects

def is_pv(t):
    solar = t.get("generator:source") == "solar" or t.get("plant:source") == "solar"
    thermal = t.get("generator:method") == "thermal" or t.get("generator:type") == "solar_thermal_collector"
    return solar and not thermal

class Handler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.fab = osmium.geom.WKBFactory()

    def node(self, n):
        if is_pv(n.tags):
            solar_pts.append((n.location.lon, n.location.lat))

    def area(self, a):
        t = a.tags
        from_way = a.from_way()
        oid = ("w%d" if from_way else "r%d") % a.orig_id()
        cand_hit = (from_way and a.orig_id() in wanted_ways) or (not from_way and a.orig_id() in wanted_rels)
        site_hit = ("name" in t) and ("building" not in t) and any(k in t for k in SITE_KEYS)
        pv_hit = is_pv(t)
        if not (cand_hit or site_hit or pv_hit):
            return
        try:
            g = shp_wkb.loads(self.fab.create_multipolygon(a), hex=True)
        except Exception:
            return
        if cand_hit:
            own_tags[oid] = {k: t.get(k, "") for k in KEEP}
            own_geom[oid] = g
        if site_hit:
            kind = next(k + "=" + t.get(k) for k in SITE_KEYS if k in t)
            sites.append((t.get("name"), kind, g))
        if pv_hit:
            p = g.representative_point()
            solar_pts.append((p.x, p.y))

Handler().apply_file(pbf, locations=True, idx="flex_mem")
print("candidate outlines found:", len(own_geom), "of", len(cand))
print("named site areas:", len(sites), "| solar PV objects in extract:", len(solar_pts))

# Fresh solar recheck: any PV object inside the outline grown by ~2 m
pts = shapely.points([p[0] for p in solar_pts], [p[1] for p in solar_pts])
tree = shapely.STRtree(pts)
# Which named site contains each building (smallest containing site wins)
site_tree = shapely.STRtree([s[2] for s in sites])

rows = []
for oid in cand["oid"]:
    tags = own_tags.get(oid, {k: "" for k in KEEP})
    g = own_geom.get(oid)
    solar_now = None
    site_name, site_kind = "", ""
    if g is not None:
        hits = tree.query(g.buffer(0.00003), predicate="intersects")
        solar_now = len(hits) > 0
        rp = g.representative_point()
        best = None
        for i in site_tree.query(rp, predicate="within"):
            if best is None or sites[i][2].area < sites[best][2].area:
                best = i
        if best is not None:
            site_name, site_kind = sites[best][0], sites[best][1]
    addr = " ".join(x for x in [tags["addr:housenumber"] or tags["addr:housename"], tags["addr:street"], tags["addr:postcode"]] if x)
    rows.append({"oid": oid, "osm_name": tags["name"], "osm_operator": tags["operator"] or tags["brand"],
                 "osm_address": addr, "osm_website": tags["website"],
                 "site_name": site_name, "site_kind": site_kind, "solar_recheck": solar_now})

pd.DataFrame(rows).to_csv(out_csv, index=False)
print("written:", out_csv)
