# -*- coding: utf-8 -*-
"""Stage61: CITY-scale environment representativeness at 5/10/20km.

Reuses already computed M3 Stage54 5km cells and Stage55 10/20km cells.
Clips grid cell footprints to UCDB city polygons and area-weights whole-
cell built fractions. This is a PROXY (edge cells include outside-city
land); Stage58 directly samples the source raster inside city polygons.
Stratify results by city, Köppen zone and geocoding area-precision proxy.
No flood label duplication, no GEE use, no climate model claimed.
"""
from __future__ import annotations
import argparse,json,math
from pathlib import Path
import pandas as pd
import numpy as np
import geopandas as gpd
from shapely.geometry import box

def weighted_quantile(vals,weights,p):
    idx=np.argsort(vals);v=vals[idx];w=weights[idx]
    return float(v[min(np.searchsorted(np.cumsum(w),p*np.sum(w),side="left"),len(v)-1)])

def city_scale_summary(geom,grid,scale):
    if geom.is_empty or not geom.is_valid:
        return {"status":"BAD_CITY_GEOMETRY","n_cells":0}
    size=scale*1000
    xlo,ylo,xhi,yhi=geom.bounds
    sub=grid.loc[grid.grid_x.between(math.floor(xlo/size)-1,math.floor(xhi/size)+1)&
                 grid.grid_y.between(math.floor(ylo/size)-1,math.floor(yhi/size)+1)]
    weights=[];values=[];n_total=0;area_intersect=0
    for row in sub.itertuples(index=False):
        poly=box(row.grid_x*size,row.grid_y*size,
                 (row.grid_x+1)*size,(row.grid_y+1)*size)
        overlap=geom.intersection(poly).area
        if overlap<=0:continue
        n_total+=1
        area_intersect+=overlap
        if not np.isfinite(row.built_fraction_cell) or not np.isfinite(row.source_valid_fraction):
            continue
        if row.source_valid_fraction<=0:continue
        weights.append(overlap*min(1,max(0,row.source_valid_fraction)))
        values.append(max(0,min(1,float(row.built_fraction_cell))))
    if len(weights)<1 or sum(weights)<=0:
        return {"status":"NO_VALID_ENVIRONMENT","n_cells":n_total,
                "city_geom_area_km2":float(geom.area/1e6)}
    w=np.array(weights,dtype=float);v=np.array(values,dtype=float)
    mean=float(np.average(v,weights=w))
    sd=float(np.sqrt(np.average((v-mean)**2,weights=w)))
    return {"status":"PASS_CONTEXTUAL_PROXY","n_cells":n_total,
        "n_valid_cells":len(w),"mean_built_fraction":mean,"sd_built_fraction":sd,
        "p90_built_fraction":weighted_quantile(v,w,.9),
        "fraction_area_built_ge20pct":float(np.sum(w[v>=.2])/np.sum(w)),
        "max_built_fraction":float(np.max(v)),
        "city_geom_area_km2":float(geom.area/1e6),
        "covered_grid_overlap_fraction":float(area_intersect/max(geom.area,1)),
        "source_weighted_overlap_fraction":float(np.sum(w)/max(geom.area,1))}

