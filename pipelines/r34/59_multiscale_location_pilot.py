# -*- coding: utf-8 -*-
"""Stage59: local multiscale event GEOLOCATION support pilot, no false labels.

Cross-city, Köppen-zone and geocoding-footprint-size stratified pilot from
original Groundsource polygons. At 5/10/20km and two grid origins, measure
how many grid cells each geocoded polygon could refer to and max area share.
These are location-SUPPORT DIAGNOSTICS, not 5km/10km reported flood labels
or a completed precipitation-response model. No single 'optimal scale'
without corresponding meteorological source and paired model validation.
"""
from __future__ import annotations
import argparse,hashlib,json,math
from pathlib import Path
from collections import defaultdict
import numpy as np
import pandas as pd
import geopandas as gpd
import pyarrow.dataset as ds
from pyproj import Transformer
from shapely import from_wkb,from_wkt,from_geojson
from shapely.geometry import box
from shapely.ops import transform
from shapely.strtree import STRtree

SCALES=(5,10,20)

def geo(v):
    if v is None:return None
    if isinstance(v,(bytes,bytearray,memoryview)):
        try:return from_wkb(bytes(v))
        except Exception:return None
    if isinstance(v,str):
        try:
            if v.lstrip().startswith("{"):return from_geojson(v)
            return from_wkt(v)
        except Exception:return None
    return None

def area_precision(area):
    if not np.isfinite(area):return "unknown"
    if area<=5:return "small_0_5km2"
    if area<=100:return "medium_5_100km2"
    return "broad_gt100km2"

def footprint_metrics(poly,scale_km,offset=0,max_cells=1000):
    """Number of overlapped cells and dominant cell overlap, not flood extent."""
    width=scale_km*1000.
    xmin,ymin,xmax,ymax=poly.bounds
    x0=math.floor(xmin/width-offset)
    x1=math.floor(xmax/width-offset)
    y0=math.floor(ymin/width-offset)
    y1=math.floor(ymax/width-offset)
    size=(x1-x0+1)*(y1-y0+1)
    if size>max_cells:return {"status":"TOO_BROAD","touched_cells":np.nan,
                               "max_geocode_overlap_fraction":np.nan}
    count=0;best=0.
    for x in range(x0,x1+1):
        for y in range(y0,y1+1):
            tile=box((x+offset)*width,(y+offset)*width,
                     (x+1+offset)*width,(y+1+offset)*width)
            if not poly.intersects(tile):continue
            a=poly.intersection(tile).area
            if a>1e-6:
                count+=1;best=max(best,a)
    return {"status":"OK","touched_cells":count,
            "max_geocode_overlap_fraction":min(1.,best/max(poly.area,1e-12))}

def majority_zones(crosswalk,zonegrids):
    a=crosswalk[["grid_id","city_id","overlap_km2"]].copy()
    b=zonegrids[["grid_id","zone"]].drop_duplicates("grid_id")
    if b.grid_id.duplicated().any():raise ValueError("Duplicate zone-grid keys")
    a["grid_id"]=a.grid_id.astype(str);a["city_id"]=a.city_id.astype(str)
    b["grid_id"]=b.grid_id.astype(str)
    merged=a.merge(b,on="grid_id",how="left",validate="many_to_one")
    merged=merged.dropna(subset=["zone"])
    if merged.empty:raise RuntimeError("No climate-zone overlap; need M3 zone table")
    x=merged.groupby(["city_id","zone"],observed=True).overlap_km2.sum().reset_index()
    x=x.sort_values(["city_id","overlap_km2","zone"],ascending=[True,False,True])
    return x.drop_duplicates("city_id")[["city_id","zone"]]

def choose_candidates(crosswalk,zones,max_per_zone=130,seed=2026):
    support=(crosswalk.groupby("city_id",sort=True).agg(
        n_grids=("grid_id","nunique"),area_km2=("overlap_km2","sum")).reset_index())
    support["city_id"]=support.city_id.astype(str)
    d=support.merge(zones,on="city_id",how="inner",validate="one_to_one")
    d=d[d.n_grids>=1].copy()
    rng=np.random.default_rng(seed)
    sampled=[]
    for zone,grp in d.groupby("zone",sort=True):
        # Stratify large/small city footprints; do not only pick megacities.
        grp=grp.copy()
        grp["size_bin"]=pd.qcut(grp.area_km2.rank(method="first"),q=min(3,len(grp)),labels=False)
        subset=[]
        for _,bin_group in grp.groupby("size_bin"):
            n=min(len(bin_group),max(1,math.ceil(max_per_zone/3)))
            subset.append(bin_group.iloc[rng.choice(len(bin_group),n,replace=False)])
        sampled.append(pd.concat(subset).head(max_per_zone))
    return pd.concat(sampled,ignore_index=True)

