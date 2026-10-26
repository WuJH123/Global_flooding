"""Stage34b: continuously varying environmental modifiers on SAME city/test sets.

Compare (i) Stage34 global baseline, (ii) fixed Stage33 categorical type
interactions, (iii) ONE-at-a-time continuous P24 modifiers, (iv) simultaneous
continuous modifiers; optional (v) P24+API7 continuous modifiers.

For every comparison all models have IDENTICAL complete-case eligible
grid-date risk strata, year>=2020 by default, train/test heldouts and
onset weights. Trained exposure transforms and environmental centering are
TRAIN-ONLY. City variables are always STATIC within grid-calendar strata:
only the interaction with dynamic exposure is identifiable (not main effect).
No claims about flood probability or drainage capacity.
"""
from __future__ import annotations
import argparse,importlib.util,json
from pathlib import Path
import numpy as np
import pandas as pd

BASE=("P24","API7","SM1","C3_resid","P24_x_API7")
ALLOWED={
    "ghsl_built_fraction_wmean":"built",
    "ghsl_urban_area_fraction_wmean":"urban",
    "merit_local_relief_m_approx":"relief",
    "cru_pre_annual_mm":"climate",
    "basin_slope_degrees":"slope",
    "nearest_mapped_river_km":"river_distance",
    "hand_lt2_fraction":"low_hand",
    "impervious_mean_fraction":"impervious"
}
PRESETS=("ghsl_built_fraction_wmean","merit_local_relief_m_approx",
         "cru_pre_annual_mm","ghsl_urban_area_fraction_wmean")

def load_modules():
    home=Path(__file__).resolve().parent
    files=[home/"34_city_type_response.py"]+[
        home.parent/"r32"/name for name in
        ("23_conditional_poisson_stage32.py","25_conditional_interactions_ci.py",
         "27_rainfall_parity_identifiability.py","29_rainfall_structure.py")
    ]
    result=[]
    for i,f in enumerate(files):
        sp=importlib.util.spec_from_file_location("s34b_"+str(i),f)
        mod=importlib.util.module_from_spec(sp);sp.loader.exec_module(mod)
        result.append(mod)
    return result

def get_features(json_file,requested):
    if json_file is not None:
        raw=json.loads(Path(json_file).read_text(encoding="utf-8"))
        selected=list(raw["selected"])
    else:
        selected=list(PRESETS)
    if requested:
        selected=[x.strip() for x in requested.split(",") if x.strip()]
    bad=[x for x in selected if x not in ALLOWED]
    if bad:raise ValueError("Unrecognized continuous moderator(s): "+str(bad))
    if len(set(selected))!=len(selected):raise ValueError("Duplicate moderators")
    if not 1<=len(selected)<=6:raise ValueError("1..6 continuous moderators required")
    return selected

def link_complete_environment(frame,types,static,city_environment,features,exclude_low_support=False):
    """Inner city link and complete-case restriction BEFORE split and model fit."""
    stage34,_,_,_,_=load_modules()
    if city_environment.city_id.isna().any() or city_environment.city_id.duplicated().any():
        raise ValueError("City environment table must have unique nonnull city IDs")
    missing=set(features)-set(city_environment)
    if missing:raise ValueError("Missing environment columns: "+str(missing))
    d=stage34.prepare(frame,types,static,exclude_low_support)
    ids=static[["grid_id","city_id"]]
    if ids.grid_id.duplicated().any():raise ValueError("Duplicate grid IDs in static input")
    d=d.merge(ids,on="grid_id",how="left",validate="many_to_one")
    if d.city_id.isna().any():raise RuntimeError("Typed grid has no primary city")
    env=city_environment[["city_id"]+features].copy()
    for col in features:
        env[col]=pd.to_numeric(env[col],errors="coerce")
    joined=d.merge(env,on="city_id",how="left",validate="many_to_one")
    finite=np.isfinite(joined[features].to_numpy(dtype=float)).all(axis=1)
    physical=(joined[features].to_numpy(dtype=float)>=0).all(axis=1)
    retained=joined.loc[finite&physical].copy()
    if retained.empty:raise RuntimeError("No complete-case city environment observations")
    report={"typed_rows_before":int(len(joined)),
            "complete_rows":int(len(retained)),
            "retained_rows_fraction":float(len(retained)/len(joined)),
            "typed_onsets_before":int(joined.episode_onset.sum()),
            "complete_onsets":int(retained.episode_onset.sum()),
            "full_cities":int(joined.city_id.nunique()),
            "retained_cities":int(retained.city_id.nunique()),
            "dropped_cities_missing_environment":int(joined.city_id.nunique()-retained.city_id.nunique()),
            "exclusion":"rows missing ANY selected static moderator; identical rows for all candidate models"}
    return retained,report

