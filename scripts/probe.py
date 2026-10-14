"""One-off: record the live datasets' columns and a few rows, so the connector maps them correctly."""
import json, urllib.parse, urllib.request
API = "https://data.boston.gov/api/3/action/"
RES = {"legacy_2026": "1a0b420d-99f1-4887-9851-990b2a5a6e17",
       "legacy_2025": "9d7c2214-4709-478a-a2e8-fb2020a5bb94",
       "new_system": "254adca6-64ab-4c5c-9fc0-a6da622be185"}
def get(action, **params):
    url = API + action + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "boston-311-pulse/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)
import os
os.makedirs("probe", exist_ok=True)
out = {}
for name, rid in RES.items():
    try:
        d = get("datastore_search", resource_id=rid, limit=3)["result"]
        out[name] = {"total": d.get("total"), "fields": [(f["id"], f.get("type")) for f in d["fields"]],
                     "records": d["records"]}
    except Exception as e:
        out[name] = {"error": repr(e)}
try:
    q = f'SELECT MAX("open_dt") AS latest, COUNT(*) AS n FROM "{RES["legacy_2026"]}"'
    out["sql_test"] = get("datastore_search_sql", sql=q)["result"]["records"]
except Exception as e:
    out["sql_test"] = {"error": repr(e)}
json.dump(out, open("probe/schema.json", "w"), indent=1, default=str)
print(json.dumps({k: (v.get("fields") if isinstance(v, dict) else v) for k, v in out.items()}, default=str)[:3000])
