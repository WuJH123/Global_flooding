"""Stage29: non-collinear rainfall amount + concentration residual.

Daily P24 and maximum 3-hour P3H use the SAME spatial aggregation operator.
Primary short-burst component is the TRAIN-only residual of log1p(P3H)
regressed on log1p(P24). It is an information residual, not a physical
fraction of daily rain. An optional raw P3H/P24 ratio is QC ONLY.

Models share identical complete grid×calendar-stratum risk sets. No absolute
flood probability, causal effects, or storm independence is claimed.
"""
from __future__ import annotations
import argparse, importlib.util, json
from pathlib import Path
import numpy as np
import pandas as pd

REPRESENTATIONS={
    "mean":("imerg_p24_utc_mm_mean","imerg_p3h_max_mm_mean"),
    "max":("imerg_p24_utc_mm_max","imerg_p3h_max_mm_max"),
}
MODELS={
    "R0_amount":("P24",),
    "R1_memory":("P24","API7","SM1"),
    "R2_concentration":("P24","API7","SM1","C3_resid"),
    "R3_memory_x_concentration":("P24","API7","SM1","C3_resid",
                                  "P24_x_API7","C3_x_API7"),
}

def imports():
    here=Path(__file__).resolve().parent
    mods=[]
    for fn in ("23_conditional_poisson_stage32.py",
               "25_conditional_interactions_ci.py",
               "27_rainfall_parity_identifiability.py"):
        p=here/fn
        spec=importlib.util.spec_from_file_location("s29_"+p.stem,p)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        mods.append(mod)
    return mods

def residual_peak(daily,peak,train):
    """Residualize transformed rainfall on TRAIN observations only.

    Retain train-only 0.5/99.5 percentile clipping for BOTH rainfall measures.
    No test outcomes, climate frequencies, or wet-event labels enter the fit.
    """
    if (daily<0).any() or (peak<0).any() or not np.isfinite(daily+peak).all():
        raise ValueError("Rainfall must be finite and nonnegative")
    x=np.log1p(np.asarray(daily,float))
    y=np.log1p(np.asarray(peak,float))
    tr=np.asarray(train,bool)
    if tr.sum()<50:raise ValueError("Need >=50 training observations")
    c=[]
    clip=[]
    for arr in (x,y):
        lo,hi=np.quantile(arr[tr],[.005,.995])
        c.append(np.clip(arr,lo,hi))
        clip.append([float(lo),float(hi)])
    x,y=c
    matrix=np.column_stack([np.ones(tr.sum()),x[tr]])
    beta=np.linalg.lstsq(matrix,y[tr],rcond=None)[0]
    resid=y-(beta[0]+beta[1]*x)
    mu,sd=resid[tr].mean(),resid[tr].std()
    if sd<=1e-10:raise RuntimeError("Peak residual is constant")
    return (resid-mu)/sd,{"regression_intercept":float(beta[0]),
                         "regression_slope":float(beta[1]),
                         "train_resid_sd":float(sd),"train_resid_mean":float(mu),
                         "clip_log1p_005_995":clip,
                         "train_corr_log1p":float(np.corrcoef(x[tr],y[tr])[0,1]),
                         "train_corr_P24_C3":float(np.corrcoef(x[tr],resid[tr])[0,1])}

