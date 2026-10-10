# Step 1: take the arena's scored table, keep Oxford City, drop buildings with mapped solar,
# sort the way the winning method says (points, then area, then OSM id), and add coordinates.
import sys
import pandas as pd

scratch = sys.argv[1]   # the arena scratch folder
out = sys.argv[2]       # where to write candidates.csv

scored = pd.read_parquet(scratch + "/a006r4/scored_gap.parquet")
oxford = scored[scored["la"].str.startswith("E07000178")].copy()   # E07000178 = Oxford City
print("Oxford City buildings in the arena table:", len(oxford))
print("  of which already have solar mapped in OpenStreetMap:", int(oxford["y_now"].sum()))

# Building details (coordinates, building tag, heritage tags, levels) from the arena's building file
details = pd.read_csv(scratch + "/a006/b_oxfordshire.csv",
                      usecols=["oid", "building", "amenity", "shop", "heritage", "listed", "levels", "lon", "lat", "x", "y"])
oxford = oxford.merge(details, on="oid", how="left")
print("  missing coordinates after join:", int(oxford["lat"].isna().sum()))

# Rank: points high to low, then bigger roof first, then OSM id (makes ties repeatable)
oxford = oxford.sort_values(["pts", "area", "oid"], ascending=[False, False, True])
oxford["has_mapped_solar"] = oxford["y_now"] == 1
oxford.to_csv(out, index=False)
print("written:", out)
