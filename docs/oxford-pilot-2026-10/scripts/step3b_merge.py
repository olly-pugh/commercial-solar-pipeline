# Step 3b: join the outputs of steps 1, 2 and 3 into one table, one row per building.
# (This was run inline on 7 October 2026; it is saved here so every step is on record.)
import pandas as pd

candidates = pd.read_csv("candidates.csv")   # step 1
names = pd.read_csv("osm_names.csv")         # step 2
flags = pd.read_csv("flags.csv")             # step 3

merged = candidates.merge(names, on="oid").merge(flags, on="oid")
merged.to_csv("merged.csv", index=False)
print("merged rows:", len(merged))
