# -*- coding: utf-8 -*-
"""Stage54: fine (5 km) *additive* urban-environment cache from local GHSL BUILT-S.

NO GEE tasks. Target cells are exact nested 5km children of existing EPSG:6933
20km M3 grid cells. Source GHSL BUILT-S values are built area PER NATIVE PIXEL
(m2), not area fraction. Convert to per-m2 density before area-weighted warp.

This script does NOT disaggregate events or climate, does NOT generate native
1km accuracy, and does NOT imply 5km meteorological observations.
"""
from __future__ import annotations
import argparse
import json
import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window, from_bounds

FINE_M=5000
PARENT_M=20000
FINE_AREA_M2=25_000_000.0
REQUIRED=("grid_id","grid_x","grid_y")

def load_parents(path:Path):
    if path.suffix.lower()==".parquet": df=pd.read_parquet(path)
    else: df=pd.read_csv(path)
    missing=set(REQUIRED)-set(df.columns)
    if missing:raise ValueError("Parent file missing "+str(sorted(missing)))
    if df[list(REQUIRED)].isna().any().any():raise ValueError("Null parent coordinates/IDs")
    for col in ("grid_x","grid_y"):
        n=pd.to_numeric(df[col],errors="raise")
        if not np.isfinite(n).all() or not (n==np.floor(n)).all():raise ValueError("Non-integer "+col)
        df[col]=n.astype("int32")
    expected=df.grid_x.astype(str)+"_"+df.grid_y.astype(str)
    if not (df.grid_id.astype(str).to_numpy()==expected.to_numpy()).all():
        raise ValueError("grid_id must equal grid_x_grid_y; cannot infer another grid origin")
    if df.grid_id.duplicated().any() or df[["grid_x","grid_y"]].duplicated().any():
        raise ValueError("Duplicate 20km parent")
    return df[list(REQUIRED)].sort_values(["grid_x","grid_y"]).reset_index(drop=True)

def _crop_window(ds,x0,y0):
    lo,bo,hi,to=transform_bounds("EPSG:6933",ds.crs,x0,y0,x0+PARENT_M,y0+PARENT_M,
                                 densify_pts=21)
    w=from_bounds(lo,bo,hi,to,transform=ds.transform)
    # Padding for fractional boundary pixels in the source reprojection.
    left=max(0,int(math.floor(w.col_off))-3)
    top=max(0,int(math.floor(w.row_off))-3)
    right=min(ds.width,int(math.ceil(w.col_off+w.width))+3)
    bottom=min(ds.height,int(math.ceil(w.row_off+w.height))+3)
    if right<=left or bottom<=top:return None
    return Window(left,top,right-left,bottom-top)

def parent_5km(ds,gx,gy):
    """Return 4x4 source-valid area and built area in m2; top row is north."""
    x0=int(gx)*PARENT_M;y0=int(gy)*PARENT_M
    dst_transform=Affine(FINE_M,0,x0,0,-FINE_M,y0+PARENT_M)
    win=_crop_window(ds,x0,y0)
    if win is None:return np.zeros((4,4)),np.zeros((4,4)),0
    arr=ds.read(1,window=win,masked=True).astype("float64").filled(np.nan)
    native_area=abs(ds.transform.a*ds.transform.e-ds.transform.b*ds.transform.d)
    if native_area<=0:raise ValueError("Invalid source pixel area")
    valid=np.isfinite(arr)&(arr>=0)&(arr<=native_area*1.02)
    invalid_physical=int((np.isfinite(arr)&~valid).sum())
    density=np.where(valid,arr/native_area,0.0).astype("float64")
    coverage=valid.astype("float64")
    source_transform=ds.window_transform(win)
    warped=np.zeros((4,4),dtype="float64")
    warped_coverage=np.zeros((4,4),dtype="float64")
    for src,out in ((density,warped),(coverage,warped_coverage)):
        reproject(
            source=src,destination=out,
            src_transform=source_transform,src_crs=ds.crs,
            dst_transform=dst_transform,dst_crs="EPSG:6933",
            dst_nodata=0,
            resampling=Resampling.average,num_threads=1)
    # The two area values are additive across nested grids.
    built=warped*FINE_AREA_M2
    covered=warped_coverage*FINE_AREA_M2
    return built,covered,invalid_physical

