"""Stage72: hard-stop provenance/area/geometry audit for frozen GF10 datasets.

No raster projection/centroid approximation is allowed to create a NEW grid.
All materialized tables MUST use frozen Stage62 grid_id and native lattice.
Fails closed on suspicious area, duplicates, mismatched coords or missing source.
"""
from __future__ import annotations
import argparse,json,hashlib
from pathlib import Path
import numpy as np,pandas as pd
from pyproj import Geod,Transformer

CELL=10000
NOMINAL=100000000
def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        while c:=f.read(8<<20):h.update(c)
    return h.hexdigest()
def validate(grid,others,output,max_geodesic=1000,tolerance=0.005):
    p=Path(grid)
    if not p.exists():raise FileNotFoundError(p)
    g=pd.read_parquet(p)
    expected={"grid_id","grid_x","grid_y","cell_area_m2","urban_fraction_2020",
              "source_valid_fraction","urban_area_km2_2020"}
    if not expected.issubset(g):raise ValueError("Missing frozen contract fields "+str(expected-set(g)))
    if g.grid_id.duplicated().any() or g[["grid_x","grid_y"]].duplicated().any():
        raise ValueError("Duplicate frozen cell ID")
    xs=g.grid_x.to_numpy(dtype=int);ys=g.grid_y.to_numpy(dtype=int)
    ids=np.array([f"{x}_{y}" for x,y in zip(xs,ys)])
    if not np.array_equal(g.grid_id.astype(str).to_numpy(),ids):
        raise ValueError("grid_id is not exactly grid_x_grid_y")
    if np.any((xs<-1736)|(xs>=1736)|(ys<-732)|(ys>=732)):
        raise ValueError("Frozen grid outside valid contracted domain")
    area=g.cell_area_m2.to_numpy(dtype=float)
    if not np.isfinite(area).all() or not np.allclose(area,NOMINAL,rtol=0,atol=1e-5):
        raise ValueError("Nominal EPSG:6933 full-cell area not 100,000,000 m2")
    u=g.urban_fraction_2020.to_numpy(dtype=float)
    v=g.source_valid_fraction.to_numpy(dtype=float)
    if np.any(~np.isfinite(u)|~np.isfinite(v)|(u<0)|(v<0)|(u>v+1e-5)|(v>1+1e-5)):
        raise ValueError("Nonphysical urban/valid fraction")
    if not np.allclose(g.urban_area_km2_2020.to_numpy(dtype=float),u*100,atol=1e-3):
        raise ValueError("Urban fraction * FULL 100km2 not equal urban area")
    # WGS84 geodesic area of densified EPSG:6933 edge; no latitude cosine approximation.
    rng=np.random.default_rng(2026)
    sel=rng.choice(len(g),size=min(max_geodesic,len(g)),replace=False)
    t=Transformer.from_crs("EPSG:6933","EPSG:4326",always_xy=True)
    geod=Geod(ellps="WGS84");z=np.linspace(0,1,33)
    ratio=[]
    for i in sel:
        x0=xs[i]*CELL;x1=x0+CELL;y0=ys[i]*CELL;y1=y0+CELL
        X=np.r_[x0+CELL*z,np.full_like(z,x1),x1-CELL*z,np.full_like(z,x0)]
        Y=np.r_[np.full_like(z,y0),y0+CELL*z,np.full_like(z,y1),y1-CELL*z]
        lo,la=t.transform(X,Y)
        if not np.isfinite(lo).all() or not np.isfinite(la).all():
            raise ValueError("Invalid ellipsoid coordinate")
        a,_=geod.polygon_area_perimeter(lo,la)
        ratio.append(abs(a)/NOMINAL)
    if max(abs(k-1) for k in ratio)>tolerance:
        raise ValueError("WGS84 geodetic boundary area differs more than tolerance")
    result={"status":"PASS_GF10_V1_AREA_AND_KEYS","frozen_file":str(p),
            "sha256":sha(p),"n_cells":len(g),"nominal_full_cell_km2":100,
            "min_geodesic_ratio":min(ratio),"max_geodesic_ratio":max(ratio),
            "edge_limit":"EPSG:6933 +/-86 latitude, 2020 urban only",
            "other_tables":[]}
    accepted=set(g.grid_id.astype(str))
    for path in others:
        q=Path(path)
        if not q.exists():raise FileNotFoundError(q)
        df=pd.read_parquet(q,columns=["grid_id"])
        unique=df.grid_id.astype(str)
        outsiders=set(unique.unique())-accepted
        if outsiders:
            raise ValueError(f"{q} contains {len(outsiders)} cells NOT in frozen urban domain")
        result["other_tables"].append({"path":str(q),"rows":len(df),
            "unique_grid_ids":unique.nunique(),"sha256":sha(q)})
    output.mkdir(parents=True,exist_ok=True)
    (output/"72_integrity.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print("[PASS]",result)
    return result
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--grid",type=Path,required=True)
    p.add_argument("--tables",type=Path,nargs="*",default=[])
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--geodesic-samples",type=int,default=1000)
    a=p.parse_args()
    if a.geodesic_samples<1:raise ValueError("Must check geodetic area")
    validate(a.grid,a.tables,a.out,a.geodesic_samples)
if __name__=="__main__":main()
