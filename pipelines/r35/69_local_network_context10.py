# -*- coding: utf-8 -*-
"""Stage69: local streamed line-network statistics over frozen 10km urban cells.

Works with GRIP4 roads or HydroRIVERS line vectors. Intersect geometry in
EPSG:6933; sum actual clipped lengths. Road density is a **road-network proxy**,
NOT measured storm-sewer capacity. Exposes source coverage and all-zero
uncertainty (missing source vs no roads) separately.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import geopandas as gpd,pandas as pd,numpy as np
from shapely.geometry import box
from shapely import intersection,length
import pyogrio

def run(grid,source,out,kind,layer=None,batch_size=100000):
    if kind not in ("roads","rivers"):raise ValueError("Select roads/rivers")
    f=pd.read_parquet(grid,columns=["grid_id","grid_x","grid_y"])
    if f.grid_id.duplicated().any():raise ValueError("Duplicate grid keys")
    urban=gpd.GeoDataFrame(f,geometry=[box(int(x)*10000,int(y)*10000,
         (int(x)+1)*10000,(int(y)+1)*10000) for x,y in zip(f.grid_x,f.grid_y)],
         crs="EPSG:6933")
    info=pyogrio.read_info(source,layer=layer)
    n=int(info["features"])
    sums={}
    for start in range(0,n,batch_size):
        part=pyogrio.read_dataframe(source,layer=layer,skip_features=start,
                                    max_features=batch_size,read_geometry=True,
                                    columns=[])
        if part.empty:continue
        if part.crs is None:raise ValueError("Network vector missing CRS")
        part=part.to_crs("EPSG:6933")
        part=part[part.geometry.notna()&~part.geometry.is_empty]
        if part.empty:continue
        candidates=gpd.sjoin(part[["geometry"]],urban[["grid_id","geometry"]],
                    how="inner",predicate="intersects")
        if candidates.empty:continue
        pieces=urban.loc[candidates.index_right,"geometry"].to_numpy()
        segment=length(intersection(candidates.geometry.to_numpy(),pieces))/1000
        good=np.isfinite(segment)&(segment>0)
        sub=pd.DataFrame({"grid_id":candidates.loc[good,"grid_id"].to_numpy(),
                          "km":segment[good]})
        totals=sub.groupby("grid_id").km.sum()
        for k,v in totals.items():sums[k]=sums.get(k,0.)+float(v)
        if (start//batch_size)%10==0:print("[NETWORK]",kind,start,n,flush=True)
    f["network_length_km"]=f.grid_id.map(sums).fillna(0)
    f["network_density_km_per_km2"]=f.network_length_km/100.
    f["source_vector"]=str(source)
    f["network_kind"]=kind
    out.mkdir(parents=True,exist_ok=True)
    f.to_parquet(out/f"69_{kind}_grid10.parquet",index=False)
    qc={"kind":kind,"lines_source_features":n,"urban_grids":len(f),
        "nonzero_cells":int((f.network_length_km>0).sum()),
        "length_units":"km","density_units":"km per km2",
        "area_definition":"100km2 full grid; urban-specific length not inferred",
        "limitation":"Zero may mean no vector road or incomplete source coverage. Not drainage capacity."}
    (out/f"69_{kind}_qc.json").write_text(json.dumps(qc,indent=2),encoding="utf-8")
    print("[DONE]",qc)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--grid",type=Path,required=True)
    p.add_argument("--source",type=Path,required=True)
    p.add_argument("--kind",choices=["roads","rivers"],required=True)
    p.add_argument("--layer")
    p.add_argument("--batch-size",type=int,default=100000)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args();run(a.grid,a.source,a.out,a.kind,a.layer,a.batch_size)
if __name__=="__main__":main()
