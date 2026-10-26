"""Stage28 P0: one common-cohort joint terrain & climate moderation model.

Uses aligned rainfall *mean_mean* by default (P24 grid mean, P3H grid mean).
A single all-four-static-factor complete-case sample is frozen BEFORE any
candidate model comparison, so relief/slope ranking is not a sample artifact.
Static attributes enter ONLY via interactions with P24 and API7.

Nested tests:
 J0 weather I3;
 J1 J0 + local-relief weather interactions;
 J2 J0 + basin-slope weather interactions;
 J3 J0 + relief & slope weather interactions;
 J4 J3 + CRU and basin-urban weather interactions.
J1 vs J3 checks independent slope; J2 vs J3 checks independent relief.
No storm-independent inference, no absolute flood probability, no GEE calls.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd


BASE=("P24","P3H","API7","SM1","P24_x_API7","P24_x_SM1")
MODS=("merit_local_relief_m_approx","basin_slope_degrees",
      "cru_pre_annual_mm","basin_urb_pc_sse")
SHORT={"merit_local_relief_m_approx":"RELIEF",
       "basin_slope_degrees":"SLOPE",
       "cru_pre_annual_mm":"CRU",
       "basin_urb_pc_sse":"URBAN"}
MODELS={
    "J0_hydroclimate":BASE,
    "J1_relief":BASE+("P24_x_RELIEF","API7_x_RELIEF"),
    "J2_slope":BASE+("P24_x_SLOPE","API7_x_SLOPE"),
    "J3_relief_slope":BASE+("P24_x_RELIEF","API7_x_RELIEF",
                            "P24_x_SLOPE","API7_x_SLOPE"),
    "J4_all_context":BASE+("P24_x_RELIEF","API7_x_RELIEF",
                            "P24_x_SLOPE","API7_x_SLOPE",
                            "P24_x_CRU","API7_x_CRU",
                            "P24_x_URBAN","API7_x_URBAN"),
}
PAIRS=[("J0_hydroclimate","J1_relief"),
       ("J0_hydroclimate","J2_slope"),
       ("J1_relief","J3_relief_slope"),
       ("J2_slope","J3_relief_slope"),
       ("J3_relief_slope","J4_all_context")]


def imports():
    here=Path(__file__).resolve().parents[1]
    modules=[]
    for loc in (here/"r32"/"23_conditional_poisson_stage32.py",
                here/"r32"/"25_conditional_interactions_ci.py",
                here/"r32"/"27_rainfall_parity_identifiability.py",
                here/"r33"/"26_city_environment_modifiers.py"):
        spec=importlib.util.spec_from_file_location("s28_"+loc.stem,loc)
        m=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        modules.append(m)
    return modules


def common_static(frame,static):
    """All four environmental modifiers complete and nonnegative, same cohort."""
    if static.grid_id.isna().any() or static.grid_id.duplicated().any():
        raise ValueError("Static grid ID duplicated/NA")
    merged=frame.merge(static[["grid_id","city_id",*MODS]],
                       on="grid_id",how="left",validate="many_to_one")
    good=np.ones(len(merged),dtype=bool)
    for col in MODS:
        values=pd.to_numeric(merged[col],errors="coerce").to_numpy(dtype=float)
        good &= np.isfinite(values)&(values>=0)
    result=merged.loc[good].copy()
    if result.empty:
        raise RuntimeError("No grid-days with four static factors")
    return result,{"before_grid_days":len(frame),"common_grid_days":len(result),
                   "common_strata":result.calendar_stratum_id.nunique(),
                   "common_onsets":int(result.episode_onset.sum()),
                   "common_grids":result.grid_id.nunique(),
                   "city_mapped_days":int(result.city_id.notna().sum())}


def env_features(data,train):
    """Only training observations define environmental transforms."""
    result,meta={},{}
    for col in MODS:
        raw=np.log1p(data[col].to_numpy(dtype=float))
        low,high=np.quantile(raw[train],[.005,.995])
        clip=np.clip(raw,low,high)
        mn=float(clip[train].mean())
        sd=float(clip[train].std())
        if sd<1e-10:
            raise RuntimeError("Unidentified static modifier: "+col)
        z=(clip-mn)/sd
        result[SHORT[col]]=z
        meta[col]={"raw_scale":"log1p","q005":float(low),"q995":float(high),
                   "mean":mn,"sd":sd}
    return result,meta


def fit_validation(data,base,split,rep,seed,block_cells,reps):
    stage23,stage25,stage27,_=imports()
    weather=data.copy()
    weather["imerg_p24_utc_mm_mean"]=data[stage27.P24C[rep]].to_numpy()
    weather["imerg_p3h_max_mm_max"]=data[stage27.P3HC[rep]].to_numpy()
    train,test=stage23.split_train_test(weather,split,seed,block_cells,base)
    if not train.any() or not test.any():
        raise RuntimeError("Missing train or test rows")
    scaled,wmeta=stage23.standardize(weather,train)
    X=stage25.design(scaled)
    z,envmeta=env_features(data,train)
    for short, v in z.items():
        X["P24_x_"+short]=X["P24"]*v
        X["API7_x_"+short]=X["API7"]*v
    keys=data.calendar_stratum_id.to_numpy()
    y=data.episode_onset.to_numpy(dtype=float)
    blocks=stage25.block_ids(data,block_cells)
    stats={}
    coeff=[]
    for label,cols in MODELS.items():
        matrix=np.column_stack([X[k] for k in cols])
        tr=stage25.sorted_groups(matrix,y,keys,blocks,train)
        te=stage25.sorted_groups(matrix,y,keys,blocks,test)
        beta,se,cov,g,tloss,loss,per=stage25.infer_one(tr,te,cols,1e-4)
        stats[label]=(tr,te,per,loss)
        for ix,term in enumerate(cols):
            if "_x_" in term and any(k in term for k in SHORT.values()):
                coeff.append({"validation":split,"model":label,"term":term,
                              "beta":float(beta[ix]),
                              "beta_ci_low":float(beta[ix]-1.96*se[ix]),
                              "beta_ci_high":float(beta[ix]+1.96*se[ix]),
                              "train_spatial_blocks":g,"heldout_loss":loss})
        print(f"[28 {split} {label}] loss={loss:.6f}",flush=True)
    gains=[]
    for before,after in PAIRS:
        a,b=stats[before],stats[after]
        if not np.array_equal(a[1][7],b[1][7]):
            raise RuntimeError("Inconsistent heldout stratum identity")
        gain=stage27.paired_gain(a[2],b[2],a[1][6],a[1][4],reps,seed+23)
        gains.append({"validation":split,"baseline":before,
                      "augmented":after,**gain})
    return coeff,gains,{"validation":split,"weather":wmeta,"environment":envmeta}


def static_correlations(data):
    """Unique-grid-level static correlation; avoid pseudo-replicating dates."""
    one=data[["grid_id",*MODS]].drop_duplicates("grid_id")
    env=np.column_stack([np.log1p(one[c].to_numpy(dtype=float)) for c in MODS])
    c=np.corrcoef(env,rowvar=False)
    if not np.isfinite(c).all():
        raise RuntimeError("Invalid static correlation matrix")
    return {"columns":MODS,
            "log1p_unique_grid_correlation":c.tolist(),
            "unique_grids":len(one),
            "relief_vs_slope_corr":float(c[0,1])}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--panel-path",type=Path)
    p.add_argument("--static-path",type=Path)
    p.add_argument("--rain-parity",choices=("mean_mean","max_max","mean_max"),default="mean_mean")
    p.add_argument("--validation",choices=("temporal","spatial","city","all"),default="all")
    p.add_argument("--max-strata",type=int)
    p.add_argument("--bootstrap-reps",type=int,default=399)
    p.add_argument("--block-cells",type=int,default=25)
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--out",type=Path)
    a=p.parse_args()
    if a.block_cells<1 or a.bootstrap_reps<99:
        raise ValueError("block-cells>=1; bootstrap-reps>=99")
    stage23,stage25,stage27,stage26=imports()
    panel=a.panel_path or a.base/"standard_calendar_22_compact"/"22_standard_daily_with_climate.parquet"
    stat=a.static_path or a.base/"mechanism_project"/"05_static_merged"/"grid_static_context_m3.parquet"
    original,before=stage27.load_common(panel,a.max_strata)
    fixed=stage26.load_context(stat,list(MODS))
    data,coverage=common_static(original,fixed)
    corr=static_correlations(data)
    out=a.out or a.base/"stage33_joint"
    out.mkdir(parents=True,exist_ok=True)
    coefficients,gains,transforms=[],[],[]
    kinds=("temporal","spatial","city") if a.validation=="all" else (a.validation,)
    for split in kinds:
        c,g,t=fit_validation(data,a.base,split,a.rain_parity,a.seed,a.block_cells,a.bootstrap_reps)
        coefficients.extend(c);gains.extend(g);transforms.append(t)
    suffix="_smoke"+str(a.max_strata) if a.max_strata else ""
    pd.DataFrame(coefficients).to_csv(out/f"28_joint_environment_coefficients{suffix}.csv",index=False)
    pd.DataFrame(gains).to_csv(out/f"28_joint_environment_gains{suffix}.csv",index=False)
    (out/f"28_common_cohort_manifest{suffix}.json").write_text(
        json.dumps({"weather_cohort":before,"static_cohort":coverage,
                    "rainfall_representation":a.rain_parity,
                    "static_corr":corr,"transforms":transforms,
                    "warning":"Joint static associations only; not independent storm clusters; leave-one-city-out and changing climate hazard require separate evidence."},
                   ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",out,"Joint terrain and climate sensitivity, not causal claims")


if __name__=="__main__":
    main()
