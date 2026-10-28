# -*- coding: utf-8 -*-
"""Stage71: CMIP6 future weather anomaly ensemble on frozen GF10 cells.

Pair EVERY future scenario with the SAME model's 1995-2014 historical
reference; aggregate model deltas, not raw cross-model daily P99 values.
No claim about future 3-hour floods, absolute flood probability, storm
drainage capacity or causal attribution. Strict grid/period completeness.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd

def ensemble(root,pop,out,min_models=3):
    files=sorted(root.glob("64b_cmip6_*_grid10.parquet"))
    if not files:raise FileNotFoundError("No 64b CMIP6 GF10 files")
    frames=[]
    for f in files:
        d=pd.read_parquet(f)
        if d.grid_id.duplicated().any():raise ValueError(f"Duplicate grid in {f}")
        label=str(d.label.iloc[0])
        # label: MODEL_historical_1995_2014 or MODEL_ssp245_2041_2060
        import re
        mm=re.fullmatch(r"(.+)_(historical|ssp245|ssp585)_(\d{4})_(\d{4})",label)
        if not mm:raise ValueError(f"Unknown CMIP6 label {label}")
        d["model"]=mm[1];d["scenario"]=mm[2];d["start_year"]=int(mm[3])
        d["end_year"]=int(mm[4]);frames.append(d)
    all=pd.concat(frames,ignore_index=True)
    if all.duplicated(["grid_id","model","scenario","start_year","end_year"]).any():
        raise ValueError("CMIP output duplicated")
    historical=all[(all.scenario=="historical")&(all.start_year==1995)&(all.end_year==2014)]
    if historical.empty:raise ValueError("Historical model reference 1995-2014 missing")
    historical=historical[["grid_id","model","cmip_daily_p99_mm",
                "cmip_temp_mean_c","cmip_annualized_precip_mm"]].rename(columns={
        "cmip_daily_p99_mm":"hist_p99",
        "cmip_temp_mean_c":"hist_temp",
        "cmip_annualized_precip_mm":"hist_annual"})
    future=all[all.scenario!="historical"].copy()
    if future.empty:raise ValueError("No SSP futures")
    x=future.merge(historical,on=["grid_id","model"],how="inner",validate="many_to_one")
    for var,base,new in [("p99","hist_p99","cmip_daily_p99_mm"),
                         ("temp","hist_temp","cmip_temp_mean_c"),
                         ("annual","hist_annual","cmip_annualized_precip_mm")]:
        x["delta_"+var]=x[new]-x[base]
    key=["grid_id","scenario","start_year","end_year"]
    group=x.groupby(key)
    q=group[["delta_p99","delta_temp","delta_annual"]].agg(["median",
       lambda v:v.quantile(.05),lambda v:v.quantile(.95)]).reset_index()
    q.columns=["grid_id","scenario","start_year","end_year",
       "delta_p99_median","delta_p99_p05","delta_p99_p95",
       "delta_temp_median","delta_temp_p05","delta_temp_p95",
       "delta_annual_median","delta_annual_p05","delta_annual_p95"]
    models=group.model.nunique().rename("n_models").reset_index()
    q=q.merge(models,on=key,validate="one_to_one")
    q=q[q.n_models>=min_models].copy()
    if q.empty:raise RuntimeError(f"No grid with >= {min_models} paired CMIP models")
    pop2020=pd.read_parquet(pop)
    if pop2020.grid_id.duplicated().any():raise ValueError("Population duplicate grid")
    q=q.merge(pop2020[["grid_id","population_count"]],on="grid_id",how="left",validate="many_to_one")
    q["population_increased_p99_2020_fixed"]=np.where(
        q.delta_p99_median>0,q.population_count,np.nan)
    out.mkdir(parents=True,exist_ok=True)
    q.to_parquet(out/"71_cmip6_anomaly_ensemble_grid10.parquet",index=False)
    qc={"rows":len(q),"model_min":min_models,
        "scenarios":sorted(q.scenario.unique().tolist()),
        "periods":q[["start_year","end_year"]].drop_duplicates().values.tolist(),
        "method":"paired same-GCM future minus 1995-2014 historical; ensemble median/p05/p95",
        "caution":"Across-model percentiles are spread, NOT calibrated 95% confidence interval; SSP population not supplied"}
    (out/"71_cmip6_qc.json").write_text(json.dumps(qc,indent=2),encoding="utf-8")
    print("[DONE]",qc)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--cmip-root",type=Path,required=True)
    p.add_argument("--population-2020",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--min-models",type=int,default=3)
    a=p.parse_args();ensemble(a.cmip_root,a.population_2020,a.out,a.min_models)
if __name__=="__main__":main()