def load_scale_tables(fine_root,coarse_root):
    parts=sorted(fine_root.glob("fine5_parent_*.parquet"))
    if not parts or not (fine_root/"54_fine5_manifest.json").is_file():
        raise FileNotFoundError("Verified Stage54 5km files and manifest required")
    manifest=json.loads((fine_root/"54_fine5_manifest.json").read_text(encoding="utf8"))
    if len(parts)!=len(manifest.get("parts",[])):raise RuntimeError("Stage54 parts mismatch")
    frames=[pd.read_parquet(x,columns=["grid_x5","grid_y5","built_fraction_cell","source_valid_fraction"]) for x in parts]
    fine=pd.concat(frames,ignore_index=True)
    fine=fine.rename(columns={"grid_x5":"grid_x","grid_y5":"grid_y"})
    tables={5:fine}
    for scale in (10,20):
        path=coarse_root/f"environment_grid_{scale}km_2020.parquet"
        d=pd.read_parquet(path,columns=["grid_x","grid_y","built_fraction_cell","source_valid_fraction"])
        tables[scale]=d
    for scale,frame in tables.items():
        if frame[["grid_x","grid_y"]].duplicated().any():
            raise RuntimeError(f"Duplicate {scale}km grid ID")
        if frame.empty:raise RuntimeError("Empty environment source table")
    if len(tables[5])!=len(tables[20])*16 or len(tables[10])!=len(tables[20])*4:
        raise RuntimeError("Nested 5/10/20km cache row hierarchy mismatch")
    return tables

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pilot-cities",type=Path,required=True)
    p.add_argument("--ucdb",type=Path,required=True)
    p.add_argument("--ucdb-layer",default="GHS_UCDB_THEME_GENERAL_CHARACTERISTICS_GLOBE_R2024A")
    p.add_argument("--ucdb-id",default="ID_UC_G0")
    p.add_argument("--fine-root",type=Path,required=True)
    p.add_argument("--coarse-root",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--limit",type=int,default=0)
    a=p.parse_args()
    pilot=pd.read_csv(a.pilot_cities,dtype={"city_id":str})
    if pilot.city_id.duplicated().any() or pilot.city_id.isna().any():
        raise RuntimeError("Invalid pilot city IDs")
    if not {"zone","location_precision_proxy"}<=set(pilot):
        raise RuntimeError("Need Stage59 zone and precision-proxy sampling strata")
    if a.limit:pilot=pilot.iloc[:a.limit].copy()
    cities=gpd.read_file(a.ucdb,layer=a.ucdb_layer,columns=[a.ucdb_id],engine="pyogrio")
    if cities.crs is None:raise RuntimeError("UCDB CRS unknown")
    cities["city_id"]=cities[a.ucdb_id].astype("string").str.strip().str.replace(
        r"^(\d+)\.0$",r"\1",regex=True)
    cities=cities[cities.city_id.isin(pilot.city_id)].to_crs("EPSG:6933")
    if cities.city_id.duplicated().any():raise RuntimeError("UCDB duplicated city ID")
    by_city=cities.set_index("city_id").geometry
    tables=load_scale_tables(a.fine_root,a.coarse_root)
    records=[]
    for idx,city in enumerate(pilot.itertuples(index=False)):
        geom=by_city.get(str(city.city_id))
        for scale,frame in tables.items():
            metrics=city_scale_summary(geom,frame,scale) if geom is not None else {"status":"UCDB_CITY_MISSING"}
            records.append({"city_id":str(city.city_id),"zone":city.zone,
              "location_precision_proxy":city.location_precision_proxy,
              "scale_km":scale,**metrics})
        if (idx+1)%10==0:print("[SCALE CITY]",idx+1,"/",len(pilot),flush=True)
    out=pd.DataFrame(records)
    a.out.mkdir(parents=True,exist_ok=True)
    out.to_csv(a.out/"61_city_environment_by_scale.csv",index=False)
    grouped=out[out.status=="PASS_CONTEXTUAL_PROXY"].groupby(
        ["scale_km","zone","location_precision_proxy"],observed=True).agg(
        cities=("city_id","nunique"),
        median_built_mean=("mean_built_fraction","median"),
        median_built_sd=("sd_built_fraction","median"),
        median_high_built_fraction=("fraction_area_built_ge20pct","median"),
        median_city_grid_coverage=("covered_grid_overlap_fraction","median")).reset_index()
    grouped.to_csv(a.out/"61_scale_by_zone_precision.csv",index=False)
    qc={"status":"CONTEXTUAL_SCALE_AUDIT_NO_RESPONSE_MODEL",
        "n_cities_requested":len(pilot),
        "cities_with_all_3_scales":int(out[out.status=="PASS_CONTEXTUAL_PROXY"].groupby("city_id").scale_km.nunique().eq(3).sum()),
        "scales":[5,10,20],"source_cache":"existing 54/55 local cache",
        "interpretation":"within-city intersection area weights WHOLE-cell built fractions; compare Stage58 native source city clipping",
        "limitation":"Does not identify flood response scale or rainfall resolution"}
    (a.out/"61_environment_scale_qc.json").write_text(json.dumps(qc,indent=2,ensure_ascii=False),encoding="utf8")
    print("[QC]",json.dumps(qc,ensure_ascii=False))
if __name__=="__main__":main()
