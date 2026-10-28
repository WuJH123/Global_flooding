# -*- coding: utf-8 -*-
"""Convert Stage45 full-universe E2020 SMOD GeoTIFF into 20km Table CSV.

No GEE calls. Unique grid_id/grid_x/grid_y and chunk_id for the EE upload.
Requires raster CRS and affine exact lattice match; invalid latitude rejected.
"""
from __future__ import annotations
import argparse
import json
import hashlib
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform

VALID_GLOBAL_SHAPES={(1736,732):"V3_safe", (1740,740):"V2_legacy_unusable_extent"}

def convert(inp:Path,out:Path,urban_min:float=.001,valid_min:float=.95,chunk_size:int=5000,allow_test:bool=False):
    with rasterio.open(inp) as ds:
        if ds.count!=2:
            raise ValueError(f"Expected 2 fractions bands, got {ds.count}")
        if not allow_test and (ds.width,ds.height)!=(1736,732):
            raise ValueError(f"Production requires V3 1736x732 global raster; got {ds.width}x{ds.height}; never pass V2 or TEST as production")
        if not ds.crs or ds.crs.to_epsg()!=6933:raise ValueError(f"Expected EPSG:6933, got {ds.crs}")
        t=ds.transform
        if max(abs(t.a-20000),abs(t.e+20000),abs(t.b),abs(t.d))>1e-4:
            raise ValueError(f"Unexpected 20km affine transform: {t}")
        if abs(t.c/20000-round(t.c/20000))>1e-8 or abs(t.f/20000-round(t.f/20000))>1e-8:
            raise ValueError("Raster origin not aligned to integer 20km EASE grid")
        if not allow_test and (abs(t.c+17360000)>1e-4 or abs(t.f-7320000)>1e-4):
            raise ValueError("Production V3 origin mismatch")
        first_grid_x=int(round(t.c/20000))
        first_grid_y=int(round(t.f/20000))-1
        urban=ds.read(1,masked=True).filled(np.nan)
        valid=ds.read(2,masked=True).filled(np.nan)
    for name,v in [("urban",urban),("valid",valid)]:
        if np.any(np.isfinite(v)&((v < -1e-5)|(v > 1.00001))):
            raise ValueError(f"Nonphysical {name} fractions")
    rr,cc=np.nonzero(np.isfinite(urban)&np.isfinite(valid)&
        (urban>=urban_min)&(valid>=valid_min))
    if len(rr)<(1 if allow_test else 1000):raise RuntimeError(f"Implausibly small urban domain: {len(rr)}")
    gx=(cc+first_grid_x).astype("int32")
    gy=(first_grid_y-rr).astype("int32")
    xs=(gx+.5)*20000
    ys=(gy+.5)*20000
    lon,lat=transform("EPSG:6933","EPSG:4326",xs.tolist(),ys.tolist())
    lon=np.array(lon);lat=np.array(lat)
    good=np.isfinite(lon)&np.isfinite(lat)&(np.abs(lat)<=90)&(np.abs(lon)<=180)
    excluded=int((~good).sum())
    df=pd.DataFrame({
        "grid_id":[f"{x}_{y}" for x,y in zip(gx[good],gy[good])],
        "grid_x":gx[good],"grid_y":gy[good],
        "longitude":lon[good],"latitude":lat[good],
        "urban_fraction_2020":urban[rr[good],cc[good]],
        "source_valid_fraction":valid[rr[good],cc[good]]})
    df.sort_values(["grid_x","grid_y"],inplace=True,ignore_index=True)
    df["chunk_id"]=(np.arange(len(df))//chunk_size).astype("int32")
    if not len(df) or df.grid_id.duplicated().any():raise RuntimeError("Empty/duplicate urban grid IDs")
    out.parent.mkdir(parents=True,exist_ok=True)
    df.to_csv(out,index=False)
    summary={"rows":len(df),"chunks":int(df.chunk_id.max())+1,
        "excluded_invalid_centres":excluded,
        "min_urban_fraction":urban_min,"min_valid_fraction":valid_min,
        "input_sha256":hashlib.sha256(inp.read_bytes()).hexdigest(),
        "output_sha256":hashlib.sha256(out.read_bytes()).hexdigest(),
        "coordinate_system":"EPSG:6933","epoch":2020,
        "raster_width":int(urban.shape[1]),"raster_height":int(urban.shape[0]),
        "raster_first_grid_x":first_grid_x,"raster_first_grid_y":first_grid_y,
        "production_V3":bool(not allow_test),
        "coverage_note":"V3 conservative +/-179.922 longitude +/-85.56 latitude; not literal full globe"}
    qc=out.with_name(out.stem+"_qc.json")
    qc.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    print("[QC]",json.dumps(summary,ensure_ascii=False))
    print("[SAVED]",out)
    return summary

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tif",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--urban-min",type=float,default=.001)
    p.add_argument("--valid-min",type=float,default=.95)
    p.add_argument("--chunk-size",type=int,default=5000)
    p.add_argument("--allow-test",action="store_true",help="Only convert a cropped V3 TEST raster; never use for the universe asset")
    a=p.parse_args()
    if not (0<a.urban_min<=1 and 0<=a.valid_min<=1 and a.chunk_size>0):
        raise ValueError("Invalid fraction thresholds or chunk size")
    convert(a.tif,a.out,a.urban_min,a.valid_min,a.chunk_size,a.allow_test)
if __name__=="__main__":main()
