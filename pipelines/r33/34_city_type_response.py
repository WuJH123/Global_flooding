"""Stage34: UCDB environment-type effect modification on paired heldout risk sets.

Unsupervised class labels are made from city attributes and never flood labels.
This script tests whether P24, API7 and TRAIN-residualized P3H responses
differ between classes. Class main effects are unidentified within time strata.
Do not interpret class conditional relative ratios as absolute flood risk.
"""
from __future__ import annotations
import argparse,importlib.util,json
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

def mods():
    paths=[Path(__file__).parents[1]/"r32"/p for p in
           ("23_conditional_poisson_stage32.py",
            "25_conditional_interactions_ci.py",
            "27_rainfall_parity_identifiability.py",
            "29_rainfall_structure.py")]
    result=[]
    for i,p in enumerate(paths):
        sp=importlib.util.spec_from_file_location("st34_"+str(i),p)
        m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
        result.append(m)
    return result

def prepare(frame,types,static,exclude_low_support=False):
    if types.city_id.duplicated().any():
        raise ValueError("City types must be one city_id per row")
    if static.grid_id.duplicated().any():
        raise ValueError("Static grid IDs not unique")
    cols=["city_id","environment_type_id"]
    if exclude_low_support and "low_support_city" in types.columns:
        types=types.loc[~types.low_support_city]
    t=types[cols].merge(static[["grid_id","city_id"]],on="city_id",
                        how="inner",validate="one_to_many")
    if t.grid_id.duplicated().any():
        raise RuntimeError("One primary UCDB city is required per M3 grid")
    data=frame.merge(t[["grid_id","environment_type_id"]],on="grid_id",
                     how="inner",validate="many_to_one")
    if data.empty:raise RuntimeError("No correctly mapped urban type observations")
    return data

def fit_validation(data,base,validation,seed,blocks,reps,aggregation):
    st23,st25,s27,s29=mods()
    daily,peak=s29.REPRESENTATIONS[aggregation]
    w=data.copy()
    w["imerg_p24_utc_mm_mean"]=data[daily]
    w["imerg_p3h_max_mm_max"]=data[peak]
    train,test=st23.split_train_test(w,validation,seed,blocks,base)
    X,transform=st23.standardize(w,train)
    c,meta=s29.residual_peak(data[daily].to_numpy(float),
                              data[peak].to_numpy(float),train)
    X["C3_resid"]=c
    X["P24_x_API7"]=X["P24"]*X["API7"]
    labels=sorted(w.environment_type_id.dropna().unique())
    if len(labels)<2:raise RuntimeError("Need >=2 climate-independent city environment groups")
    counts=w.loc[train].groupby("environment_type_id").calendar_stratum_id.nunique()
    if any(counts.get(v,0)<20 for v in labels):
        raise RuntimeError("At least 20 training strata per urban class needed")
    base_terms=("P24","API7","SM1","C3_resid","P24_x_API7")
    terms=list(base_terms)
    for lab in labels[1:]:
        for variable in ("P24","API7","C3_resid"):
            key=variable+"_x_"+str(lab)
            X[key]=X[variable]*(w.environment_type_id.eq(lab).to_numpy(float))
            terms.append(key)
    y=w.episode_onset.to_numpy(float)
    ids=w.calendar_stratum_id.to_numpy()
    blk=st25.block_ids(w,blocks)
    result={}
    coefs=[]
    for name,cols in (("T0_global",base_terms),("T1_type_response",tuple(terms))):
        xx=np.column_stack([X[z] for z in cols])
        tr=st25.sorted_groups(xx,y,ids,blk,train)
        te=st25.sorted_groups(xx,y,ids,blk,test)
        b,se,cov,n,trainloss,testloss,per=st25.infer_one(tr,te,cols,1e-4)
        result[name]=(tr,te,per,testloss)
        for i,key in enumerate(cols):
            coefs.append({"validation":validation,"model":name,"term":key,
                          "beta":float(b[i]),"ci_low":float(b[i]-1.96*se[i]),
                          "ci_high":float(b[i]+1.96*se[i]),
                          "train_spatial_blocks":n})
        print(f"[34 {validation}] {name} {testloss:.6f}",flush=True)
    A,B=result["T0_global"],result["T1_type_response"]
    if not np.array_equal(A[1][7],B[1][7]):raise RuntimeError("Changed test risk strata")
    gain=s27.paired_gain(A[2],B[2],A[1][6],A[1][4],reps,seed+34)
    rows=[{"validation":validation,"model_baseline":"T0_global",
           "model_augmented":"T1_type_response",
           "baseline_loss":A[3],"augmented_loss":B[3],**gain,
           "n_city_types":len(labels),"n_test_strata":len(A[1][4])}]
    return coefs,rows,{"validation":validation,
                       "training_transform":transform,
                       "peak_residual":meta,
                       "reference_type":labels[0],
                       "other_types":labels[1:]}

