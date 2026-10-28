# -*- coding: utf-8 -*-
"""Stage56: bridge fine-scale heterogeneity into existing city response pipeline.

Take Stage55 20km grid summaries, weight these GRID contextual attributes by
the existing UCDB grid-city overlap area, and append city-level modifiers to
a NEW city table; do not overwrite Stage30b city file or assign subgrids flood
labels. These city modifiers are descriptive area-overlap proxies, not
within-city 5km polygon intersection and not sewer/drainage capacity.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd

MAPPING={
    "built_fraction_cell":"ghsl_built_5km_mean_wmean",
    "child_built_sd":"ghsl_built_5km_sd_wmean",
    "child_built_max":"ghsl_built_5km_max_wmean",
    "high_built_area_fraction":"ghsl_built_5km_high_area_wmean",
    "source_valid_fraction":"ghsl_built_5km_coverage_wmean",
}

def build(city_table, grid_context, crosswalk, epoch=2020):
    cities=city_table.copy()
    env=grid_context.copy()
    links=crosswalk.copy()
    if "city_id" not in cities or cities.city_id.isna().any() or cities.city_id.duplicated().any():
        raise ValueError("City table must have unique nonnull city_id")
    if not {"grid_id","epoch",*MAPPING}<=set(env.columns):
        raise ValueError("Stage55 environment missing required 20km fields")
    if env.grid_id.duplicated().any() or (env.epoch!=epoch).any():
        raise ValueError("Stage55 environment duplicate grid_id or wrong epoch")
    if not {"grid_id","city_id","overlap_km2"}<=set(links):
        raise ValueError("City crosswalk must contain grid_id, city_id, overlap_km2")
    if links.duplicated(["grid_id","city_id"]).any():raise ValueError("Repeated city-grid links")
    links["overlap_km2"]=pd.to_numeric(links.overlap_km2,errors="coerce")
    if links.overlap_km2.isna().any() or (links.overlap_km2<=0).any():
        raise ValueError("Invalid overlap_km2 weights")
    for d in (cities,links):
        d["city_id"]=d.city_id.astype(str)
    for d in (env,links):
        d["grid_id"]=d.grid_id.astype(str)
    joined=links.merge(env[["grid_id",*MAPPING]],on="grid_id",
                       how="left",validate="many_to_one")
    grouped=[]
    for city_id,g in joined.groupby("city_id",sort=True):
        row={"city_id":city_id,
             "built5_city_grid_count":int(g.grid_id.nunique()),
             "built5_overlap_total_km2":float(g.overlap_km2.sum())}
        for source,target in MAPPING.items():
            numeric=pd.to_numeric(g[source],errors="coerce")
            ok=numeric.notna()&np.isfinite(numeric)&(g.overlap_km2>0)
            row[target]=float(np.average(numeric[ok],weights=g.loc[ok,"overlap_km2"])) if ok.any() else np.nan
            row[target+"_weight_coverage"]=float(g.loc[ok,"overlap_km2"].sum()/g.overlap_km2.sum())
        grouped.append(row)
    derived=pd.DataFrame(grouped)
    if derived.empty:raise RuntimeError("No city x grid links matched")
    conflicts=set(MAPPING.values()) & set(cities.columns)
    if conflicts:raise ValueError("Destination city table already has modifiers: "+str(sorted(conflicts)))
    out=cities.merge(derived,on="city_id",how="left",validate="one_to_one")
    if len(out)!=len(cities):raise RuntimeError("City row loss")
    return out,derived,joined

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--grid-20km",type=Path,required=True)
    p.add_argument("--city-crosswalk",type=Path,required=True)
    p.add_argument("--city-table",type=Path,required=True)
    p.add_argument("--out-dir",type=Path,required=True)
    a=p.parse_args()
    read=lambda x:pd.read_parquet(x) if x.suffix.lower()==".parquet" else pd.read_csv(x)
    out,derived,joined=build(read(a.city_table),read(a.grid_20km),read(a.city_crosswalk))
    a.out_dir.mkdir(parents=True,exist_ok=True)
    out.to_parquet(a.out_dir/"56_city_environment_with_built5.parquet",index=False)
    derived.to_csv(a.out_dir/"56_built5_city_modifiers.csv",index=False)
    status={"status":"PASS_STRUCTURE","city_rows":len(out),
            "city_modifiers_with_data":int(derived["ghsl_built_5km_sd_wmean"].notna().sum()),
            "city_crosswalk_rows":len(joined),
            "note":"UCDB-overlap-weighted summary of 20km grid internal 5km heterogeneity. Not independent 5km event labels."}
    (a.out_dir/"56_city_bridge_qc.json").write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",json.dumps(status,ensure_ascii=False))
if __name__=="__main__":main()
