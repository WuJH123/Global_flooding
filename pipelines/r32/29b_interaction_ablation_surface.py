"""Stage29b: isolate rainfall-memory interactions and export conditional surfaces.

Case-time-stratified conditional Poisson using exactly the Stage29 cohort,
date referents, climate transforms, rain operators, and train/test split.
Four NESTED designs compare:
 B0: P24+API7+SM1+C3 residual
 B1: B0 + P24*API7
 B2: B0 + C3*API7
 B3: B0 + both interactions

Every comparison uses identical risk sets and paired spatial-block heldout
bootstrap (fixed fitted models). Effect surfaces express RELATIVE conditional
onset intensity, NOT annual flood probabilities, true inundation risk, or
causally identified effects. Surfaces and training covariance are conditional
on the fitted model; storms shared across blocks are not independent.
"""
from __future__ import annotations
import argparse,importlib.util,json
from pathlib import Path
import numpy as np
import pandas as pd

BASE=("P24","API7","SM1","C3_resid")
MODELS={
    "B0_no_interactions":BASE,
    "B1_P24_x_API7":BASE+("P24_x_API7",),
    "B2_C3_x_API7":BASE+("C3_x_API7",),
    "B3_both_interactions":BASE+("P24_x_API7","C3_x_API7"),
}
COMPARISONS=(
    ("B0_no_interactions","B1_P24_x_API7"),
    ("B0_no_interactions","B2_C3_x_API7"),
    ("B0_no_interactions","B3_both_interactions"),
    ("B1_P24_x_API7","B3_both_interactions"),
    ("B2_C3_x_API7","B3_both_interactions"),
)

def get_stage_modules():
    path=Path(__file__).resolve().parent
    mods=[]
    for i,filename in enumerate(("23_conditional_poisson_stage32.py",
                                 "25_conditional_interactions_ci.py",
                                 "27_rainfall_parity_identifiability.py",
                                 "29_rainfall_structure.py")):
        sp=importlib.util.spec_from_file_location("s29b_"+str(i),path/filename)
        m=importlib.util.module_from_spec(sp)
        sp.loader.exec_module(m);mods.append(m)
    return mods

def design_features(scaled,c3):
    x=dict(scaled)
    x["C3_resid"]=np.asarray(c3,dtype=float)
    x["P24_x_API7"]=np.asarray(x["P24"])*np.asarray(x["API7"])
    x["C3_x_API7"]=np.asarray(c3)*np.asarray(x["API7"])
    return x

def design_row(terms,p24,api7,sm1=0.,c3=0.):
    d={"P24":p24,"API7":api7,"SM1":sm1,"C3_resid":c3,
       "P24_x_API7":p24*api7,"C3_x_API7":c3*api7}
    return np.array([d[t] for t in terms],dtype=float)

def contrast(beta,cov,x,ref):
    g=np.asarray(x,float)-np.asarray(ref,float)
    value=float(g@beta)
    error=float(np.sqrt(max(0.,g@cov@g)))
    return value,float(np.exp(np.clip(value,-25,25))),float(np.exp(np.clip(value-1.96*error,-25,25))),float(np.exp(np.clip(value+1.96*error,-25,25)))

def native_mm(z,metadata):
    return float(max(0.,np.expm1(float(z)*metadata["train_sd"]+metadata["train_mean"])))

