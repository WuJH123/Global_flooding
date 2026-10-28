"""Stage58: UCDB city-boundary clipped 5/10/20km built-environment comparisons.

Clip each grid polygon to the UCDB polygon in EPSG:6933, weight cached
BUILT-S subcell density by overlap area, compare with Stage56 coarse
city proxy. This is precise CITY BOUNDARY intersection of 5km cells,
but density within each intersected 5km cell is assumed uniform; it is
NOT native 100m pixel-exact city building area. No GEE.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd,geopandas as gpd,pyogrio
from shapely.geometry import box

def clean(c):return str(c).lstrip("\ufeff").strip()
def read_cities(gpkg,layer,ids):
    info=pyogrio.read_info(gpkg,layer=layer)
    fields={clean(v):v for v in info["fields"]}
    key=next((fields[k] for k in ("ID_UC_G0","ID_UC","city_id") if k in fields),None)
    if not key:raise ValueError("UCDB stable ID missing; available "+str(list(fields)))
    cities=gpd.read_file(gpkg,layer=layer,columns=[key])
    cities.columns=[clean(c) for c in cities.columns]
    k=clean(key)
    cities["city_id"]=cities[k].astype(str)
    cities=cities.loc[cities.city_id.isin(set(map(str,ids)))].copy()
    if cities.empty:raise ValueError("No city ids intersect UCDB layer")
    if cities.crs is None:raise ValueError("UCDB CRS absent")
    cities=cities.to_crs("EPSG:6933")
    cities=cities.loc[~cities.geometry.is_empty].copy()
    if cities.city_id.duplicated().any():
        cities=cities.dissolve(by="city_id",as_index=False)
    return cities[["city_id","geometry"]]

def city_scale(city,frame,scale):
    # Input frame is environment_grid_5km OR 10km/20km; shapes exactly aligned
    width=scale*1000
    subset=frame.loc[frame.grid_x.between(int(np.floor(city.bounds[0]/width))-1,
                                         int(np.ceil(city.bounds[2]/width))+1)&
                     frame.grid_y.between(int(np.floor(city.bounds[1]/width))-1,
                                         int(np.ceil(city.bounds[3]/width))+1)]
    areas=[];built=[];cover=[];r=[]
    for q in subset.itertuples(index=False):
        geom=box(q.grid_x*width,q.grid_y*width,(q.grid_x+1)*width,(q.grid_y+1)*width)
        area=city.intersection(geom).area
        if area<=0:continue
        areas.append(area)
        # density under piecewise uniform source-cell hypothesis
        built.append(float(q.built_fraction_cell)*area)
        cover.append(float(q.source_valid_fraction)*area)
        r.append(float(q.built_fraction_cell))
    if not areas:return None
    aa=np.array(areas);rr=np.array(r)
    mean=float(np.sum(built)/np.sum(aa))
    sd=float(np.sqrt(np.average((rr-mean)**2,weights=aa)))
    return {"scale_km":scale,"cells_intersected":len(aa),
            "intersected_area_km2":float(aa.sum()/1e6),
            "city_built_fraction":mean,"city_built_sd":sd,
            "city_built_range":float(rr.max()-rr.min()),
            "coverage_fraction":float(sum(cover)/aa.sum()),
            "city_area_fraction_high_built":float(aa[rr>=.2].sum()/aa.sum())}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--gpkg",type=Path,required=True)
    p.add_argument("--layer",default="GHS_UCDB_THEME_GENERAL_CHARACTERISTICS_GLOBE_R2024A")
    p.add_argument("--fine-root",type=Path,required=True)
    p.add_argument("--coarse-root",type=Path,required=True)
    p.add_argument("--city-ids",type=Path,required=True,help="CSV with city_id and optional climate_zone and location_quality")
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--max-cities",type=int,default=60)
    a=p.parse_args()
    ids=pd.read_csv(a.city_ids,dtype={"city_id":str})
    if "city_id" not in ids or ids.city_id.duplicated().any():raise ValueError("Unique city_id required")
    ids=ids.iloc[:a.max_cities]
    cities=read_cities(a.gpkg,a.layer,ids.city_id)
    frames={}
    import duckdb
    con=duckdb.connect()
    fine=sorted(a.fine_root.glob("fine5_parent_*.parquet"))
    if not fine:raise FileNotFoundError("Stage54 5km cache absent")
    raw=con.from_parquet([str(x) for x in fine]).df()
    frames[5]=raw.rename(columns={"grid_x5":"grid_x","grid_y5":"grid_y"})
    for scale in (10,20):
        frames[scale]=pd.read_parquet(a.coarse_root/f"environment_grid_{scale}km_2020.parquet")
    out=[]
    for q in cities.itertuples(index=False):
        for scale in (5,10,20):
            val=city_scale(q.geometry,frames[scale],scale)
            if val is not None:out.append({"city_id":q.city_id,**val})
    ans=pd.DataFrame(out).merge(ids,on="city_id",how="left",validate="many_to_one")
    a.out.mkdir(parents=True,exist_ok=True)
    ans.to_parquet(a.out/"58_city_boundary_multiscale.parquet",index=False)
    qc={"cities_requested":len(ids),"cities_found":cities.city_id.nunique(),
        "city_scale_rows":len(ans),"scales":sorted(ans.scale_km.unique().tolist()),
        "method":"UCDB polygon x cached 5/10/20km cell exact geometric overlap, within-cell constant built density; not source-100m-exact",
        "status":"EXPLORATORY_CITY_BOUNDARY"}
    (a.out/"58_boundary_qc.json").write_text(json.dumps(qc,indent=2),encoding="utf-8")
    print("[DONE]",qc)
if __name__=="__main__":main()
