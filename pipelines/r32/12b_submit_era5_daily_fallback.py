#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Efficient optional ERA5-Land coastal fallback: DAILY_AGGR + one spatial
buffer per pass + quarter-wise Earth Engine batch exports.

Use only AFTER global QC, and after uploading:
  E:\\Global_flooding\\climate_global\\era5_fallback_requests.csv
as EE table:
  projects/eco-sector-503721-b7/assets/era5_fallback_requests

Dry run (no exports):
  python 12b_submit_era5_daily_fallback.py
Start ONE pilot batch:
  python 12b_submit_era5_daily_fallback.py --submit --max-tasks 1
Start remaining batches after inspecting the pilot:
  python 12b_submit_era5_daily_fallback.py --submit

Use --buffer-km 30 only AFTER producing a new request asset containing
unresolved rows from the +15 km stage; provide --asset to point to it.
The daily aggregate variables are not assumed identical to hourly sums:
validate overlapping samples and report this as a sensitivity processing choice.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import ee
import pandas as pd

PROJECT = "eco-sector-503721-b7"
ROOT = Path(r"E:\Global_flooding")
MANIFEST = ROOT / "climate_global" / "era5_fallback_manifest.csv"
ASSET_DEFAULT = f"projects/{PROJECT}/assets/era5_fallback_requests"

EASE6933_WKT = (
    'PROJCS["WGS 84 / NSIDC EASE-Grid 2.0 Global",'
    'GEOGCS["WGS 84",'
    'DATUM["WGS_1984",'
    'SPHEROID["WGS 84",6378137,298.257223563,'
    'AUTHORITY["EPSG","7030"]],'
    'AUTHORITY["EPSG","6326"]],'
    'PRIMEM["Greenwich",0,'
    'AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.0174532925199433,'
    'AUTHORITY["EPSG","9122"]],'
    'AUTHORITY["EPSG","4326"]],'
    'PROJECTION["Cylindrical_Equal_Area"],'
    'PARAMETER["standard_parallel_1",30],'
    'PARAMETER["central_meridian",0],'
    'PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",0],'
    'UNIT["metre",1,'
    'AUTHORITY["EPSG","9001"]],'
    'AXIS["Easting",EAST],'
    'AXIS["Northing",NORTH],'
    'AUTHORITY["EPSG","6933"]]'
)

FIELDS = [
    "era5_event_p24_mm", "era5_event_runoff24_mm", "era5_event_surface_runoff24_mm",
    "era5_ant_p3d_mm", "era5_ant_p7d_mm", "era5_ant_runoff3d_mm",
    "era5_ant_runoff7d_mm", "era5_api3_mm", "era5_api7_mm",
    "era5_sm1_prevday", "era5_sm2_prevday"
]
OUTPUT_FIELDS = ["climate_id", "grid_id", "grid_x", "grid_y", "date_str",
                 "year", "month", "day", "fallback_level_candidate", "fallback_expansion_km"]
for f in FIELDS:
    OUTPUT_FIELDS.extend([f + "_mean", f + "_max"])