def make_surface(beta,cov,terms,scaled,train,meta,validation,scheme,n=25,support_min=25):
    """Avoid displaying unsupported corners of 2D exposure domain as findings."""
    p=np.asarray(scaled["P24"])[train]
    a=np.asarray(scaled["API7"])[train]
    if len(p)<100:raise ValueError("Too little training data for a surface")
    pe=np.linspace(*np.quantile(p,[.05,.95]),n)
    ae=np.linspace(*np.quantile(a,[.05,.95]),n)
    # Histogram counts are training OBSERVATIONS (not independent storms).
    hist,px,ax=np.histogram2d(p,a,bins=(np.linspace(pe[0],pe[-1],14),
                                       np.linspace(ae[0],ae[-1],14)))
    pref=float(np.median(p))
    aref=float(np.median(a))
    reference=design_row(terms,pref,aref)
    results=[]
    for aidx,az in enumerate(ae):
        for pidx,pz in enumerate(pe):
            i=np.searchsorted(px,pz,side="right")-1
            j=np.searchsorted(ax,az,side="right")-1
            i=int(np.clip(i,0,hist.shape[0]-1))
            j=int(np.clip(j,0,hist.shape[1]-1))
            count=int(hist[i,j])
            v,ratio,lo,hi=contrast(beta,cov,design_row(terms,float(pz),float(az)),reference)
            results.append({"validation":validation,"rain_aggregation":scheme,
                            "model":"B3_both_interactions",
                            "p24_index":pidx,"api7_index":aidx,
                            "p24_z":float(pz),"api7_z":float(az),
                            "p24_mm":native_mm(pz,meta["P24"]),
                            "api7_mm":native_mm(az,meta["API7"]),
                            "c3_resid_z":0.,"sm1_z":0.,
                            "relative_log_conditional_intensity":v,
                            "relative_conditional_ratio":ratio,
                            "ratio_ci_low":lo,"ratio_ci_high":hi,
                            "train_support_observations_bin":count,
                            "within_empirical_support":bool(count>=support_min),
                            "reference":"train P24/API7 medians, C3=0, SM1=0",
                            "ci_method":"train spatial-cluster sandwich; fixed fitted model"})
    curves=[]
    for level_name,quant in (("low_api7",.1),("median_api7",.5),("high_api7",.9)):
        z=float(np.quantile(a,quant))
        anchor=design_row(terms,pref,z)
        for pidx,pz in enumerate(pe):
            val,rr,lo,hi=contrast(beta,cov,design_row(terms,float(pz),z),anchor)
            curves.append({"validation":validation,"rain_aggregation":scheme,
                           "api7_level":level_name,"p24_index":pidx,
                           "p24_mm":native_mm(pz,meta["P24"]),
                           "api7_mm":native_mm(z,meta["API7"]),
                           "p24_z":float(pz),"api7_z":z,
                           "conditional_ratio_same_api7_reference":rr,
                           "ratio_ci_low":lo,"ratio_ci_high":hi,
                           "reference":"P24 train median at fixed API7"})
    return pd.DataFrame(results),pd.DataFrame(curves)

def fit_one(df,base,validation,scheme,block_cells,seed,reps,surface_points=25):
    st23,st25,st27,s29=get_stage_modules()
    daily,peak=s29.REPRESENTATIONS[scheme]
    w=df.copy()
    w["imerg_p24_utc_mm_mean"]=w[daily].to_numpy(dtype=float)
    w["imerg_p3h_max_mm_max"]=w[peak].to_numpy(dtype=float)
    train,test=st23.split_train_test(w,validation,seed,block_cells,base)
    if train.sum()<100 or test.sum()<100:
        raise RuntimeError("Not enough train/test weather observations")
    scaled,meta=st23.standardize(w,train)
    peak_resid,peak_meta=s29.residual_peak(w[daily].to_numpy(float),
                                           w[peak].to_numpy(float),train)
    design=design_features(scaled,peak_resid)
    y=w.episode_onset.to_numpy(float)
    ids=w.calendar_stratum_id.to_numpy()
    blocks=st25.block_ids(w,block_cells)
    models={};coef=[]
    for name,terms in MODELS.items():
        x=np.column_stack([design[t] for t in terms])
        tr=st25.sorted_groups(x,y,ids,blocks,train)
        te=st25.sorted_groups(x,y,ids,blocks,test)
        beta,se,cov,nblock,tloss,vloss,per=st25.infer_one(tr,te,terms,1e-4)
        models[name]={"beta":beta,"cov":cov,"train":tr,"test":te,
                      "per":per,"loss":vloss,"n_train_blocks":nblock}
        for term,b,s in zip(terms,beta,se):
            coef.append({"validation":validation,"rain_aggregation":scheme,
                         "model":name,"term":term,"beta":float(b),
                         "ci_low":float(b-1.96*s),"ci_high":float(b+1.96*s),
                         "train_spatial_blocks":nblock})
        print(f"[29b {validation} {scheme}] {name}: heldout_loss={vloss:.6f}",flush=True)
    gains=[]
    null=models["B0_no_interactions"]["test"]
    for old,new in COMPARISONS:
        A,B=models[old],models[new]
        if not np.array_equal(A["test"][7],B["test"][7]):
            raise RuntimeError("Model comparisons have different test risk strata")
        if not np.array_equal(A["test"][4],B["test"][4]):
            raise RuntimeError("Model comparisons have different test onset counts")
        g=st27.paired_gain(A["per"],B["per"],A["test"][6],
                          A["test"][4],reps=reps,seed=seed+89)
        gains.append({"validation":validation,"rain_aggregation":scheme,
                      "baseline":old,"augmented":new,
                      "baseline_loss":A["loss"],"augmented_loss":B["loss"],
                      **g})
    full=models["B3_both_interactions"]
    surface,curve=make_surface(full["beta"],full["cov"],MODELS["B3_both_interactions"],
                               design,train,meta,validation,scheme,n=surface_points)
    manifest={"validation":validation,"rain_aggregation":scheme,
              "n_train_strata":int(len(full["train"][4])),
              "n_test_strata":int(len(full["test"][4])),
              "test_onsets":int(full["test"][4].sum()),
              "n_test_spatial_blocks":int(np.unique(full["test"][6]).size),
              "weather_transform":meta,"peak_residual":peak_meta,
              "surface_support_note":"train observations per P24 x API7 histogram bin; not independent storms",
              "curve_note":"conditional intensity ratio relative to training P24 median at fixed API7; C3=SM1=0"}
    return pd.DataFrame(coef),pd.DataFrame(gains),surface,curve,manifest

