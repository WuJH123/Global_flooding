# -*- coding: utf-8 -*-
"""Stage46: GHSL multiepoch built surface, population and SMOD on exact EPSG:6933 20km cells.

M3 asset default; NOT the complete worldwide 2020 urban universe.
No tasks/EE calls unless --submit or --verify-asset. Do NOT call roads 'drainage capacity'.
Task IDs use a versioned prefix. Retried/repartitioned exports must be ID-deduplicated locally.
Requires an EE TABLE with unique grid_id, numeric grid_x/grid_y and chunk_id.
"""
from __future__ import annotations
import argparse
import re
import time

PROJECT = "eco-sector-503721-b7"
DEFAULT_ASSET = f"projects/{PROJECT}/assets/groundsource_merit_m3_grids"
GRID_M = 20000
EPOCHS = (2000, 2005, 2010, 2015, 2020)
SMOD_URBAN_CODES = (21, 22, 23, 30)
SMOD_DENSE_CODES = (23, 30)
VERSION = "GHSL_EPOCH_V1"

def parse_chunks(spec: str) -> list[int]:
    result = set()
    for part in spec.split(","):
        token = part.strip()
        if re.fullmatch(r"\d+", token):
            result.add(int(token))
        elif re.fullmatch(r"\d+-\d+", token):
            a, b = map(int, token.split("-"))
            if a > b:
                raise ValueError(f"Reverse range: {token}")
            result.update(range(a, b + 1))
        else:
            raise ValueError(f"Invalid chunks spec: {token}")
    if not result:
        raise ValueError("Empty chunks")
    return sorted(result)

def parse_epochs(spec: str) -> list[int]:
    years = [int(x.strip()) for x in spec.split(",")]
    if not years or len(set(years)) != len(years) or any(y not in EPOCHS for y in years):
        raise ValueError(f"Use distinct observed historical epochs from {EPOCHS}; do not backfill E2025/E2030.")
    return sorted(years)

def names(years: list[int], chunk: int, part: int, subdivisions: int, test: bool):
    yy = "".join(str(y)[-2:] for y in years)
    suffix = f"Y{yy}_C{chunk:02d}_S{part:02d}of{subdivisions:02d}"
    task = f"{VERSION}_{suffix}" + ("_TEST" if test else "")
    return task, task.lower()

def expected_fields(years: list[int]) -> list[str]:
    fields = ["grid_id", "grid_x", "grid_y", "chunk_id"]
    for y in years:
        fields += [f"ghsl_{y}_{b}" for b in (
            "built_total_m2", "built_nres_m2", "built_valid_area_m2",
            "pop_total", "pop_valid_area_m2", "smod_urban_area_m2",
            "smod_dense_area_m2", "smod_land_area_m2")]
    return fields

def reconstruct(f, proj, ee):
    f = ee.Feature(f)
    x = ee.Number(f.get("grid_x")).multiply(GRID_M)
    y = ee.Number(f.get("grid_y")).multiply(GRID_M)
    geom = ee.Geometry.Rectangle([x, y, x.add(GRID_M), y.add(GRID_M)],
                                 proj, False, True)
    return ee.Feature(geom, f.toDictionary(["grid_id", "grid_x", "grid_y", "chunk_id"]))

def aggregate_to_km(image, ee, proj, source_scale=100, target_scale=1000):
    """Area-weighted footprint sum on target cells, not bilinear interpolation of counts."""
    # reduceResolution respects pixel overlap and should be QC-checked at 1km.
    return (image.reduceResolution(reducer=ee.Reducer.sum(), maxPixels=256)
            .reproject(crs=proj, scale=target_scale))

def epoch_image(year: int, ee, proj):
    built = ee.Image(f"JRC/GHSL/P2023A/GHS_BUILT_S/{year}")
    pop = ee.Image(f"JRC/GHSL/P2023A/GHS_POP/{year}")
    smod = ee.Image(f"JRC/GHSL/P2023A/GHS_SMOD_V2-0/{year}").select("smod_code")
    # 100m built_surface and population_count are cell TOTALS, not densities.
    bu = built.select("built_surface")
    nr = built.select("built_surface_nres")
    pc = pop.select("population_count")
    pix_b = ee.Image.pixelArea().reproject(crs=bu.projection())
    pix_p = ee.Image.pixelArea().reproject(crs=pc.projection())
    pix_s = ee.Image.pixelArea().reproject(crs=smod.projection())
    bvalid = pix_b.updateMask(bu.mask())
    pvalid = pix_p.updateMask(pc.mask())

    # 1km SMOD classes: exclude water(10) and nodata(-200) from valid land denominator.
    land = smod.gte(11).And(smod.lte(30))
    urban = smod.eq(21).Or(smod.eq(22)).Or(smod.eq(23)).Or(smod.eq(30))
    dense = smod.eq(23).Or(smod.eq(30))
    smod_land_m2 = pix_s.updateMask(land)
    smod_urban_m2 = pix_s.updateMask(urban)
    smod_dense_m2 = pix_s.updateMask(dense)
    prefix = f"ghsl_{year}_"
    bands = ee.Image.cat([
        aggregate_to_km(bu, ee, proj).rename(prefix+"built_total_m2"),
        aggregate_to_km(nr, ee, proj).rename(prefix+"built_nres_m2"),
        aggregate_to_km(bvalid, ee, proj).rename(prefix+"built_valid_area_m2"),
        aggregate_to_km(pc, ee, proj).rename(prefix+"pop_total"),
        aggregate_to_km(pvalid, ee, proj).rename(prefix+"pop_valid_area_m2"),
        smod_urban_m2.rename(prefix+"smod_urban_area_m2"),
        smod_dense_m2.rename(prefix+"smod_dense_area_m2"),
        smod_land_m2.rename(prefix+"smod_land_area_m2"),
    ])
    return bands

