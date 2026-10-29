# -*- coding: utf-8 -*-
"""Stage64c: lossless spatial mosaic of finished GHSL V2 GF10 aligned tiles.

The GEE row pieces MUST exactly tile the frozen west/east 10km lattice.
Recombines source raster cells without resampling or center matching.
Rejects V1, duplicates, gaps, differing CRS, transforms, nodata and band count.
"""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
import numpy as np,rasterio
from rasterio.windows import Window
from affine import Affine

GRID=10000
GLOBAL_TOP=7320000
STRIPE_X=(-17360000,0)
STRIPE_W=1736
ROWS=1464
NODATA=-9999.0

def digest(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        while chunk:=f.read(8<<20):h.update(chunk)
    return h.hexdigest()

def tile_height(parts):
    if not(1<=parts<=32) or ROWS%parts:
        raise ValueError("row-parts must divide 1464 exactly")
    return ROWS//parts

def merge(input_dir,out,year,parts=4):
    if year not in range(1975,2021,5):raise ValueError("Only historical GHSL 5-year epochs")
    h=tile_height(parts)
    out.mkdir(parents=True,exist_ok=True)
    checks=[]
    for stripe in (0,1):
        dst=out/f"ghs10_v2_e{year}_s{stripe:02d}.tif"
        paths=[]
        for row in range(parts):
            stem=f"ghs10_v2_e{year}_s{stripe:02d}_r{row:02d}of{parts:02d}"
            matches=[p for p in input_dir.iterdir()
                     if p.is_file() and p.suffix.lower() in (".tif",".tiff")
                     and p.stem.lower()==stem]
            if len(matches)!=1:
                raise FileNotFoundError(f"Expected EXACTLY one tile {stem}.tif; found {len(matches)}")
            paths.append(matches[0])
        writer=None
        try:
            for row,path in enumerate(paths):
                with rasterio.open(path) as src:
                    affine=src.transform
                    want=Affine(GRID,0,STRIPE_X[stripe],0,-GRID,GLOBAL_TOP-row*h*GRID)
                    if src.crs is None or src.crs.to_epsg()!=6933:
                        raise ValueError(f"Wrong EPSG:6933 CRS: {path}: {src.crs}")
                    if src.count!=3 or src.width!=STRIPE_W or src.height!=h:
                        raise ValueError(f"Wrong GHSL tile shape/bands {path}: {(src.count,src.width,src.height)}")
                    if max(abs(a-b) for a,b in zip(affine[:6],want[:6]))>1e-4:
                        raise ValueError(f"Wrong 10km aligned transform {path}: {affine} != {want}")
                    if row==0:
                        profile=src.profile.copy()
                        profile.update(driver="GTiff",height=ROWS,width=STRIPE_W,
                                       transform=Affine(GRID,0,STRIPE_X[stripe],0,-GRID,GLOBAL_TOP),
                                       dtype="float32",nodata=NODATA,
                                       compress="deflate",tiled=True,
                                       blockxsize=256,blockysize=256,
                                       BIGTIFF="IF_SAFER")
                        writer=rasterio.open(dst,"w",**profile)
                    data=src.read(masked=True)
                    # Nodata is NEVER changed to a real zero.
                    arr=data.filled(NODATA).astype("float32")
                    writer.write(arr,window=Window(0,row*h,STRIPE_W,h))
                    valid=~np.any(np.ma.getmaskarray(data),axis=0)
                    checks.append({"tile":str(path),"stripe":stripe,"row":row,
                                   "sha256":digest(path),"valid_3band_cells":int(valid.sum())})
        finally:
            if writer is not None:writer.close()
        with rasterio.open(dst) as merged:
            if merged.count!=3 or (merged.width,merged.height)!=(STRIPE_W,ROWS):
                raise RuntimeError("Mosaic incorrect")
        print("[MOSAIC]",dst)
    qc={"status":"PASS_GHSL_V2_MOSAIC","year":year,"row_parts":parts,
        "epsg":6933,"full_cell_area_m2":GRID*GRID,
        "method":"aligned raster window copy; NO reprojection / interpolation",
        "tiles":checks}
    (out/f"64c_ghsl_v2_e{year}_qc.json").write_text(
        json.dumps(qc,indent=2,ensure_ascii=False),encoding="utf-8")
    return qc

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input-dir",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--year",type=int,required=True)
    p.add_argument("--row-parts",type=int,default=4)
    a=p.parse_args()
    merge(a.input_dir,a.out,a.year,a.row_parts)

if __name__=="__main__":main()