def read(path):
    return pd.read_parquet(path) if path.suffix==".parquet" else pd.read_csv(path,dtype={"grid_id":str,"city_id":str})

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--panel-path",type=Path)
    p.add_argument("--city-types",type=Path,required=True)
    p.add_argument("--static-path",type=Path)
    p.add_argument("--aggregation",choices=("mean","max"),default="mean")
    p.add_argument("--validation",choices=("temporal","spatial","city","all"),default="all")
    p.add_argument("--max-strata",type=int)
    p.add_argument("--type-epoch",type=int,default=2020)
    p.add_argument("--allow-retrospective-types",action="store_true",
                   help="Exploratory only: allow 2020 city environment class on pre-2020 flood records")
    p.add_argument("--exclude-low-support-cities",action="store_true")
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--block-cells",type=int,default=25)
    p.add_argument("--bootstrap-reps",type=int,default=399)
    p.add_argument("--out",type=Path)
    a=p.parse_args()
    if a.bootstrap_reps<99 or a.block_cells<1:raise ValueError("Invalid parameters")
    st23,st25,s27,s29=mods()
    path=a.panel_path or a.base/"standard_calendar_22_compact"/"22_standard_daily_with_climate.parquet"
    static=a.static_path or a.base/"mechanism_project"/"05_static_merged"/"grid_static_context_m3.parquet"
    frame,qc=s27.load_common(path,a.max_strata)
    if not a.allow_retrospective_types:
        frame=frame.loc[frame.year.ge(a.type_epoch)].copy()
    data=prepare(frame,read(a.city_types),read(static),a.exclude_low_support_cities)
    coefs,comparison,meta=[],[],[]
    for mode in (("temporal","spatial","city") if a.validation=="all" else (a.validation,)):
        c,g,m=fit_validation(data,a.base,mode,a.seed,a.block_cells,a.bootstrap_reps,a.aggregation)
        coefs.extend(c);comparison.extend(g);meta.append(m)
    out=a.out or a.base/"stage33_city_response"
    out.mkdir(parents=True,exist_ok=True)
    suf="_smoke"+str(a.max_strata) if a.max_strata else ""
    pd.DataFrame(coefs).to_csv(out/f"34_type_response_coefficients{suf}.csv",index=False)
    pd.DataFrame(comparison).to_csv(out/f"34_type_heldout_gain{suf}.csv",index=False)
    (out/f"34_type_response_manifest{suf}.json").write_text(json.dumps({
        "weather":qc,"typed_grid_days":len(data),"typed_strata":data.calendar_stratum_id.nunique(),
        "typed_onsets":int(data.episode_onset.sum()),"validation":meta,
        "type_epoch":a.type_epoch,"allow_retrospective_types":a.allow_retrospective_types,
        "warning":"Type membership from covariates only, but UCDB M3 cities are positively selected; not general global flood risk."
    },ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",out)

if __name__=="__main__":main()
