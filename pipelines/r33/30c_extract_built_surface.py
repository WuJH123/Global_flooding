"""Stage30c: LOCAL additive GHS-BUILT-S raster -> existing EASE 20-km grid.

Supports official GHS_BUILT_S_EYYYY_*_54009_100/1000_* raster GeoTIFFs.
GHSL pixels store built-up SURFACE AREA (m2), NOT a built-up fraction.

For each 20-km EPSG:6933 cell, average SOURCE BUILT AREA / SOURCE PIXEL
AREA on an equal-area source raster (e.g. World Mollweide) using area-weighted
GDAL resampling. Multiply the resulting fraction by the 20-km EASE area
(400e6 m2) to recover additive built surface. Valid source coverage is
reprojected separately; unknown source coverage is NOT classified as 0%.

E2030 is a PROJECTION/EXTRAPOLATION, exported ONLY as a scenario/context
epoch. This command NEVER copies E2030 to 2020/2025. No GEE calls.
"""
from __future__ import annotations
import argparse, hashlib, json, re, sys
from pathlib import Path
import numpy as np
import pandas as pd

GRID_M=20000
GRID_AREA_M2=GRID_M*GRID_M
PATTERN=re.compile(r"^GHS_BUILT_S_E(?P<year>20[0-3]\d)_GLOBE_R2023A_54009_(?P<meters>100|1000)_V\d+_\d+\.tif$",re.I)

def inspect_file(raster_path,requested_epoch=None):
    import rasterio
    match=PATTERN.match(raster_path.name)
    if not match:
        raise ValueError("Not an official single-epoch total GHS-BUILT-S R2023A "
                         "100m/1km Mollweide filename: "+raster_path.name)
    year=int(match.group("year"))
    if requested_epoch is not None and int(requested_epoch)!=year:
        raise ValueError("Filename E epoch and --epoch disagree; refuse temporal substitution")
    if year not in (2000,2005,2010,2015,2020,2025,2030):
        raise ValueError("Selected epoch must be in the accepted project series 2000–2030")
    if not raster_path.is_file():raise FileNotFoundError(raster_path)
    with rasterio.open(raster_path) as src:
        if src.count!=1:raise ValueError("Expected one total GHS-BUILT-S raster band")
        if src.crs is None or not src.crs.is_projected:
            raise ValueError("Source requires a projected, meter-based equal-area CRS")
        if abs(src.transform.b)>1e-7 or abs(src.transform.d)>1e-7:
            raise ValueError("Rotated source grids are not supported by this extractor")
        declared=int(match.group("meters"))
        resolution=(abs(src.transform.a),abs(src.transform.e))
        if any(abs(v-declared)>declared*.01 for v in resolution):
            raise ValueError(f"Source pixel size {resolution} inconsistent with filename {declared}")
        unit=str(src.crs.linear_units).lower()
        if "met" not in unit:
            raise ValueError(f"Source projected unit must be metres, not {unit}")
        crs_name=str(src.crs).lower()
        wkt=src.crs.to_wkt().lower()
        if not ("mollweide" in crs_name or "mollweide" in wkt
                or "54009" in crs_name or "6933" in crs_name):
            raise ValueError("Requires equal-area World Mollweide or EASE-grid CRS")
        return dict(epoch=year,source=str(raster_path),width=src.width,
                    height=src.height,crs=str(src.crs),
                    resolution=resolution,source_pixel_m2=abs(src.transform.a*src.transform.e),
                    nodata=src.nodata,dtype=src.dtypes[0],kind="scenario" if year>=2025 else "historical_reconstruction")

def read_centers(path):
    x=pd.read_csv(path,dtype={"grid_id":str})
    required={"grid_id","grid_x","grid_y"}
    if not required.issubset(x.columns):
        raise ValueError("Center file requires grid_id, grid_x, grid_y")
    x=x[["grid_id","grid_x","grid_y"]].copy()
    if x.isna().any().any() or x.grid_id.duplicated().any():
        raise ValueError("Null/duplicate grid center ID")
    x["grid_x"]=pd.to_numeric(x.grid_x,errors="raise").astype("int64")
    x["grid_y"]=pd.to_numeric(x.grid_y,errors="raise").astype("int64")
    keys=x.grid_x.astype(str)+"_"+x.grid_y.astype(str)
    if not (keys==x.grid_id).all():
        raise ValueError("grid_id and integer EPSG:6933 grid_x/grid_y disagree")
    return x

