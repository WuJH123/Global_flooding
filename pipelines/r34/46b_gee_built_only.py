# -*- coding: utf-8 -*-
"""Stage46b: GHSL BUILT-S 100m source totals and non-residential areas only.

Avoid repeating already-complete M3 POP/SMOD. Domain defaults to E2020 full
urban Table asset. No 2025/2030 projected BUILT surface used here.
"""
from __future__ import annotations
import argparse
import re
from ee_ease_projection import probe_projection, rectangular_grid

PROJECT="eco-sector-503721-b7"
DEFAULT_ASSET=f"projects/{PROJECT}/assets/urban_universe_smod2020_20km_v1"
VERSION="BUILT_HIST_V2"
EPOCHS=(2000,2005,2010,2015,2020)

def parse_epochs(s):
    ys=[int(x.strip()) for x in s.split(",")]
    if not ys or len(set(ys))!=len(ys) or any(y not in EPOCHS for y in ys):
        raise ValueError(f"Epochs must be distinct within {EPOCHS}")
    return sorted(ys)

def parse_chunks(s):
    result=set()
    for x in s.split(","):
        x=x.strip()
        if re.fullmatch(r"\d+",x):result.add(int(x))
        elif re.fullmatch(r"\d+-\d+",x):
            a,b=map(int,x.split("-"))
            if a>b:raise ValueError("Reversed chunks")
            result.update(range(a,b+1))
        else:raise ValueError("Bad chunk syntax "+x)
    if not result:raise ValueError("No chunks")
    return sorted(result)

def area_aggregate(image,ee,proj):
    # A 100m pixel gives m2 TOTAL, not surface density.
    native_area=ee.Image.pixelArea().reproject(crs=image.projection())
    density=image.divide(native_area).unmask(0)
    smooth=density.reduceResolution(reducer=ee.Reducer.mean(),maxPixels=256)
    return smooth.reproject(crs=proj,scale=1000).multiply(
        ee.Image.pixelArea().reproject(crs=proj,scale=1000))

def stack(ee,proj,ys):
    bands=[]
    for y in ys:
        source=ee.Image(f"JRC/GHSL/P2023A/GHS_BUILT_S/{y}")
        built=source.select("built_surface")
        nonres=source.select("built_surface_nres")
        valid=ee.Image.pixelArea().reproject(crs=built.projection()).updateMask(built.mask())
        for name,im in [("built_total_m2",built),("built_nres_m2",nonres),("source_valid_area_m2",valid)]:
            bands.append(area_aggregate(im,ee,proj).rename(f"built_{y}_{name}"))
    return ee.Image.cat(bands)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project",default=PROJECT)
    p.add_argument("--asset",default=DEFAULT_ASSET)
    p.add_argument("--epochs",default="2000,2005,2010,2015,2020")
    p.add_argument("--chunks",default="0")
    p.add_argument("--subdivisions",type=int,default=1)
    p.add_argument("--tile-scale",type=int,default=8)
    p.add_argument("--max-tasks",type=int,default=1)
    p.add_argument("--verify-asset",action="store_true")
    p.add_argument("--probe-projection",action="store_true")
    p.add_argument("--test",action="store_true")
    p.add_argument("--submit",action="store_true")
    p.add_argument("--drive-folder",default="GlobalFlood_R34_BUILT_History")
    a=p.parse_args()
    ys=parse_epochs(a.epochs); cs=parse_chunks(a.chunks)
    if a.subdivisions<1 or not 1<=a.tile_scale<=16 or a.max_tasks<0:raise ValueError("Invalid split/tileScale/task count")
    def task_name(c,s):
        return f'{VERSION}_Y{"".join(str(y)[-2:] for y in ys)}_C{c:02d}_S{s:02d}of{a.subdivisions:02d}'+("_TEST" if a.test else "")
    plan=[(c,s) for c in cs for s in range(a.subdivisions)]
    print("[PLAN]",len(plan),"tasks; domain",a.asset)
    for c,s in plan:print("[PLAN]",task_name(c,s))
    if not (a.verify_asset or a.probe_projection or a.submit):
        print("[DRY RUN] no GEE calls");return
    import ee
    ee.Initialize(project=a.project)
    ee.data.getAsset(a.asset)
    data=ee.FeatureCollection(a.asset)
    if a.verify_asset:
        print("[ASSET]",data.size().getInfo())
    if not (a.probe_projection or a.submit):return
    proj=probe_projection(ee)
    if not a.submit:return
    image=stack(ee,proj,ys)
    cols=["grid_id","grid_x","grid_y","chunk_id"]+[f"built_{y}_{n}" for y in ys for n in
        ("built_total_m2","built_nres_m2","source_valid_area_m2")]
    recent={str(t.get("description")):str(t.get("state")) for t in ee.data.getTaskList()}
    started=0
    for c,s in plan:
        name=task_name(c,s)
        if name in recent:print("[SKIP existing]",name,recent[name]);continue
        fc=data.filter(ee.Filter.eq("chunk_id",c))
        if a.subdivisions>1:
            fc=fc.randomColumn("split_2026",seed=2026).filter(
                ee.Filter.gte("split_2026",s/a.subdivisions)).filter(
                ee.Filter.lt("split_2026",(s+1)/a.subdivisions))
        if a.test:fc=fc.sort("grid_id").limit(25)
        def poly(f):
            f=ee.Feature(f)
            return ee.Feature(rectangular_grid(ee,f.get("grid_x"),f.get("grid_y"),proj),
                f.toDictionary(["grid_id","grid_x","grid_y","chunk_id"]))
        res=(image.reduceRegions(collection=fc.map(poly),reducer=ee.Reducer.sum(),
                crs=proj,scale=1000,tileScale=a.tile_scale)
             .map(lambda f:ee.Feature(None,ee.Feature(f).toDictionary(cols))))
        task=ee.batch.Export.table.toDrive(collection=res,description=name,
            folder=a.drive_folder,fileNamePrefix=name.lower(),fileFormat="CSV",selectors=cols)
        task.start()
        print("[STARTED]",name,task.id);started+=1
        if a.max_tasks and started>=a.max_tasks:break
    print("[DONE] submitted",started)
if __name__=="__main__":main()
