# Step 3: two caveat flags (they never change the rank):
#   (a) heritage: listed-building or heritage tags in OpenStreetMap on the building itself or on
#       a heritage object inside it, plus OSM conservation-area polygons (OSM coverage is incomplete)
#   (b) REPD: registered rooftop solar projects (150 kW+) near the building
import sys, csv, math
import osmium
import pandas as pd
import shapely
from shapely import wkb as shp_wkb
from pyproj import Transformer

pbf, cand_csv, repd_csv, out_csv = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
cand = pd.read_csv(cand_csv)
wanted = set(cand["oid"])

geoms = {}         # candidate oid -> outline
own_listed = {}    # candidate oid -> its own listed / heritage tag text
listed_pts = []    # (lon, lat, label) for separate listed objects
conserv = []       # (name, outline) conservation areas mapped in OSM

def listed_label(t):
    for k in ["listed_status", "HE_ref", "heritage", "historic:listed"]:
        if k in t:
            return "%s=%s" % (k, t.get(k))
    return ""

class H(osmium.SimpleHandler):
    def __init__(self):
        super().__init__(); self.fab = osmium.geom.WKBFactory()
    def node(self, n):
        lab = listed_label(n.tags)
        if lab:
            listed_pts.append((n.location.lon, n.location.lat, lab))
    def area(self, a):
        t = a.tags
        oid = ("w%d" if a.from_way() else "r%d") % a.orig_id()
        is_cand = oid in wanted
        lab = listed_label(t)
        title = (t.get("protection_title", "") + " " + t.get("designation", "")).lower()
        is_ca = "conservation" in title
        if not (is_cand or lab or is_ca):
            return
        try:
            g = shp_wkb.loads(self.fab.create_multipolygon(a), hex=True)
        except Exception:
            return
        if is_cand:
            geoms[oid] = g
            if lab:
                own_listed[oid] = lab
        elif lab:
            p = g.representative_point(); listed_pts.append((p.x, p.y, lab))
        if is_ca:
            conserv.append((t.get("name", "conservation area"), g))

H().apply_file(pbf, locations=True, idx="flex_mem")
print("listed/heritage objects:", len(listed_pts), "| OSM conservation areas:", len(conserv),
      "| candidates with own heritage tag:", len(own_listed))

lp_tree = shapely.STRtree(shapely.points([p[0] for p in listed_pts], [p[1] for p in listed_pts]))
ca_tree = shapely.STRtree([c[1] for c in conserv]) if conserv else None

# REPD: rooftop solar in Oxfordshire, any status; British National Grid -> lon/lat
to_ll = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
repd = []
with open(repd_csv, encoding="latin-1") as f:
    for r in csv.DictReader(f):
        if r.get("Technology Type", "").strip() != "Solar Photovoltaics":
            continue
        if r.get("Mounting Type for Solar", "").strip().lower() != "roof":
            continue
        if "oxford" not in (r.get("County", "") + r.get("Planning Authority", "")).lower():
            continue
        try:
            lon, lat = to_ll.transform(float(r["X-coordinate"]), float(r["Y-coordinate"]))
        except Exception:
            continue
        repd.append((lon, lat, "%s | %s MW | %s" % (r["Site Name"].strip(), r["Installed Capacity (MWelec)"].strip(), r["Development Status (short)"].strip())))
print("REPD rooftop solar records in Oxfordshire:", len(repd))
for x in repd:
    print("   ", x[2])
repd_tree = shapely.STRtree(shapely.points([p[0] for p in repd], [p[1] for p in repd])) if repd else None

rows = []
for oid in cand["oid"]:
    g = geoms.get(oid)
    her, ca, rp = own_listed.get(oid, ""), "", ""
    if g is not None:
        if not her:
            hits = lp_tree.query(g.buffer(0.00002), predicate="intersects")
            if len(hits):
                her = "listed object inside: " + listed_pts[hits[0]][2]
        if ca_tree is not None:
            hits = ca_tree.query(g.representative_point(), predicate="within")
            if len(hits):
                ca = conserv[hits[0]][0]
        if repd_tree is not None:
            # every REPD rooftop record within ~400 m, with its distance (REPD points are approximate)
            c = g.representative_point()
            hits = repd_tree.query(c.buffer(0.006), predicate="intersects")
            found = []
            for i in hits:
                dx = (repd[i][0] - c.x) * math.cos(math.radians(c.y)) * 111320
                dy = (repd[i][1] - c.y) * 110540
                d = math.hypot(dx, dy)
                if d <= 400:
                    found.append((d, repd[i][2]))
            rp = " || ".join("%s [%d m]" % (txt, d) for d, txt in sorted(found))
    rows.append({"oid": oid, "heritage_osm": her, "conservation_area_osm": ca, "repd_rooftop_nearby": rp})
pd.DataFrame(rows).to_csv(out_csv, index=False)
print("written:", out_csv)