def choose_final(event_rows,candidate,target=60,min_events=10):
    events=pd.DataFrame(event_rows)
    if events.empty:raise RuntimeError("No source polygons matched candidate UCDB cities")
    city=events.groupby("city_id").agg(
        n_events=("event_id","nunique"),
        n_small=("precision_class",lambda v:(v=="small_0_5km2").sum()),
        n_medium=("precision_class",lambda v:(v=="medium_5_100km2").sum()),
        n_broad=("precision_class",lambda v:(v=="broad_gt100km2").sum())).reset_index()
    city=city.merge(candidate[["city_id","zone","n_grids","area_km2"]],
                   on="city_id",validate="one_to_one")
    city=city[city.n_events>=min_events].copy()
    if city.empty:raise RuntimeError("No cities with enough location polygons; reduce --min-events after audit")
    keys=["n_small","n_medium","n_broad"]
    names=["small_0_5km2","medium_5_100km2","broad_gt100km2"]
    city["location_precision_proxy"]=[names[np.argmax(v)] for v in city[keys].to_numpy()]
    city=city.sort_values(["zone","location_precision_proxy","n_events"],
                          ascending=[True,True,False])
    # Balanced zone x precision, round-robin; refuse universal optimal scale.
    selected=[];groups={key:list(part.index) for key,part in
                        city.groupby(["zone","location_precision_proxy"],sort=True)}
    while len(selected)<target and any(groups.values()):
        for key,indices in groups.items():
            if indices and len(selected)<target:selected.append(indices.pop(0))
    return city.loc[selected].reset_index(drop=True),events

