# -*- coding: utf-8 -*-
"""Stage70 Results 3.1: fixed 10km urban reporting footprint & composition.

Observational coverage only. No report is NOT evidence of a flood-free cell.
Optional Köppen native categorical raster reprojected by MODE to fixed 10km
projection, avoids center-point grid shifts. Country/reporting digital
coverage is optional external context, NOT inferred from news volume.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import pandas as pd,numpy as np,duckdb
import rasterio
from rasterio.warp import reproject,Resampling
from affine import Affine

def run(grid,links,out,pop=None,koppen=None):
    urban=pd.read_parquet(grid)
    if urban.grid_id.duplicated().any():raise RuntimeError("Duplicate universe grid_id")
    files=sorted(links.glob("63_event_grid_links_*.parquet"))
    if not files:raise FileNotFoundError("No geocoded event links")
    con=duckdb.connect()
    rel=con.from_parquet([str(x) for x in files])
    rel.create_view("links")
    grouped=con.execute("""SELECT grid_id,COUNT(DISTINCT event_id) n_geocoded_records,
      SUM(CAST("primary" AS INT)) n_primary_links,
      SUM(CAST("primary" AND is_high_geocode_specificity AS INT)) n_highspecificity_primary,
      MIN(TRY_CAST(start_date AS DATE)) first_report_day,
      MAX(TRY_CAST(start_date AS DATE)) last_report_day
      FROM links GROUP BY grid_id""").df()
    joined=urban.merge(grouped,on="grid_id",how="left",validate="one_to_one")
    for c in ["n_geocoded_records","n_primary_links","n_highspecificity_primary"]:
        joined[c]=joined[c].fillna(0).astype(int)
    joined["ever_reported"]=joined.n_geocoded_records.gt(0)
    if pop is not None:
        population=pd.read_parquet(pop)
        if population.grid_id.duplicated().any():raise ValueError("Duplicate pop grid")
        joined=joined.merge(population[["grid_id","population_count"]],
                            on="grid_id",how="left",validate="one_to_one")
    if koppen is not None:
        with rasterio.open(koppen) as ds:
            mapped=np.full((1464,3472),-999,dtype=np.int16)
            reproject(source=rasterio.band(ds,1),destination=mapped,
                src_transform=ds.transform,src_crs=ds.crs,
                src_nodata=ds.nodata,dst_transform=Affine(10000,0,-17360000,0,-10000,7320000),
                dst_crs="EPSG:6933",dst_nodata=-999,resampling=Resampling.mode,
                num_threads=2)
        cc=(joined.grid_x.to_numpy()+1736).astype(int)
        rr=(731-joined.grid_y.to_numpy()).astype(int)
        if np.any((cc<0)|(cc>=3472)|(rr<0)|(rr>=1464)):
            raise RuntimeError("Grid center index outside frozen raster")
        joined["koppen_native_class"]=mapped[rr,cc]
        joined.loc[joined.koppen_native_class<1,"koppen_native_class"]=np.nan
    out.mkdir(parents=True,exist_ok=True)
    joined.to_parquet(out/"70_urban_reporting_coverage.parquet",index=False)
    groupcols=["koppen_native_class"] if "koppen_native_class" in joined else []
    if groupcols:
        result=joined.groupby(groupcols,dropna=False).agg(
            total_urban_grids=("grid_id","count"),
            ever_reported_grids=("ever_reported","sum"),
            reported_event_links=("n_geocoded_records","sum"),
            urban_area_km2=("urban_area_km2_2020","sum")).reset_index()
        result["archive_report_fraction"]=result.ever_reported_grids/result.total_urban_grids
        result.to_csv(out/"70_coverage_by_koppen_class.csv",index=False)
    qc={"total_urban_grids":len(joined),
        "ever_reported_cells":int(joined.ever_reported.sum()),
        "no_report_cells":int((~joined.ever_reported).sum()),
        "total_urban_area_km2":float(joined.urban_area_km2_2020.sum()),
        "geocoded_report_records_total_with_duplicate_grid_intersections":int(joined.n_geocoded_records.sum()),
        "scope":"all fixed E2020 broad urban grid cells; news reporting coverage NOT flood risk",
        "caution":"Unmatched/broad geocodes and false negatives require separate quality and country-by-year reporting bias analyses"}
    (out/"70_observation_qc.json").write_text(json.dumps(qc,ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",qc)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--grid",type=Path,required=True)
    p.add_argument("--links-root",type=Path,required=True)
    p.add_argument("--population-2020",type=Path)
    p.add_argument("--koppen",type=Path)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args();run(a.grid,a.links_root,a.out,a.population_2020,a.koppen)
if __name__=="__main__":main()
