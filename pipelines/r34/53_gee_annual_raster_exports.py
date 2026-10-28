# -*- coding: utf-8 -*-
"""Stage53: annual ERA5/IMERG 20km global raster export, one GEE Task/year.

Dry-run by default, plus cloud CRS probe and small spatial smoke. Export 20km
AREA-WEIGHTED spatial means of yearly pixel metrics, not risk probabilities.
Global yearly IMERG composites are expensive; test each source/year before
submitting all years; consider partitioning after any computation timeout.
"""
from __future__ import annotations
import argparse
import importlib.util
from pathlib import Path
import re
from ee_ease_projection import EASE_WKT,probe_projection

PROJECT="eco-sector-503721-b7"
TRANSFORM=[20000,0,-17400000,0,-20000,7400000]
SOURCES={
    "era5":("47_gee_era5_annual_context.py","year_image","ERA5RASTER_V2"),
    "imerg":("50_imerg_annual_daily_extremes.py","annual","IMERGRASTER_V2")}

def parse_years(s):
    out=set()
    for item in s.split(","):
        item=item.strip()
        if re.fullmatch(r"\d{4}",item):out.add(int(item))
        elif re.fullmatch(r"\d{4}-\d{4}",item):
            a,b=map(int,item.split("-"))
            if a>b:raise ValueError("Reversed years")
            out.update(range(a,b+1))
        else:raise ValueError("Bad year: "+item)
    if not out or min(out)<2000 or max(out)>2025:raise ValueError("Complete years 2000..2025 only")
    return sorted(out)

def load(filename):
    location=Path(__file__).resolve().parent/filename
    if not location.exists():raise FileNotFoundError(location)
    spec=importlib.util.spec_from_file_location("r34_"+location.stem,location)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",choices=sorted(SOURCES),required=True)
    p.add_argument("--years",default="2020")
    p.add_argument("--project",default=PROJECT)
    p.add_argument("--folder",default="GlobalFlood_R34_Annual_Rasters")
    p.add_argument("--probe",action="store_true")
    p.add_argument("--test",action="store_true")
    p.add_argument("--submit",action="store_true")
    p.add_argument("--max-tasks",type=int,default=1)
    a=p.parse_args()
    if a.max_tasks<0:raise ValueError("max-tasks must be >=0")
    years=parse_years(a.years)
    fname,func,prefix=SOURCES[a.source]
    print("[PLAN]",a.source,len(years),"image Tasks; 20km EASE grid")
    for year in years:print("[PLAN]",f"{prefix}_Y{year}"+("_TEST" if a.test else ""))
    if not (a.submit or a.probe):
        print("[DRY RUN] no GEE calls");return
    import ee
    ee.Initialize(project=a.project)
    proj=probe_projection(ee)
    if not a.submit:return
    module=load(fname)
    previous={str(t.get("description")):str(t.get("state")) for t in ee.data.getTaskList()}
    region=(ee.Geometry.Rectangle([9500000,2500000,9600000,2600000],proj,False)
        if a.test else ee.Geometry.Rectangle([-17400000,-7400000,17400000,7400000],proj,False))
    native_collection=("ECMWF/ERA5_LAND/DAILY_AGGR" if a.source=="era5"
        else "NASA/GPM_L3/IMERG_V07")
    native_band=("temperature_2m" if a.source=="era5" else "precipitation")
    created=0
    for year in years:
        name=f"{prefix}_Y{year}"+("_TEST" if a.test else "")
        if name in previous:
            print("[SKIP existing task]",name,previous[name]);continue
        image=(module.year_image(year,ee) if a.source=="era5" else module.annual(year,ee))
        native=(ee.ImageCollection(native_collection).filterDate(f"{year}-01-01",f"{year+1}-01-01")
            .first().select(native_band).projection())
        image=(image.setDefaultProjection(native)
            .reduceResolution(reducer=ee.Reducer.mean(),maxPixels=256))
        task=ee.batch.Export.image.toDrive(
            image=image,description=name,folder=a.folder,
            fileNamePrefix=name.lower(),region=region,crs=EASE_WKT,
            crsTransform=TRANSFORM,maxPixels=2_000_000,fileFormat="GeoTIFF")
        task.start()
        print("[STARTED]",name,task.id);created+=1
        if a.max_tasks and created>=a.max_tasks:break
    print("[DONE]",created,"new Tasks; do not assume source parity or completed exports")
if __name__=="__main__":main()
