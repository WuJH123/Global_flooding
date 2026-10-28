# -*- coding: utf-8 -*-
"""Stage62b: freeze the **ONLY** 10km urban grid IDs from 2 completed EE TIFFs.

No upsampling from 20km, no smoothing. Strict raster CRS, affine, bands,
stripe continuity, nodata, and source-valid fractions. Materialize 2020
broad-urban frame, with optional unreported/nonurban event audit kept separate.
"""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
import numpy as np,pandas as pd,rasterio
from rasterio.warp import transform
from pyproj import Geod,Transformer

CELL=10000
XMIN,YMAX=-17360000,7320000
XMAX,YMIN=17360000,-7320000
WIDTH,HEIGHT=3472,1464
STRIPE_BOUNDS=((XMIN,0),(0,XMAX))

def audit_geodetic_cell(x,y,segments=16):
    """WGS84 ellipsoidal area of densified projected cell boundary."""
    t=Transformer.from_crs(6933,4326,always_xy=True)
    x0=x*CELL;x1=x0+CELL;y0=y*CELL;y1=y0+CELL
    z=np.linspace(0,1,segments+1)
    X=np.concatenate([x0+z*(x1-x0),np.full_like(z,x1),
      x1-z*(x1-x0),np.full_like(z,x0)])
    Y=np.concatenate([np.full_like(z,y0),y0+z*(y1-y0),
      np.full_like(z,y1),y1-z*(y1-y0)])
    lon,lat=t.transform(X,Y)
    a,_=Geod(ellps="WGS84").polygon_area_perimeter(lon,lat)
    return abs(a)

def build(tifs,out,urban_min=.001,valid_min=.95,chunk_size=5000,geodetic_sample=100):
    if len(tifs)!=2:raise ValueError("Provide exactly two complete 10km SMOD stripe TIFFs")
    if not (0<urban_min<=1 and 0<valid_min<=1 and chunk_size>0):
        raise ValueError("Invalid urban/valid thresholds")
    out.mkdir(parents=True,exist_ok=True)
    total=0;records=[];checks=[]
    for k,p in enumerate(tifs):
        with rasterio.open(p) as ds:
            t=ds.transform
            want=STRIPE_BOUNDS[k]
            if ds.count!=2 or ds.crs is None or ds.crs.to_epsg()!=6933:
                raise ValueError(f"Expected EPSG:6933 two-band image: {p}")
            expected_w=(want[1]-want[0])//CELL
            if (ds.width,ds.height)!=(expected_w,HEIGHT):
                raise ValueError(f"Stripe {k} wrong size: {(ds.width,ds.height)} vs {(expected_w,HEIGHT)}")
            actual=[t.a,t.b,t.c,t.d,t.e,t.f]
            target=[CELL,0,want[0],0,-CELL,YMAX]
            if max(abs(a-b) for a,b in zip(actual,target))>1e-4:
                raise ValueError(f"Stripe {k} affine mismatch {actual} != {target}")
            u=ds.read(1,masked=True).filled(np.nan).astype("float64")
            v=ds.read(2,masked=True).filled(np.nan).astype("float64")
            if np.any(np.isfinite(u)&((u<-1e-5)|(u>1.00001))) or np.any(
                np.isfinite(v)&((v<-1e-5)|(v>1.00001))):
                raise ValueError(f"Nonphysical SMOD fractions: {p}")
            rows,cols=np.nonzero(np.isfinite(u)&np.isfinite(v)&(u>=urban_min)&(v>=valid_min))
            gx=np.int32(want[0]//CELL+cols)
            gy=np.int32(YMAX//CELL-rows-1)
            if not len(gx):raise ValueError(f"Empty stripe urban mask: {p}")
            X=(gx.astype("float64")+.5)*CELL
            Y=(gy.astype("float64")+.5)*CELL
            lon,lat=transform("EPSG:6933","EPSG:4326",X.tolist(),Y.tolist())
            if not np.isfinite(lon).all() or not np.isfinite(lat).all():
                raise ValueError("Invalid city cell centroid; check latitude/projection edges")
            f=pd.DataFrame({"grid_id":[f"{a}_{b}" for a,b in zip(gx,gy)],
                "grid_x":gx,"grid_y":gy,"x_center_m":X,"y_center_m":Y,
                "longitude":lon,"latitude":lat,
                "cell_area_m2":np.full(len(gx),CELL*CELL,dtype="int64"),
                "urban_fraction_2020":u[rows,cols],
                "source_valid_fraction":v[rows,cols],
                "urban_area_km2_2020":u[rows,cols]*100.,
                "valid_source_area_km2":v[rows,cols]*100.})
            f["urban_fraction_of_valid"]=np.where(
                f.source_valid_fraction>0,
                f.urban_fraction_2020/f.source_valid_fraction,np.nan)
            if (f.urban_fraction_of_valid>1.01).any():
                raise ValueError("Urban area exceeds valid land/SMOD coverage")
            records.append(f)
            checks.append({"stripe":k,"rows":len(f),"input":str(p),"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),
                           "min_urban":float(f.urban_fraction_2020.min())})
            total+=len(f)
            print("[READ]",p.name,len(f),"urban cells")
    frame=pd.concat(records,ignore_index=True).sort_values(["grid_x","grid_y"]).reset_index(drop=True)
    if frame.grid_id.duplicated().any() or frame[["grid_x","grid_y"]].duplicated().any():
        raise RuntimeError("Overlapping stripes / duplicate 10km IDs")
    frame["chunk_id"]=(np.arange(len(frame))//chunk_size).astype("int32")
    # Ellipsoidal area check on samples across latitude and longitude.
    rng=np.random.default_rng(2026)
    idx=rng.choice(len(frame),size=min(geodetic_sample,len(frame)),replace=False)
    ratios=[audit_geodetic_cell(int(frame.grid_x[i]),int(frame.grid_y[i]))/1e8 for i in idx]
    if max(abs(x-1) for x in ratios)>.005:
        raise RuntimeError(f"Equal-area versus WGS84 geodesic discrepancy too large: {min(ratios)} .. {max(ratios)}")
    frame.to_parquet(out/"grid10_urban_2020.parquet",index=False)
    frame[["grid_id","grid_x","grid_y","longitude","latitude","chunk_id",
        "urban_fraction_2020","source_valid_fraction"]].to_csv(
            out/"grid10_urban_2020_gee_upload.csv",index=False)
    qc={"status":"PASS_10KM_GRID_CONTRACT","urban_grids":len(frame),
        "global_candidate_cells":WIDTH*HEIGHT,"cell_area_km2":100.,
        "geodetic_area_ratio_min":float(min(ratios)),
        "geodetic_area_ratio_max":float(max(ratios)),
        "urban_min":urban_min,"valid_min":valid_min,
        "source_checks":checks,
        "warning":"2020 GHSL defined urban universe, not a flood-free control universe; full region limited to 86S-86N"}
    (out/"62b_grid10_qc.json").write_text(json.dumps(qc,indent=2,ensure_ascii=False),encoding="utf-8")
    print("[SAVED]",qc)
    return frame,qc

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--west",type=Path,required=True)
    p.add_argument("--east",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--urban-min",type=float,default=.001)
    p.add_argument("--valid-min",type=float,default=.95)
    p.add_argument("--chunk-size",type=int,default=5000)
    a=p.parse_args()
    build([a.west,a.east],a.out,a.urban_min,a.valid_min,a.chunk_size)
if __name__=="__main__":main()