def transform_environment(data,train,features):
    """1/99% train-city clipping of log1p physical context, train-city z-scores.

    Unique city values, rather than number of media-reported grid-days, set
    the environmental transform. Test cities never enter transform fitting
    when using the independent city split.
    """
    trcity=data.loc[train,["city_id"]+features].drop_duplicates("city_id")
    if len(trcity)<30:raise RuntimeError("Need >=30 training cities")
    transformed={};meta={}
    for f in features:
        raw=data[f].to_numpy(dtype=float)
        if not np.isfinite(raw).all() or (raw<0).any():
            raise ValueError("Continuous city environment must be nonnegative finite")
        unique=np.log1p(trcity[f].to_numpy(dtype=float))
        qlo,qhi=np.quantile(unique,[.01,.99])
        fit=np.clip(unique,qlo,qhi)
        mu=float(fit.mean());sd=float(fit.std())
        if sd<1e-8:raise RuntimeError(f"No train-city environmental variation in {f}")
        z=(np.clip(np.log1p(raw),qlo,qhi)-mu)/sd
        name="Z_"+ALLOWED[f]
        if name in transformed:raise RuntimeError("Duplicate moderator label")
        transformed[name]=z
        meta[f]={"design_name":name,"transform":"train-city clip log1p then z",
                 "train_city_count":len(trcity),
                 "train_log_q01":float(qlo),"train_log_q99":float(qhi),
                 "train_log_mean":mu,"train_log_sd":sd,
                 "environment_p10":float(np.quantile(trcity[f],.1)),
                 "environment_p50":float(np.quantile(trcity[f],.5)),
                 "environment_p90":float(np.quantile(trcity[f],.9))}
    return transformed,meta

def features_to_models(X,labels,features,include_extended=False):
    terms={}
    terms["E0_global"]=BASE
    type_add=[]
    for lab in labels[1:]:
        for weather in ("P24","API7","C3_resid"):
            key=weather+"_x_"+str(lab)
            type_add.append(key)
    terms["E1_type"]=BASE+tuple(type_add)
    singles=[]
    for f in features:
        field="P24_x_Z_"+ALLOWED[f]
        singles.append(field)
        terms["S_"+ALLOWED[f]]=BASE+(field,)
    terms["E2_continuous_P24"]=BASE+tuple(singles)
    if include_extended:
        extra=tuple("API7_x_Z_"+ALLOWED[f] for f in features)
        terms["E3_continuous_P24_API7"]=BASE+tuple(singles)+extra
    required=set(sum((list(v) for v in terms.values()),[]))
    if not required.issubset(X):
        raise RuntimeError("Missing model terms: "+str(required-set(X)))
    return terms

