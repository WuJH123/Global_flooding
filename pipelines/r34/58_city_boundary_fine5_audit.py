# -*- coding: utf-8 -*-
"""Stage58: independent E2020 BUILT-S within-UCDB-city boundary audit.

For selected pilot UCDB cities, use source 100m pixels (not 5km parent-grid
proxies) to compute city-clipped built fractions and across-5km-cell SD.
City boundary inclusion is 100m source-pixel-centre quadrature and is
approximate at polygon boundaries; no actual storm/flood locations inferred.
Requires Stage59 selection CSV for balanced city sampling.
"""
from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.windows import from_bounds,Window
from rasterio.warp import transform_bounds
from pyproj import Transformer
from shapely import contains_xy

def source_city_cells(ds,geom,max_pixels=3_000_000):
    if geom is None or geom.is_empty:return {"status":"INVALID_GEOMETRY"},None
    left,bottom,right,top=transform_bounds("EPSG:6933",ds.crs,
                                          *geom.bounds,densify_pts=41)
    w=from_bounds(left,bottom,right,top,transform=ds.transform)
    c0=max(0,math.floor(w.col_off)-2);r0=max(0,math.floor(w.row_off)-2)
    c1=min(ds.width,math.ceil(w.col_off+w.width)+2)
    r1=min(ds.height,math.ceil(w.row_off+w.height)+2)
    if c1<=c0 or r1<=r0:return {"status":"SOURCE_OUTSIDE"},None
    count=(r1-r0)*(c1-c0)
    if count>max_pixels:return {"status":"SKIP_MAX_PIXELS","source_window_pixels":count},None
    win=Window(c0,r0,c1-c0,r1-r0)
    raw=ds.read(1,window=win,masked=True).astype("float64").filled(np.nan)
    rr,cc=np.indices(raw.shape,dtype=np.float64);rr+=r0+.5;cc+=c0+.5
    sx=ds.transform.c+cc*ds.transform.a+rr*ds.transform.b
    sy=ds.transform.f+cc*ds.transform.d+rr*ds.transform.e
    x,y=Transformer.from_crs(ds.crs,"EPSG:6933",always_xy=True).transform(sx,sy)
    inside=contains_xy(geom,x,y)
    native_area=abs(ds.transform.a*ds.transform.e-ds.transform.b*ds.transform.d)
    physical=np.isfinite(raw)&(raw>=0)&(raw<=native_area*1.05)
    valid=inside&physical
    if valid.sum()<20:return {"status":"INSUFFICIENT_VALID_PIXELS","source_window_pixels":count},None
    xv=np.floor(x[valid]/5000).astype("int64")
    yv=np.floor(y[valid]/5000).astype("int64")
    values=raw[valid]
    df=pd.DataFrame({"grid_x5":xv,"grid_y5":yv,"built_area":values,
                      "valid_area":native_area})
    groups=df.groupby(["grid_x5","grid_y5"],sort=True).agg(
        built_area=("built_area","sum"),valid_area=("valid_area","sum"),
        pixels=("valid_area","size")).reset_index()
    groups["built_fraction"]=groups.built_area/groups.valid_area
    good=groups.loc[groups.pixels>=10].copy()
    if len(good)<2:return {"status":"INSUFFICIENT_FINE_CELLS","n_good_cells":len(good)},groups
    summary={"status":"PASS_QUADRATURE","source_window_pixels":count,
      "valid_source_pixels_in_city":int(valid.sum()),
      "valid_5km_cells":int(len(good)),"total_5km_cells":int(len(groups)),
      "city_built_fraction":float(groups.built_area.sum()/groups.valid_area.sum()),
      "within_city_5km_sd":float(np.std(good.built_fraction,ddof=0)),
      "within_city_5km_p90":float(good.built_fraction.quantile(.90)),
      "within_city_5km_mean":float(good.built_fraction.mean()),
      "city_source_covered_area_km2":float(groups.valid_area.sum()/1e6),
      "city_geom_area_km2":float(geom.area/1e6)}
    summary["city_valid_area_coverage"]=summary["city_source_covered_area_km2"]/max(summary["city_geom_area_km2"],1e-12)
    return summary,groups