def run(events_path,city_gpkg,layer,id_field,crosswalk,zones,out,
        target=60,candidates_per_zone=130,min_events=10,max_events_per_city=50,
        geom_field="geometry",id_col="uuid",event_crs="EPSG:4326",seed=2026):
    cross=pd.read_csv(crosswalk,dtype={"grid_id":str,"city_id":str})
    cross["overlap_km2"]=pd.to_numeric(cross.overlap_km2,errors="raise")
    zones=majority_zones(cross,pd.read_csv(zones,dtype={"grid_id":str}))
    candidate=choose_candidates(cross,zones,candidates_per_zone,seed)
    if candidate.empty:raise RuntimeError("No candidate cities")
    g=gpd.read_file(city_gpkg,layer=layer,columns=[id_field],engine="pyogrio")
    if g.crs is None:raise RuntimeError("UCDB has no CRS")
    g["city_id"]=g[id_field].astype("string").str.strip().str.replace(r"^(\d+)\.0$",r"\1",regex=True)
    g=g[g.city_id.isin(candidate.city_id)].to_crs(event_crs)
    if g.city_id.duplicated().any() or g.empty:raise RuntimeError("UCDB city selection invalid")
    polys=list(g.geometry);ids=list(g.city_id.astype(str))
    tree=STRtree(polys)
    # Sample by deterministic hash ranking for reproducible years/cities.
    keep=defaultdict(list);invalid=0;matched=0;seen=set()
    source=ds.dataset(str(events_path),format="parquet")
    for col in (geom_field,id_col):
        if col not in source.schema.names:raise ValueError(f"Event parquet missing {col}")
    scanner=source.scanner(columns=[id_col,geom_field],batch_size=10000)
    for batch in scanner.to_batches():
        frame=batch.to_pandas()
        for event_id,gval in frame[[id_col,geom_field]].itertuples(index=False,name=None):
            if event_id is None:continue
            geom=geo(gval)
            if geom is None or geom.is_empty or not geom.is_valid or geom.area<=0:
                invalid+=1;continue
            rp=geom.representative_point()
            choices=tree.query(rp,predicate="within")
            if len(choices)!=1:continue
            city_id=ids[int(choices[0])]
            hashval=int.from_bytes(hashlib.blake2b(f"{event_id}".encode(),digest_size=8).digest(),"big")
            bucket=keep[city_id]
            if any(item[1]==str(event_id) for item in bucket):continue
            bucket.append((hashval,str(event_id),geom))
            bucket.sort(key=lambda item:item[0])
            if len(bucket)>max_events_per_city:bucket.pop()
            matched+=1
    to_equal_area=Transformer.from_crs(event_crs,"EPSG:6933",always_xy=True).transform
    event_rows=[]
    for city_id,items in keep.items():
        for h,event_id,geom in items:
            p=transform(to_equal_area,geom)
            if p.is_empty or not p.is_valid or p.area<=0:continue
            event_rows.append({"city_id":city_id,"event_id":event_id,
               "geocode_area_km2":p.area/1e6,"precision_class":area_precision(p.area/1e6),
               "geometry_6933":p})
    selected,all_events=choose_final(event_rows,candidate,target,min_events)
    selected_ids=set(selected.city_id)
    final=[]
    for r in event_rows:
        if r["city_id"] not in selected_ids:continue
        poly=r["geometry_6933"];stem={k:v for k,v in r.items() if k!="geometry_6933"}
        for scale in SCALES:
            for offset in (0.,.5):
                m=footprint_metrics(poly,scale,offset)
                final.append({**stem,"scale_km":scale,"origin_offset_fraction":offset,**m})
    out.mkdir(parents=True,exist_ok=True)
    selected.to_csv(out/"59_pilot_cities.csv",index=False)
    pd.DataFrame(final).to_parquet(out/"59_geocoding_scale_sensitivity.parquet",index=False)
    tab=pd.DataFrame(final)
    stats=(tab.groupby(["scale_km","origin_offset_fraction","precision_class"],
         dropna=False).agg(n_events=("event_id","count"),
         fraction_single=("touched_cells",lambda v:float((v==1).mean())),
         median_touched=("touched_cells","median"),
         median_dominant_overlap=("max_geocode_overlap_fraction","median"),
         n_too_broad=("status",lambda v:int((v=="TOO_BROAD").sum()))).reset_index())
    stats.to_csv(out/"59_scale_stratified_summary.csv",index=False)
    coverage=selected.groupby(["zone","location_precision_proxy"]).size().reset_index(name="n_cities")
    coverage.to_csv(out/"59_pilot_balance.csv",index=False)
    qc={"status":"LOCATION_SUPPORT_ONLY_NOT_OPTIMAL_SCALE",
        "selected_cities":len(selected),"candidate_cities":len(candidate),
        "n_sampled_event_ids":int(tab.event_id.nunique()),
        "events_with_invalid_geometry_scanned":invalid,
        "events_matching_city_candidates":matched,
        "event_geometry_crs_assumed":event_crs,
        "original_storm_independence_not_established":True,
        "geolocation_proxy":"source geocoded polygon area, NOT manually validated precision",
        "climate_zone_provenance":str(zones),
        "required_for_final_scale_decision":[
          "same-source 5/10km meteorological exposure, not interpolated 20km",
          "event/reference dates rebuilt from geocoded polygons",
          "same-location-precision and same-storm heldout performance",
          "both grid origins and all climate zones covered"],
        "verdict":"NO_UNIVERSAL_OPTIMUM_FROM_GEOCODING_ALONE"}
    (out/"59_scale_qc.json").write_text(json.dumps(qc,indent=2,ensure_ascii=False),encoding="utf8")
    print("[QC]",json.dumps(qc,ensure_ascii=False))
    return selected,tab,stats

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--events",required=True,type=Path)
    p.add_argument("--ucdb",required=True,type=Path)
    p.add_argument("--ucdb-layer",default="GHS_UCDB_THEME_GENERAL_CHARACTERISTICS_GLOBE_R2024A")
    p.add_argument("--ucdb-id",default="ID_UC_G0")
    p.add_argument("--crosswalk",required=True,type=Path)
    p.add_argument("--zone-grids",required=True,type=Path,
                   help="grid_id,zone from documented Köppen reference; report if E2025 geography")
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--target-cities",type=int,default=60)
    p.add_argument("--candidate-per-zone",type=int,default=130)
    p.add_argument("--min-events",type=int,default=10)
    p.add_argument("--max-events-per-city",type=int,default=50)
    p.add_argument("--geom-field",default="geometry")
    p.add_argument("--id-col",default="uuid")
    p.add_argument("--event-crs",default="EPSG:4326")
    p.add_argument("--seed",type=int,default=2026)
    a=p.parse_args()
    if a.target_cities<1 or a.candidate_per_zone<1 or a.max_events_per_city<1:
        raise ValueError("Invalid sampling parameters")
    run(a.events,a.ucdb,a.ucdb_layer,a.ucdb_id,a.crosswalk,a.zone_grids,a.out,
        a.target_cities,a.candidate_per_zone,a.min_events,a.max_events_per_city,
        a.geom_field,a.id_col,a.event_crs,a.seed)
if __name__=="__main__":main()