def extract_batch(ds,batch,epoch):
    rows=[];bad_sources=0
    for p in batch.itertuples(index=False):
        b,v,bad=parent_5km(ds,p.grid_x,p.grid_y)
        bad_sources+=bad
        for row in range(4):
            for col in range(4):
                x5=int(p.grid_x)*4+col
                y5=int(p.grid_y)*4+(3-row)
                valid_area=float(v[row,col]);built_area=float(b[row,col])
                if not (np.isfinite(valid_area) and np.isfinite(built_area)):
                    raise RuntimeError(f"Nonfinite result: {p.grid_id}")
                if valid_area<-1 or valid_area>FINE_AREA_M2*1.005:
                    raise RuntimeError(f"Coverage invalid for {p.grid_id}: {valid_area}")
                if built_area<-1 or built_area>valid_area+FINE_AREA_M2*.01:
                    raise RuntimeError(f"Built exceeds valid area: {p.grid_id} {built_area} {valid_area}")
                rows.append({
                    "grid5_id":f"{x5}_{y5}","grid_x5":x5,"grid_y5":y5,
                    "parent_grid_id":str(p.grid_id),
                    "parent_grid_x":int(p.grid_x),"parent_grid_y":int(p.grid_y),
                    "epoch":int(epoch),
                    "built_total_m2":max(0,built_area),
                    "built_valid_area_m2":max(0,valid_area),
                    "cell_area_m2":FINE_AREA_M2,
                    "built_fraction_cell":built_area/FINE_AREA_M2,
                    "built_fraction_valid":built_area/valid_area if valid_area>1 else np.nan,
                    "source_valid_fraction":valid_area/FINE_AREA_M2,
                })
    df=pd.DataFrame.from_records(rows)
    if df.grid5_id.duplicated().any():raise RuntimeError("Duplicate 5km grid IDs in batch")
    return df,bad_sources

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--parents",required=True,type=Path,help="M3 grid_centers.csv or exact 20km parent table")
    p.add_argument("--built-tif",required=True,type=Path,help="Actual 2020 GHSL BUILT-S 100m source TIFF")
    p.add_argument("--out-root",required=True,type=Path)
    p.add_argument("--epoch",type=int,default=2020)
    p.add_argument("--batch-parents",type=int,default=100)
    p.add_argument("--limit-parents",type=int,default=0,help="Smoke: first N 20km parents")
    p.add_argument("--overwrite",action="store_true")
    args=p.parse_args()
    if args.batch_parents<1 or args.limit_parents<0:raise ValueError("Bad batch settings")
    if args.epoch != 2020:raise ValueError("Default study uses E2020; other epoch requires audited epoch-specific source")
    parents=load_parents(args.parents)
    if args.limit_parents:parents=parents.iloc[:args.limit_parents].copy()
    if parents.empty:raise ValueError("No parents")
    args.out_root.mkdir(parents=True,exist_ok=True)
    manifest=[];total=0
    with rasterio.open(args.built_tif) as ds:
        if ds.count<1 or ds.crs is None or not ds.crs.is_projected:
            raise RuntimeError("Expected projected BUILT-S GeoTIFF")
        if ds.nodata is None:raise RuntimeError("BUILT-S nodata is required for valid-area QA")
        source_signature={"path":str(args.built_tif),"size":args.built_tif.stat().st_size,
                          "mtime_ns":args.built_tif.stat().st_mtime_ns,
                          "crs":str(ds.crs),"width":ds.width,"height":ds.height,
                          "nodata":float(ds.nodata),"native_pixel_area_m2":abs(ds.transform.a*ds.transform.e-ds.transform.b*ds.transform.d)}
        for i in range(0,len(parents),args.batch_parents):
            chunk=parents.iloc[i:i+args.batch_parents]
            final=args.out_root/f"fine5_parent_{i//args.batch_parents:04d}.parquet"
            stamp=final.with_suffix(".json")
            payload={"first":str(chunk.iloc[0].grid_id),"last":str(chunk.iloc[-1].grid_id),
                     "n_parents":len(chunk),"epoch":args.epoch,"source":source_signature}
            sig=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
            if final.exists() and stamp.exists() and not args.overwrite:
                old=json.loads(stamp.read_text(encoding="utf-8"))
                if old.get("signature")!=sig or old.get("rows")!=len(chunk)*16:
                    raise RuntimeError(f"Existing cache signature differs; use new --out-root: {final}")
                total+=int(old["rows"])
                print("[REUSE]",final.name,old["rows"])
            else:
                if final.exists() and not args.overwrite:
                    raise RuntimeError(f"Parquet exists without verified manifest: {final}")
                result,bad=extract_batch(ds,chunk,args.epoch)
                tmp=final.with_suffix(".parquet.tmp")
                result.to_parquet(tmp,index=False)
                tmp.replace(final)
                stamp.write_text(json.dumps({"signature":sig,"rows":len(result),
                    "invalid_source_values":bad,"payload":payload},ensure_ascii=False,indent=2),encoding="utf-8")
                total+=len(result)
                print("[SAVED]",final.name,"grids",len(result),"source invalid pixels",bad)
            manifest.append({"file":str(final),"parents":len(chunk),"rows":len(chunk)*16,"signature":sig})
    summary={"status":"PASS_STRUCTURE","parent_grids":len(parents),"fine5_grids":total,
             "expected_fine5_grids":len(parents)*16,"epoch":args.epoch,
             "source":source_signature,"parts":manifest,
             "note":"Source raster geometric warp/area QC only. Compare summed 20km built against prior Stage30c before inference."}
    (args.out_root/"54_fine5_manifest.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    if total!=len(parents)*16:raise RuntimeError("5km row coverage mismatch")
    print("[DONE]",total,"5km cells from",len(parents),"20km parents; no GEE tasks")
if __name__=="__main__":main()
