# -*- coding: utf-8 -*-
"""Stage53b: sample Stage53 annual TIFFs to exact E2020 20km urban grid IDs.

Strict mode accepts only V3 safe-projected annual TIFFs, rejects TEST, duplicate years,
wrong CRS/affine, and centers outside raster. Coastal nulls are preserved.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio

METRICS={
    "era5":["temp2m_mean_c","annual_precip_mm","rx1day_mm","heavy20_days",
       "wet1_days","sm1_mean","valid_precip_days","valid_temp_days",
       "valid_sm1_days","negative_precip_days"],
    "imerg":["total_mm","rx1day_mm","heavy20_days","wet1_days","rx30min_mm","valid_days"]}
PAT={
    "era5":re.compile(r"^era5raster_v3_y(\d{4})(?:_test)?(?:[-_].*)?\.tif$",re.I),
    "imerg":re.compile(r"^imergraster_v3_y(\d{4})(?:_test)?(?:[-_].*)?\.tif$",re.I)}
YEARS=set(range(2000,2026))
X0=-17400000
Y0=7400000

def sample_file(path,grid,source,strict):
    match=PAT[source].match(path.name)
    if not match:raise ValueError("Wrong source/version TIFF: "+path.name)
    year=int(match.group(1))
    if not 2000<=year<=2025:raise ValueError("Year out of requested complete-year range")
    if "_test" in path.stem.lower() and strict:
        raise RuntimeError(f"TEST TIFF forbidden under --require-full: {path.name}")
    with rasterio.open(path) as ds:
        if ds.count!=len(METRICS[source]):raise RuntimeError(f"Wrong band count: {path.name}")
        if ds.crs is None or ds.crs.to_epsg()!=6933:raise RuntimeError(f"Wrong CRS: {ds.crs}")
        t=ds.transform
        if max(abs(t.a-20000),abs(t.e+20000),abs(t.b),abs(t.d))>1e-4:
            raise RuntimeError("Not an axis-aligned 20km grid")
        x=(grid.grid_x.to_numpy(dtype="float64")+.5)*20000
        y=(grid.grid_y.to_numpy(dtype="float64")+.5)*20000
        col=np.floor((x-t.c)/20000).astype("int64")
        row=np.floor((t.f-y)/20000).astype("int64")
        inside=(row>=0)&(row<ds.height)&(col>=0)&(col<ds.width)
        if strict and not inside.all():
            raise RuntimeError(f"{path.name}: {int((~inside).sum())} expected grid centers OUTSIDE TIFF")
        out=grid.loc[inside,["grid_id","grid_x","grid_y"]].copy()
        out["year"]=year
        desc=ds.descriptions
        names=[f"{source}_{year}_{s}" for s in METRICS[source]]
        if all(desc) and any(x not in (names[i],METRICS[source][i]) for i,x in enumerate(desc)):
            raise RuntimeError(f"Unexpected GeoTIFF band order or labels in {path.name}: {desc}")
        cube=ds.read(masked=True).astype("float64").filled(np.nan)
        for band,name in enumerate(names):
            out[name]=cube[band,row[inside],col[inside]]
    return year,out

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",choices=sorted(METRICS),required=True)
    p.add_argument("--tif-root",type=Path,required=True)
    p.add_argument("--grid-csv",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--require-full",action="store_true")
    p.add_argument("--allow-test",action="store_true")
    a=p.parse_args()
    grid=pd.read_csv(a.grid_csv,dtype={"grid_id":"string"})
    required={"grid_id","grid_x","grid_y"}
    if not required<=set(grid.columns):raise RuntimeError(f"Missing columns: {sorted(required-set(grid.columns))}")
    if grid.grid_id.isna().any() or grid.grid_id.duplicated().any():
        raise RuntimeError("Empty or duplicate E2020 grid IDs")
    expected=grid.grid_x.astype(str)+"_"+grid.grid_y.astype(str)
    if not np.array_equal(expected.to_numpy(),grid.grid_id.astype(str).to_numpy()):
        raise RuntimeError("grid_id must equal grid_x_grid_y")
    files=sorted(x for x in a.tif_root.rglob("*.tif") if PAT[a.source].match(x.name))
    if not a.allow_test:files=[x for x in files if "_test" not in x.stem.lower()]
    if not files:raise FileNotFoundError("No matching annual TIFFs under "+str(a.tif_root))
    parsed=[int(PAT[a.source].match(x.name).group(1)) for x in files]
    if len(parsed)!=len(set(parsed)):raise RuntimeError("Repeated year TIFF(s)")
    if a.require_full and set(parsed)!=YEARS:
        raise RuntimeError(f"Missing/extra year(s): missing={sorted(YEARS-set(parsed))} extra={sorted(set(parsed)-YEARS)}")
    a.out.mkdir(parents=True,exist_ok=True)
    report=[]
    for path in files:
        year,frame=sample_file(path,grid,a.source,a.require_full)
        metrics=[k for k in frame if k.startswith(f"{a.source}_{year}_")]
        nulls=int(frame[metrics].isna().all(axis=1).sum())
        outfile=a.out/f"{a.source}_{year}_grid.parquet"
        frame.to_parquet(outfile,index=False)
        report.append({"year":year,"grids":len(frame),"missing_all":nulls,
                       "tif":str(path),"parquet":str(outfile)})
        print("[SAVED]",outfile,"grids",len(frame),"all-null",nulls)
    (a.out/f"{a.source}_53b_qc.json").write_text(
        json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print("[DONE]",len(report),"years; spatial/missingness QC only, not numeric ERA5/IMERG verification")
if __name__=="__main__":main()
