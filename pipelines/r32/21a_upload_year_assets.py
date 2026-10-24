"""Preview (default) or upload Standard Calendar 20b yearly request CSV assets.

Requires authenticated gcloud and Earth Engine CLIs. Never submits weather exports.
"""
from __future__ import annotations
import argparse
import csv
import shutil
import subprocess
from pathlib import Path

KEYS = {"climate_id", "grid_id", "grid_x", "grid_y", "year", "month",
        "request_chunk", "date_str", "longitude", "latitude"}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, default=Path(r"E:\Global_flooding"))
    p.add_argument("--bucket", default="gs://groundsource-climate-503721-b7")
    p.add_argument("--project", default="eco-sector-503721-b7")
    p.add_argument("--year", type=int)
    p.add_argument("--upload", action="store_true")
    a = p.parse_args()
    root = a.base / "standard_calendar_20b" / "gee_upload_standard"
    paths = sorted(root.glob("standard_requests_*.csv"))
    if a.year is not None:
        paths = [x for x in paths if x.stem == f"standard_requests_{a.year}"]
    if not paths:
        raise RuntimeError(f"No yearly request CSVs in {root}")
    total = 0
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            if not KEYS.issubset(set(rows.fieldnames or [])):
                raise ValueError(f"Missing fields in {path.name}: {sorted(KEYS - set(rows.fieldnames or []))}")
            count = 0
            seen = set()
            for rec in rows:
                cid = rec["climate_id"]
                if not cid or cid in seen:
                    raise ValueError(f"Blank / duplicate climate_id in {path.name}: {cid}")
                seen.add(cid)
                lon, lat = float(rec["longitude"]), float(rec["latitude"])
                if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                    raise ValueError(f"Invalid coordinate: {cid}")
                if str(rec["year"]) != path.stem[-4:]:
                    raise ValueError(f"Wrong year: {cid}")
                count += 1
        total += count
        year = path.stem[-4:]
        uri = f"{a.bucket.rstrip('/')}/stage32/{path.name}"
        asset = f"projects/{a.project}/assets/standard_requests_{year}"
        print(f"[CHECK] {year}: {count:,} requests → {asset}")
        if a.upload:
            for command in ("gcloud", "earthengine"):
                if not shutil.which(command):
                    raise RuntimeError(f"Executable not found: {command}")
            subprocess.run(["gcloud", "storage", "cp", str(path), uri], check=True)
            subprocess.run(["earthengine", "upload", "table",
                            f"--asset_id={asset}", uri], check=True)
    print(f"[DONE] {len(paths)} years, {total:,} rows; upload={a.upload}")
    if a.upload:
        print("WAIT for ingestion to complete. Check asset row counts before weather submission.")
    else:
        print("Preview only; use --upload explicitly.")

if __name__ == "__main__":
    main()
