# -*- coding: utf-8 -*-
"""Stage47: annual ERA5-Land urban-climate context in batches, no pseudo daily flood counts.

Export compact 20-km GRID x YEAR summaries. This is ERA5-Land reanalysis,
not IMERG P3H, event-day API7 or observed inundation. No absolute flood risk.
Dry run is default. 2025-only assets may contain coastal no-data; never fill 0.
"""
from __future__ import annotations
import argparse
import re
import time

PROJECT = "eco-sector-503721-b7"
DEFAULT_ASSET = f"projects/{PROJECT}/assets/groundsource_merit_m3_grids"
VERSION = "ERA5YR_V1"
GRID_M = 20000
METRICS = ("temp2m_mean_c", "annual_precip_mm", "rx1day_mm",
           "heavy20_days", "wet1_days", "sm1_mean", "valid_precip_days",
           "valid_temp_days", "valid_sm1_days", "negative_precip_days")

def parse_years(spec: str) -> list[int]:
    years = set()
    for token in spec.split(","):
        token = token.strip()
        if re.fullmatch(r"\d{4}", token):
            years.add(int(token))
        elif re.fullmatch(r"\d{4}-\d{4}", token):
            a, b = map(int, token.split("-"))
            if a > b:
                raise ValueError("Reversed year range")
            years.update(range(a, b + 1))
        else:
            raise ValueError(f"Invalid year token: {token!r}")
    if not years or min(years) < 1951 or max(years) > 2025:
        raise ValueError("Use complete years within 1951-2025; 2026 is incomplete")
    return sorted(years)

def groups(years: list[int], size: int) -> list[list[int]]:
    if size < 1 or size > 5:
        raise ValueError("--years-per-task must be 1..5 (ERA5 daily compute is expensive)")
    s = []
    for i in range(0, len(years), size):
        s.append(years[i:i+size])
    return s

def parse_chunks(spec: str) -> list[int]:
    result = set()
    for token in spec.split(","):
        token = token.strip()
        if re.fullmatch(r"\d+", token):
            result.add(int(token))
        elif re.fullmatch(r"\d+-\d+", token):
            a, b = map(int, token.split("-"))
            if a > b:
                raise ValueError("Reversed chunks")
            result.update(range(a, b+1))
        else:
            raise ValueError(f"Invalid chunk {token}")
    if not result: raise ValueError("No chunks")
    return sorted(result)

def fields(years: list[int]) -> list[str]:
    return ["grid_id", "grid_x", "grid_y", "chunk_id"] + [
        f"era5_{y}_{m}" for y in years for m in METRICS]

def name(years, c, sub, n, test):
    yy = f"{min(years)}_{max(years)}"
    ident = f"{VERSION}_{yy}_C{c:02d}_S{sub:02d}of{n:02d}"
    return ident + ("_TEST" if test else "")

def grid_geom(f, ee, proj):
    f = ee.Feature(f)
    x = ee.Number(f.get("grid_x")).multiply(GRID_M)
    y = ee.Number(f.get("grid_y")).multiply(GRID_M)
    return ee.Feature(
        ee.Geometry.Rectangle([x, y, x.add(GRID_M), y.add(GRID_M)],
                              proj, False, True),
        f.toDictionary(["grid_id", "grid_x", "grid_y", "chunk_id"]))

def year_image(year, ee):
    start = f"{year}-01-01"
    stop = f"{year+1}-01-01"
    daily = ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterDate(start, stop)
    t = daily.select("temperature_2m")
    sm = daily.select("volumetric_soil_water_layer_1")
    rain = daily.select("total_precipitation_sum").map(
        lambda img: ee.Image(img).multiply(1000.0).rename("precip_mm"))
    # Preserve negative-accumulation QC rather than silently hiding GRIB packing artefacts.
    negative = rain.map(lambda img: ee.Image(img).lt(0).rename("negative"))
    valid = rain.map(lambda img: ee.Image(img).max(0).rename("precip_clean"))
    y = f"era5_{year}_"
    return ee.Image.cat([
        t.mean().subtract(273.15).rename(y+"temp2m_mean_c"),
        valid.sum().rename(y+"annual_precip_mm"),
        valid.max().rename(y+"rx1day_mm"),
        valid.map(lambda im: ee.Image(im).gte(20)).sum().rename(y+"heavy20_days"),
        valid.map(lambda im: ee.Image(im).gte(1)).sum().rename(y+"wet1_days"),
        sm.mean().rename(y+"sm1_mean"),
        rain.count().rename(y+"valid_precip_days"),
        t.count().rename(y+"valid_temp_days"),
        sm.count().rename(y+"valid_sm1_days"),
        negative.sum().rename(y+"negative_precip_days"),
    ])

