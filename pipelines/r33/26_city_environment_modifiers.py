"""Stage26: explain environmental modification of within-stratum weather response.

Time-invariant city/basin attributes ENTER ONLY THROUGH INTERACTIONS with
time-varying weather. Includes a common sample-restricted baseline and each
candidate modifier on EXACTLY the same training/test strata.

Default moderators, whose availability was verified in Stage05 code:
- basin_urb_pc_sse: BasinATLAS BASIN-LAND URBAN context, NOT city imperviousness
- cru_pre_annual_mm: 1991–2020 long-term mean annual precipitation
- basin_slope_degrees: center-assigned BasinATLAS level07 slope
- merit_local_relief_m_approx: 20-km topographic relief

No unsupported GHSL built_fraction is silently synthesized; optional external
GHSL grid table may be passed via --ghsl-parquet --ghsl-variable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from importlib.util import spec_from_file_location,module_from_spec


def shared():
    path=Path(__file__).resolve().parents[1]/"r32"/"25_conditional_interactions_ci.py"
    spec=spec_from_file_location("stage25_for_city",path)
    obj=module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


STATIC_MODIFIERS={
    "basin_urb_pc_sse":"basin-level urban cover indicator (source coding preserved)",
    "cru_pre_annual_mm":"1991–2020 rainfall climatology in mm/yr",
    "basin_slope_degrees":"BasinATLAS basin mean slope degrees",
    "merit_local_relief_m_approx":"20km local relief in m",
}


def load_context(path:Path,mods,ghsl=None,ghsl_variable=None):
    con=duckdb.connect()
    available={str(x[0]) for x in con.execute("DESCRIBE SELECT * FROM read_parquet(?)",[str(path)]).fetchall()}
    if not set(["grid_id","city_id",*mods]).issubset(available):
        raise ValueError("Static file missing: "+str(sorted(set(["grid_id","city_id",*mods])-available)))
    frame=con.execute("SELECT grid_id,city_id,"+",".join('"'+c+'"' for c in mods)+" FROM read_parquet(?)",[str(path)]).fetchdf()
    if ghsl is not None:
        if not ghsl_variable:
            raise ValueError("--ghsl-variable required with --ghsl-parquet")
        ghsl_cols={x[0] for x in con.execute("DESCRIBE SELECT * FROM read_parquet(?)",[str(ghsl)]).fetchall()}
        if not {"grid_id",ghsl_variable}.issubset(ghsl_cols):
            raise ValueError("GHSL grid table lacks exact grid_id and specified column")
        gh=con.execute(f'SELECT grid_id, "{ghsl_variable}" FROM read_parquet(?)',[str(ghsl)]).fetchdf()
        if gh.grid_id.duplicated().any():
            raise RuntimeError("GHSL input must have one row/grid; use fixed epoch snapshot")
        if ghsl_variable in frame.columns:
            raise RuntimeError("GHSL modifier duplicates existing static variable")
        frame=frame.merge(gh,on="grid_id",how="left",validate="one_to_one")
    con.close()
    if frame.grid_id.isna().any() or frame.grid_id.duplicated().any():
        raise RuntimeError("Static grid_id must be unique and nonnull")
    return frame


def prepare_static(panel,static,mods):
    mapcols=static.set_index("grid_id")
    for col in ["city_id",*mods]:
        panel[col]=panel.grid_id.map(mapcols[col])
    mask=np.ones(len(panel),dtype=bool)
    for col in mods:
        s=pd.to_numeric(panel[col],errors="coerce")
        panel[col]=s
        mask &= np.isfinite(s.to_numpy(dtype=float)) & s.to_numpy(dtype=float).__ge__(0)
    return mask


def moderation_fit(data,modifier,base,validation,seed,block_cells,
                   reps=399,ridge=1e-4):
    stage=shared()
    train,test=stage.get_stage23().split_train_test(data,validation,seed,block_cells,base)
    if not train.any() or not test.any():
        raise RuntimeError(f"{validation}: No training or test rows with static environmental attributes")
    scale,transform=stage.get_stage23().standardize(data,train)
    x=stage.design(scale)
    raw=np.log1p(data[modifier].to_numpy(dtype=float))
    lo,hi=np.quantile(raw[train],[.005,.995])
    raw=np.clip(raw,lo,hi)
    mean=float(raw[train].mean())
    sd=float(raw[train].std())
    if sd<=1e-10:
        raise RuntimeError(f"Environment variable {modifier} constant after filtering")
    z=(raw-mean)/sd
    x["P24_x_ENV"]=x["P24"]*z
    x["API7_x_ENV"]=x["API7"]*z
    basic=("P24","API7","SM1","P3H","P24_x_API7")
    terms={"M0_common":basic,"M1_env_modulation":basic+("P24_x_ENV","API7_x_ENV")}
    y=data.episode_onset.to_numpy(dtype=np.float64)
    sid=data.calendar_stratum_id.to_numpy()
    blk=stage.block_ids(data,block_cells)
    fits={}
    coef=[]
    for label,columns in terms.items():
        X=np.column_stack([x[v] for v in columns])
        tr=stage.sorted_groups(X,y,sid,blk,train)
        te=stage.sorted_groups(X,y,sid,blk,test)
        b,se,cov,ng,t_loss,e_loss,per=stage.infer_one(tr,te,columns,ridge)
        fits[label]=(tr,te,b,se,cov,ng,t_loss,e_loss,per)
        for idx,term in enumerate(columns):
            val=float(b[idx]);err=float(se[idx])
            coef.append({"validation":validation,"modifier":modifier,"model":label,
                         "term":term,"beta":val,"cluster_se":err,
                         "beta_ci_low":val-1.96*err,"beta_ci_high":val+1.96*err,
                         "conditional_ratio":float(np.exp(np.clip(val,-25,25))),
                         "ratio_ci_low":float(np.exp(np.clip(val-1.96*err,-25,25))),
                         "ratio_ci_high":float(np.exp(np.clip(val+1.96*err,-25,25))),
                         "n_train_spatial_blocks":ng})
        print(f"[{validation} {modifier}] {label} heldout_loss={e_loss:.6f}",flush=True)
    orig=fits["M0_common"];updated=fits["M1_env_modulation"]
    if not np.array_equal(orig[1][7],updated[1][7]):
        raise RuntimeError("Environmental comparison lost paired risk strata")
    blk=orig[1][6]
    uniq,inv=np.unique(blk,return_inverse=True)
    if len(uniq)<15:
        raise RuntimeError("Too few spatial held-out blocks to resample")
    num=np.bincount(inv,weights=orig[8]-updated[8],minlength=len(uniq))
    den=np.bincount(inv,weights=orig[1][4],minlength=len(uniq))
    rng=np.random.default_rng(seed+99)
    boot=np.empty(reps)
    for i in range(reps):
        ix=rng.integers(0,len(uniq),len(uniq))
        boot[i]=num[ix].sum()/den[ix].sum()
    low,high=np.percentile(boot,[2.5,97.5])
    metric={"validation":validation,"modifier":modifier,"baseline":orig[7],
            "with_modifier":updated[7],"gain":float(num.sum()/den.sum()),
            "gain_ci_low":float(low),"gain_ci_high":float(high),
            "train_strata":len(orig[0][4]),"test_strata":len(orig[1][4]),
            "test_onsets":int(orig[1][4].sum()),
            "bootstrap_spatial_blocks":len(uniq),
            "n_rows_static_complete":len(data),
            "modifier_log1p_p005":float(lo),"modifier_log1p_p995":float(hi),
            "modifier_log1p_mean":mean,"modifier_log1p_sd":sd}
    return pd.DataFrame(coef),metric


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--panel-path",type=Path,default=None)
    p.add_argument("--static-path",type=Path,default=None)
    p.add_argument("--modifiers",default="basin_urb_pc_sse,cru_pre_annual_mm,basin_slope_degrees,merit_local_relief_m_approx")
    p.add_argument("--ghsl-parquet",type=Path,default=None)
    p.add_argument("--ghsl-variable",default=None)
    p.add_argument("--validation",choices=("temporal","spatial","city","all"),default="city")
    p.add_argument("--max-strata",type=int,default=None)
    p.add_argument("--block-cells",type=int,default=25)
    p.add_argument("--bootstrap-reps",type=int,default=399)
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--min-complete-strata",type=int,default=1000)
    p.add_argument("--out",type=Path,default=None)
    args=p.parse_args()
    if args.bootstrap_reps<99 or args.block_cells<1:
        raise ValueError("At least 99 bootstrap reps, block_cells >=1")
    modifiers=[x.strip() for x in args.modifiers.split(",") if x.strip()]
    if not modifiers or len(set(modifiers))!=len(modifiers):
        raise ValueError("--modifiers must be unique and nonempty")
    allowed=set(STATIC_MODIFIERS)
    if args.ghsl_parquet is not None and args.ghsl_variable:
        allowed.add(args.ghsl_variable)
    if set(modifiers)-allowed:
        raise ValueError("Unsupported modifier(s): "+str(sorted(set(modifiers)-allowed)))
    static=args.static_path or args.base/"mechanism_project"/"05_static_merged"/"grid_static_context_m3.parquet"
    panel=args.panel_path or args.base/"standard_calendar_22_compact"/"22_standard_daily_with_climate.parquet"
    stage=shared()
    data,before,after=stage.get_stage23().load_panel(args.base,args.max_strata,False,panel)
    if not args.max_strata and (
        after["eligible_days"],after["eligible_strata"],after["eligible_onsets"]
    )!=(2543887,600723,641587):
        raise RuntimeError("Stage23 eligibility changed; stop before city moderation")
    static_df=load_context(static,[v for v in modifiers if v!=args.ghsl_variable],
                           args.ghsl_parquet,args.ghsl_variable)
    mask=prepare_static(data,static_df,modifiers)
    if args.validation in ("city","all"):
        # City IDs only for city-holdout split; other contexts enter as interaction only.
        n_city=data.loc[mask,"city_id"].notna().sum()
        if n_city==0:
            raise RuntimeError("No primary UCDB city IDs remain")
    out=args.out or args.base/"stage33_modifiers"
    out.mkdir(parents=True,exist_ok=True)
    all_coef,all_metric=[],[]
    coverage=[]
    for variable in modifiers:
        subset=data[
            np.isfinite(data[variable].to_numpy(dtype=float)) &
            (data[variable].to_numpy(dtype=float)>=0)
        ].copy()
        st=subset.calendar_stratum_id.nunique()
        if st<args.min_complete_strata:
            raise RuntimeError(f"{variable} has too few complete static strata: {st}")
        coverage.append({"modifier":variable,"complete_grid_dates":len(subset),
                         "complete_strata":st,"unique_grids":subset.grid_id.nunique(),
                         "ucdb_mapped_grid_dates":int(subset.city_id.notna().sum()),
                         "missing_or_invalid_grid_dates":int(len(data)-len(subset))})
        for kind in (("temporal","spatial","city") if args.validation=="all" else (args.validation,)):
            coef,metric=moderation_fit(subset,variable,args.base,kind,args.seed,
                                       args.block_cells,args.bootstrap_reps)
            all_coef.append(coef);all_metric.append(metric)
    suffix="_smoke"+str(args.max_strata) if args.max_strata else ""
    pd.concat(all_coef,ignore_index=True).to_csv(out/f"26_environment_effect_modification_ci{suffix}.csv",index=False)
    pd.DataFrame(all_metric).to_csv(out/f"26_environment_heldout_gain_ci{suffix}.csv",index=False)
    pd.DataFrame(coverage).to_csv(out/f"26_environment_coverage{suffix}.csv",index=False)
    (out/f"26_environment_manifest{suffix}.json").write_text(
        json.dumps({"variables":{k:STATIC_MODIFIERS.get(k,"GHSL externally supplied, user-defined") for k in modifiers},
                    "original_stage23_eligibility":after,"source_static":str(static),
                    "source_climate":str(panel),
                    "warning":"Every modifier uses its OWN complete-case sample. Compare against its paired M0 only, not across modifiers. Basin urban is not city imperviousness. No independent-storm causal inference."},
                   ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",out,"Environment interactions only, NOT static main effects",flush=True)


if __name__=="__main__":
    main()
