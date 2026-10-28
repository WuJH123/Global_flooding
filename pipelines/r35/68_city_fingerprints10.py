# -*- coding: utf-8 -*-
"""Stage68 Results 3.3: city response fingerprints from GF10 matched weather.

One dominant-UCDB-city assignment per frozen grid. Within-city conditional
onset coefficients estimated with ridge; only cities with n_strata >= min
retained. Raw city estimates are NOT causal or identifiable drainage effects;
different rainfall support and location errors must be audited separately.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd
from scipy.optimize import minimize
def make_design(df):
    x=pd.DataFrame({
      "P24":np.log1p(df.imerg_p24_utc_mm_mean.clip(lower=0)),
      "P3H":np.log1p(df.imerg_p3h_max_mm_max.clip(lower=0)),
      "API7":np.log1p(df.era5_api7_mm_mean.clip(lower=0)),
      "SM1":df.era5_sm1_prevday_mean})
    # descriptive fixed global scale, not changing each city transform
    x=(x-x.mean())/x.std().clip(lower=1e-8)
    x["P24xAPI7"]=x.P24*x.API7
    return x
def softmax_city(x,y,ids,penalty=1.):
    sizes=np.bincount(ids)
    starts=np.r_[0,np.cumsum(sizes)[:-1]]
    def fun(beta):
        z=np.clip(x@beta,-30,30);m=np.maximum.reduceat(z,starts)
        p=np.exp(z-np.repeat(m,sizes))
        p=p/np.repeat(np.add.reduceat(p,starts),sizes)
        l=-np.sum(y*np.log(np.maximum(p,1e-30)))+penalty*np.sum(beta**2)/2
        grad=x.T@(p-y)+penalty*beta
        return l,grad
    return minimize(fun,np.zeros(x.shape[1]),method="L-BFGS-B",jac=True)
def run(panel,crosswalk,out,min_strata=80):
    source=pd.read_parquet(panel)
    links=pd.read_parquet(crosswalk)
    if links.grid_id.duplicated().any():
        raise ValueError("Provide 62c_dominant_city_grid10.parquet, not all overlap links")
    df=source.merge(links[["grid_id","city_id"]],on="grid_id",how="inner",validate="many_to_one")
    req=["imerg_p24_utc_mm_mean","imerg_p3h_max_mm_max","era5_api7_mm_mean",
         "era5_sm1_prevday_mean"]
    valid=df[req].replace([np.inf,-np.inf],np.nan).notna().all(axis=1)
    valid&=df.era5_sm1_prevday_mean.between(0,1)
    if "imerg_halfhour_count" in df:valid&=df.imerg_halfhour_count.ge(48)
    if "era5_api7_valid_days" in df:valid&=df.era5_api7_valid_days.ge(7)
    full=valid.groupby(df.stratum_id).transform("all")
    df=df[full].copy().sort_values(["city_id","stratum_id","event_date"])
    stats=df.groupby(["city_id","stratum_id"]).is_onset.agg(["count","sum"]).reset_index()
    ok=stats[(stats["count"]>=2)&(stats["sum"]==1)]
    df=df.merge(ok[["city_id","stratum_id"]],on=["city_id","stratum_id"],how="inner")
    if df.empty:raise RuntimeError("No eligible city-specific strata")
    design=make_design(df)
    features=design.columns.tolist()
    df=df.reset_index(drop=True);design=design.reset_index(drop=True)
    outputs=[]
    for city,g in df.groupby("city_id",sort=True):
        if g.stratum_id.nunique()<min_strata:continue
        xx=design.loc[g.index].to_numpy(float)
        yy=g.is_onset.to_numpy(float)
        group=pd.factorize(g.stratum_id,sort=False)[0]
        result=softmax_city(xx,yy,group,penalty=1.0)
        if not result.success:continue
        item={"city_id":city,"n_strata":int(g.stratum_id.nunique()),
              "n_onset":int(g.is_onset.sum()),"status":"ridge_city_exploratory"}
        item.update({"beta_"+name:float(value) for name,value in zip(features,result.x)})
        outputs.append(item)
    out.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(outputs).to_parquet(out/"68_city_response_fingerprints.parquet",index=False)
    qc={"n_cities_fitted":len(outputs),"min_strata":min_strata,"observational":True,
        "caution":"City ridge shrinkage, not cross-city heldout or storm-independent uncertainty"}
    (out/"68_fingerprint_qc.json").write_text(json.dumps(qc,indent=2),encoding="utf-8")
    print("[DONE]",qc)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--panel",type=Path,required=True)
    p.add_argument("--dominant-city",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--min-strata",type=int,default=80)
    a=p.parse_args();run(a.panel,a.dominant_city,a.out,a.min_strata)
if __name__=="__main__":main()