def fit_validation(data,base,features,mode,aggregation,seed,block_cells,reps,include_extended):
    _,st23,st25,st27,s29=load_modules()
    daily,peak=s29.REPRESENTATIONS[aggregation]
    w=data.copy()
    w["imerg_p24_utc_mm_mean"]=w[daily].to_numpy(dtype=float)
    w["imerg_p3h_max_mm_max"]=w[peak].to_numpy(dtype=float)
    train,test=st23.split_train_test(w,mode,seed,block_cells,base)
    if train.sum()<100 or test.sum()<100:raise RuntimeError("Not enough train/test observations")
    weather,weather_meta=st23.standardize(w,train)
    c,cmeta=s29.residual_peak(w[daily].to_numpy(float),w[peak].to_numpy(float),train)
    X=dict(weather)
    X["C3_resid"]=c
    X["P24_x_API7"]=np.asarray(X["P24"])*np.asarray(X["API7"])
    labels=sorted(w.environment_type_id.dropna().unique())
    if len(labels)<2:raise RuntimeError("At least two environment types needed")
    for label in labels[1:]:
        mask=w.environment_type_id.eq(label).to_numpy(dtype=float)
        for weather_key in ("P24","API7","C3_resid"):
            X[weather_key+"_x_"+str(label)]=np.asarray(X[weather_key])*mask
    Z,envmeta=transform_environment(w,train,features)
    for f in features:
        z=Z["Z_"+ALLOWED[f]]
        X["P24_x_Z_"+ALLOWED[f]]=np.asarray(X["P24"])*z
        if include_extended:
            X["API7_x_Z_"+ALLOWED[f]]=np.asarray(X["API7"])*z
    models=features_to_models(X,labels,features,include_extended)
    y=w.episode_onset.to_numpy(dtype=float)
    group=w.calendar_stratum_id.to_numpy()
    blk=st25.block_ids(w,block_cells)
    out={};coeff=[]
    for model,terms in models.items():
        xx=np.column_stack([X[n] for n in terms])
        tr=st25.sorted_groups(xx,y,group,blk,train)
        te=st25.sorted_groups(xx,y,group,blk,test)
        beta,se,cov,nblocks,train_loss,test_loss,per=st25.infer_one(tr,te,terms,1e-4)
        out[model]={"train":tr,"test":te,"beta":beta,"cov":cov,
                    "terms":terms,"loss":test_loss,"per":per}
        coeff.extend([{"validation":mode,"model":model,"term":term,
                       "beta":float(b),"ci_low":float(b-1.96*s),
                       "ci_high":float(b+1.96*s),"n_train_spatial_blocks":nblocks}
                      for term,b,s in zip(terms,beta,se)])
        print(f"[34b {mode}] {model} loss={test_loss:.6f}",flush=True)
    reference=out["E0_global"]["test"]
    for model,result in out.items():
        if not np.array_equal(reference[7],result["test"][7]) or not np.array_equal(reference[4],result["test"][4]):
            raise RuntimeError(f"Different heldout case/reference strata in {model}")
    refpairs=[("E0_global","E1_type")]
    refpairs += [("E0_global","S_"+ALLOWED[f]) for f in features]
    refpairs += [("E0_global","E2_continuous_P24"),("E1_type","E2_continuous_P24")]
    if include_extended:
        refpairs += [("E2_continuous_P24","E3_continuous_P24_API7"),
                     ("E1_type","E3_continuous_P24_API7")]
    gains=[]
    for old,new in refpairs:
        A,B=out[old],out[new]
        g=st27.paired_gain(A["per"],B["per"],A["test"][6],
                          A["test"][4],reps=reps,seed=seed+46)
        gains.append({"validation":mode,"baseline":old,"augmented":new,
                      "baseline_loss":A["loss"],"augmented_loss":B["loss"],**g})
    response=[]
    choose=out["E2_continuous_P24"]
    terms=choose["terms"]
    idx=terms.index("P24")
    for f in features:
        column="P24_x_Z_"+ALLOWED[f]
        j=terms.index(column)
        for label,quant in (("p10",.1),("p50",.5),("p90",.9)):
            value=envmeta[f]["environment_"+label]
            m=envmeta[f]
            z=(np.clip(np.log1p(value),m["train_log_q01"],m["train_log_q99"])
               -m["train_log_mean"])/m["train_log_sd"]
            grad=np.zeros(len(terms));grad[idx]=1.;grad[j]=z
            contrast=float(grad@choose["beta"])
            se=float(np.sqrt(max(0.,grad@choose["cov"]@grad)))
            response.append({"validation":mode,"model":"E2_continuous_P24",
                             "environment_feature":f,"environment_level":label,
                             "environment_raw_value":float(value),
                             "environment_z":float(z),
                             "p24_z_change":1.0,"api7_z_fixed":0.0,
                             "conditional_P24_ratio":float(np.exp(np.clip(contrast,-25,25))),
                             "ratio_ci_low":float(np.exp(np.clip(contrast-1.96*se,-25,25))),
                             "ratio_ci_high":float(np.exp(np.clip(contrast+1.96*se,-25,25))),
                             "other_environment_z_fixed":0.0,
                             "CI_note":"train spatial sandwich, model-conditional; NO flood probability"})
    manifest={"validation":mode,"weather_transform":weather_meta,"peak_residual":cmeta,
              "environment_transform":envmeta,"selected_features":features,
              "train_strata":int(len(reference[4])) if False else int(len(out["E0_global"]["train"][4])),
              "test_strata":int(len(reference[4])),
              "test_onsets":int(reference[4].sum()),
              "test_spatial_blocks":int(np.unique(reference[6]).size),
              "sample_consistency":"all fitted models share same complete-case panel and heldout strata"}
    return pd.DataFrame(coeff),pd.DataFrame(gains),pd.DataFrame(response),manifest

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--panel-path",type=Path)
    p.add_argument("--static-path",type=Path)
    p.add_argument("--city-types",type=Path,required=True)
    p.add_argument("--city-environment",type=Path)
    p.add_argument("--selection-json",type=Path)
    p.add_argument("--features",help="Comma-separated continuous city feature names; overrides selection JSON")
    p.add_argument("--type-epoch",type=int,default=2020)
    p.add_argument("--allow-retrospective",action="store_true",
                   help="EXPLORATORY: permit static 2020 city exposure on pre-2020 flood dates")
    p.add_argument("--exclude-low-support-cities",action="store_true")
    p.add_argument("--aggregation",choices=("mean","max"),default="mean")
    p.add_argument("--validation",choices=("temporal","spatial","city","all"),default="all")
    p.add_argument("--max-strata",type=int)
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--block-cells",type=int,default=25)
    p.add_argument("--bootstrap-reps",type=int,default=399)
    p.add_argument("--extended-api7",action="store_true")
    p.add_argument("--out",type=Path)
    a=p.parse_args()
    if a.bootstrap_reps<99 or a.block_cells<1:
        raise ValueError("Invalid bootstrap settings")
    st34,st23,st25,st27,s29=load_modules()
    panel=a.panel_path or a.base/"standard_calendar_22_compact"/"22_standard_daily_with_climate.parquet"
    staticpath=a.static_path or a.base/"mechanism_project"/"05_static_merged"/"grid_static_context_m3.parquet"
    envpath=a.city_environment or a.base/"mechanism_project"/"30_urban_context"/f"30b_city_features_{a.type_epoch}.parquet"
    features=get_features(a.selection_json,a.features)
    data,qc=st27.load_common(panel,a.max_strata)
    if not a.allow_retrospective:
        data=data.loc[data.year.ge(a.type_epoch)].copy()
    types=st34.read(a.city_types)
    stat=st34.read(staticpath)
    env=st34.read(envpath)
    if "epoch" in env:
        env=env.loc[env.epoch.eq(a.type_epoch)].copy()
    d,coverage=link_complete_environment(data,types,stat,env,features,a.exclude_low_support_cities)
    coefs,comparisons,response,manifests=[],[],[],[]
    for mode in ("temporal","spatial","city") if a.validation=="all" else (a.validation,):
        c,g,r,m=fit_validation(d,a.base,features,mode,a.aggregation,
                               a.seed,a.block_cells,a.bootstrap_reps,a.extended_api7)
        coefs.append(c);comparisons.append(g);response.append(r);manifests.append(m)
    out=a.out or a.base/"stage33_continuous_response"
    out.mkdir(parents=True,exist_ok=True)
    suffix="_smoke"+str(a.max_strata) if a.max_strata else ""
    pd.concat(coefs,ignore_index=True).to_csv(out/f"34b_coefficients{suffix}.csv",index=False)
    pd.concat(comparisons,ignore_index=True).to_csv(out/f"34b_heldout_comparison{suffix}.csv",index=False)
    pd.concat(response,ignore_index=True).to_csv(out/f"34b_P24_environment_marginal_ratios{suffix}.csv",index=False)
    (out/f"34b_manifest{suffix}.json").write_text(json.dumps({
        "source_panel":str(panel),"source_city_table":str(envpath),
        "weather_coverage_before_city_match":qc,
        "complete_case_coverage":coverage,"fits":manifests,
        "features":features,"type_epoch":a.type_epoch,
        "historical_leakage_guard":not a.allow_retrospective,
        "interpretation":"City continuous moderators affect P24 slope, not city flood probability",
        "limitation":"M3 positive-observed cities, 20km urban proxies; spatial bootstrap not independent storms",
        "uncertainty":"training spatial sandwich and paired spatial-block fixed-model heldout gain",
        "SMOKE":bool(a.max_strata)
    },ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",out,"common_cases",coverage["complete_onsets"],flush=True)

if __name__=="__main__":main()