def model_one(df,base,mode,scheme,block_cells,seed,reps):
    st23,st25,st27=imports()
    daily,peak=REPRESENTATIONS[scheme]
    w=df.copy()
    w["imerg_p24_utc_mm_mean"]=w[daily].to_numpy(dtype=float)
    w["imerg_p3h_max_mm_max"]=w[peak].to_numpy(dtype=float)
    train,test=st23.split_train_test(w,mode,seed,block_cells,base)
    if not (train.any() and test.any()):raise RuntimeError("Empty validation split")
    scaled,meta=st23.standardize(w,train)
    c,cmeta=residual_peak(w[daily].to_numpy(dtype=float),
                          w[peak].to_numpy(dtype=float),train)
    scaled["C3_resid"]=c
    scaled["P24_x_API7"]=scaled["P24"]*scaled["API7"]
    scaled["C3_x_API7"]=c*scaled["API7"]
    y=w.episode_onset.to_numpy(dtype=float)
    sid=w.calendar_stratum_id.to_numpy()
    blocks=st25.block_ids(w,block_cells)
    fits,coeff=[],[]
    result={}
    for name,terms in MODELS.items():
        X=np.column_stack([scaled[z] for z in terms])
        tr=st25.sorted_groups(X,y,sid,blocks,train)
        te=st25.sorted_groups(X,y,sid,blocks,test)
        b,se,cov,clusters,ltrain,ltest,per=st25.infer_one(tr,te,terms,1e-4)
        result[name]=(tr,te,per,ltest)
        coeff.extend({"validation":mode,"rain_aggregation":scheme,"model":name,"term":term,
                      "beta":float(b[i]),"ci_low":float(b[i]-1.96*se[i]),
                      "ci_high":float(b[i]+1.96*se[i]),
                      "train_clusters":clusters} for i,term in enumerate(terms))
        print(f"[29 {mode} {scheme}] {name} loss={ltest:.6f}",flush=True)
    null=result["R0_amount"][1]
    null_loss=float(np.dot(null[4],np.log(null[3]))/null[4].sum())
    gains=[]
    for before,after in [("R0_amount","R1_memory"),
                         ("R1_memory","R2_concentration"),
                         ("R2_concentration","R3_memory_x_concentration")]:
        a,b=result[before],result[after]
        if not np.array_equal(a[1][7],b[1][7]):
            raise RuntimeError("Heldout risk sets differ across models")
        g=st27.paired_gain(a[2],b[2],a[1][6],a[1][4],reps,seed+83)
        gains.append({"validation":mode,"rain_aggregation":scheme,
                      "baseline":before,"augmented":after,
                      "baseline_logloss":a[3],"augmented_logloss":b[3],
                      "null_conditional_logloss":null_loss,
                      "model_skill_vs_null":float(1-b[3]/null_loss),
                      **g})
    return coeff,gains,{"validation":mode,"rain_aggregation":scheme,
                        "source_P24":daily,"source_P3H":peak,
                        "weather_transform":meta,"concentration_residual":cmeta}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--panel-path",type=Path)
    p.add_argument("--aggregation",choices=("mean","max","both"),default="both")
    p.add_argument("--validation",choices=("temporal","spatial","city","all"),default="all")
    p.add_argument("--max-strata",type=int)
    p.add_argument("--block-cells",type=int,default=25)
    p.add_argument("--bootstrap-reps",type=int,default=399)
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--out",type=Path)
    args=p.parse_args()
    if args.bootstrap_reps<99 or args.block_cells<1:raise ValueError("Invalid resampling configuration")
    _,_,s27=imports()
    path=args.panel_path or args.base/"standard_calendar_22_compact"/"22_standard_daily_with_climate.parquet"
    frame,qc=s27.load_common(path,args.max_strata)
    modes=("temporal","spatial","city") if args.validation=="all" else (args.validation,)
    schemes=("mean","max") if args.aggregation=="both" else (args.aggregation,)
    rows,changes,params=[],[],[]
    for mode in modes:
        for scheme in schemes:
            c,g,settings=model_one(frame,args.base,mode,scheme,args.block_cells,args.seed,args.bootstrap_reps)
            rows.extend(c);changes.extend(g);params.append(settings)
    out=args.out or args.base/"stage32_rain_structure"
    out.mkdir(parents=True,exist_ok=True)
    suffix="_smoke"+str(args.max_strata) if args.max_strata else ""
    pd.DataFrame(rows).to_csv(out/f"29_coefficients{suffix}.csv",index=False)
    pd.DataFrame(changes).to_csv(out/f"29_nested_gains{suffix}.csv",index=False)
    (out/f"29_manifest{suffix}.json").write_text(json.dumps({
        "common_sample":qc,"fits":params,
        "interpretation":"Residual concentration is TRAIN conditional information, not physical concentration ratio; reported flood onsets only.",
        "cv_null":"Risk-stratum uniform day probability conditional on onset count",
        "uncertainty":"Training coefficient spatial-cluster sandwich; heldout gain paired spatial-block bootstrap conditional on fits"
    },indent=2,ensure_ascii=False),encoding="utf-8")
    print("[DONE]",out,flush=True)

if __name__=="__main__":main()