def plot_surface(surface,out):
    """Static result chart from computed table; no synthetic values or interpolation."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for (validation,scheme),t in surface.groupby(["validation","rain_aggregation"]):
        good=t.copy()
        good.loc[~good.within_empirical_support,"relative_conditional_ratio"]=np.nan
        n=int(t.p24_index.max()+1)
        m=int(t.api7_index.max()+1)
        im=good.pivot(index="api7_index",columns="p24_index",values="relative_conditional_ratio").to_numpy()
        xx=t.drop_duplicates("p24_index").sort_values("p24_index").p24_mm.to_numpy()
        yy=t.drop_duplicates("api7_index").sort_values("api7_index").api7_mm.to_numpy()
        if im.shape!=(m,n):raise RuntimeError("Response surface cannot be reshaped")
        fig,ax=plt.subplots(figsize=(7,5))
        mesh=ax.pcolormesh(xx,yy,im,shading="nearest")
        fig.colorbar(mesh,ax=ax,label="Relative conditional onset intensity")
        ax.set(xlabel="P24 (mm/day, 24h UTC)",ylabel="API7 (mm, antecedent index)",
               title=f"Stage29b {validation} / {scheme}: conditional surface")
        fig.tight_layout()
        fig.savefig(out/f"29b_surface_{validation}_{scheme}.png",dpi=180)
        plt.close(fig)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--panel-path",type=Path)
    p.add_argument("--aggregation",choices=("mean","max","both"),default="both")
    p.add_argument("--validation",choices=("temporal","spatial","city","all"),default="all")
    p.add_argument("--max-strata",type=int)
    p.add_argument("--block-cells",type=int,default=25)
    p.add_argument("--bootstrap-reps",type=int,default=399)
    p.add_argument("--surface-points",type=int,default=25)
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--no-plots",action="store_true")
    p.add_argument("--out",type=Path)
    a=p.parse_args()
    if a.bootstrap_reps<99 or a.block_cells<1 or not 7<=a.surface_points<=101:
        raise ValueError("Invalid inference or surface settings")
    st23,st25,st27,s29=get_stage_modules()
    source=a.panel_path or a.base/"standard_calendar_22_compact"/"22_standard_daily_with_climate.parquet"
    df,qc=st27.load_common(source,a.max_strata)
    validations=("temporal","spatial","city") if a.validation=="all" else (a.validation,)
    representations=("mean","max") if a.aggregation=="both" else (a.aggregation,)
    cs,gs,surfaces,curves,meta=[],[],[],[],[]
    for val in validations:
        for rep in representations:
            c,g,s,v,m=fit_one(df,a.base,val,rep,a.block_cells,a.seed,
                              a.bootstrap_reps,a.surface_points)
            cs.append(c);gs.append(g);surfaces.append(s);curves.append(v);meta.append(m)
    out=a.out or a.base/"stage32_interaction_ablation"
    out.mkdir(parents=True,exist_ok=True)
    suffix="_smoke"+str(a.max_strata) if a.max_strata else ""
    pd.concat(cs,ignore_index=True).to_csv(out/f"29b_coefficients{suffix}.csv",index=False)
    pd.concat(gs,ignore_index=True).to_csv(out/f"29b_ablation_gains{suffix}.csv",index=False)
    surf=pd.concat(surfaces,ignore_index=True)
    surf.to_csv(out/f"29b_conditional_surface{suffix}.csv",index=False)
    pd.concat(curves,ignore_index=True).to_csv(out/f"29b_conditional_curves{suffix}.csv",index=False)
    if not a.no_plots:
        plot_surface(surf,out)
    (out/f"29b_manifest{suffix}.json").write_text(json.dumps({
        "weather_sample":qc,"fits":meta,"source":str(source),
        "method":"conditional Poisson; nested interactions; same risk sets; spatial-block paired gain bootstrap",
        "scope":"reported flood-onset conditional associations, NOT flood probability, NOT causal",
        "uncertainty":"train spatial sandwich and test spatial block bootstrap; not storm-independent",
        "SMOKE":bool(a.max_strata)
    },ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",out,flush=True)

if __name__=="__main__":main()
