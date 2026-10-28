# -*- coding: utf-8 -*-
"""Stage55: aggregate *additive* 5km environment statistics to 10/20km offline.

Recompute ratios from summed numerators/denominators. Do not average quantiles,
temporal maxima, river distances, or flood occurrence labels. QC optionally
compares the 20km built fraction against existing Stage30c output.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
import duckdb

FINE=5
FIELDS=("built_total_m2","built_valid_area_m2","cell_area_m2")

def rollup(input_root:Path,out_root:Path,scales=(10,20),reference=None,
           strict_parity=False,max_p95_diff=.03):
    sources=sorted(input_root.glob("fine5_parent_*.parquet"))
    if not sources:raise FileNotFoundError("No Stage54 fine5_parent_*.parquet in "+str(input_root))
    input_manifest=input_root/"54_fine5_manifest.json"
    if not input_manifest.exists():raise RuntimeError("Stage54 manifest missing; cannot audit files")
    meta=json.loads(input_manifest.read_text(encoding="utf-8"))
    if len(sources)!=len(meta["parts"]):raise RuntimeError("Manifest/file count mismatch")
    paths={str(p.resolve()) for p in sources}
    if paths!={str(Path(p["file"]).resolve()) for p in meta["parts"]}:
        raise RuntimeError("Manifest does not match Parquet paths")
    con=duckdb.connect()
    con.execute("PRAGMA threads=4")
    files=[str(p) for p in sources]
    con.from_parquet(files).create_view("fine")
    pre=con.execute("""SELECT COUNT(*) n, COUNT(DISTINCT grid5_id) n_unique,
            COUNT(DISTINCT parent_grid_id) parents, MIN(epoch) year_min, MAX(epoch) year_max,
            SUM(CASE WHEN grid_x5 != parent_grid_x*4 +
                     (grid_x5 - parent_grid_x*4) % 4 OR
                     NOT (grid_x5 BETWEEN parent_grid_x*4 AND parent_grid_x*4+3) OR
                     NOT (grid_y5 BETWEEN parent_grid_y*4 AND parent_grid_y*4+3)
                  THEN 1 ELSE 0 END) bad_nesting
            FROM fine""").fetchdf().iloc[0].to_dict()
    if int(pre["n"])!=int(meta["fine5_grids"]) or int(pre["n"])!=int(pre["n_unique"]):
        raise RuntimeError("Stage54 count or unique 5km grid ID mismatch")
    if int(pre["bad_nesting"])!=0 or int(pre["year_min"])!=int(pre["year_max"]):
        raise RuntimeError("Bad 5km hierarchy or mixed epochs")
    if int(pre["parents"])*16!=int(pre["n"]):raise RuntimeError("Partial 20km parent(s)")
    out_root.mkdir(parents=True,exist_ok=True)
    summary={"fine5_rows":int(pre["n"]),"fine5_parents":int(pre["parents"]),
             "epoch":int(pre["year_min"]),"aggregation":"additive numerators; ratio recomputed",
             "outputs":{}}
    results={}
    for scale in scales:
        if scale not in (10,20):raise ValueError("Only nested 10km/20km scales")
        factor=scale//FINE
        n_required=factor*factor
        query=f"""
        WITH base AS (
          SELECT CAST(FLOOR(grid_x5 / {factor}.0) AS BIGINT) grid_x,
                 CAST(FLOOR(grid_y5 / {factor}.0) AS BIGINT) grid_y,
                 epoch, built_total_m2, built_valid_area_m2, cell_area_m2,
                 built_fraction_cell
          FROM fine
        ),
        agg AS (
          SELECT grid_x,grid_y,epoch,
                 COUNT(*) n_children,
                 SUM(built_total_m2) built_total_m2,
                 SUM(built_valid_area_m2) built_valid_area_m2,
                 SUM(cell_area_m2) cell_area_m2,
                 MIN(built_fraction_cell) child_built_min,
                 MAX(built_fraction_cell) child_built_max,
                 STDDEV_POP(built_fraction_cell) child_built_sd,
                 SUM(CASE WHEN built_fraction_cell >= 0.20
                          THEN cell_area_m2 ELSE 0 END) high_built_area_m2
          FROM base GROUP BY grid_x,grid_y,epoch
        )
        SELECT CAST(grid_x AS VARCHAR)||'_'||CAST(grid_y AS VARCHAR) grid_id,
               grid_x,grid_y,epoch,n_children,built_total_m2,
               built_valid_area_m2,cell_area_m2,child_built_min,
               child_built_max,child_built_sd,high_built_area_m2,
               built_total_m2/NULLIF(cell_area_m2,0) built_fraction_cell,
               built_total_m2/NULLIF(built_valid_area_m2,0) built_fraction_valid,
               built_valid_area_m2/NULLIF(cell_area_m2,0) source_valid_fraction,
               high_built_area_m2/NULLIF(cell_area_m2,0) high_built_area_fraction
        FROM agg ORDER BY grid_x,grid_y"""
        frame=con.execute(query).fetchdf()
        if frame.grid_id.duplicated().any() or (frame.n_children!=n_required).any():
            raise RuntimeError(f"Partial/duplicate {scale}km area aggregation")
        if (frame.built_total_m2<-1).any() or (
            frame.built_total_m2>frame.built_valid_area_m2+frame.cell_area_m2*.01).any():
            raise RuntimeError("Built area out of physical bounds")
        if (abs(frame.cell_area_m2-(scale*1000)**2)>1).any():
            raise RuntimeError(f"Wrong {scale}km area")
        dest=out_root/f"environment_grid_{scale}km_2020.parquet"
        frame.to_parquet(dest,index=False)
        results[scale]=frame
        summary["outputs"][str(scale)]={"path":str(dest),"rows":len(frame),
                                         "n_children":n_required,
                                         "valid_fraction_below_95pct":int((frame.source_valid_fraction<.95).sum())}
        print("[SAVED]",dest,"rows",len(frame),"source coverage <95%",summary["outputs"][str(scale)]["valid_fraction_below_95pct"])
    con.close()
    if reference is not None:
        if 20 not in results:raise ValueError("Reference comparison requires --scales 20")
        ref=(pd.read_parquet(reference) if reference.suffix==".parquet"
             else pd.read_csv(reference))
        if not {"grid_id","built_fraction"}<=set(ref):
            raise ValueError("Stage30c reference needs grid_id,built_fraction")
        if ref.grid_id.duplicated().any():raise RuntimeError("Duplicate Stage30c parent IDs")
        df=results[20][["grid_id","built_fraction_cell","source_valid_fraction"]].merge(
            ref[["grid_id","built_fraction"]],on="grid_id",how="left",validate="one_to_one")
        df["abs_diff"]=(df.built_fraction_cell-df.built_fraction).abs()
        df.to_csv(out_root/"55_stage30c_built_parity.csv",index=False)
        good=df.abs_diff.dropna()
        if good.empty:raise RuntimeError("No comparable Stage30c built fractions")
        summary["stage30c_parity"]={"n_compared":len(good),"n_reference_missing":int(df.built_fraction.isna().sum()),
                    "median_abs_diff":float(good.median()),"p95_abs_diff":float(good.quantile(.95)),
                    "max_abs_diff":float(good.max()),"threshold_p95":max_p95_diff,
                    "status":"PASS" if good.quantile(.95)<=max_p95_diff else "REVIEW"}
        if strict_parity and summary["stage30c_parity"]["status"]!="PASS":
            (out_root/"55_scale_qc.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
            raise RuntimeError("Stage30c parity failed. Verify source raster, pixel semantics, and coastal denominators")
    (out_root/"55_scale_qc.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    print("[QC]",json.dumps(summary,ensure_ascii=False,indent=2))
    return summary

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fine-root",type=Path,required=True)
    p.add_argument("--out-root",type=Path,required=True)
    p.add_argument("--scales",default="10,20")
    p.add_argument("--reference-20km",type=Path)
    p.add_argument("--strict-parity",action="store_true")
    p.add_argument("--max-p95-diff",type=float,default=.03)
    a=p.parse_args()
    ss=[int(x) for x in a.scales.split(",")]
    if len(set(ss))!=len(ss):raise ValueError("Duplicate scale")
    if a.max_p95_diff<=0:raise ValueError("Invalid parity tolerance")
    rollup(a.fine_root,a.out_root,ss,a.reference_20km,a.strict_parity,a.max_p95_diff)
if __name__=="__main__":main()
