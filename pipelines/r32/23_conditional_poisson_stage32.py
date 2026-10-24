"""Results 3.2 candidate conditional-Poisson analysis.

This models relative associations between reported grid-level onsets and weather
within grid×year×month×weekday strata. Not a causal effect, flood probability,
or evidence of independent storms. Legacy 1:4 models are separate sensitivity.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import minimize

COLS = {
    "P24": "imerg_p24_utc_mm_mean",
    "API7": "era5_api7_mm_mean",
    "SM1": "era5_sm1_prevday_mean",
    "P3H": "imerg_p3h_max_mm_max",
}
MODELS = {
    "D0_daily": ["P24"],
    "D1_antecedent": ["P24", "API7"],
    "D2_plus_soil": ["P24", "API7", "SM1"],
    "D3_short_daily_API": ["P24", "P3H", "API7"],
    "D4_exploratory_curvature": ["P24", "API7", "SM1", "P24_sq", "API7_sq", "SM1_sq"],
}

def digest(value):
    return int.from_bytes(hashlib.blake2b(str(value).encode(), digest_size=8).digest(), "little")

def load_panel(base, max_strata, include_continuing, panel_path=None):
    source = Path(panel_path) if panel_path else base / "standard_calendar_22" / "22_standard_daily_with_climate.parquet"
    if not source.is_file():
        raise FileNotFoundError(source)
    con = duckdb.connect()
    cols = ["grid_id", "grid_x", "grid_y", "calendar_stratum_id", "year",
            "episode_onset", "reported_non_onset_day"] + list(COLS.values())
    available = {row[0] for row in con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(source)]).fetchall()}
    if set(cols) - available:
        raise ValueError(f"Stage22 columns missing: {sorted(set(cols)-available)}")
    names = ",".join('p."' + col + '"' for col in cols)
    if max_strata:
        con.execute("CREATE TEMP TABLE chosen AS SELECT calendar_stratum_id FROM read_parquet(?) "
                    "GROUP BY 1 ORDER BY hash(calendar_stratum_id) LIMIT ?",
                    [str(source), max_strata])
        data = con.execute(f"SELECT {names} FROM read_parquet(?) p JOIN chosen USING(calendar_stratum_id)",
                           [str(source)]).fetchdf()
    else:
        data = con.execute(f"SELECT {names} FROM read_parquet(?) p", [str(source)]).fetchdf()
    con.close()
    before = {"raw_days": len(data), "raw_strata": data.calendar_stratum_id.nunique(),
              "raw_onsets": int(data.episode_onset.sum())}
    for col in COLS.values():
        data[col] = pd.to_numeric(data[col], errors="coerce")
    if not include_continuing:
        data = data[data.reported_non_onset_day.eq(0)].copy()
    all_ok = np.ones(len(data), dtype=bool)
    for col in COLS.values():
        all_ok &= np.isfinite(data[col].to_numpy(dtype=float))
    for col in ("imerg_p24_utc_mm_mean", "imerg_p3h_max_mm_max", "era5_api7_mm_mean"):
        all_ok &= data[col].ge(0).to_numpy()
    all_ok &= data["era5_sm1_prevday_mean"].between(0, 1).to_numpy()
    data["_valid_exposure"] = all_ok
    all_complete = data.groupby("calendar_stratum_id", sort=False)["_valid_exposure"].transform("all")
    data = data[all_complete].copy()
    sizes = data.groupby("calendar_stratum_id", sort=False).agg(
        n=("episode_onset", "size"), onsets=("episode_onset", "sum"))
    ok_strata = sizes.index[(sizes.n >= 2) & (sizes.onsets >= 1) & (sizes.onsets < sizes.n)]
    data = data[data.calendar_stratum_id.isin(ok_strata)].drop(columns="_valid_exposure")
    data = data.sort_values(["calendar_stratum_id", "grid_id", "year"]).reset_index(drop=True)
    if data.empty:
        raise RuntimeError("No complete informative strata: inspect Stage22 QC and weather missingness")
    after = {"eligible_days": len(data), "eligible_strata": data.calendar_stratum_id.nunique(),
             "eligible_onsets": int(data.episode_onset.sum()),
             "lost_strata": before["raw_strata"]-data.calendar_stratum_id.nunique(),
             "ongoing_positive_days": "included_as_nononset_sensitivity" if include_continuing else "excluded_from_risk_set"}
    return data, before, after

def split_train_test(data, kind, seed, block_cells, base):
    if kind == "temporal":
        year = data.year.to_numpy(dtype=int)
        return year <= 2022, (year >= 2023) & (year <= 2025)
    if kind == "spatial":
        x = data.grid_x.to_numpy(dtype=int)//block_cells
        y = data.grid_y.to_numpy(dtype=int)//block_cells
        is_test = np.fromiter((digest(f"{a}_{b}_{seed}")%100 < 20 for a,b in zip(x,y)),
                              dtype=bool, count=len(data))
        return ~is_test, is_test
    if kind == "city":
        path = base/"mechanism_project"/"05_static_merged"/"grid_static_context_m3.parquet"
        if not path.exists():
            raise FileNotFoundError(path)
        con = duckdb.connect()
        cities = con.execute("SELECT grid_id,city_id FROM read_parquet(?)", [str(path)]).fetchdf()
        con.close()
        if cities.grid_id.duplicated().any():
            raise RuntimeError("One grid must have no more than one PRIMARY city")
        city = data[["grid_id"]].merge(cities, on="grid_id", how="left", validate="many_to_one").city_id
        known = city.notna().to_numpy()
        is_test = np.fromiter((digest(str(v)+"_"+str(seed))%100 < 20 if pd.notna(v) else False
                              for v in city), dtype=bool, count=len(city))
        return known & ~is_test, known & is_test
    raise ValueError(kind)

def standardize(data, train):
    x = {}
    transforms = {}
    for name,col in COLS.items():
        raw = data[col].to_numpy(dtype=np.float64)
        if name != "SM1":
            raw = np.log1p(raw)
        low, high = np.quantile(raw[train], [0.005, 0.995])
        cap = np.clip(raw, low, high)
        mean, sd = float(cap[train].mean()), float(cap[train].std())
        sd = sd if sd > 1e-10 else 1.0
        x[name] = (cap-mean)/sd
        transforms[name] = {"source":col,"transform":"raw" if name=="SM1" else "log1p",
                            "p005":float(low),"p995":float(high),"train_mean":mean,"train_sd":sd}
    for name in ("P24","API7","SM1"):
        key = name+"_sq"
        value = x[name]**2
        x[key] = value-value[train].mean()
    return x,transforms

def prepare_set(X, y, groups, mask):
    indices = np.flatnonzero(mask)
    indices = indices[np.argsort(groups[indices], kind="stable")]
    xx, yy, ids = X[indices], y[indices], groups[indices]
    if len(ids) == 0:
        raise RuntimeError("Empty split")
    starts = np.r_[0,np.flatnonzero(ids[1:]!=ids[:-1])+1]
    lengths = np.diff(np.r_[starts, len(ids)])
    events = np.add.reduceat(yy, starts)
    valid = (lengths >= 2) & (events > 0) & (events < lengths)
    if not valid.all():
        allowed = np.repeat(valid, lengths)
        xx, yy, ids = xx[allowed], yy[allowed], ids[allowed]
        if len(ids) == 0:
            raise RuntimeError("No informative strata after split")
        starts = np.r_[0,np.flatnonzero(ids[1:]!=ids[:-1])+1]
        lengths = np.diff(np.r_[starts,len(ids)])
        events = np.add.reduceat(yy, starts)
    return xx,yy,starts,lengths,events

def fit_one(data, scaled, train, test, name):
    cols = MODELS[name]
    X = np.column_stack([scaled[col] for col in cols]).astype(np.float64)
    y = data.episode_onset.to_numpy(dtype=np.float64)
    ids = data.calendar_stratum_id.to_numpy()
    train_set = prepare_set(X,y,ids,train)
    test_set = prepare_set(X,y,ids,test)
    xtr,ytr,st,nt,mt = train_set
    xte,yte,se,ne,me = test_set
    if len(mt)<100 or len(me)<20:
        raise RuntimeError(f"Too few informative strata: {name} train={len(mt)} test={len(me)}")

    def loglike(x, yy, starts, ns, events, beta, grad=False):
        eta = x @ beta
        vmax = np.maximum.reduceat(eta,starts)
        weights = np.exp(eta-np.repeat(vmax,ns))
        denom = np.add.reduceat(weights,starts)
        loss = (np.sum(events*(vmax+np.log(denom)))-np.dot(yy,eta))/events.sum()
        if not grad:
            return float(loss)
        probs = weights / np.repeat(denom, ns)
        xmean = np.add.reduceat(probs[:,None]*x,starts,axis=0)
        derivative = (np.sum(events[:,None]*xmean,axis=0)-x.T@yy)/events.sum()
        return float(loss),derivative

    def objective(beta):
        value, deriv = loglike(xtr,ytr,st,nt,mt,beta,grad=True)
        ridge = 1e-4
        return value + 0.5*ridge*np.dot(beta,beta), deriv + ridge*beta

    result = minimize(objective, np.zeros(len(cols)), method="L-BFGS-B", jac=True,
                      options={"maxiter":250,"ftol":1e-11})
    if not result.success:
        raise RuntimeError(f"Optimization did not converge {name}: {result.message}")
    train_loss = loglike(xtr,ytr,st,nt,mt,result.x)
    test_loss = loglike(xte,yte,se,ne,me,result.x)
    eta = xte@result.x
    best = np.array([b+int(np.argmax(eta[b:b+n])) for b,n in zip(se,ne)],dtype=int)
    baseline = float(np.mean(me/ne))
    accuracy = float(np.mean(yte[best]>0))
    row={"model":name,"n_terms":len(cols),"train_strata":len(mt),
         "test_strata":len(me),"train_onsets":int(mt.sum()),"test_onsets":int(me.sum()),
         "train_conditional_logloss":train_loss,"test_conditional_logloss":test_loss,
         "test_top1_onset":accuracy,"within_stratum_baseline":baseline,
         "converged":True}
    coeff=[{"model":name,"feature":c,"standardized_beta":float(beta),
            "conditional_OR":float(np.exp(np.clip(beta,-30,30)))}
           for c,beta in zip(cols,result.x)]
    return row,coeff

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--validation",choices=("temporal","spatial","city","all"),default="all")
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--block-cells",type=int,default=25)
    p.add_argument("--max-strata",type=int,default=None)
    p.add_argument("--panel-path",type=Path,default=None,help="Explicit climate-complete Stage22 panel (e.g. 22_compact)")
    p.add_argument("--include-continuing",action="store_true",
                   help="Sensitivity: count continuing reported flood days as non-onset")
    a=p.parse_args()
    if a.block_cells<1:
        raise ValueError("--block-cells must be >=1")
    data,before,after=load_panel(a.base,a.max_strata,a.include_continuing,a.panel_path)
    out=a.base/"stage32_results"
    out.mkdir(parents=True,exist_ok=True)
    suffix=""
    if a.include_continuing:
        suffix+="_sensitivity_continuing"
    if a.max_strata:
        suffix+=f"_smoke{a.max_strata}"
    (out/f"23_eligibility_qc{suffix}.json").write_text(
        json.dumps({"before":before,"after":after},indent=2),encoding="utf-8")
    data.groupby("year").agg(
        eligible_days=("episode_onset","size"),onsets=("episode_onset","sum"),
        strata=("calendar_stratum_id","nunique")).reset_index().to_csv(
            out/f"23_eligibility_by_year{suffix}.csv",index=False)
    all_modes=("temporal","spatial","city") if a.validation=="all" else (a.validation,)
    table,coefficients,transforms=[],[],{}
    for validation in all_modes:
        train,test=split_train_test(data,validation,a.seed,a.block_cells,a.base)
        if not train.any() or not test.any():
            raise RuntimeError(f"No train/test observations: {validation}")
        splits=data[["calendar_stratum_id"]].assign(train=train,test=test)
        mixed=splits.groupby("calendar_stratum_id")[["train","test"]].any()
        if (mixed.train & mixed.test).any():
            raise RuntimeError("Train/test leakage: same time stratum in both")
        scaled,settings=standardize(data,train)
        transforms[validation]=settings
        for name in MODELS:
            metrics,coefs=fit_one(data,scaled,train,test,name)
            metrics["validation"]=validation
            table.append(metrics)
            for row in coefs:
                row["validation"]=validation
            coefficients.extend(coefs)
            print(f"[{validation}] {name} loss={metrics['test_conditional_logloss']:.6f} top1={metrics['test_top1_onset']:.3f}",flush=True)
    pd.DataFrame(table).to_csv(out/f"23_model_comparison{suffix}.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame(coefficients).to_csv(out/f"23_coefficients_exploratory{suffix}.csv",index=False,encoding="utf-8-sig")
    (out/f"23_feature_transforms{suffix}.json").write_text(
        json.dumps(transforms,indent=2,ensure_ascii=False),encoding="utf-8")
    print("[DONE] Exploratory relative event/report association only. No absolute flood probability or causal interpretation.")

if __name__=="__main__":
    main()
