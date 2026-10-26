"""Stage30b: create audited one-row-per-city environment table.

Combine Stage30 UCDB-city GHSL epoch, existing M3 static MERIT/Basin/CRU,
and OPTIONAL Stage31 grid hydrogeomorphic proxies. Read-only inputs; refuses
duplicated IDs. Population columns are context, not physical flood processes.
"""
from __future__ import annotations
import argparse,importlib.util,json
from pathlib import Path
import numpy as np
import pandas as pd

def load(path):
    if not path.is_file():raise FileNotFoundError(path)
    return pd.read_parquet(path) if path.suffix.lower()==".parquet" else pd.read_csv(path,dtype={"grid_id":str,"city_id":str})

def assemble(ghsl,static,hydro=None,epoch=2020):
    script=Path(__file__).with_name("33_city_typology.py")
    spec=importlib.util.spec_from_file_location("city33_stage30b",script)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    table=m.build_city_table(ghsl,static,hydro,epoch=epoch)
    if table.city_id.isna().any() or table.city_id.duplicated().any():
        raise RuntimeError("City table has missing/duplicate IDs")
    if len(table)<50:raise RuntimeError("Unexpectedly few city rows: verify input epoch and overlaps")
    return table

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--epoch",type=int,default=2020)
    p.add_argument("--ghsl-city",type=Path)
    p.add_argument("--static-grid",type=Path)
    p.add_argument("--hydro-grid",type=Path)
    p.add_argument("--out",type=Path)
    args=p.parse_args()
    ghsl=args.ghsl_city or args.base/"mechanism_project"/"30_urban_context"/"30_city_ghsl_epochs.parquet"
    static=args.static_grid or args.base/"mechanism_project"/"05_static_merged"/"grid_static_context_m3.parquet"
    table=assemble(load(ghsl),load(static),
                   load(args.hydro_grid) if args.hydro_grid else None,args.epoch)
    out=args.out or args.base/"mechanism_project"/"30_urban_context"
    out.mkdir(parents=True,exist_ok=True)
    target=out/f"30b_city_features_{args.epoch}.parquet"
    table.to_parquet(target,index=False)
    qc={"epoch":args.epoch,"cities":len(table),
        "ghsl_city_source":str(ghsl),"m3_static":str(static),
        "hydro_source":str(args.hydro_grid) if args.hydro_grid else None,
        "environment_columns":[c for c in table if c not in ("city_id","epoch")],
        "n_city_rows_unmapped_static":int(table.grid_count_primary.isna().sum()) if "grid_count_primary" in table else None,
        "note":"M3 observed UCDB city environment, not entire global city domain"}
    (out/f"30b_city_features_{args.epoch}_qc.json").write_text(
        json.dumps(qc,ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",target,"cities",len(table),flush=True)

if __name__=="__main__":main()
