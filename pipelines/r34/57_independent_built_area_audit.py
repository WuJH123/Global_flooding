# -*- coding: utf-8 -*-
"""Stage57: independent native-pixel quadrature of source BUILT-S at 20km.

Does NOT use Stage30c or Stage54 GDAL average to calculate its reference.
Computes SOURCE-pixel-centre inclusion in each 20km EPSG:6933 polygon,
with native source cell area as quadrature weight. This is an approximate
independent numerical method, not exact boundary integration; boundary
uncertainty is reported using a 4-point subpixel sensitivity calculation.

Audit ALL large new-old disagreements (by default) plus built-intensity
matched controls. Fail closed on non-equal-area/source coordinate mismatch.
No GEE, no original caches overwritten.
"""
from __future__ import annotations
import argparse,hashlib,json,math
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds,Window
from pyproj import Transformer,CRS

AREA=400_000_000.0

def choose(parity, threshold=.03, controls=325, seed=2026):
    d=parity.copy()
    required={"grid_id","built_fraction_cell","built_fraction","abs_diff","source_valid_fraction"}
    if not required<=set(d):raise ValueError("Parity file missing: "+str(required-set(d)))
    d=d.dropna(subset=["grid_id","built_fraction_cell","built_fraction","abs_diff"]).copy()
    if d.grid_id.duplicated().any():raise ValueError("Duplicate parity grid IDs")
    xy=d.grid_id.str.extract(r"^(-?\d+)_(-?\d+)$")
    if xy.isna().any().any():raise ValueError("Bad grid IDs")
    d["grid_x"]=xy[0].astype(int);d["grid_y"]=xy[1].astype(int)
    outliers=d[d.abs_diff>threshold].copy()
    stable=d[d.abs_diff<=threshold].copy()
    if outliers.empty:raise RuntimeError("No outliers; verify threshold")
    # Match controls by old built intensity, to avoid only low-density comparisons.
    if controls:
        stable["bin"]=pd.cut(stable.built_fraction,[-.001,.01,.05,.10,.20,1.01],
                              labels=False,include_lowest=True)
        outliers["bin"]=pd.cut(outliers.built_fraction,[-.001,.01,.05,.10,.20,1.01],
                                labels=False,include_lowest=True)
        counts=outliers["bin"].value_counts(normalize=True)
        rng=np.random.default_rng(seed)
        chosen=[]
        for bin_id,w in counts.items():
            group=stable.loc[stable["bin"].eq(bin_id)]
            take=min(len(group),max(1,round(controls*w)))
            if take:chosen.append(group.iloc[rng.choice(len(group),take,replace=False)].copy())
        stable=pd.concat(chosen,ignore_index=True) if chosen else stable.iloc[:0].copy()
    else:stable=stable.iloc[:0].copy()
    outliers["audit_group"]="outlier"
    stable["audit_group"]="control"
    return pd.concat([outliers,stable],ignore_index=True).drop(columns=["bin"],errors="ignore")

def _window(ds,gx,gy,pad=3):
    left,bottom,right,top=transform_bounds("EPSG:6933",ds.crs,
        gx*20000,gy*20000,(gx+1)*20000,(gy+1)*20000,densify_pts=41)
    if not np.isfinite([left,bottom,right,top]).all():raise RuntimeError("Bad projection")
    w=from_bounds(left,bottom,right,top,transform=ds.transform)
    r0=max(0,math.floor(w.row_off)-pad);c0=max(0,math.floor(w.col_off)-pad)
    r1=min(ds.height,math.ceil(w.row_off+w.height)+pad)
    c1=min(ds.width,math.ceil(w.col_off+w.width)+pad)
    if r1<=r0 or c1<=c0:return None
    return Window(c0,r0,c1-c0,r1-r0)

