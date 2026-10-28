# -*- coding: utf-8 -*-
"""Stage66 Results 3.2: native GF10 case-time-stratified conditional softmax.

B0: P24,P3H,API7,SM1; B1 adds P24 x API7;
B2 adds P24 x ERA daily T and API7 x T;
B3 adds P24 x GHSL2020 built fraction (optional context).
Models are OBSERVATIONAL relative-onset association, not true flood probabilities.
No model is valid until Stage65b weather/coverage QC and leakage checks pass.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd
from scipy.optimize import minimize

FEATURES={"P24":"imerg_p24_utc_mm_mean","P3H":"imerg_p3h_max_mm_max",
          "API7":"era5_api7_mm_mean","SM1":"era5_sm1_prevday_mean",
          "TEMP":"era5_temp_day_c"}
def standardize(train,allvalues,log=False):
    arr=np.log1p(np.maximum(allvalues,0)) if log else allvalues.copy()
    mu=float(np.mean(arr[train]));sd=float(np.std(arr[train]))
    return (arr-mu)/max(sd,1e-8),{"mean":mu,"sd":sd,"log1p":log}
def group_sums(v,starts):
    return np.add.reduceat(v,starts,axis=0)
def prepare(raw,extra=None):
    d=raw.copy()
    if extra is not None:
        if extra.grid_id.duplicated().any():raise ValueError("Duplicate context grid")
        if not {"grid_id","built_fraction_wholecell"}<=set(extra):
            raise ValueError("Missing built_fraction_wholecell")
        d=d.merge(extra[["grid_id","built_fraction_wholecell"]],
                  on="grid_id",how="left",validate="many_to_one")
    required=["stratum_id","is_onset","event_date","grid_x","grid_y",*FEATURES.values()]
    if not set(required)<=set(d):raise ValueError(f"Missing model columns {set(required)-set(d)}")
    d["event_date"]=pd.to_datetime(d.event_date,errors="raise")
    d=d.replace([np.inf,-np.inf],np.nan)
    good=d[list(FEATURES.values())].notna().all(axis=1)
    if "imerg_halfhour_count" in d:
        good &= d.imerg_halfhour_count.ge(48)
    if "era5_api7_valid_days" in d:
        good &= d.era5_api7_valid_days.ge(7)
    good &= d.era5_sm1_prevday_mean.between(0,1)
    # No selective dropping of missing referents. Drop WHOLE matched sets.
    complete=good.groupby(d.stratum_id).transform("all")
    d=d[complete].copy()
    d=d.sort_values(["stratum_id","event_date"]).reset_index(drop=True)
    counts=d.groupby("stratum_id").is_onset.agg(["sum","count"])
    good=counts[(counts["sum"]==1)&(counts["count"]>=2)].index
    d=d[d.stratum_id.isin(good)].copy().reset_index(drop=True)
    if d.empty:raise ValueError("No strict complete weather matched strata")
    return d

def fit_softmax(x,y,starts,reg=1e-5):
    n=np.diff(np.r_[starts,len(y)])
    def loss(beta):
        logits=np.clip(x@beta,-40,40)
        maxlog=np.maximum.reduceat(logits,starts)
        ez=np.exp(logits-np.repeat(maxlog,n))
        denom=group_sums(ez,starts)
        prob=ez/np.repeat(denom,n)
        ll=-np.sum(y*np.log(np.maximum(prob,1e-30)))/len(starts)
        grad=-(x.T@(y-prob))/len(starts)
        return ll+reg*np.sum(beta**2)/2,grad+reg*beta
    return minimize(loss,np.zeros(x.shape[1]),jac=True,method="L-BFGS-B")

def eval_softmax(x,y,starts,beta):
    n=np.diff(np.r_[starts,len(y)])
    logits=np.clip(x@beta,-40,40)
    mx=np.maximum.reduceat(logits,starts)
    ex=np.exp(logits-np.repeat(mx,n))
    denom=group_sums(ex,starts)
    return -group_sums(y*(logits-np.repeat(mx+np.log(denom),n)),starts)

def run(panel,out,context=None,seed=2026):
    raw=pd.read_parquet(panel)
    extra=pd.read_parquet(context) if context else None
    d=prepare(raw,extra)
    first=d.groupby("stratum_id",sort=True).first().reset_index()
    first["year"]=first.event_date.dt.year
    # Time split; grouped spatial blocks hash: no event independently resampled
    first["spatial_group"]=(first.grid_x//25).astype(str)+"_"+(first.grid_y//25).astype(str)
    import hashlib
    first["spatial_fold"]=first.spatial_group.map(lambda s:
       int(hashlib.sha256((s+"|"+str(seed)).encode()).hexdigest()[:8],16)%5)
    train_ids=set(first.loc[first.spatial_fold!=0,"stratum_id"])
    test_ids=set(first.loc[first.spatial_fold==0,"stratum_id"])
    if len(test_ids)<100 or len(train_ids)<100:raise RuntimeError("Insufficient spatial-block split")
    tr=d.stratum_id.isin(train_ids).to_numpy()
    d["fold"]="train"
    d.loc[~tr,"fold"]="test"
    transform_info={};z={}
    for key,col in FEATURES.items():
        z[key],transform_info[key]=standardize(tr,d[col].to_numpy(float),
                                              log=key in ("P24","P3H","API7"))
    X={k:v for k,v in z.items()}
    X["P24xAPI7"]=z["P24"]*z["API7"]
    X["P24xTEMP"]=z["P24"]*z["TEMP"]
    X["API7xTEMP"]=z["API7"]*z["TEMP"]
    specs={"B0_shared":["P24","P3H","API7","SM1"],
       "B1_wet_interaction":["P24","P3H","API7","SM1","P24xAPI7"],
       "B2_temperature_modulation":["P24","P3H","API7","SM1",
                                     "P24xAPI7","P24xTEMP","API7xTEMP"]}
    if extra is not None:
        # static coverage must be complete on the common cohort for fair comparison
        c=d["built_fraction_wholecell"].to_numpy(float)
        if not np.isfinite(c).all():
            raise RuntimeError("Context missing; restrict common cohort BEFORE splitting")
        X["BUILT"],transform_info["BUILT"]=standardize(tr,c)
        X["P24xBUILT"]=z["P24"]*X["BUILT"]
        specs["B3_urban_modulation"]=specs["B1_wet_interaction"]+["P24xBUILT"]
    out.mkdir(parents=True,exist_ok=True)
    rows=[];pred={};coefs=[]
    for name,features in specs.items():
        losses=[]
        for fold in ("train","test"):
            j=np.flatnonzero(d.fold.to_numpy()==fold)
            block=d.iloc[j]
            s=np.r_[0,np.flatnonzero(block.stratum_id.to_numpy()[1:] !=
                                     block.stratum_id.to_numpy()[:-1])+1]
            xx=np.column_stack([X[t][j] for t in features])
            yy=block.is_onset.to_numpy(float)
            if fold=="train":
                result=fit_softmax(xx,yy,s)
                if not result.success:raise RuntimeError(f"Optimization failed: {name} {result.message}")
                beta=result.x
                for t,b in zip(features,beta):coefs.append({"model":name,"feature":t,"beta_standardized":float(b)})
            losses=eval_softmax(xx,yy,s,beta)
            rows.append({"model":name,"fold":fold,"strata":len(s),
                         "conditional_logloss":float(losses.mean())})
            if fold=="test":
                keys=block.stratum_id.to_numpy()[s]
                pred[name]=pd.DataFrame({"stratum_id":keys,"loss":losses})
        print("[FIT]",name,rows[-1])
    # paired loss by same matched stratum, provisional spatial/year block cluster
    baseline=pred["B0_shared"]
    audit={}
    for name in list(pred)[1:]:
        comparison=baseline.merge(pred[name],on="stratum_id",validate="one_to_one",
                                   suffixes=("_base","_new"))
        comparison["gain"]=comparison.loss_base-comparison.loss_new
        t=first.set_index("stratum_id")[["spatial_group","year"]]
        comparison=comparison.join(t,on="stratum_id")
        # Blocks are not independent storms, sensitivity only.
        comparison["proxy_group"]=comparison.spatial_group+"_"+comparison.year.astype(str)
        clusters=comparison.groupby("proxy_group").gain.agg(["sum","count"]).reset_index()
        rand=np.random.default_rng(seed)
        vals=[]
        for _ in range(399):
            ii=rand.integers(0,len(clusters),len(clusters))
            sub=clusters.iloc[ii]
            vals.append(sub["sum"].sum()/sub["count"].sum())
        audit[name]={"paired_gain":float(comparison.gain.mean()),
                     "proxy_cluster_ci95":list(map(float,np.quantile(vals,[.025,.975]))),
                     "caution":"spatial/year PROXY bootstrap, not storm-independent CI"}
    pd.DataFrame(rows).to_csv(out/"66_comparison.csv",index=False)
    pd.DataFrame(coefs).to_csv(out/"66_coefficients.csv",index=False)
    q={"eligible_strata":len(first),"train_strata":len(train_ids),"test_strata":len(test_ids),
       "transforms_fit_on_train_only":transform_info,
       "paired_comparisons":audit,"status":"EXPLORATORY_NOT_CAUSAL",
       "WARNING":"No absolute flood probability; not independent storms; source location errors unverified"}
    (out/"66_model_qc.json").write_text(json.dumps(q,ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",q)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--panel",type=Path,required=True)
    p.add_argument("--context",type=Path)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--seed",type=int,default=2026)
    a=p.parse_args();run(a.panel,a.out,a.context,a.seed)
if __name__=="__main__":main()