def app():
    parser = argparse.ArgumentParser()
    parser.add_argument("--submit", action="store_true", help="Actually call task.start(); otherwise dry run")
    parser.add_argument("--max-tasks", type=int, default=0, help="Submit at most N new tasks (0 means all)")
    parser.add_argument("--buffer-km", type=int, default=15, choices=[15, 30])
    parser.add_argument("--asset", default=ASSET_DEFAULT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--only-year", type=int, default=None)
    parser.add_argument("--only-quarter", type=int, choices=[1,2,3,4], default=None)
    args = parser.parse_args()
    if not args.manifest.exists():
        raise FileNotFoundError(args.manifest)
    manifest = pd.read_csv(args.manifest)
    needed = {"year", "month", "requests"}
    if not needed.issubset(manifest.columns):
        raise RuntimeError(f"Missing manifest columns: {needed - set(manifest.columns)}")
    manifest["year"] = manifest.year.astype(int)
    manifest["month"] = manifest.month.astype(int)
    manifest["quarter"] = (manifest.month - 1) // 3 + 1
    tasks = manifest.groupby(["year", "quarter"], as_index=False).requests.sum()
    if args.only_year is not None:
        tasks = tasks[tasks.year == args.only_year]
    if args.only_quarter is not None:
        tasks = tasks[tasks.quarter == args.only_quarter]
    tasks = tasks.sort_values(["year", "quarter"])
    print(f"[PLAN] {len(tasks)} nonempty quarterly jobs, {tasks.requests.sum():,} requests, buffer={args.buffer_km} km")
    if tasks.empty:
        return
    if not args.submit:
        print("DRY RUN: no GEE work started. To submit one pilot, add: --submit --max-tasks 1")
        print(tasks.to_string(index=False))
        return
    ee.Initialize(project=PROJECT)
    ee.data.getAsset(args.asset)  # fails clearly if asset not fully ingested
    existing = {}
    for t in ee.data.getTaskList():
        n = t.get("description")
        if n:
            existing.setdefault(n, []).append(t.get("state", "UNKNOWN"))
    daily = ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
    proj = ee.Projection(EASE6933_WKT)
    rec = ee.Reducer.mean().combine(reducer2=ee.Reducer.max(), sharedInputs=True)
    raw = ee.FeatureCollection(args.asset)
    buffer_m = args.buffer_km * 1000
    level = 1 if args.buffer_km == 15 else 2

    def geom_one(f):
        f = ee.Feature(f)
        x = ee.Number(f.get("grid_x")).multiply(20000)
        y = ee.Number(f.get("grid_y")).multiply(20000)
        g = ee.Geometry.Rectangle(ee.List([
            x.subtract(buffer_m), y.subtract(buffer_m),
            x.add(20000 + buffer_m), y.add(20000 + buffer_m)
        ]), proj, False, True)
        return ee.Feature(g, f.toDictionary()).set({
            "fallback_level_candidate": level, "fallback_expansion_km": args.buffer_km})

    def daily_flow(d, days, band, name):
        d = ee.Date(d)
        return (daily.filterDate(d.advance(-days, "day"), d)
                    .select(band).map(lambda im: ee.Image(im).max(0))
                    .sum().multiply(1000).rename(name))

    def current_flow(d, band, name):
        d = ee.Date(d)
        return (ee.Image(daily.filterDate(d, d.advance(1, "day")).select(band).first())
                .max(0).multiply(1000).rename(name))

    def api(d, nd, name):
        d = ee.Date(d)
        imlist = ee.List.sequence(1, nd).map(lambda k: (
            ee.Image(daily.filterDate(
                d.advance(ee.Number(k).multiply(-1), "day"),
                d.advance(ee.Number(k).multiply(-1).add(1), "day"))
                .select("total_precipitation_sum").first())
            .max(0).multiply(1000)
            .multiply(ee.Number(0.85).pow(ee.Number(k).subtract(1))).rename(name)))
        return ee.ImageCollection.fromImages(imlist).sum().rename(name)

    def metrics(d):
        d = ee.Date(d)
        prev = daily.filterDate(d.advance(-1, "day"), d)
        return ee.Image.cat([
            current_flow(d, "total_precipitation_sum", "era5_event_p24_mm"),
            current_flow(d, "runoff_sum", "era5_event_runoff24_mm"),
            current_flow(d, "surface_runoff_sum", "era5_event_surface_runoff24_mm"),
            daily_flow(d, 3, "total_precipitation_sum", "era5_ant_p3d_mm"),
            daily_flow(d, 7, "total_precipitation_sum", "era5_ant_p7d_mm"),
            daily_flow(d, 3, "runoff_sum", "era5_ant_runoff3d_mm"),
            daily_flow(d, 7, "runoff_sum", "era5_ant_runoff7d_mm"),
            api(d, 3, "era5_api3_mm"),
            api(d, 7, "era5_api7_mm"),
            prev.select("volumetric_soil_water_layer_1").mean().rename("era5_sm1_prevday"),
            prev.select("volumetric_soil_water_layer_2").mean().rename("era5_sm2_prevday")
        ])

    def process_chunk(year, quarter):
        low = (quarter - 1) * 3 + 1
        high = low + 3
        subset = raw.filter(ee.Filter.eq("year", int(year))).filter(
            ee.Filter.gte("month", low)).filter(ee.Filter.lt("month", high))
        days = ee.List(subset.aggregate_array("date_str")).distinct().sort()
        def extract_one(s):
            s = ee.String(s)
            d = ee.Date.parse("YYYY-MM-dd", s)
            features = subset.filter(ee.Filter.eq("date_str", s)).map(geom_one)
            im = metrics(d)
            result = im.reduceRegions(collection=features, reducer=rec,
                                      scale=11132, crs=im.select(0).projection(), tileScale=4)
            return result.map(lambda f: ee.Feature(None, ee.Feature(f).toDictionary(OUTPUT_FIELDS)))
        return ee.FeatureCollection(days.map(extract_one)).flatten()

    submitted = 0
    for row in tasks.itertuples(index=False):
        year, q = int(row.year), int(row.quarter)
        desc = f"ERA5FB_DAILY_B{args.buffer_km}_{year}_Q{q}"
        states = existing.get(desc, [])
        if any(s in {"READY", "RUNNING", "COMPLETED"} for s in states):
            print(f"[SKIP] {desc} existing states={states}")
            continue
        if "FAILED" in states:
            print(f"[FAILED] {desc} previously failed; inspect before resubmitting")
            continue
        result = process_chunk(year, q)
        task = ee.batch.Export.table.toDrive(
            collection=result, description=desc,
            folder="Groundsource_ERA5_Fallback_Daily",
            fileNamePrefix=f"era5fb_daily_b{args.buffer_km}_{year}_q{q}",
            fileFormat="CSV", selectors=OUTPUT_FIELDS)
        task.start()
        submitted += 1
        print(f"[STARTED] {desc}: {int(row.requests):,} requests, task ID={task.id}")
        if args.max_tasks and submitted >= args.max_tasks:
            break
        time.sleep(0.5)
    print(f"Submission done: {submitted} new tasks. Batch continues on server.")


if __name__ == "__main__":
    app()
