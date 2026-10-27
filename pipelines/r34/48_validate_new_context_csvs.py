# -*- coding: utf-8 -*-
"""Audit Stage46/47 Drive CSVs after download, before any merge into main tables.

Examples:
python pipelines/r34/48_validate_new_context_csvs.py --kind ghsl --csv-root E:\...\GHSL_Epochs --expected-ids E:\...\grid_centers.csv
python pipelines/r34/48_validate_new_context_csvs.py --kind era5 --csv-root E:\...\ERA5_Annual --expected-ids E:\...\grid_centers.csv
No GEE/API calls. Never silently impute invalid pixels or duplicate exports.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import pandas as pd
import numpy as np

EPOCH_PATTERN = re.compile(r"ghsl_(\d{4})_built_total_m2$")
YEAR_PATTERN = re.compile(r"era5_(\d{4})_annual_precip_mm$")
AREA = 400000000.0

def read_frame(path):
    f = pd.read_csv(path, low_memory=False, dtype={"grid_id":"string"})
    f.columns = [str(c).lstrip("\ufeff").strip() for c in f.columns]
    if "grid_id" not in f: raise RuntimeError(f"No grid_id: {path}")
    if f.grid_id.isna().any() or f.grid_id.duplicated().any():
        raise RuntimeError(f"Null/duplicate grid_id within: {path}")
    return f

def audit_files(paths, kind):
    seen=set()
    records=[]
    errors=[]
    ids_by_year={}
    for path in paths:
        f=read_frame(path)
        match=EPOCH_PATTERN if kind=="ghsl" else YEAR_PATTERN
        years=sorted(int(match.fullmatch(c).group(1)) for c in f.columns if match.fullmatch(c))
        if not years:
            raise RuntimeError(f"No {kind} per-year fields: {path}")
        for y in years:
            prefix=f"ghsl_{y}_" if kind=="ghsl" else f"era5_{y}_"
            current=set(f.grid_id.astype(str))
            duplicate=current & ids_by_year.get(y,set())
            if duplicate: errors.append(f"Duplicate {y} grid IDs across files, e.g. {sorted(duplicate)[:3]}")
            ids_by_year.setdefault(y,set()).update(current)
            if kind=="ghsl":
                required=["built_total_m2","built_nres_m2","built_valid_area_m2",
                          "pop_total","pop_valid_area_m2","smod_urban_area_m2",
                          "smod_dense_area_m2","smod_land_area_m2"]
                if any(prefix+k not in f for k in required):
                    errors.append(f"{path.name}: missing {y} GHSL bands")
                    continue
                x=f[[prefix+k for k in required]].apply(pd.to_numeric,errors="coerce")
                b=x[prefix+"built_total_m2"]
                n=x[prefix+"built_nres_m2"]
                urban=x[prefix+"smod_urban_area_m2"]
                dense=x[prefix+"smod_dense_area_m2"]
                land=x[prefix+"smod_land_area_m2"]
                if ((b<0)|(b>AREA*1.1)|(n<0)|(n>b+1e-5)).any():
                    errors.append(f"GHSL {y}: built area impossible or NRES greater than total")
                if ((urban<0)|(urban>land+1e-4)|(dense>urban+1e-4)|(land>AREA*1.2)).any():
                    errors.append(f"GHSL {y}: SMOD area ordering invalid")
                if ((x[prefix+"pop_total"]<0)|(x[prefix+"built_valid_area_m2"]>AREA*1.2)).any():
                    errors.append(f"GHSL {y}: invalid population/valid area")
                rec={"year":y,"file":path.name,"rows":len(f),"missing_built":int(b.isna().sum()),
                     "missing_pop":int(x[prefix+"pop_total"].isna().sum()),
                     "missing_smod":int(land.isna().sum())}
            else:
                required=["annual_precip_mm","rx1day_mm","heavy20_days",
                          "wet1_days","sm1_mean","temp2m_mean_c",
                          "valid_precip_days","valid_temp_days","valid_sm1_days",
                          "negative_precip_days"]
                if any(prefix+k not in f for k in required):
                    errors.append(f"{path.name}: missing {y} ERA5 annual bands")
                    continue
                x=f[[prefix+k for k in required]].apply(pd.to_numeric,errors="coerce")
                n=x[prefix+"valid_precip_days"]
                if ((x[prefix+"annual_precip_mm"]<0)|
                    (x[prefix+"rx1day_mm"]<0)|
                    (x[prefix+"heavy20_days"]>n+1)|
                    (x[prefix+"wet1_days"]>n+1)|
                    (n>366.5)|(n<0)|
                    (x[prefix+"sm1_mean"]<0)|(x[prefix+"sm1_mean"]>1)).any():
                    errors.append(f"ERA5 {y}: physical/count range invalid")
                rec={"year":y,"file":path.name,"rows":len(f),
                     "missing_precip":int(x[prefix+"annual_precip_mm"].isna().sum()),
                     "missing_temp":int(x[prefix+"temp2m_mean_c"].isna().sum()),
                     "missing_sm1":int(x[prefix+"sm1_mean"].isna().sum()),
                     "negative_precip_days_sum":float(x[prefix+"negative_precip_days"].sum())}
            records.append(rec)
    return records,errors,ids_by_year

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kind",choices=["ghsl","era5"],required=True)
    p.add_argument("--csv-root",type=Path,required=True)
    p.add_argument("--expected-ids",type=Path,help="grid_centers.csv with one row per expected grid")
    p.add_argument("--out",type=Path,default=Path("audits/stage46_47"))
    p.add_argument("--allow-test",action="store_true",help="Include _test CSVs ONLY for isolated smoke audit")
    p.add_argument("--require-full",action="store_true",help="Fail unless every output year has exact expected grid ID set")
    args=p.parse_args()
    pattern="ghsl_epoch_v1_*.csv" if args.kind=="ghsl" else "era5yr_v1_*.csv"
    files=sorted(args.csv_root.rglob(pattern))
    if not args.allow_test:files=[f for f in files if "_test" not in f.stem.lower()]
    if not files:raise FileNotFoundError(f"No CSVs matching {pattern} under {args.csv_root}")
    rows,errors,ids_by_year=audit_files(files,args.kind)
    expected=None
    if args.expected_ids:
        expected=set(read_frame(args.expected_ids).grid_id.astype(str))
        for y,actual in ids_by_year.items():
            extra=actual-expected
            missing=expected-actual
            if extra:errors.append(f"{y}: {len(extra)} unknown grid IDs")
            if missing and args.require_full:errors.append(f"{y}: {len(missing)} missing grid IDs")
    args.out.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(rows).to_csv(args.out/f"48_{args.kind}_file_audit.csv",index=False)
    report={"kind":args.kind,"input_files":len(files),
            "years":sorted(ids_by_year),"unique_grids_per_year":{str(y):len(v) for y,v in ids_by_year.items()},
            "expected_grids":len(expected) if expected is not None else None,
            "errors":errors,"status":"FAIL" if errors else "PASS",
            "warning":"PASS checks exported CSV data structure and plausible values; it does not validate source physics or establish flood risk."}
    (args.out/f"48_{args.kind}_qc.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print("[QC]",json.dumps(report,indent=2,ensure_ascii=False))
    if errors:raise RuntimeError(f"QC FAILED: {len(errors)} issues")

if __name__=="__main__":main()
