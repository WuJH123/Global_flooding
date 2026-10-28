# -*- coding: utf-8 -*-
"""Stage50: IMERG V07 yearly UTC-day extremes for 20km urban grid TABLE.

IMERG 30-minute precipitation is mm/hour, converted to 0.5-hour totals.
Exclude a daily value at a pixel unless 48 half-hourly samples are valid.
This computes UTC daily maximum and 30-minute max, NOT rolling 3h max.
Dry-run by default. For fewer GEE tasks prefer Stage53 raster export pilots.
"""
from __future__ import annotations
import argparse
import re
from ee_ease_projection import probe_projection, rectangular_grid

PROJECT="eco-sector-503721-b7"
ASSET=f"projects/{PROJECT}/assets/urban_universe_smod2020_20km_v1"
COLLECTION="NASA/GPM_L3/IMERG_V07"
VERSION="IMERGANN_V2"
METRICS=("total_mm","rx1day_mm","heavy20_days","wet1_days","rx30min_mm","valid_days")

def parse_range(s,lo,hi):
    out=set()
    for part in s.split(","):
        part=part.strip()
        if re.fullmatch(r"\d+",part):out.add(int(part))
        elif re.fullmatch(r"\d+-\d+",part):
            a,b=map(int,part.split("-"))
            if a>b:raise ValueError("Reversed range")
            out.update(range(a,b+1))
        else:raise ValueError(f"Bad range component: {part}")
    if not out or min(out)<lo or max(out)>hi:raise ValueError(f"Expected range [{lo},{hi}]")
    return sorted(out)

def annual(year,ee):
    collection=(ee.ImageCollection(COLLECTION)
        .filterDate(f"{year}-01-01",f"{year+1}-01-01").select("precipitation"))
    start=ee.Date(f"{year}-01-01")
    days=ee.Date(f"{year+1}-01-01").difference(start,"day")
    indices=ee.List.sequence(0,days.subtract(1))
    def utc_day(index):
        begin=start.advance(ee.Number(index),"day")
        frames=collection.filterDate(begin,begin.advance(1,"day"))
        count=frames.count()
        rain=frames.sum().multiply(0.5).rename("daily_mm")
        return rain.updateMask(count.eq(48)).set("system:time_start",begin.millis())
    daily=ee.ImageCollection.fromImages(indices.map(utc_day))
    prefix=f"imerg_{year}_"
    return ee.Image.cat([
        daily.sum().rename(prefix+"total_mm"),
        daily.max().rename(prefix+"rx1day_mm"),
        daily.map(lambda im:ee.Image(im).gte(20)).sum().rename(prefix+"heavy20_days"),
        daily.map(lambda im:ee.Image(im).gte(1)).sum().rename(prefix+"wet1_days"),
        collection.max().multiply(.5).rename(prefix+"rx30min_mm"),
        daily.count().rename(prefix+"valid_days")
    ])

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project",default=PROJECT)
    p.add_argument("--asset",default=ASSET)
    p.add_argument("--years",default="2020")
    p.add_argument("--chunks",default="0")
    p.add_argument("--max-tasks",type=int,default=1)
    p.add_argument("--tile-scale",type=int,default=8)
    p.add_argument("--test",action="store_true")
    p.add_argument("--verify-asset",action="store_true")
    p.add_argument("--probe-projection",action="store_true")
    p.add_argument("--submit",action="store_true")
    p.add_argument("--drive-folder",default="GlobalFlood_R34_IMERG_Annual")
    a=p.parse_args()
    years=parse_range(a.years,2000,2025);chunks=parse_range(a.chunks,0,999)
    if a.max_tasks<0 or not 1<=a.tile_scale<=16:raise ValueError("Bad task/tileScale parameter")
    def name(y,c):return f"{VERSION}_Y{y}_C{c:02d}"+("_TEST" if a.test else "")
    print("[PLAN]",len(years)*len(chunks),"tasks, domain",a.asset)
    for y in years:
        for c in chunks:print("[PLAN]",name(y,c))
    if not (a.submit or a.verify_asset or a.probe_projection):
        print("[DRY RUN] no EE calls");return
    import ee
    ee.Initialize(project=a.project)
    ee.data.getAsset(a.asset)
    grid=ee.FeatureCollection(a.asset)
    if a.verify_asset:print("[ASSET]",grid.size().getInfo())
    if not (a.submit or a.probe_projection):return
    proj=probe_projection(ee)
    if not a.submit:return
    recent={str(t.get("description")):str(t.get("state")) for t in ee.data.getTaskList()}
    started=0
    for year in years:
        image=annual(year,ee)
        cols=["grid_id","grid_x","grid_y","chunk_id"]+[f"imerg_{year}_{x}" for x in METRICS]
        for c in chunks:
            desc=name(year,c)
            if desc in recent:print("[SKIP exists]",desc,recent[desc]);continue
            fc=grid.filter(ee.Filter.eq("chunk_id",c))
            if a.test:fc=fc.sort("grid_id").limit(10)
            def polygon(f):
                f=ee.Feature(f)
                return ee.Feature(rectangular_grid(ee,f.get("grid_x"),f.get("grid_y"),proj),
                    f.toDictionary(["grid_id","grid_x","grid_y","chunk_id"]))
            reduced=(image.reduceRegions(collection=fc.map(polygon),reducer=ee.Reducer.mean(),
                crs=proj,scale=11000,tileScale=a.tile_scale)
                .map(lambda f:ee.Feature(None,ee.Feature(f).toDictionary(cols))))
            t=ee.batch.Export.table.toDrive(collection=reduced,description=desc,
                fileNamePrefix=desc.lower(),folder=a.drive_folder,fileFormat="CSV",selectors=cols)
            t.start();print("[STARTED]",desc,t.id);started+=1
            if a.max_tasks and started>=a.max_tasks:break
        if a.max_tasks and started>=a.max_tasks:break
    print("[DONE] submitted",started,"tasks; numerical source parity is still required")
if __name__=="__main__":main()
