"""Results 3.2 / Stage25: prespecified conditional-Poisson interactions.

Uses EXACT Stage23 complete grid×year×month×weekday risk sets and TRAIN-only
feature transforms. Fits conditional likelihood (not a probability of flood).
Provides spatial-block sandwich CIs on TRAIN coefficients and cluster-bootstrap
CIs for HELD-OUT per-onset log-loss gains. Neither proves causality nor
accounts for a single storm spanning several spatial blocks.

No GEE calls; original Stage22/23 artifacts are never overwritten.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

BASE_MODELS = {
    "I0_additive": ("P24","API7","SM1","P3H"),
    "I1_P24xAPI7": ("P24","API7","SM1","P3H","P24_x_API7"),
    "I2_P24xSM1": ("P24","API7","SM1","P3H","P24_x_SM1"),
    "I3_both_interactions": ("P24","API7","SM1","P3H","P24_x_API7","P24_x_SM1"),
    "I4_three_interactions": ("P24","API7","SM1","P3H","P24_x_API7","P24_x_SM1","P3H_x_API7"),
}
RANGE = ("temporal","spatial","city")


def get_stage23():
    path=Path(__file__).with_name("23_conditional_poisson_stage32.py")
    spec=importlib.util.spec_from_file_location("stage23_reference_in25",path)
    module=importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def design(scaled):
    out={name:np.asarray(scaled[name],dtype=np.float64) for name in ("P24","API7","SM1","P3H")}
    out["P24_x_API7"]=out["P24"]*out["API7"]
    out["P24_x_SM1"]=out["P24"]*out["SM1"]
    out["P3H_x_API7"]=out["P3H"]*out["API7"]
    return out


def sorted_groups(X,y,ids,blocks,mask):
    """One (grid,time) stratum must be wholly in or out of a split."""
    where=np.flatnonzero(mask)
    where=where[np.argsort(ids[where],kind="stable")]
    xx=np.asarray(X[where],dtype=np.float64)
    yy=np.asarray(y[where],dtype=np.float64)
    key=np.asarray(ids[where])
    blk=np.asarray(blocks[where])
    if len(xx)==0:
        raise ValueError("Empty train/test observations")
    starts=np.r_[0,np.flatnonzero(key[1:]!=key[:-1])+1]
    sizes=np.diff(np.r_[starts,len(key)])
    m=np.add.reduceat(yy,starts)
    keep=(sizes>=2)&(m>0)&(m<sizes)
    if not keep.all():
        mask2=np.repeat(keep,sizes)
        return sorted_groups(xx,yy,key,blk,mask2)
    if np.any(m<1):
        raise ValueError("Uninformative stratum survived")
    # A spatial block must not vary inside one grid/time stratum.
    if not np.array_equal(blk[starts],np.maximum.reduceat(blk,starts)):
        raise RuntimeError("One risk stratum spans multiple spatial clusters")
    return (xx,yy,starts,sizes,m,blk,blk[starts],key[starts])


def calc(beta,arrays,with_grad=False):
    X,y,st,n,m,_,_,_=arrays
    eta=X@beta
    top=np.maximum.reduceat(eta,st)
    shifted=np.exp(eta-np.repeat(top,n))
    denom=np.add.reduceat(shifted,st)
    probs=shifted/np.repeat(denom,n)
    losses=m*(top+np.log(denom))-np.add.reduceat(y*eta,st)
    loss=float(losses.sum())
    if not with_grad:
        return loss,losses,probs
    pred=np.repeat(m,n)*probs
    grad=X.T@(pred-y)
    return loss,grad


def fit(arrays,ridge=1e-4,maxiter=250):
    npar=arrays[0].shape[1]
    total=float(np.sum(arrays[4]))
    def objective(beta):
        loss,g=calc(beta,arrays,True)
        return loss/total+0.5*ridge*(beta@beta),g/total+ridge*beta
    res=minimize(objective,np.zeros(npar),jac=True,method="L-BFGS-B",
                 options={"maxiter":maxiter,"ftol":1e-11})
    if not res.success:
        raise RuntimeError("Conditional optimizer failed: "+str(res.message))
    return res.x


def spatial_sandwich(arrays,beta,ridge=1e-4):
    """Spatial-cluster sandwich with analytic conditional Hessian.

    Cluster is 25x25 grid block by default. Does not claim inter-block storm
    independence. Ridge included in penalized bread, not in cluster score.
    """
    X,y,st,n,m,blocks,_,_=arrays
    loss,per,prob=calc(beta,arrays,False)
    mu=np.repeat(m,n)*prob
    score_rows=(y-mu)[:,None]*X
    cluster_ids, inverse=np.unique(blocks,return_inverse=True)
    g=len(cluster_ids)
    if g<15:
        raise RuntimeError("Too few independent spatial blocks for sandwich CI (<15)")
    scores=np.zeros((g,X.shape[1]),dtype=np.float64)
    np.add.at(scores,inverse,score_rows)
    meat=scores.T@scores
    xbar=np.add.reduceat(X*prob[:,None],st,axis=0)
    center=X-np.repeat(xbar,n,axis=0)
    hessian=center.T@(center*mu[:,None])
    hessian+=np.eye(X.shape[1])*ridge*np.sum(m)
    bread=np.linalg.pinv(hessian,rcond=1e-12)
    cov=bread@meat@bread * g/(g-1)
    cov=(cov+cov.T)/2
    stderr=np.sqrt(np.maximum(0,np.diag(cov)))
    return stderr,cov,g


def bootstrap_loss_delta(a,b,groups,reps=399,seed=2026):
    """Resample held-out spatial blocks, never individual grid-days.

    a and b are UNNORMALIZED negative loglikelihood PER STRATUM from
    same held-out strata, so sign a-b>0 favors model b.
    This bootstrap conditions on trained models; it is NOT a fit-resampling CI.
    """
    if not (len(a)==len(b)==len(groups)):
        raise ValueError("Paired held-out strata length mismatch")
    unique,inv=np.unique(groups,return_inverse=True)
    if len(unique)<15:
        raise RuntimeError("Too few test spatial blocks for loss bootstrap")
    numerator=np.bincount(inv,weights=a-b,minlength=len(unique))
    den=np.bincount(inv,weights=np.ones(len(inv)),minlength=len(unique))
    obs=float(numerator.sum()/den.sum())
    rng=np.random.default_rng(seed)
    results=np.empty(reps)
    for i in range(reps):
        drawn=rng.integers(0,len(unique),size=len(unique))
        results[i]=numerator[drawn].sum()/den[drawn].sum()
    return obs,float(np.percentile(results,2.5)),float(np.percentile(results,97.5)),len(unique)


def block_ids(data,block_cells):
    gx=data.grid_x.to_numpy(dtype=np.int64)//block_cells
    gy=data.grid_y.to_numpy(dtype=np.int64)//block_cells
    codes,_=pd.factorize(pd.MultiIndex.from_arrays([gx,gy]),sort=False)
    return codes.astype(np.int32)


def infer_one(train_arr,test_arr,terms,ridge):
    beta=fit(train_arr,ridge)
    se,cov,nblocks=spatial_sandwich(train_arr,beta,ridge)
    loss_train=float(calc(beta,train_arr)[0]/train_arr[4].sum())
    loss_test,per,_=calc(beta,test_arr)
    # Multi-onset strata require event count weighting in heldout bootstrap.
    return beta,se,cov,nblocks,loss_train,float(loss_test/test_arr[4].sum()),per


def analyze(data,base,validation,block_cells,seed,bootstrap_reps,
            ridge=1e-4):
    stage=get_stage23()
    train,test=stage.split_train_test(data,validation,seed,block_cells,base)
    if train.sum()==0 or test.sum()==0:
        raise RuntimeError(f"Empty split: {validation}")
    # Keep train transforms from training only; identical Stage23 workflow.
    scaled,meta=stage.standardize(data,train)
    features=design(scaled)
    ids=data.calendar_stratum_id.to_numpy()
    y=data.episode_onset.to_numpy(dtype=np.float64)
    spatial=block_ids(data,block_cells)
    permodel,coefficients={},[]
    for name,terms in BASE_MODELS.items():
        X=np.column_stack([features[c] for c in terms])
        tr=sorted_groups(X,y,ids,spatial,train)
        te=sorted_groups(X,y,ids,spatial,test)
        beta,se,cov,ng,loss_tr,loss_te,per=infer_one(tr,te,terms,ridge)
        permodel[name]={"train":tr,"test":te,"coef":beta,"se":se,
                        "train_loss":loss_tr,"test_loss":loss_te,"cov":cov,
                        "per_stratum":per,"n_spatial_clusters":ng}
        for i,term in enumerate(terms):
            value=float(beta[i])
            error=float(se[i])
            coefficients.append({
                "validation":validation,"model":name,"term":term,
                "beta":value,"cluster_se":error,
                "beta_ci_low":value-1.96*error,
                "beta_ci_high":value+1.96*error,
                "conditional_ratio_at_1sd":float(np.exp(np.clip(value,-25,25))),
                "ratio_ci_low":float(np.exp(np.clip(value-1.96*error,-25,25))),
                "ratio_ci_high":float(np.exp(np.clip(value+1.96*error,-25,25))),
                "n_train_spatial_blocks":ng,
                "CI_note":"Training spatial-block sandwich; blocks not proven independent storms",
            })
        print(f"[{validation}] {name} test_loss={loss_te:.7f} training_blocks={ng}",flush=True)
    # Physically interpretable marginal contrast: the conditional ratio for
    # +1 TRAIN-standardized P24 at different fixed API7 levels; SM1/P3H set 0.
    # No claim of absolute flood probability, and clipping makes this a local
    # standardized association contrast rather than raw mm increments.
    response=[]
    for modelname in ("I1_P24xAPI7","I3_both_interactions"):
        r=permodel[modelname]
        terms=BASE_MODELS[modelname]
        idxp=terms.index("P24")
        idxint=terms.index("P24_x_API7")
        for api_z in (-1.0,0.0,1.0):
            gradient=np.zeros(len(terms))
            gradient[idxp]=1.0
            gradient[idxint]=api_z
            beta=float(gradient@r["coef"])
            stderr=float(np.sqrt(max(0.,gradient@r["cov"]@gradient)))
            response.append({
                "validation":validation,"model":modelname,
                "api7_standardized_level":api_z,
                "p24_standardized_change":1.0,
                "log_conditional_ratio":beta,
                "conditional_ratio":float(np.exp(np.clip(beta,-25,25))),
                "ratio_ci_low":float(np.exp(np.clip(beta-1.96*stderr,-25,25))),
                "ratio_ci_high":float(np.exp(np.clip(beta+1.96*stderr,-25,25))),
                "contrast_definition":"P24 +1 SD at fixed API7 z; other weather z=0",
            })
    comparisons=[]
    names=list(BASE_MODELS)
    for old,new in zip([names[0]]*4,names[1:]):
        A=permodel[old];B=permodel[new]
        if not np.array_equal(A["test"][7],B["test"][7]):
            raise RuntimeError("Paired test strata differ across candidate models")
        if not np.array_equal(A["test"][4],B["test"][4]):
            raise RuntimeError("Paired test onset counts differ across candidate models")
        blocks=A["test"][6]
        # For multi-onset strata the bootstrap denominator must be total onsets.
        _, inv=np.unique(blocks,return_inverse=True)
        uniq=np.unique(blocks)
        if len(uniq)<15:
            raise RuntimeError("Too few heldout test clusters")
        gain=(A["per_stratum"]-B["per_stratum"])
        nums=np.bincount(inv,weights=gain,minlength=len(uniq))
        weights=np.bincount(inv,weights=A["test"][4],minlength=len(uniq))
        rng=np.random.default_rng(seed+57)
        boot=np.zeros(bootstrap_reps)
        for j in range(bootstrap_reps):
            take=rng.integers(0,len(uniq),len(uniq))
            boot[j]=nums[take].sum()/weights[take].sum()
        estimate=float(nums.sum()/weights.sum())
        low,high=np.percentile(boot,[2.5,97.5])
        comparisons.append({
            "validation":validation,"baseline":old,"augmented":new,
            "test_loss_baseline":A["test_loss"],
            "test_loss_augmented":B["test_loss"],
            "heldout_loss_gain":estimate,
            "gain_CI_low":float(low),"gain_CI_high":float(high),
            "test_onsets":int(A["test"][4].sum()),
            "test_strata":len(blocks),"bootstrap_spatial_blocks":len(uniq),
            "reps":bootstrap_reps,
            "CI_note":"Test-block bootstrap conditional on trained fits; NOT refit bootstrap"
        })
    return pd.DataFrame(coefficients),pd.DataFrame(comparisons),pd.DataFrame(response),meta


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--panel-path",type=Path,default=None)
    p.add_argument("--validation",choices=("temporal","spatial","city","all"),default="all")
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--block-cells",type=int,default=25)
    p.add_argument("--bootstrap-reps",type=int,default=399)
    p.add_argument("--max-strata",type=int,default=None)
    p.add_argument("--ridge",type=float,default=1e-4)
    p.add_argument("--out",type=Path,default=None)
    args=p.parse_args()
    if args.bootstrap_reps<99 or args.block_cells<1 or args.ridge<0:
        raise ValueError("At least 99 bootstrap replicates, block-cells >=1, ridge >=0")
    stage=get_stage23()
    panel=args.panel_path or args.base/"standard_calendar_22_compact"/"22_standard_daily_with_climate.parquet"
    data,before,after=stage.load_panel(args.base,args.max_strata,False,panel)
    if not args.max_strata:
        if tuple(after[k] for k in ("eligible_days","eligible_strata","eligible_onsets"))!=(2543887,600723,641587):
            raise RuntimeError("Unexpected Stage23 eligible sample. Audit before inference.")
    out=args.out or args.base/"stage32_interactions"
    out.mkdir(parents=True,exist_ok=True)
    tables,coefs,responses,transform={},[],[],{}
    for validation in (RANGE if args.validation=="all" else (args.validation,)):
        beta,gain,response,params=analyze(data,args.base,validation,args.block_cells,args.seed,
                                          args.bootstrap_reps,args.ridge)
        coefs.append(beta)
        responses.append(response)
        tables[validation]=gain
        transform[validation]=params
    prefix="_smoke"+str(args.max_strata) if args.max_strata else ""
    pd.concat(coefs,ignore_index=True).to_csv(out/f"25_interaction_cluster_ci{prefix}.csv",index=False)
    pd.concat(tables.values(),ignore_index=True).to_csv(out/f"25_heldout_paired_gain_ci{prefix}.csv",index=False)
    pd.concat(responses,ignore_index=True).to_csv(out/f"25_conditional_response_contrasts{prefix}.csv",index=False)
    (out/f"25_transform_manifest{prefix}.json").write_text(
        json.dumps({"model_terms":BASE_MODELS,"scaling":transform,"eligibility":after,
                    "ridge":args.ridge,"block_cells":args.block_cells,
                    "bootstrap_reps":args.bootstrap_reps,
                    "interpretation":"Association with flood news onset only; spatial blocks do not isolate independent storms"},
                    ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",out,"NO causal or absolute flood risk claim",flush=True)


if __name__=="__main__":
    main()
