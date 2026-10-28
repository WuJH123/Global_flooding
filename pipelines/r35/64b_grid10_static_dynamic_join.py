# -*- coding: utf-8 -*-
"""Stage64b: join frozen 10km IDs to completed annual/epoch GEE GeoTIFFs.

Every stripe must have exact EPSG:6933 affine (global 10km grid).
Never use TIFF pixel centers from another projection, any nearest-grid join
or old 20km outputs. Missing bands retained as NULL; no null-to-zero.
All count/area fields retain source units and provenance.
"""
import argparse,json,re,hashlib
from pathlib import Path
import numpy as np,pandas as pd,rasterio
from rasterio.windows import Window
BANDS={
"ghsl":["population_count","built_total_m2","built_nres_m2"],
"era5":["era5_annual_rain_mm","era5_p24_max_mm","era5_daily_p95_mm",
        "era5_wet20_days","era5_temp_mean_c","era5_soil1_mean","era5_valid_days"],
"imerg":["imerg_annual_total_mm","imerg_p30_max_mm","imerg_valid_halfhour_steps"],
"cmip6":["cmip_annualized_precip_mm","cmip_daily_p99_mm","cmip_fraction_wet20_days",
         "cmip_temp_mean_c","cmip_available_days"]}
TRANSFORM=[10000,0,-17360000,0,-10000,7320000]
def join(grid,source,tifs,out,label):
    if source not in BANDS:raise ValueError("Unknown source")
    if len(tifs)!=2:raise ValueError("Exactly west/east stripe TIFFs")
    f=pd.read_parquet(grid)
    if f.grid_id.duplicated().any() or not {"grid_x","grid_y"}<=set(f):
        raise ValueError("Missing/duplicate 10km grid IDs")
    cols=BANDS[source];result=f[["grid_id","grid_x","grid_y"]].copy()
    result["label"]=label;result["dataset"]=source
    for c in cols:result[c]=np.nan
    checks=[]
    for idx,p in enumerate(tifs):
        with rasterio.open(p) as ds:
            x0=-17360000 if idx==0 else 0
            expected=[10000,0,x0,0,-10000,7320000]
            actual=[ds.transform.a,ds.transform.b,ds.transform.c,
                    ds.transform.d,ds.transform.e,ds.transform.f]
            if ds.crs is None or ds.crs.to_epsg()!=6933 or ds.count!=len(cols):
                raise ValueError(f"Wrong {source} CRS or band count: {p} {ds.crs} {ds.count}")
            if max(abs(a-b) for a,b in zip(actual,expected))>1e-4:
                raise ValueError(f"Wrong 10km grid affine {p} {actual}")
            if (ds.width,ds.height)!=(1736,1464):
                raise ValueError(f"Wrong production stripe shape {(ds.width,ds.height)}")
            hit=(result.grid_x.between(-1736,-1) if idx==0 else
                 result.grid_x.between(0,1735))
            i=np.flatnonzero(hit)
            rr=(731-result.loc[hit,"grid_y"].to_numpy()).astype(int)
            cc=(result.loc[hit,"grid_x"].to_numpy()+(1736 if idx==0 else 0)).astype(int)
            if np.any((rr<0)|(rr>=ds.height)|(cc<0)|(cc>=ds.width)):
                raise ValueError("Grid coordinates exceed raster bounds")
            for j,c in enumerate(cols,1):
                img=ds.read(j,masked=True).filled(np.nan).astype("float64")
                result.loc[hit,c]=img[rr,cc]
            checks.append({"stripe":idx,"n_grid":len(i),"path":str(p),
                           "sha256":hashlib.sha256(p.read_bytes()).hexdigest()})
    if source=="ghsl":
        finite=np.isfinite(result["built_total_m2"])
        if (result.loc[finite,"built_total_m2"]>1.02e8).any():
            raise ValueError("Built exceeds 100 km2 cell")
        if ((result.built_nres_m2-result.built_total_m2)>1e6).any():
            raise ValueError("Non-residential built exceeds built total")
        result["built_fraction_wholecell"]=result.built_total_m2/1e8
        result["pop_density_gross_per_km2"]=result.population_count/100.
    if source=="era5":
        if (result.era5_soil1_mean.dropna().between(-.01,1.01)==False).any():
            raise ValueError("ERA5 soil moisture out of range")
        if (result.era5_annual_rain_mm.dropna()<-1e-5).any():
            raise ValueError("Negative annual rain")
    out.mkdir(parents=True,exist_ok=True)
    result.to_parquet(out/f"64b_{source}_{label}_grid10.parquet",index=False)
    qc={"source":source,"period":label,"n_grids":len(result),
        "n_all_complete":int(result[cols].notna().all(axis=1).sum()),
        "grid_contract":"GF10: EPSG:6933, 10000m, x0=-17360000, y_top=7320000",
        "units":{"ghsl":"population persons, built m2",
                 "era5":"rain mm, air temp degC, soil volume fraction",
                 "imerg":"depth mm, native 30min rate*0.5",
                 "cmip6":"annualized rainfall mm, daily climate only"}[source],
        "files":checks}
    (out/f"64b_{source}_{label}_qc.json").write_text(json.dumps(qc,indent=2),encoding="utf-8")
    print("[DONE]",qc)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--grid",type=Path,required=True)
    p.add_argument("--source",choices=BANDS,required=True)
    p.add_argument("--west",type=Path,required=True)
    p.add_argument("--east",type=Path,required=True)
    p.add_argument("--label",required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args();join(a.grid,a.source,[a.west,a.east],a.out,a.label)
if __name__=="__main__":main()
