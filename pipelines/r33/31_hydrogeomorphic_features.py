"""Stage31: derive auditable hydrogeomorphic proxies on 20 km EASE cells.

Optional high-resolution *real HAND* raster, DEM, impervious fraction raster,
and vector river network. No computation of HAND from MERIT Hydro hnd (hnd is
hydro-adjusted elevation). Each raster is warped on demand into EPSG:6933
then sampled on a user-set subgrid (default 250 m). Cells with poor pixel
coverage are flagged, NOT treated as dry/non-flood. Drainage capacity is NEVER
inferred from proxies. Global river vectors may be RAM-intensive; country/
continent tiles are recommended.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd
from pyproj import Transformer

GRID=20000.0

def geom_stats(hand,dem,impervious,coverage_threshold=.7):
    res={}
    def valid(array):
        if array is None:return np.zeros((0,0),bool)
        return np.isfinite(array)
    if hand is not None:
        ok=valid(hand)&(hand>=0)
        res["hand_valid_fraction"]=float(ok.mean())
        if ok.any():
            res["hand_p10_m"]=float(np.quantile(hand[ok],.10))
            res["hand_lt2_fraction"]=float((ok&(hand<2)).sum()/ok.sum())
            res["hand_lt5_fraction"]=float((ok&(hand<5)).sum()/ok.sum())
            if impervious is not None:
                impok=valid(impervious)&(impervious>=0)&(impervious<=1)
                shared=ok&impok
                res["hand_impervious_overlap_coverage"]=float(shared.mean())
                weighted=impervious[shared]
                res["impervious_surface_weighted_hand_lt2_share"]=(
                    float(weighted[hand[shared]<2].sum()/weighted.sum())
                    if weighted.sum()>0 else np.nan)
        if ok.mean()<coverage_threshold:
            for key in ("hand_p10_m","hand_lt2_fraction","hand_lt5_fraction",
                        "impervious_surface_weighted_hand_lt2_share"):
                res[key]=np.nan
    if dem is not None:
        ok=valid(dem)
        res["dem_valid_fraction"]=float(ok.mean())
        if ok.mean()>=coverage_threshold:
            res["dem_relief_p90_minus_p10_m"]=float(np.quantile(dem[ok],.9)-np.quantile(dem[ok],.1))
        else:
            res["dem_relief_p90_minus_p10_m"]=np.nan
    if impervious is not None:
        ok=valid(impervious)&(impervious>=0)&(impervious<=1)
        res["impervious_valid_fraction"]=float(ok.mean())
        res["impervious_mean_fraction"]=(float(impervious[ok].mean())
                                           if ok.mean()>=coverage_threshold else np.nan)
    return res

def raster_tile(path,x,y,pixels,kind):
    # Lazy optional geo dependency; data-free tests do not require rasterio.
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds
    if kind=="categorical":algo=Resampling.nearest
    else:algo=Resampling.bilinear
    with rasterio.open(path) as src:
        if src.crs is None:raise ValueError(f"No CRS in {path}")
        with WarpedVRT(src,crs="EPSG:6933",resampling=algo) as vrt:
            l,b,r,t=x*GRID,y*GRID,(x+1)*GRID,(y+1)*GRID
            win=from_bounds(l,b,r,t,transform=vrt.transform)
            arr=vrt.read(1,window=win,out_shape=(pixels,pixels),
                         resampling=algo,masked=True)
            vals=arr.astype("float64").filled(np.nan)
            return vals

def nearest_rivers(centers,vector_path,max_features=3000000):
    import geopandas as gpd
    from shapely.geometry import Point
    from shapely.strtree import STRtree
    from shapely.ops import nearest_points
    from pyproj import Transformer
    lines=gpd.read_file(vector_path,columns=[],engine="pyogrio")
    if len(lines)>max_features:
        raise RuntimeError("River file too large for one STRtree. Split by continent; "
                           "pass regionally clipped river vectors instead.")
    if lines.crs is None:raise ValueError("River CRS metadata missing")
    lines=lines.to_crs("EPSG:6933")
    geom=[g for g in lines.geometry if g is not None and not g.is_empty]
    tree=STRtree(geom)
    result={}
    for x,y,gid in centers:
        pt=Point((x+.5)*GRID,(y+.5)*GRID)
        idx=tree.nearest(pt)
        result[gid]=float(pt.distance(geom[int(idx)]))
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--centers",type=Path,required=True)
    p.add_argument("--hand-tif",type=Path)
    p.add_argument("--hand-provenance",type=str,default=None,
                   help="Citation/source of actual HAND computation; required with HAND.")
    p.add_argument("--dem-tif",type=Path)
    p.add_argument("--impervious-tif",type=Path)
    p.add_argument("--rivers",type=Path)
    p.add_argument("--sample-pixels",type=int,default=80)
    p.add_argument("--min-valid",type=float,default=.70)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    if not any([a.hand_tif,a.dem_tif,a.impervious_tif,a.rivers]):
        raise ValueError("Supply an actual raster or river-vector source")
    if a.hand_tif and not a.hand_provenance:
        raise ValueError("--hand-provenance required. MERIT Hydro hnd IS NOT HAND")
    if a.sample_pixels<10 or a.sample_pixels>400:
        raise ValueError("Sample pixels must be in 10..400")
    if not 0<a.min_valid<=1:raise ValueError("min-valid out of (0,1]")
    points=pd.read_csv(a.centers,dtype={"grid_id":str})
    if not {"grid_id","grid_x","grid_y"}.issubset(points.columns):
        raise ValueError("centers needs grid_id/grid_x/grid_y")
    if points.grid_id.duplicated().any():
        raise RuntimeError("Duplicate grid ID")
    rows=[]
    for i,r in enumerate(points.itertuples(index=False)):
        g={"grid_id":str(r.grid_id)}
        vals={}
        for field,path,kind in (("hand",a.hand_tif,"continuous"),
                                ("dem",a.dem_tif,"continuous"),
                                ("impervious",a.impervious_tif,"continuous")):
            if path is not None:
                if not path.exists():raise FileNotFoundError(path)
                vals[field]=raster_tile(path,int(r.grid_x),int(r.grid_y),
                                        a.sample_pixels,kind)
            else:vals[field]=None
        g.update(geom_stats(vals["hand"],vals["dem"],vals["impervious"],a.min_valid))
        rows.append(g)
        if (i+1)%1000==0:print("[31]",i+1,"of",len(points),flush=True)
    tab=pd.DataFrame(rows)
    if a.rivers:
        if not a.rivers.exists():raise FileNotFoundError(a.rivers)
        centers=[(int(r.grid_x),int(r.grid_y),str(r.grid_id))
                 for r in points.itertuples(index=False)]
        distance=nearest_rivers(centers,a.rivers)
        tab["nearest_mapped_river_km"]=tab.grid_id.map(distance)/1000
    out=a.out;out.mkdir(parents=True,exist_ok=True)
    tab.to_parquet(out/"31_hydrogeomorphic_grid_context.parquet",index=False)
    tab.to_csv(out/"31_hydrogeomorphic_grid_context.csv",index=False)
    (out/"31_manifest.json").write_text(json.dumps({
        "hand_source":str(a.hand_tif) if a.hand_tif else None,
        "hand_provenance":a.hand_provenance,
        "dem_source":str(a.dem_tif) if a.dem_tif else None,
        "impervious_source":str(a.impervious_tif) if a.impervious_tif else None,
        "rivers":str(a.rivers) if a.rivers else None,
        "sample_resolution_m_approx":GRID/a.sample_pixels,
        "n_grids":len(tab),
        "meaning":"Raster-sampled proxies; no pipe/drainage-capacity measurement; spatial coverage must be checked."
    },indent=2,ensure_ascii=False),encoding="utf-8")
    print("[DONE]",len(tab),out)

if __name__=="__main__":main()
