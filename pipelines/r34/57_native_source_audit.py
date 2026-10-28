"""Stage57: independent native-pixel BUILT-S area check for selected 20km cells.

Uses source grid pixel CENTRE inclusion, NOT Stage54/55 warp. Pixel-edge
uncertainty bracket is computed with all_touched rasterization. This is a
second measurement, not exact polygon-pixel intersection. No GEE.
"""
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd,rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds,Window
from shapely.geometry import box,mapping
from shapely.ops import transform
from pyproj import Transformer

def inspect(ds,gx,gy):
    geom=transform(Transformer.from_crs("EPSG:6933",ds.crs,always_xy=True).transform,
                   box(gx*20000,gy*20000,(gx+1)*20000,(gy+1)*20000))
    win=from_bounds(*geom.bounds,transform=ds.transform)
    col=max(0,int(np.floor(win.col_off))-2);row=max(0,int(np.floor(win.row_off))-2)
    col2=min(ds.width,int(np.ceil(win.col_off+win.width))+2)
    row2=min(ds.height,int(np.ceil(win.row_off+win.height))+2)
    if col>=col2 or row>=row2:return (np.nan,)*4
    window=Window(col,row,col2-col,row2-row)
    arr=ds.read(1,window=window,masked=True).astype("float64").filled(np.nan)
    area=abs(ds.transform.a*ds.transform.e-ds.transform.b*ds.transform.d)
    valid=np.isfinite(arr)&(arr>=0)&(arr<=1.02*area)
    kw=dict(out_shape=arr.shape,transform=ds.window_transform(window))
    inner=geometry_mask([mapping(geom)],invert=True,all_touched=False,**kw)
    outer=geometry_mask([mapping(geom)],invert=True,all_touched=True,**kw)
    center=float(np.where(valid&inner,arr,0).sum())/400e6
    upper=float(np.where(valid&outer,arr,0).sum())/400e6
    # Ambiguous partial pixels bracket an area-based estimate;
    # centre method is not a formal lower bound.
    maxdiff=float(np.where(valid&(outer^inner),arr,0).sum())/400e6
    covered=float((valid&inner).sum()*area)/400e6
    return center,upper,maxdiff,covered

def audit(parity,raster,output,threshold=.03,random_n=200,seed=2026,max_rows=0):
    df=pd.read_csv(parity).dropna(subset=["built_fraction","built_fraction_cell"]).copy()
    if df.grid_id.duplicated().any():raise ValueError("Duplicated parity grid_id")
    outliers=df.loc[df.abs_diff>threshold]
    ordinary=df.loc[df.abs_diff<=threshold]
    sample=ordinary.sample(n=min(random_n,len(ordinary)),random_state=seed)
    work=pd.concat([outliers,sample],ignore_index=True).drop_duplicates("grid_id")
    if max_rows:work=work.iloc[:max_rows]
    rows=[]
    with rasterio.open(raster) as src:
        if src.crs is None or src.nodata is None:raise ValueError("Raster CRS/nodata missing")
        for i,r in enumerate(work.itertuples(index=False)):
            x,y=(int(v) for v in str(r.grid_id).split("_"))
            mid,upper,boundary,valid=inspect(src,x,y)
            rows.append({"grid_id":r.grid_id,"old_fraction":r.built_fraction,
                "new_fraction":r.built_fraction_cell,"pixel_center_fraction":mid,
                "all_touched_fraction":upper,"boundary_upper_minus_center":boundary,
                "source_center_coverage":valid,"is_outlier":bool(r.abs_diff>threshold),
                "abs_err_old":abs(r.built_fraction-mid),
                "abs_err_new":abs(r.built_fraction_cell-mid),
                "closer_to_new":abs(r.built_fraction_cell-mid)<abs(r.built_fraction-mid)})
            if (i+1)%50==0:print("[CHECK]",i+1,len(work),flush=True)
    output.mkdir(parents=True,exist_ok=True)
    result=pd.DataFrame(rows)
    result.to_csv(output/"57_native_pixel_audit.csv",index=False)
    summary={"status":"PILOT_NATIVE_PIXEL_CHECK","n":len(result),
        "n_outliers":int(result.is_outlier.sum()),"n_ordinary":int((~result.is_outlier).sum()),
        "new_closer_fraction":float(result.closer_to_new.mean()),
        "outlier_new_closer_fraction":float(result.loc[result.is_outlier,"closer_to_new"].mean()),
        "mae_new":float(result.abs_err_new.mean()),"mae_old":float(result.abs_err_old.mean()),
        "warning":"Pixel-centre source integration is approximate. For publication-grade exact area reference use polygon-pixel fractional coverage; boundary diagnostic provided."}
    (output/"57_native_qc.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print("[DONE]",summary)
    return result,summary

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--parity",type=Path,required=True)
    p.add_argument("--built-tif",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--threshold",type=float,default=.03)
    p.add_argument("--random-n",type=int,default=200)
    p.add_argument("--max-rows",type=int,default=0)
    a=p.parse_args();audit(a.parity,a.built_tif,a.out,a.threshold,a.random_n,max_rows=a.max_rows)