def states(ee):
    r = {}
    for t in ee.data.getTaskList():
        d = str(t.get("description", ""))
        if d.startswith(VERSION):
            r.setdefault(d, set()).add(str(t.get("state", "UNKNOWN")).upper())
    return r

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", default=PROJECT)
    p.add_argument("--asset", default=DEFAULT_ASSET)
    p.add_argument("--years", default="2018-2025")
    p.add_argument("--years-per-task", type=int, default=2)
    p.add_argument("--chunks", default="0-5")
    p.add_argument("--subdivisions", type=int, default=1)
    p.add_argument("--max-tasks", type=int, default=1)
    p.add_argument("--tile-scale", type=int, default=8)
    p.add_argument("--test", action="store_true")
    p.add_argument("--verify-asset", action="store_true")
    p.add_argument("--submit", action="store_true")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--drive-folder", default="GlobalFlood_R34_ERA5_Annual")
    p.add_argument("--pause-seconds", type=float, default=0.5)
    args = p.parse_args()
    ys = parse_years(args.years)
    blocks = groups(ys, args.years_per_task)
    chunks = parse_chunks(args.chunks)
    if args.subdivisions < 1 or args.max_tasks < 0 or not 1 <= args.tile_scale <= 16:
        raise ValueError("Invalid partitions/task/tileScale")
    tasks = [(c,s,b) for b in blocks for c in chunks for s in range(args.subdivisions)]
    print("[PLAN]", len(tasks), "tasks, grid domain", args.asset, "annual years", ys)
    for c, s, b in tasks:
        print("[PLAN]", name(b,c,s,args.subdivisions,args.test))
    if not args.submit and not args.verify_asset:
        print("[DRY RUN] No EE calls, no tasks. Inspect then --test --submit first.")
        return

    import ee
    ee.Initialize(project=args.project)
    meta = ee.data.getAsset(args.asset)
    if str(meta.get("type","")).upper() not in ("TABLE","FEATURE_COLLECTION"):
        raise RuntimeError("Asset must be a TABLE of grid_id/grid_x/grid_y/chunk_id")
    if args.verify_asset:
        print("[ASSET]", args.asset, "rows", ee.FeatureCollection(args.asset).size().getInfo())
        if not args.submit:
            return
    raw = ee.FeatureCollection(args.asset)
    proj = ee.Projection("EPSG:6933")
    remote = states(ee)
    created=0
    for c, sub, b in tasks:
        desc = name(b,c,sub,args.subdivisions,args.test)
        st = remote.get(desc,set())
        if st & {"READY","RUNNING","COMPLETED","SUCCEEDED"}:
            print("[SKIP EXISTS]", desc,sorted(st));continue
        if "FAILED" in st and not args.retry_failed:
            print("[SKIP FAILED]",desc, "Try subdividing; avoid mixing parent and child outputs");continue
        fc = raw.filter(ee.Filter.eq("chunk_id",c))
        if args.subdivisions>1:
            fc = (fc.randomColumn("era5split_2026",seed=2026)
                  .filter(ee.Filter.gte("era5split_2026",sub/args.subdivisions))
                  .filter(ee.Filter.lt("era5split_2026",(sub+1)/args.subdivisions)))
        if args.test:
            fc = fc.sort("grid_id").limit(25)
        polygons = fc.map(lambda f:grid_geom(f,ee,proj))
        img = ee.Image.cat([year_image(y,ee) for y in b])
        result = img.reduceRegions(
            collection=polygons, reducer=ee.Reducer.mean(),
            crs=proj, scale=11000, tileScale=args.tile_scale)
        cols=fields(b)
        result = result.map(lambda f:ee.Feature(None,ee.Feature(f).toDictionary(cols)))
        fname=desc.lower()
        task=ee.batch.Export.table.toDrive(
            collection=result, description=desc,
            folder=args.drive_folder,fileNamePrefix=fname,fileFormat="CSV",selectors=cols)
        task.start()
        print("[STARTED]",desc,task.id,fname+".csv")
        created+=1
        if args.max_tasks and created>=args.max_tasks:break
        if args.pause_seconds:time.sleep(args.pause_seconds)
    print("[DONE] submitted",created,"tasks.")
    print("[SCIENCE] Annual ERA5 regional weather context, not observed flood risk.")
    print("[SCIENCE] Rain/snow/soil no-data and coastal grid masks must be audited before trends.")

if __name__=="__main__":
    main()
