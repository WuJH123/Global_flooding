"""Stage32: physically grouped city feature selection WITHOUT outcomes.

Uses one row per UCDB city, pre-specified candidate groups, train-city-only
correlations/VIF/transform estimation. Population is tagged REPORTING context
and EXCLUDED from default mechanism selection, not silently called hydrology.
Missing values are never fitted from test cities.
"""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
import numpy as np
import pandas as pd

GROUPS={
    "surface":[
        "ghsl_built_fraction_wmean","ghsl_urban_area_fraction_wmean",
        "ghsl_dense_area_fraction_wmean","impervious_mean_fraction"],
    "hydro_lowland":[
        "impervious_surface_weighted_hand_lt2_share","hand_lt2_fraction",
        "hand_lt5_fraction","hand_p10_m","nearest_mapped_river_km"],
    "terrain":[
        "dem_relief_p90_minus_p10_m","merit_local_relief_m_approx",
        "basin_slope_degrees"],
    "climate":["cru_pre_annual_mm"],
    "reporting_only":["ghsl_pop_density_wmean","ghsl_pop_total_wmean",
                      "ghsl_urban_pop_share_wmean"],
}
PREFERRED=("impervious_surface_weighted_hand_lt2_share",
           "ghsl_built_fraction_wmean","impervious_mean_fraction",
           "hand_lt2_fraction","nearest_mapped_river_km",
           "dem_relief_p90_minus_p10_m","merit_local_relief_m_approx",
           "basin_slope_degrees","cru_pre_annual_mm")

def hash_test(ids,seed=2026,frac=.2):
    return np.array([int.from_bytes(hashlib.blake2b(
        (str(v)+"_"+str(seed)).encode(),digest_size=8).digest(),"little")%1000
        <int(frac*1000) for v in ids],bool)

def spearman(a,b):
    x=pd.Series(a).rank().to_numpy(dtype=float)
    y=pd.Series(b).rank().to_numpy(dtype=float)
    return float(np.corrcoef(x,y)[0,1])

def vif(x):
    x=np.asarray(x,float)
    if len(x)<max(40,3*x.shape[1]):raise ValueError("Too few train cities for VIF")
    z=(x-x.mean(0))/np.maximum(x.std(0),1e-12)
    out=[]
    for j in range(z.shape[1]):
        response=z[:,j]
        pred=np.delete(z,j,axis=1)
        if pred.shape[1]==0:out.append(1.);continue
        coef=np.linalg.lstsq(pred,response,rcond=None)[0]
        r2=1-np.sum((response-pred@coef)**2)/max(1e-12,np.sum(response**2))
        out.append(1/max(1e-6,1-r2))
    return np.array(out)

def select_city_features(df,seed=2026,max_missing=.2,corr_cut=.75,vif_max=5.,
                         include_reporting=False):
    if df.city_id.isna().any() or df.city_id.duplicated().any():
        raise ValueError("One row per unique nonnull UCDB city is required")
    mask=~hash_test(df.city_id,seed)
    if mask.sum()<50:raise RuntimeError("Insufficient training cities")
    train=df.loc[mask]
    keys=[]
    group_by={}
    priority={name:i for i,name in enumerate(PREFERRED)}
    coverage=[]
    for grp,columns in GROUPS.items():
        if grp=="reporting_only" and not include_reporting:continue
        for col in columns:
            if col not in df:continue
            v=pd.to_numeric(train[col],errors="coerce")
            miss=float((~np.isfinite(v.to_numpy(float))).mean())
            distinct=v.nunique(dropna=True)
            coverage.append({"feature":col,"group":grp,"train_missing_fraction":miss,
                             "distinct_train":int(distinct)})
            if miss<=max_missing and distinct>5:
                keys.append(col);group_by[col]=grp
    if not keys:raise RuntimeError("No usable environmental variables in input city table")
    # choose within each physical group the higher-priority feature where |rho|>.75
    dropped=[]
    chosen=[]
    ordered=sorted(keys,key=lambda x:(priority.get(x,100+keys.index(x)),x))
    for col in ordered:
        colvalues=pd.to_numeric(train[col],errors="coerce")
        redundant=None
        for previous in chosen:
            oth=pd.to_numeric(train[previous],errors="coerce")
            ok=np.isfinite(colvalues)&np.isfinite(oth)
            if ok.sum()>40:
                corr=spearman(colvalues[ok],oth[ok])
                cutoff=corr_cut if group_by[previous]==group_by[col] else .90
                if np.isfinite(corr) and abs(corr)>cutoff:
                    redundant=(previous,float(corr))
                    break
        if redundant:
            dropped.append({"feature":col,"reason":"spearman_redundant","with":redundant[0],
                            "rho":redundant[1]})
        else:chosen.append(col)
    def matrix(keys):
        v=train[keys].apply(pd.to_numeric,errors="coerce")
        # complete-case only for VIF; never fill test observations
        return v.loc[np.isfinite(v.to_numpy(float)).all(axis=1)].to_numpy(dtype=float)
    while len(chosen)>1:
        arr=matrix(chosen)
        if len(arr)<max(50,5*len(chosen)):
            raise RuntimeError("VIF complete-case training cities insufficient; "
                               "reduce missingness or choose high-coverage features")
        vals=vif(arr)
        if vals.max()<=vif_max:break
        index=int(np.argmax(vals))
        eliminated=chosen.pop(index)
        dropped.append({"feature":eliminated,"reason":"train_vif_exceeded",
                        "vif":float(vals[index])})
    selected={name:group_by[name] for name in chosen}
    return {"selected":selected,"dropped":dropped,"coverage":coverage,
            "train_cities":int(mask.sum()),"test_cities":int((~mask).sum()),
            "train_only":True,"seed":seed,"corr_cut":corr_cut,
            "vif_max":vif_max,"include_reporting":include_reporting,
            "preprocessing":"Natural-scale ranks for Spearman; complete-case raw numeric values for VIF, later model uses train-only transformations."}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--city-table",type=Path,required=True)
    p.add_argument("--epoch",type=int,default=2020)
    p.add_argument("--include-reporting",action="store_true")
    p.add_argument("--max-missing",type=float,default=.20)
    p.add_argument("--corr-cut",type=float,default=.75)
    p.add_argument("--vif-max",type=float,default=5.)
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    t=pd.read_parquet(a.city_table) if a.city_table.suffix.lower()==".parquet" else pd.read_csv(a.city_table,dtype={"city_id":str})
    if "epoch" in t:
        t=t.loc[t.epoch.eq(a.epoch)].copy()
    t["city_id"]=t.city_id.astype(str)
    result=select_city_features(t,a.seed,a.max_missing,a.corr_cut,a.vif_max,a.include_reporting)
    a.out.mkdir(parents=True,exist_ok=True)
    (a.out/"32_feature_selection.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    pd.DataFrame(result["coverage"]).to_csv(a.out/"32_candidate_coverage.csv",index=False)
    pd.DataFrame(result["dropped"]).to_csv(a.out/"32_dropped_features.csv",index=False)
    print("[DONE]",result["selected"],"cities",len(t))

if __name__=="__main__":main()