def load_city_polygons(gpkg,layer,id_field):
    cities=gpd.read_file(gpkg,layer=layer,columns=[id_field],engine="pyogrio")
    if cities.crs is None:raise RuntimeError("UCDB geometry CRS missing")
    cities["city_id"]=cities[id_field].astype("string").str.strip().str.replace(r"^(\d+)\.0$",r"\1",regex=True)
    if cities.city_id.isna().any() or cities.city_id.duplicated().any():
        raise RuntimeError("UCDB city IDs are null or duplicated")
    cities=cities.to_crs("EPSG:6933")
    return cities.set_index("city_id")[["geometry"]]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pilot-cities",type=Path,required=True,
                   help="Stage59 city sample with city_id, zone, location_precision_proxy")
    p.add_argument("--ucdb",type=Path,required=True)
    p.add_argument("--ucdb-layer",default="GHS_UCDB_THEME_GENERAL_CHARACTERISTICS_GLOBE_R2024A")
    p.add_argument("--ucdb-id",default="ID_UC_G0")
    p.add_argument("--built-tif",type=Path,required=True)
    p.add_argument("--city-proxy",type=Path,help="Stage56 56_built5_city_modifiers.csv")
    p.add_argument("--max-source-pixels",type=int,default=3_000_000)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--limit",type=int,default=0)
    a=p.parse_args()
    pilot=pd.read_csv(a.pilot_cities,dtype={"city_id":str})
    if pilot.city_id.isna().any() or pilot.city_id.duplicated().any():
        raise RuntimeError("Pilot city IDs invalid/duplicate")
    if a.limit:pilot=pilot.iloc[:a.limit].copy()
    cities=load_city_polygons(a.ucdb,a.ucdb_layer,a.ucdb_id)
    proxy=(pd.read_csv(a.city_proxy,dtype={"city_id":str}) if a.city_proxy else None)
    if proxy is not None and proxy.city_id.duplicated().any():raise RuntimeError("Duplicated city proxy IDs")
    a.out.mkdir(parents=True,exist_ok=True)
    entries=[];cell_frames=[]
    with rasterio.open(a.built_tif) as ds:
        for k,row in enumerate(pilot.itertuples(index=False)):
            city_id=str(row.city_id)
            if city_id not in cities.index:
                entries.append({"city_id":city_id,"status":"UCDB_ID_MISSING"});continue
            poly=cities.loc[city_id].geometry
            res,cells=source_city_cells(ds,poly,a.max_source_pixels)
            res.update({"city_id":city_id,
              "zone":getattr(row,"zone",None),
              "location_precision_proxy":getattr(row,"location_precision_proxy",None)})
            entries.append(res)
            if cells is not None:
                cells["city_id"]=city_id
                cell_frames.append(cells)
            if (k+1)%10==0:print("[CITY AUDIT]",k+1,"/",len(pilot),flush=True)
    report=pd.DataFrame(entries)
    if proxy is not None:report=report.merge(proxy[["city_id","ghsl_built_5km_sd_wmean"]],
                                             on="city_id",how="left",validate="one_to_one")
    if "ghsl_built_5km_sd_wmean" in report:
        report["city_clipped_sd_minus_grid_proxy"]=(
            report.within_city_5km_sd-report.ghsl_built_5km_sd_wmean)
    report.to_csv(a.out/"58_city_boundary_comparison.csv",index=False)
    if cell_frames:pd.concat(cell_frames,ignore_index=True).to_parquet(
        a.out/"58_city_clipped_5km_cells.parquet",index=False)
    qc={"n_selected":len(pilot),"n_pass":int(report.status.eq("PASS_QUADRATURE").sum()),
        "status_counts":report.status.value_counts().to_dict(),
        "limitation":"Within-city source-pixel centre quadrature, not exact parcel or sewer geometry.",
        "source":str(a.built_tif)}
    (a.out/"58_boundary_qc.json").write_text(json.dumps(qc,indent=2,ensure_ascii=False),encoding="utf-8")
    print("[QC]",json.dumps(qc,ensure_ascii=False))
if __name__=="__main__":main()
