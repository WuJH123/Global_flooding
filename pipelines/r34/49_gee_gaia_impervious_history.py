# -*- coding: utf-8 -*-
"""Stage49: optional GAIA 1985-2018 impervious-class footprint for M3 20-km grids.

GAIA is a *binary threshold* (>50% impervious) classification at 30m,
NOT exact impervious area fraction in mixed pixels; index year = 2019-index.
No historical GAIA beyond 2018, no road pipes and no storm sewer capacity.
Default dry-run; submission requires explicit --submit. Source area is all
20km cell area; coastal land-only denominator is NOT inferred here.
"""
from __future__ import annotations
import argparse
import re
import time
PROJECT="eco-sector-503721-b7"
ASSET=f"projects/{PROJECT}/assets/groundsource_merit_m3_grids"
VERSION="GAIA_IMP_V1"
GRID_M=20000
EPOCHS=(2000,2005,2010,2015,2018)

def epochs(s):
    a=[int(i.strip()) for i in s.split(",")]
    if not a or len(set(a))!=len(a) or any(x not in EPOCHS for x in a):
        raise ValueError("Only distinct GAIA historical epochs 2000,2005,2010,2015,2018")
    return sorted(a)

def chunks(s):
    out=set()
    for x in s.split(","):
        x=x.strip()
        if re.fullmatch(r"\d+",x):out.add(int(x))
        elif re.fullmatch(r"\d+-\d+",x):
            lo,hi=map(int,x.split("-"))
            if lo>hi:raise ValueError("Reversed chunks")
            out.update(range(lo,hi+1))
        else:raise ValueError("Bad chunk spec "+x)
    if not out:raise ValueError("Empty chunks")
    return sorted(out)

def taskname(epochs_,chunk,part,n,test):
    s="".join(str(x)[-2:] for x in epochs_)
    return f"{VERSION}_Y{s}_C{chunk:02d}_S{part:02d}of{n:02d}"+("_TEST" if test else "")

def feature_polygon(f,ee,proj):
    f=ee.Feature(f)
    x=ee.Number(f.get("grid_x")).multiply(GRID_M)
    y=ee.Number(f.get("grid_y")).multiply(GRID_M)
    return ee.Feature(
        ee.Geometry.Rectangle([x,y,x.add(GRID_M),y.add(GRID_M)],proj,False,True),
        f.toDictionary(["grid_id","grid_x","grid_y","chunk_id"]))

def stack_for(years,ee,proj):
    gaia=ee.Image("Tsinghua/FROM-GLC/GAIA/v10").select("change_year_index")
    # 0/masked = NOT mapped impervious transition; no independent data coverage claim.
    gaia=gaia.unmask(0)
    native_area=ee.Image.pixelArea().reproject(crs=gaia.projection())
    bands=[]
    for y in years:
        # Index 1=2018; 19=2000; 34=1985. Smaller index means more recent.
        yr_idx=2019-y
        imp=gaia.gte(yr_idx).And(gaia.lte(34))
        footprint=native_area.multiply(imp)
        km500=(footprint.reduceResolution(
            reducer=ee.Reducer.sum(),maxPixels=512)
            .reproject(crs=proj,scale=500))
        bands.append(km500.rename(f"gaia_{y}_impervious_class_area_m2"))
    return ee.Image.cat(bands)

def remote(ee):
    r={}
    for t in ee.data.getTaskList():
        n=str(t.get("description",""))
        if n.startswith(VERSION):
            r.setdefault(n,set()).add(str(t.get("state","")).upper())
    return r

def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument("--project",default=PROJECT)
    a.add_argument("--asset",default=ASSET)
    a.add_argument("--epochs",default="2000,2005,2010,2015,2018")
    a.add_argument("--chunks",default="0-5")
    a.add_argument("--subdivisions",type=int,default=1)
    a.add_argument("--tile-scale",type=int,default=8)
    a.add_argument("--max-tasks",type=int,default=1)
    a.add_argument("--verify-asset",action="store_true")
    a.add_argument("--submit",action="store_true")
    a.add_argument("--test",action="store_true")
    a.add_argument("--retry-failed",action="store_true")
    a.add_argument("--drive-folder",default="GlobalFlood_R34_GAIA_Impervious")
    args=a.parse_args()
    yy=epochs(args.epochs)
    cc=chunks(args.chunks)
    if args.subdivisions<1 or args.max_tasks<0 or not 1<=args.tile_scale<=16:
        raise ValueError("Invalid numeric parameters")
    plan=[(c,s) for c in cc for s in range(args.subdivisions)]
    print("[PLAN]",len(plan),"tasks, GAIA years",yy,"grid asset",args.asset)
    for c,s in plan:print("[PLAN]",taskname(yy,c,s,args.subdivisions,args.test))
    if not args.submit and not args.verify_asset:
        print("[DRY RUN] No EE calls or tasks")
        return
    import ee
    ee.Initialize(project=args.project)
    meta=ee.data.getAsset(args.asset)
    if str(meta.get("type","")).upper() not in ("TABLE","FEATURE_COLLECTION"):
        raise RuntimeError("Expected an EE TABLE asset with grid_id/grid_x/grid_y/chunk_id")
    if args.verify_asset:
        print("[ASSET ROWS]",ee.FeatureCollection(args.asset).size().getInfo())
        if not args.submit:return
    data=ee.FeatureCollection(args.asset)
    proj=ee.Projection("EPSG:6933")
    raster=stack_for(yy,ee,proj)
    cols=["grid_id","grid_x","grid_y","chunk_id"]+[f"gaia_{y}_impervious_class_area_m2" for y in yy]
    states=remote(ee)
    started=0
    for c,s in plan:
        desc=taskname(yy,c,s,args.subdivisions,args.test)
        st=states.get(desc,set())
        if st & {"READY","RUNNING","COMPLETED","SUCCEEDED"}:
            print("[SKIP EXISTING]",desc);continue
        if "FAILED" in st and not args.retry_failed:
            print("[SKIP FAILED]",desc,"check event history/subdivisions");continue
        fc=data.filter(ee.Filter.eq("chunk_id",c))
        if args.subdivisions>1:
            fc=(fc.randomColumn("gaia_split_2026",seed=2026)
                .filter(ee.Filter.gte("gaia_split_2026",s/args.subdivisions))
                .filter(ee.Filter.lt("gaia_split_2026",(s+1)/args.subdivisions)))
        if args.test:fc=fc.sort("grid_id").limit(25)
        polygons=fc.map(lambda f:feature_polygon(f,ee,proj))
        out=(raster.reduceRegions(
            collection=polygons,reducer=ee.Reducer.sum(),
            crs=proj,scale=500,tileScale=args.tile_scale)
            .map(lambda f:ee.Feature(None,ee.Feature(f).toDictionary(cols))))
        t=ee.batch.Export.table.toDrive(
            collection=out,description=desc,fileNamePrefix=desc.lower(),
            folder=args.drive_folder,fileFormat="CSV",selectors=cols)
        t.start()
        print("[STARTED]",desc,"ID",t.id)
        started+=1
        if args.max_tasks and started>=args.max_tasks:break
        time.sleep(.5)
    print("[DONE]",started,"tasks submitted. Check pilot source-area comparison before production.")
    print("[SCIENCE] Binary GAIA impervious-class area, not true impervious fraction or sewer capacity.")

if __name__=="__main__":main()