def calculate_tile(src,tx,ty,tile_cells,source_pixel_m2,max_source_pixels):
    """Return built fraction and fraction of target 20km pixel with valid source."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import Affine
    from rasterio.windows import from_bounds
    from rasterio.warp import reproject, transform_bounds
    x0=tx*tile_cells
    y0=ty*tile_cells
    target_transform=Affine(GRID_M,0,x0*GRID_M,0,-GRID_M,(y0+tile_cells)*GRID_M)
    bounds=(x0*GRID_M,y0*GRID_M,(x0+tile_cells)*GRID_M,(y0+tile_cells)*GRID_M)
    left,bottom,right,top=transform_bounds("EPSG:6933",src.crs,*bounds,densify_pts=41)
    # Crop to actual source footprint; tiles outside source have NO coverage.
    left=max(left,src.bounds.left);right=min(right,src.bounds.right)
    bottom=max(bottom,src.bounds.bottom);top=min(top,src.bounds.top)
    shape=(tile_cells,tile_cells)
    if not all(np.isfinite([left,bottom,right,top])) or left>=right or bottom>=top:
        return np.full(shape,np.nan,dtype=float),np.zeros(shape,dtype=float),0
    window=from_bounds(left,bottom,right,top,transform=src.transform)
    window=window.round_offsets().round_lengths()
    # padding accommodates resampler at the source raster's edge of window
    from rasterio.windows import Window
    col=max(0,int(window.col_off)-3)
    row=max(0,int(window.row_off)-3)
    cend=min(src.width,int(window.col_off+window.width)+4)
    rend=min(src.height,int(window.row_off+window.height)+4)
    if cend<=col or rend<=row:
        return np.full(shape,np.nan),np.zeros(shape),0
    if (cend-col)*(rend-row)>max_source_pixels:
        raise RuntimeError(
            "One tile needs too many source pixels. Lower --tile-cells "
            f"(wanted {(cend-col)*(rend-row):,} > {max_source_pixels:,})")
    view=Window(col,row,cend-col,rend-row)
    masked=src.read(1,window=view,masked=True)
    raw=np.asarray(masked.data,dtype=np.float32)
    valid=(~np.ma.getmaskarray(masked)) & np.isfinite(raw) & (raw>=0)
    # GHSL built m2 should not exceed its own 100/1000m area.
    unreasonable=valid & (raw>source_pixel_m2*1.05)
    if unreasonable.any():
        raise ValueError("GHS-BUILT-S pixel has built area greater than "
                         "native pixel area: inspect units, selected band, NoData")
    # Unknown source is ZERO for surface SUM numerator, but it is NOT counted
    # in denominator when reporting how much of target is actually observed.
    density=np.where(valid,raw/source_pixel_m2,0).astype("float32")
    valid_weight=valid.astype("float32")
    built=np.zeros(shape,dtype="float32")
    coverage=np.zeros(shape,dtype="float32")
    transform=src.window_transform(view)
    kwargs={"src_transform":transform,"src_crs":src.crs,
            "dst_transform":target_transform,"dst_crs":"EPSG:6933",
            "resampling":Resampling.average,"init_dest_nodata":True}
    reproject(density,built,**kwargs)
    reproject(valid_weight,coverage,**kwargs)
    # Set no-data where there is zero valid coverage. Partial missing coverage
    # is tracked, and optionally rejected by --min-coverage.
    built=built.astype(float)
    coverage=np.clip(coverage.astype(float),0,1)
    built[coverage<=1e-6]=np.nan
    return built,coverage,int(valid.sum())

def compute_grid(src,centers,tile_cells=4,min_coverage=.95,
                 max_source_pixels=16_000_000,log_every=100):
    if not 0<min_coverage<=1:raise ValueError("--min-coverage must be (0,1]")
    if not 1<=tile_cells<=32:raise ValueError("--tile-cells must be 1..32")
    items=centers.copy()
    items["_tx"]=np.floor_divide(items.grid_x.to_numpy(),tile_cells)
    items["_ty"]=np.floor_divide(items.grid_y.to_numpy(),tile_cells)
    results=[]
    for k,((tx,ty),group) in enumerate(items.groupby(["_tx","_ty"],sort=True)):
        frac,cov,_=calculate_tile(src,int(tx),int(ty),tile_cells,
                                  abs(src.transform.a*src.transform.e),
                                  max_source_pixels)
        rows=tile_cells-1-(group.grid_y.to_numpy()-int(ty)*tile_cells)
        cols=group.grid_x.to_numpy()-int(tx)*tile_cells
        f=frac[rows,cols]
        c=cov[rows,cols]
        f=np.where(c>=min_coverage,f,np.nan)
        if np.isfinite(f).any() and ((f[np.isfinite(f)]<-.001).any() or
                                     (f[np.isfinite(f)]>1.01).any()):
            raise ValueError(f"Built fraction outside physical [0,1] in 20km tile {tx},{ty}")
        results.append(pd.DataFrame({
            "grid_id":group.grid_id.to_numpy(),
            "grid_x":group.grid_x.to_numpy(),
            "grid_y":group.grid_y.to_numpy(),
            "built_fraction":f,
            "built_m2":f*GRID_AREA_M2,
            "source_coverage_fraction":c,
            "grid_area_m2_epsg6933":float(GRID_AREA_M2)}))
        if log_every and (k+1)%log_every==0:
            print(f"[BUILT] tiles {k+1}, grids {sum(map(len,results))}",flush=True)
    if not results:raise RuntimeError("No grid samples")
    result=pd.concat(results,ignore_index=True).sort_values("grid_id")
    if result.grid_id.duplicated().any() or len(result)!=len(centers):
        raise RuntimeError("Raster aggregate lost/duplicated grid IDs")
    return result.reset_index(drop=True)

def aggregate_city_context(grid_table,overlap_csv):
    """Area-overlap-weighted GRID CONTEXT, not city-footprint built area.

    Accurate within-city built surface must intersect ORIGINAL 100m pixels
    with UCDB city polygons. Twenty-km grid overlap weights approximate
    a city-level context ONLY; no false city built-up totals.
    """
    links=pd.read_csv(overlap_csv,dtype={"grid_id":str,"city_id":str})
    needed={"grid_id","city_id","overlap_km2"}
    if not needed.issubset(links):raise ValueError("UCDB overlap CSV missing "+str(needed))
    links=links[list(needed)].copy()
    if links.duplicated(["grid_id","city_id"]).any():
        raise ValueError("Duplicate city-grid overlap links")
    links["overlap_km2"]=pd.to_numeric(links.overlap_km2,errors="coerce")
    links=links.loc[links.overlap_km2.gt(0)].copy()
    g=links.merge(grid_table[["grid_id","built_fraction"]],
                  on="grid_id",how="inner",validate="many_to_one")
    def city_row(block):
        good=block.built_fraction.notna()
        total=block.overlap_km2.sum()
        valid=block.loc[good,"overlap_km2"].sum()
        mean=(np.average(block.loc[good,"built_fraction"],
                         weights=block.loc[good,"overlap_km2"])
              if good.any() else np.nan)
        return pd.Series({"overlap_weighted_grid_built_fraction":mean,
                          "overlap_coverage_fraction":valid/total if total>0 else np.nan,
                          "n_intersecting_20km_grids":block.grid_id.nunique()})
    return g.groupby("city_id",sort=True).apply(city_row,include_groups=False).reset_index()


def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument("--raster",type=Path,required=True)
    a.add_argument("--epoch",type=int)
    a.add_argument("--centers",type=Path,required=True)
    a.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    a.add_argument("--out",type=Path)
    a.add_argument("--inspect",action="store_true")
    a.add_argument("--city-overlap-csv",type=Path,
                   help="Optional: 2030 CITY CONTEXT from already verified UCDB overlaps; not city-accurate built totals")
    a.add_argument("--tile-cells",type=int,default=4)
    a.add_argument("--min-coverage",type=float,default=.95)
    a.add_argument("--max-source-pixels",type=int,default=16_000_000)
    a.add_argument("--max-grids",type=int)
    a.add_argument("--log-every",type=int,default=100)
    args=a.parse_args()
    information=inspect_file(args.raster,args.epoch)
    print("[RASTER]",json.dumps(information,ensure_ascii=False,indent=2),flush=True)
    if args.inspect:return
    centers=read_centers(args.centers)
    if args.max_grids:
        if args.max_grids<1:raise ValueError("--max-grids must be positive")
        centers=centers.iloc[np.linspace(0,len(centers)-1,min(args.max_grids,len(centers)),dtype=int)].copy()
    import rasterio
    with rasterio.open(args.raster) as src:
        d=compute_grid(src,centers,args.tile_cells,args.min_coverage,
                       args.max_source_pixels,args.log_every)
    d["epoch"]=information["epoch"]
    d["temporal_kind"]=information["kind"]
    out=args.out or args.base/"mechanism_project"/"30c_built_raster"
    out.mkdir(parents=True,exist_ok=True)
    suffix=f"_SMOKE{args.max_grids}" if args.max_grids else ""
    destination=out/f"30c_built_grid_{information['epoch']}{suffix}.parquet"
    d.to_parquet(destination,index=False)
    d.to_csv(out/f"30c_built_grid_{information['epoch']}{suffix}_qc.csv",index=False)
    if args.city_overlap_csv:
        context=aggregate_city_context(d,args.city_overlap_csv)
        context["epoch"]=information["epoch"]
        context["temporal_kind"]=information["kind"]
        context.to_csv(out/f"30c_city_grid_context_{information['epoch']}{suffix}.csv",index=False)
        context.to_parquet(out/f"30c_city_grid_context_{information['epoch']}{suffix}.parquet",index=False)
    report={"epoch":information["epoch"],"temporal_kind":information["kind"],
            "input_raster":str(args.raster),
            "source_pixel_m2":information["source_pixel_m2"],
            "n_requested_grids":len(centers),"n_output_grids":len(d),
            "n_valid_grids":int(d.built_fraction.notna().sum()),
            "missing_grid_fraction":float(d.built_fraction.isna().mean()),
            "min_source_coverage":args.min_coverage,
            "built_fraction_min":float(d.built_fraction.min()) if d.built_fraction.notna().any() else None,
            "built_fraction_max":float(d.built_fraction.max()) if d.built_fraction.notna().any() else None,
            "crs_output":"EPSG:6933; aligned 20-km grid_x/grid_y",
            "source_year_not_observation_year":"E2025 and E2030 are projection/extrapolation; NEVER backfill E2030 into historical years",
            "smoke":bool(args.max_grids)}
    (out/f"30c_built_grid_{information['epoch']}{suffix}_manifest.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",destination,report,flush=True)

if __name__=="__main__":main()
