# -*- coding: utf-8 -*-
"""Stage50b: strictly audit Stage46b BUILT and Stage50 IMERG CSV exports.

Uses exact expected grid ID sets when --require-full; never imputes NA values
and excludes TEST outputs unless --allow-test. QC is not source numeric parity.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import numpy as np
import pandas as pd
GLOBS={"built":"built_hist_v2_*.csv","imerg":"imergann_v2_*.csv"}
PAT={"built":re.compile(r"^built_(\d{4})_built_total_m2$"),
     "imerg":re.compile(r"^imerg_(\d{4})_total_mm$")}
AREA=400_000_000

def inspect(kind,files,years,expected=None,require_full=False):
    recorded={};errors=[];items=[]
    for file in files:
        data=pd.read_csv(file,dtype={"grid_id":"string"},low_memory=False)
        data.columns=[str(k).lstrip("\ufeff").strip() for k in data.columns]
        if "grid_id" not in data or data.grid_id.isna().any() or data.grid_id.duplicated().any():
            errors.append(f"{file.name}: null/duplicate grid_id");continue
        found=sorted(int(m.group(1)) for col in data.columns if (m:=PAT[kind].fullmatch(col)))
        if not found:errors.append(f"{file.name}: missing expected indicator columns");continue
        for year in found:
            ids=set(data.grid_id.astype(str))
            duplicate=ids & recorded.get(year,set())
            if duplicate:errors.append(f"{year} duplicate IDs across CSVs: {len(duplicate)}")
            recorded.setdefault(year,set()).update(ids)
            prefix=("built" if kind=="built" else "imerg")+f"_{year}_"
            metric=(["built_total_m2","built_nres_m2","source_valid_area_m2"] if kind=="built" else
                ["total_mm","rx1day_mm","heavy20_days","wet1_days","rx30min_mm","valid_days"])
            if any(prefix+k not in data for k in metric):
                errors.append(f"{file.name}: missing {year} fields");continue
            values=[pd.to_numeric(data[prefix+k],errors="coerce") for k in metric]
            if kind=="built":
                b,n,v=values
                bad=(b<0)|(n<0)|(v<0)|(v>AREA*1.2)|(n>b+1)|(b>v+1)
            else:
                total,rx,heavy,wet,half,valid=values
                nd=366 if (year%4==0 and (year%100!=0 or year%400==0)) else 365
                bad=(total<0)|(rx<0)|(half<0)|(valid<0)|(valid>nd+.1)|(heavy<0)|(
                    heavy>valid+.1)|(wet<0)|(wet>valid+.1)|(rx>total+.01)
            nbad=int(bad.fillna(False).sum())
            if nbad:errors.append(f"{file.name} {year}: {nbad} physical violations")
            items.append({"file":file.name,"year":year,"rows":len(data),
                          "bad_physics":nbad,"missing_primary":int(values[0].isna().sum())})
    missing=set(years)-set(recorded);extra=set(recorded)-set(years)
    if missing or extra:errors.append(f"Year mismatch missing={sorted(missing)} extra={sorted(extra)}")
    if expected is not None:
        for year,ids in recorded.items():
            if ids-expected:errors.append(f"{year}: {len(ids-expected)} unexpected grids")
            if require_full and expected-ids:
                errors.append(f"{year}: {len(expected-ids)} missing grids")
    return {"status":"FAIL" if errors else "PASS","files":len(files),"years":sorted(recorded),
            "grid_count_by_year":{str(y):len(ids) for y,ids in recorded.items()},
            "errors":errors,"note":"Bounds/coverage only; verify against source image separately"},items

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kind",choices=sorted(PAT),required=True)
    p.add_argument("--csv-root",type=Path,required=True)
    p.add_argument("--expected-ids",type=Path)
    p.add_argument("--years",required=True)
    p.add_argument("--allow-test",action="store_true")
    p.add_argument("--require-full",action="store_true")
    p.add_argument("--out",type=Path,default=Path("audits/r34_new_exports"))
    a=p.parse_args()
    files=sorted(a.csv_root.rglob(GLOBS[a.kind]))
    if not a.allow_test:files=[x for x in files if "_test" not in x.stem.lower()]
    if not files:raise FileNotFoundError(f"No {GLOBS[a.kind]} CSVs in {a.csv_root}")
    expected=None
    if a.expected_ids:
        frame=pd.read_csv(a.expected_ids,dtype={"grid_id":"string"})
        if frame.grid_id.isna().any() or frame.grid_id.duplicated().any():
            raise RuntimeError("Null/duplicate expected grid IDs")
        expected=set(frame.grid_id.astype(str))
    report,items=inspect(a.kind,files,[int(x) for x in a.years.split(",")],
                         expected,a.require_full)
    a.out.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(items).to_csv(a.out/f"50b_{a.kind}_files.csv",index=False)
    (a.out/f"50b_{a.kind}_qc.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("[QC]",json.dumps(report,ensure_ascii=False))
    if report["errors"]:raise RuntimeError("Stage50b QC failed")
if __name__=="__main__":main()