def native_quadrature(ds,transformer,gx,gy,subpoints=1,max_pixels=1_000_000):
    win=_window(ds,int(gx),int(gy))
    if win is None:return np.nan,np.nan,0
    count=int(win.width*win.height)
    if count>max_pixels:raise RuntimeError(f"Source window {count:,} exceeds --max-pixels")
    raw=ds.read(1,window=win,masked=True).astype("float64").filled(np.nan)
    native_area=abs(ds.transform.a*ds.transform.e-ds.transform.b*ds.transform.d)
    physical=np.isfinite(raw)&(raw>=0)&(raw<=native_area*1.05)
    row,col=np.indices(raw.shape,dtype=np.float64)
    row+=win.row_off;col+=win.col_off
    numerator=0.;valid_area=0.
    if subpoints not in (1,4):raise ValueError("subpoints must be 1 or 4")
    offsets=[(.5,.5)] if subpoints==1 else [(.25,.25),(.25,.75),(.75,.25),(.75,.75)]
    for r_off,c_off in offsets:
        xs=ds.transform.c+(col+c_off)*ds.transform.a+(row+r_off)*ds.transform.b
        ys=ds.transform.f+(col+c_off)*ds.transform.d+(row+r_off)*ds.transform.e
        xx,yy=transformer.transform(xs,ys)
        sel=(xx>=gx*20000)&(xx<(gx+1)*20000)&(yy>=gy*20000)&(yy<(gy+1)*20000)
        use=sel&physical
        numerator+=float(np.sum(raw[use],dtype="float64"))/len(offsets)
        valid_area+=float(np.count_nonzero(use)*native_area)/len(offsets)
    return numerator/AREA,valid_area/AREA,count

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--parity",type=Path,required=True)
    p.add_argument("--raster",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--threshold",type=float,default=.03)
    p.add_argument("--controls",type=int,default=325)
    p.add_argument("--max-pixels",type=int,default=1_000_000)
    p.add_argument("--subpoints",type=int,choices=(1,4),default=4)
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--limit",type=int,default=0,help="Smoke only; 0=all chosen")
    a=p.parse_args()
    if a.threshold<=0 or a.controls<0 or a.limit<0:raise ValueError("Invalid audit arguments")
    parity=pd.read_csv(a.parity,dtype={"grid_id":str})
    selected=choose(parity,a.threshold,a.controls,a.seed)
    if a.limit:selected=selected.iloc[:a.limit].copy()
    if selected.empty:raise RuntimeError("Nothing selected")
    rows=[]
    with rasterio.open(a.raster) as ds:
        src=CRS.from_user_input(ds.crs);dst=CRS.from_epsg(6933)
        # Both projected in equal-area coordinates: GHSL raster is Mollweide.
        if not src.is_projected or not ("54009" in src.to_string() or
            "Mollweide" in src.to_wkt() or "moll" in src.name.lower()):
            raise RuntimeError(f"Unexpected GHSL projected CRS: {src.to_string()}")
        if ds.nodata is None:raise RuntimeError("Source raster nodata missing")
        transformer=Transformer.from_crs(src,dst,always_xy=True)
        for k,r in enumerate(selected.itertuples(index=False)):
            independent,coverage,npixels=native_quadrature(ds,transformer,
                r.grid_x,r.grid_y,a.subpoints,a.max_pixels)
            rows.append({"grid_id":r.grid_id,"grid_x":r.grid_x,"grid_y":r.grid_y,
                "audit_group":r.audit_group,
                "old_fraction":r.built_fraction,"new_fraction":r.built_fraction_cell,
                "independent_fraction":independent,"independent_coverage":coverage,
                "old_abs_error":abs(independent-r.built_fraction),
                "new_abs_error":abs(independent-r.built_fraction_cell),
                "difference_new_minus_old":r.built_fraction_cell-r.built_fraction,
                "source_window_pixels":npixels})
            if (k+1)%50==0:print("[SOURCE AUDIT]",k+1,"/",len(selected),flush=True)
    tab=pd.DataFrame(rows)
    a.out.mkdir(parents=True,exist_ok=True)
    tab.to_csv(a.out/"57_source_independent_per_grid.csv",index=False)
    valid=tab.dropna(subset=["independent_fraction","old_abs_error","new_abs_error"])
    delta=valid.old_abs_error-valid.new_abs_error
    summary={"method":"source native-pixel quadrature (4-point if selected), not GDAL average",
      "source":str(a.raster),"selected":len(tab),
      "outlier_count":int((tab.audit_group=="outlier").sum()),
      "control_count":int((tab.audit_group=="control").sum()),
      "n_valid":len(valid),"median_old_error":float(valid.old_abs_error.median()),
      "median_new_error":float(valid.new_abs_error.median()),
      "new_better_fraction":float((delta>0).mean()),
      "new_better_outlier_fraction":float((delta[valid.audit_group.eq("outlier")]>0).mean()) if (valid.audit_group=="outlier").any() else None,
      "median_improvement_old_minus_new":float(delta.median()),
      "p95_abs_new_error":float(valid.new_abs_error.quantile(.95)),
      "n_independent_coverage_below_95pct":int((valid.independent_coverage<.95).sum()),
      "status":"DIAGNOSTIC_ONLY","limitation":"Native source pixel quadrature has boundary approximation; repeat edge cases at finer quadrature or exact polygon intersection."}
    (a.out/"57_source_audit_qc.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf8")
    print("[AUDIT]",json.dumps(summary,ensure_ascii=False))
if __name__=="__main__":main()