def check_asset(ee, asset: str, check_count=False):
    meta = ee.data.getAsset(asset)
    if str(meta.get("type", "")).upper() not in ("TABLE", "FEATURE_COLLECTION"):
        raise RuntimeError(f"Expected EE table, received: {meta.get('type')}")
    if check_count:
        n = ee.FeatureCollection(asset).size().getInfo()
        print("[ASSET]", asset, "rows", n)
    else:
        print("[ASSET]", asset, "type", meta.get("type"))

def task_states(ee):
    statuses = {}
    for t in ee.data.getTaskList():
        name = str(t.get("description", ""))
        if name.startswith(VERSION):
            statuses.setdefault(name, set()).add(str(t.get("state", "UNKNOWN")).upper())
    return statuses

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", default=PROJECT)
    p.add_argument("--asset", default=DEFAULT_ASSET)
    p.add_argument("--epochs", default="2000,2005,2010,2015,2020")
    p.add_argument("--chunks", default="0-5")
    p.add_argument("--subdivisions", type=int, default=1)
    p.add_argument("--tile-scale", type=int, default=8)
    p.add_argument("--max-tasks", type=int, default=1, help="0 = all planned tasks")
    p.add_argument("--test", action="store_true", help="Limit to 30 grid cells in each selected task")
    p.add_argument("--verify-asset", action="store_true")
    p.add_argument("--submit", action="store_true")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--drive-folder", default="GlobalFlood_R34_GHSL_Epochs")
    p.add_argument("--pause-seconds", type=float, default=0.5)
    args = p.parse_args()
    years = parse_epochs(args.epochs)
    chunks = parse_chunks(args.chunks)
    if args.subdivisions < 1 or not 1 <= args.tile_scale <= 16 or args.max_tasks < 0:
        raise ValueError("Invalid subdivision, tile-scale or max-tasks")
    work = [(c, sub) for c in chunks for sub in range(args.subdivisions)]
    print("[PLAN]", len(work), "tasks; epochs", years, "chunks", chunks,
          "subdivisions", args.subdivisions, "TEST", args.test)
    for c, sub in work:
        print("[PLAN]", names(years, c, sub, args.subdivisions, args.test)[0])
    if not args.submit and not args.verify_asset:
        print("[DRY RUN] No EE call or task submitted. --test --submit on one chunk first.")
        return

    import ee
    ee.Initialize(project=args.project)
    check_asset(ee, args.asset, check_count=args.verify_asset)
    if not args.submit:
        return
    raw = ee.FeatureCollection(args.asset)
    proj = ee.Projection("EPSG:6933")
    stack = ee.Image.cat([epoch_image(y, ee, proj) for y in years])
    fields = expected_fields(years)
    statuses = task_states(ee)
    started = 0
    for c, sub in work:
        name, fname = names(years, c, sub, args.subdivisions, args.test)
        states = statuses.get(name, set())
        if states & {"READY", "RUNNING", "COMPLETED", "SUCCEEDED"}:
            print("[SKIP EXISTS]", name, sorted(states))
            continue
        if "FAILED" in states and not args.retry_failed:
            print("[SKIP FAILED]", name, "Subdivide or use --retry-failed")
            continue
        fc = raw.filter(ee.Filter.eq("chunk_id", c))
        if args.subdivisions > 1:
            fc = (fc.randomColumn("split_2026", seed=2026)
                  .filter(ee.Filter.gte("split_2026", sub / args.subdivisions))
                  .filter(ee.Filter.lt("split_2026", (sub + 1) / args.subdivisions)))
        if args.test:
            fc = fc.sort("grid_id").limit(30)
        polys = fc.map(lambda f: reconstruct(f, proj, ee))
        result = stack.reduceRegions(
            collection=polys, reducer=ee.Reducer.sum(),
            scale=1000, crs=proj, tileScale=args.tile_scale)
        result = result.map(lambda f: ee.Feature(None, ee.Feature(f).toDictionary(fields)))
        task = ee.batch.Export.table.toDrive(
            collection=result, description=name,
            folder=args.drive_folder, fileNamePrefix=fname,
            fileFormat="CSV", selectors=fields)
        task.start()
        print("[STARTED]", name, "ID", task.id, "output", fname + ".csv")
        started += 1
        if args.max_tasks and started >= args.max_tasks:
            break
        if args.pause_seconds:
            time.sleep(args.pause_seconds)
    print("[DONE] submitted", started, "Tasks. QC raw counts/coverage/uniqueness before integrating.")
    print("[SCIENCE] BUILT != impervious; SMOD urban class != built surface; POP != exposure.")
    print("[SCIENCE] This export is M3 observation domain unless --asset points to a verified full universe.")

if __name__ == "__main__":
    main()
