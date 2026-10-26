"""Stage27 P0: rainfall aggregation parity and interaction identification.

All three formulations use the SAME fully observed grid-day/stratum cohort:
  mean_mean : P24 grid mean, P3H grid mean
  max_max   : P24 grid max, P3H grid max
  mean_max  : legacy Stage25 cross-statistic comparison

Trained conditional-Poisson models I1, I3, I4 use training-only transforms.
Reports coefficient sign changes, training-only VIF/correlation, paired held-out
spatial-block bootstrap gains I1->I3 and I3->I4, and OBSERVED joint-support
counts (not physical flood probabilities or causal dose-response curves).
No GEE jobs, no edits to Stage23/25 outputs, no interpolation of ERA missingness.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

P24C={"mean_mean":"imerg_p24_utc_mm_mean",
      "max_max":"imerg_p24_utc_mm_max",
      "mean_max":"imerg_p24_utc_mm_mean"}
P3HC={"mean_mean":"imerg_p3h_max_mm_mean",
       "max_max":"imerg_p3h_max_mm_max",
       "mean_max":"imerg_p3h_max_mm_max"}
API="era5_api7_mm_mean"
SM1="era5_sm1_prevday_mean"
MODELS={
    "I1_P24xAPI7":("P24","API7","SM1","P3H","P24_x_API7"),
    "I3_both":("P24","API7","SM1","P3H","P24_x_API7","P24_x_SM1"),
    "I4_three":("P24","API7","SM1","P3H","P24_x_API7","P24_x_SM1","P3H_x_API7")
}
FIELDS=list(dict.fromkeys([*P24C.values(),*P3HC.values(),API,SM1]))
KEYS=["grid_id","grid_x","grid_y","calendar_stratum_id","year",
      "episode_onset","reported_non_onset_day"]


def stage_tools():
    base=Path(__file__).parent
    modules=[]
    for name in ("23_conditional_poisson_stage32.py","25_conditional_interactions_ci.py"):
        path=base/name
        spec=importlib.util.spec_from_file_location(name.replace(".py","").replace("-","_"),path)
        module=importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        modules.append(module)
    return tuple(modules)


def load_common(path,max_strata=None):
    if not path.exists():
        raise FileNotFoundError(path)
    con=duckdb.connect()
    available={x[0] for x in con.execute("DESCRIBE SELECT * FROM read_parquet(?)",[str(path)]).fetchall()}
    missing=set(KEYS+FIELDS)-available
    if missing:
        raise ValueError("Missing Stage27 source columns: "+repr(sorted(missing)))
    columns=", ".join('p."'+name+'"' for name in KEYS+FIELDS)
    if max_strata:
        con.execute("""CREATE TEMP TABLE chosen AS
          SELECT calendar_stratum_id FROM read_parquet(?)
          GROUP BY 1 ORDER BY hash(calendar_stratum_id) LIMIT ?""",[str(path),max_strata])
        df=con.execute(f"SELECT {columns} FROM read_parquet(?) p JOIN chosen USING(calendar_stratum_id)",[str(path)]).fetchdf()
    else:
        df=con.execute(f"SELECT {columns} FROM read_parquet(?) p",[str(path)]).fetchdf()
    con.close()
    before=len(df)
    df=df.loc[df.reported_non_onset_day.eq(0)].copy()
    good=np.ones(len(df),dtype=bool)
    for col in FIELDS:
        df[col]=pd.to_numeric(df[col],errors="coerce")
        v=df[col].to_numpy(dtype=float)
        good &= np.isfinite(v)
        good &= (v>=0)
    good &= df[SM1].to_numpy(dtype=float)<=1
    df["_complete"]=good
    strata_complete=df.groupby("calendar_stratum_id",sort=False)["_complete"].transform("all")
    df=df.loc[strata_complete].copy()
    st=df.groupby("calendar_stratum_id",sort=False).episode_onset.agg(["size","sum"])
    informative=st.index[(st["size"]>=2)&(st["sum"]>0)&(st["sum"]<st["size"])]
    df=df.loc[df.calendar_stratum_id.isin(informative)].drop(columns="_complete")
    df=df.sort_values(["calendar_stratum_id","grid_id","year"]).reset_index(drop=True)
    if df.empty:
        raise RuntimeError("No complete informative common risk sets")
    return df,{"raw_days":before,"common_days":len(df),
               "common_strata":df.calendar_stratum_id.nunique(),
               "common_onsets":int(df.episode_onset.sum())}


def positive_collinearity(X, names, sample=None):
    x=np.asarray(X,dtype=float)
    if sample is not None:
        x=x[sample]
    corr=np.corrcoef(x,rowvar=False)
    if not np.isfinite(corr).all():
        raise RuntimeError("Non-finite train correlation; inspect collinearity")
    precision=np.linalg.pinv(corr,hermitian=True,rcond=1e-10)
    ev=np.linalg.eigvalsh(corr)
    cond=float(np.max(ev)/max(np.min(ev),1e-12))
    return corr,{"condition_number":cond,
                 "terms":list(names),
                 "vif_approx":{n:float(precision[i,i]) for i,n in enumerate(names)},
                 "corr_P24_P3H":float(corr[names.index("P24"),names.index("P3H")]),
                 "corr_P24xAPI7_P3HxAPI7":(
                   float(corr[names.index("P24_x_API7"),names.index("P3H_x_API7")])
                   if "P3H_x_API7" in names else None)}


def paired_gain(a,b,blocks,weights,reps=399,seed=2026):
    """a and b: unnormalized heldout negative log conditional likelihood by stratum."""
    a,b,blocks,weights=map(np.asarray,(a,b,blocks,weights))
    if len(a)!=len(b) or len(a)!=len(blocks) or len(a)!=len(weights):
        raise ValueError("Paired per-stratum dimensions differ")
    clusters,inv=np.unique(blocks,return_inverse=True)
    if len(clusters)<15:
        raise RuntimeError("Need >=15 heldout spatial blocks for CI")
    num=np.bincount(inv,weights=a-b,minlength=len(clusters))
    den=np.bincount(inv,weights=weights,minlength=len(clusters))
    rng=np.random.default_rng(seed)
    boot=[]
    for _ in range(reps):
        take=rng.integers(0,len(clusters),size=len(clusters))
        boot.append(num[take].sum()/den[take].sum())
    lo,hi=np.quantile(boot,[.025,.975])
    return {"gain":float(num.sum()/den.sum()),"gain_ci_low":float(lo),
            "gain_ci_high":float(hi),"test_clusters":len(clusters),
            "test_onsets":int(weights.sum())}


def fit_pair(df,base,validation,representation,block_cells,seed,reps):
    st23,st25=stage_tools()
    weather=df.copy(deep=False)
    weather=weather.copy()
    weather["imerg_p24_utc_mm_mean"]=df[P24C[representation]].to_numpy()
    weather["imerg_p3h_max_mm_max"]=df[P3HC[representation]].to_numpy()
    tr,te=st23.split_train_test(weather,validation,seed,block_cells,base)
    if not tr.any() or not te.any():
        raise RuntimeError("Empty Stage27 split")
    scaled,meta=st23.standardize(weather,tr)
    design=st25.design(scaled)
    ids=weather.calendar_stratum_id.to_numpy()
    y=weather.episode_onset.to_numpy(dtype=float)
    bl=st25.block_ids(weather,block_cells)
    output,bounds={},[]
    fits={}
    for label,cols in MODELS.items():
        X=np.column_stack([design[k] for k in cols])
        corr,diag=positive_collinearity(X,cols,tr)
        if label=="I4_three":
            output=diag
        train=st25.sorted_groups(X,y,ids,bl,tr)
        test=st25.sorted_groups(X,y,ids,bl,te)
        if len(train[4])<100 or len(test[4])<30:
            raise RuntimeError("Too few informative test/train strata")
        beta,se,cov,nblocks,loss_train,loss_test,per=st25.infer_one(train,test,cols,1e-4)
        fits[label]=(train,test,per,loss_test)
        for i,term in enumerate(cols):
            if "_x_" in term:
                bounds.append({
                    "validation":validation,"rainfall_representation":representation,
                    "model":label,"term":term,
                    "beta":float(beta[i]),"beta_ci_low":float(beta[i]-1.96*se[i]),
                    "beta_ci_high":float(beta[i]+1.96*se[i]),
                    "train_spatial_blocks":nblocks,
                    "heldout_loss":loss_test,"train_strata":len(train[4]),
                    "test_strata":len(test[4])})
        print(f"[27 {validation} {representation} {label}] loss={loss_test:.6f}",flush=True)
    gains=[]
    for old,new in [("I1_P24xAPI7","I3_both"),("I3_both","I4_three")]:
        a=fits[old];b=fits[new]
        if not np.array_equal(a[1][7],b[1][7]):
            raise RuntimeError("Different test risk strata")
        gain=paired_gain(a[2],b[2],a[1][6],a[1][4],reps=reps,seed=seed+11)
        gains.append({"validation":validation,"rainfall_representation":representation,
                      "baseline":old,"augmented":new,**gain})
    output.update({"validation":validation,"rainfall_representation":representation,
                   "transforms":meta})
    return bounds,gains,output


def observed_support(df):
    """Observed real-world joint rainfall support; never call this a risk surface."""
    out=[]
    for rep in P24C:
        p24=df[P24C[rep]].to_numpy(dtype=float)
        p3=df[P3HC[rep]].to_numpy(dtype=float)
        api=df[API].to_numpy(dtype=float)
        valid=p24>1
        share=p3[valid]/p24[valid]
        out.append({"representation":rep,"rows":len(df),
                    "daily_rain_gt_1mm":int(valid.sum()),
                    "p3h_gt_p24_same_stat_count":int(np.sum(p3>p24+1e-4)),
                    "p3h_p24_ratio_p50":float(np.median(share)) if len(share) else None,
                    "p3h_p24_ratio_p95":float(np.percentile(share,95)) if len(share) else None,
                    "p24_api7_corr_log1p":float(np.corrcoef(np.log1p(p24),np.log1p(api))[0,1]),
                    "p24_p3h_corr_log1p":float(np.corrcoef(np.log1p(p24),np.log1p(p3))[0,1])})
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--panel-path",type=Path)
    p.add_argument("--validation",choices=("temporal","spatial","city","all"),default="all")
    p.add_argument("--max-strata",type=int)
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--block-cells",type=int,default=25)
    p.add_argument("--bootstrap-reps",type=int,default=399)
    p.add_argument("--out",type=Path)
    a=p.parse_args()
    if a.bootstrap_reps<99 or a.block_cells<1:
        raise ValueError("bootstrap-reps >=99 and block-cells >=1 required")
    panel=a.panel_path or a.base/"standard_calendar_22_compact"/"22_standard_daily_with_climate.parquet"
    df,coverage=load_common(panel,a.max_strata)
    out=a.out or a.base/"stage32_identifiability"
    out.mkdir(parents=True,exist_ok=True)
    coeff,gain,correlation=[],[],[]
    for valid in (("temporal","spatial","city") if a.validation=="all" else (a.validation,)):
        for rep in P24C:
            c,g,v=fit_pair(df,a.base,valid,rep,a.block_cells,a.seed,a.bootstrap_reps)
            coeff.extend(c);gain.extend(g);correlation.append(v)
    suffix="_smoke"+str(a.max_strata) if a.max_strata else ""
    pd.DataFrame(coeff).to_csv(out/f"27_interaction_parity{suffix}.csv",index=False)
    pd.DataFrame(gain).to_csv(out/f"27_nested_gain_ci{suffix}.csv",index=False)
    pd.DataFrame(observed_support(df)).to_csv(out/f"27_joint_support_qc{suffix}.csv",index=False)
    (out/f"27_collinearity_and_transform{suffix}.json").write_text(
        json.dumps({"coverage":coverage,"training_diagnostics":correlation,
                    "note":"Same eligible weather cohort across all representations; observed joint support is not an estimated flood risk surface.",
                    "spatial_block_ci":"Training coefficient sandwich, heldout gain bootstrap conditional on fitted models"},
                   indent=2,ensure_ascii=False),encoding="utf-8")
    print("[DONE]",out,"Same-cohort rain aggregation parity; no physical risk interpretation")


if __name__=="__main__":
    main()
