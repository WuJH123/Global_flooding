"""Stage33: train-only city environment typology, NO flood outcomes in clusters.

Only UCDB cities with grid context data can be classified. Cities with no
Groundsource-reported grid are NOT represented by the M3-only link table.
Class labels are environmental types, not observed flood-risk ranks.
Train city holdout is deterministic, centroids learned on train-only city
attributes, and heldout cities are assigned without fitting to their outcomes.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd

def build_city_table(ghsl_city,static_grid,hydro_grid=None,epoch=2020):
    e=ghsl_city.loc[ghsl_city.epoch.eq(epoch)].copy()
    if e.city_id.duplicated().any():raise RuntimeError("GHSL duplicate city epoch")
    s=static_grid.copy()
    if s.grid_id.duplicated().any():raise RuntimeError("Duplicate static grid_id")
    if hydro_grid is not None:
        if hydro_grid.grid_id.duplicated().any():raise RuntimeError("Duplicate hydro grid_id")
        s=s.merge(hydro_grid,on="grid_id",how="left",validate="one_to_one")
    cols=[c for c in (
        "merit_local_relief_m_approx","basin_slope_degrees",
        "cru_pre_annual_mm","basin_urb_pc_sse",
        "dem_relief_p90_minus_p10_m","hand_lt2_fraction",
        "hand_lt5_fraction","hand_p10_m","nearest_mapped_river_km",
        "impervious_mean_fraction",
        "impervious_surface_weighted_hand_lt2_share"
    ) if c in s.columns]
    s=s.dropna(subset=["city_id"])
    r=s.groupby("city_id",observed=True)[cols].mean().reset_index()
    r["grid_count_primary"]=s.groupby("city_id").grid_id.nunique().to_numpy()
    combined=e.merge(r,on="city_id",how="left",validate="one_to_one")
    if combined.city_id.isna().any():
        raise RuntimeError("Missing city IDs")
    return combined

def types(data,features,seed=2026,k_min=2,k_max=6):
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score,adjusted_rand_score
    from sklearn.preprocessing import RobustScaler
    # Reuse train-city split EXACTLY with Stage32
    import importlib.util
    script=Path(__file__).with_name("32_mechanism_feature_selection.py")
    spec=importlib.util.spec_from_file_location("s32city",script)
    x=importlib.util.module_from_spec(spec);spec.loader.exec_module(x)
    train=~x.hash_test(data.city_id.astype(str),seed)
    if train.sum()<max(60,k_max*10):raise RuntimeError("Insufficient training cities")
    X=data[features].apply(pd.to_numeric,errors="coerce").to_numpy(dtype=float)
    X[~np.isfinite(X)]=np.nan
    med=np.nanmedian(X[train],axis=0)
    if not np.isfinite(med).all():raise RuntimeError("Training-only median not available")
    X=np.where(np.isnan(X),med[None,:],X)
    # Training-only impute/scale; heldout city features are transformed only
    transform=RobustScaler(quantile_range=(25,75)).fit(X[train])
    Y=transform.transform(X)
    scores=[]
    best=None
    for k in range(k_min,min(k_max,train.sum()-1)+1):
        km=KMeans(n_clusters=k,random_state=seed,n_init=15).fit(Y[train])
        silhouette=float(silhouette_score(Y[train],km.labels_,
                                         sample_size=min(1500,train.sum()),
                                         random_state=seed))
        scores.append({"k":k,"train_silhouette":silhouette,"inertia":float(km.inertia_)})
        if best is None or silhouette>best[0]:
            best=(silhouette,k,km)
    _,K,model=best
    labels=model.predict(Y)
    result=data[["city_id"]].copy()
    result["environment_type_id"]=["T"+str(i+1) for i in labels]
    result["typology_train_city"]=train
    result["typology_feature_incomplete"]=np.isnan(
        data[features].apply(pd.to_numeric,errors="coerce").to_numpy(dtype=float)).any(axis=1)
    if "grid_count_primary" in data:
        result["low_support_city"]=data.grid_count_primary.fillna(0).lt(2)
    else:result["low_support_city"]=True
    stats=data[["city_id"]+features].merge(result,on="city_id",validate="one_to_one")
    stats.to_numpy if False else None
    profile=stats.groupby("environment_type_id",observed=True)[features].median().reset_index()
    counts=stats.groupby("environment_type_id",observed=True).size().rename("cities").reset_index()
    profile=profile.merge(counts,on="environment_type_id")
    # Bootstrap training-city subsamples; classify the ORIGINAL full train group
    # under refit centroids and evaluate ARI (labels permutation invariant).
    rng=np.random.default_rng(seed+100)
    idx=np.flatnonzero(train)
    stability=[]
    if len(idx)>max(70,K*15):
        for iteration in range(15):
            part=rng.choice(idx,size=int(.8*len(idx)),replace=False)
            alternative=KMeans(n_clusters=K,random_state=seed+iteration+1,n_init=10).fit(Y[part])
            stability.append(float(adjusted_rand_score(model.labels_,
                              alternative.predict(Y[train]))))
    return result,profile,pd.DataFrame(scores),{
        "selected_k":K,"train_cities":int(train.sum()),"holdout_cities":int((~train).sum()),
        "median_imputation_train":{f:float(v) for f,v in zip(features,med)},
        "scaler_center_train":transform.center_.tolist(),
        "scaler_scale_train":transform.scale_.tolist(),
        "centroids_scaled":model.cluster_centers_.tolist(),
        "bootstrap_train_ARI_mean":float(np.mean(stability)) if stability else None,
        "bootstrap_train_ARI_min":float(np.min(stability)) if stability else None,
        "features":features,
        "warning":"Unsupervised environment groups learned without flood outcomes, only UCDB cities represented by M3+GHSL coverage."}

def read(path):
    return pd.read_parquet(path) if path.suffix.lower()==".parquet" else pd.read_csv(path,dtype={"city_id":str,"grid_id":str})

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ghsl-city",type=Path,required=True)
    p.add_argument("--static-grid",type=Path,required=True)
    p.add_argument("--hydro-grid",type=Path)
    p.add_argument("--selection-json",type=Path)
    p.add_argument("--epoch",type=int,default=2020)
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--k-min",type=int,default=2)
    p.add_argument("--k-max",type=int,default=6)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    city=build_city_table(read(a.ghsl_city),read(a.static_grid),
                          read(a.hydro_grid) if a.hydro_grid else None,a.epoch)
    if a.selection_json:
        features=list(json.loads(a.selection_json.read_text(encoding="utf-8"))["selected"])
    else:
        features=[x for x in ("ghsl_built_fraction_wmean","merit_local_relief_m_approx",
                             "basin_slope_degrees","cru_pre_annual_mm") if x in city]
    if len(features)<2:
        raise RuntimeError("At least 2 environment features required; check stage32")
    not_found=set(features)-set(city)
    if not_found:raise RuntimeError("Missing selected city features: "+str(not_found))
    assign,profile,score,manifest=types(city,features,a.seed,a.k_min,a.k_max)
    a.out.mkdir(parents=True,exist_ok=True)
    city.merge(assign,on="city_id",validate="one_to_one").to_parquet(
        a.out/"33_city_environment_features_and_types.parquet",index=False)
    assign.to_csv(a.out/"33_city_environment_types.csv",index=False)
    profile.to_csv(a.out/"33_type_median_profiles.csv",index=False)
    score.to_csv(a.out/"33_k_selection.csv",index=False)
    (a.out/"33_typology_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print("[DONE]",manifest["selected_k"],"types",len(assign),"cities",a.out)

if __name__=="__main__":main()
