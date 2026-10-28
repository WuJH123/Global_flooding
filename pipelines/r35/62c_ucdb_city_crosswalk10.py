# -*- coding: utf-8 -*-
"""Stage62c: link stable 2020 UCDB CITY polygons to locked 10km EASE grid.

Overlay by intersection AREA (not center-point): preserve multi-city links,
area-weighted weights, and dominant city ID for model validation. No GEE.
UCDB morphology (2025 administrative names) and 2020 GHSL urban eligibility
are different references; provenance recorded in QC.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd,geopandas as gpd
from shapely.geometry import box

LAYER="GHS_UCDB_THEME_GENERAL_CHARACTERISTICS_GLOBE_R2024A"
def run(grid,gpkg,out,layer=LAYER):
    g=pd.read_parquet(grid,columns=["grid_id","grid_x","grid_y","urban_area_km2_2020"])
    if g.grid_id.duplicated().any():raise ValueError("Duplicate frozen grid")
    # GeoPackage stores BOM-prefix field name in some releases.
    cities=gpd.read_file(gpkg,layer=layer)
    cities.columns=[str(x).lstrip("\ufeff") for x in cities.columns]
    field=next((x for x in ("ID_UC_G0","ID_UC","city_id") if x in cities),None)
    if field is None:raise ValueError("Cannot locate stable UCDB city identifier")
    if cities.crs is None:raise ValueError("UCDB layer lacks CRS")
    cities=cities[[field,"geometry"]].rename(columns={field:"city_id"})
    cities["city_id"]=cities.city_id.astype(str)
    cities=cities.to_crs("EPSG:6933")
    cities=cities.loc[cities.geometry.notna()&~cities.geometry.is_empty].copy()
    cities["geometry"]=cities.geometry.make_valid()
    if cities.city_id.duplicated().any():
        cities=cities.dissolve(by="city_id",as_index=False)
    grids=gpd.GeoDataFrame(g,geometry=[box(int(x)*10000,int(y)*10000,
                     (int(x)+1)*10000,(int(y)+1)*10000)
                     for x,y in zip(g.grid_x,g.grid_y)],crs="EPSG:6933")
    # spatial join returns bbox/intersect candidates; true geometry overlap after join
    hit=gpd.sjoin(grids[["grid_id","geometry"]],cities[["city_id","geometry"]],
                  how="inner",predicate="intersects")
    if hit.empty:raise RuntimeError("No UCDB urban polygon intersections")
    from shapely import intersection,area
    city_geoms=cities.geometry
    grid_geo=grids.set_index("grid_id").geometry
    # Avoid full NxM overlay, only intersect candidate pairs.
    cell=hit.geometry.to_numpy()
    other=cities.loc[hit["index_right"].to_numpy()].geometry.to_numpy()
    hit["overlap_km2"]=np.asarray(area(intersection(cell,other)))/1e6
    hit=hit[hit.overlap_km2>0].copy()
    x=hit[["grid_id","city_id","overlap_km2"]].sort_values(
        ["grid_id","overlap_km2","city_id"],ascending=[True,False,True])
    x["grid_fraction_in_city_polygon"]=x.overlap_km2/100.
    out.mkdir(parents=True,exist_ok=True)
    x.to_parquet(out/"62c_grid_city_crosswalk_10km.parquet",index=False)
    dominant=x.drop_duplicates("grid_id")[["grid_id","city_id","overlap_km2"]]
    dominant.to_parquet(out/"62c_dominant_city_grid10.parquet",index=False)
    qc={"urban_grid_count":len(g),"matched_grids":int(x.grid_id.nunique()),
        "city_links":len(x),"cities":int(x.city_id.nunique()),
        "unassigned_grids":int(len(g)-x.grid_id.nunique()),
        "multiple_city_grids":int((x.groupby("grid_id").size()>1).sum()),
        "city_layer":layer,"stable_id_field":field,
        "area_projection":"EPSG:6933; overlap_m2/1e6; full cell 100km2",
        "caution":"Urban frame E2020 GHSL SMOD; UCDB polygons are another reference epoch"}
    (out/"62c_city_qc.json").write_text(json.dumps(qc,ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",qc)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--grid",type=Path,required=True)
    p.add_argument("--ucdb-gpkg",type=Path,required=True)
    p.add_argument("--layer",default=LAYER)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args();run(a.grid,a.ucdb_gpkg,a.out,a.layer)
if __name__=="__main__":main()
