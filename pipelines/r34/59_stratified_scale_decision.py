"""Stage59: stratified city pilot and scale-decision audit (offline).

Climate region is from supplied Koeppen column; location-quality strata MUST
come from independent human-reviewed geolocation or original event geometry.
Never infer precision from the existing 20km onset label. When precision
evidence is absent, emit NEEDS_LOCATION_VALIDATION and no winning scale.
"""
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd

def select_cities(df,n,seed=2026):
    need={"city_id","climate_zone","location_quality"}
    if not need<=set(df):raise ValueError("Required fields: "+str(need-set(df)))
    if df.city_id.duplicated().any():raise ValueError("Duplicate city ids")
    if df[list(need)].isna().any().any():raise ValueError("Missing climate or location review tier")
    if not df.location_quality.isin(["precise","approximate","coarse","unreviewed"]).all():
        raise ValueError("location_quality must be precise/approximate/coarse/unreviewed")
    # deterministic stratified round-robin; preserves sparse categories
    rng=np.random.default_rng(seed)
    groups=[g.sample(frac=1,random_state=int(rng.integers(2**31-1)))
        for _,g in df.groupby(["climate_zone","location_quality"],sort=True)]
    picked=[]
    while groups and len(picked)<n:
        nxt=[]
        for g in groups:
            if len(picked)>=n:break
            picked.append(g.iloc[0].to_dict())
            if len(g)>1:nxt.append(g.iloc[1:])
        groups=nxt
    return pd.DataFrame(picked)

def evaluate(boundary,validation=None):
    pivot=boundary.pivot_table(index="city_id",columns="scale_km",
                               values=["city_built_fraction","city_built_sd","coverage_fraction"])
    eligible=set(pivot.index)
    complete=(pivot.notna().all(axis=1))
    result={"n_cities":len(pivot),"complete_multiscale_cities":int(complete.sum()),
            "source":"5km built cache; 10/20km sum; original observations remain 20km",
            "scale_winner":None,"status":"ENVIRONMENT_ONLY_NO_SCALE_SELECTION"}
    if (5 in boundary.scale_km.values and 20 in boundary.scale_km.values):
        a=boundary.pivot(index="city_id",columns="scale_km",values="city_built_fraction")
        if 5 in a and 20 in a:
            delta=(a[5]-a[20]).abs().dropna()
            result["p95_abs_city_built_diff_5vs20"]=float(delta.quantile(.95)) if len(delta) else None
    if validation is None:
        result["blocked_scale_validation"]="NOT_AVAILABLE: requires fresh 5km/10km onset/reference construction from original polygons"
        result["location_precision_validation"]="REQUIRES INDEPENDENT REVIEW/RAW_GEOMETRY"
        return result
    v=validation.copy()
    required={"scale_km","city_id","climate_zone","location_quality",
              "matched_event_id","heldout_logloss"}
    if not required<=set(v):raise ValueError("Validation missing "+str(required-set(v)))
    if not {5,10,20}<=set(v.scale_km.unique()):
        raise ValueError("All three genuinely re-estimated scales required")
    if v.location_quality.eq("unreviewed").any():
        result["status"]="INSUFFICIENT_LOCATION_REVIEW";return result
    # Scores must refer to the same events across all three scales; not interpolated labels
    key=["matched_event_id","city_id"]
    wide=v.pivot_table(index=key,columns="scale_km",values="heldout_logloss",
                       aggfunc="first")
    common=wide.dropna(subset=[5,10,20])
    result["paired_common_events"]=int(len(common))
    if len(common)<100:
        result["status"]="INSUFFICIENT_PAIRED_EVENTS";return result
    result["scale_mean_loss"]={str(s):float(common[s].mean()) for s in (5,10,20)}
    # Descriptive only: strong scale recommendation also needs storm-block CIs,
    # MAUP offset and out-of-domain transfer QC.
    result["status"]="DESCRIPTIVE_SCALE_COMPARISON_ONLY"
    result["scale_winner"]=None
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--candidate-cities",type=Path,required=True)
    p.add_argument("--boundary-parquet",type=Path)
    p.add_argument("--validated-scale-losses",type=Path)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--n",type=int,default=60)
    p.add_argument("--seed",type=int,default=2026)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    c=pd.read_parquet(a.candidate_cities) if a.candidate_cities.suffix==".parquet" else pd.read_csv(a.candidate_cities)
    selected=select_cities(c,a.n,a.seed)
    selected.to_csv(a.out/"59_balanced_city_pilot.csv",index=False)
    print("[SELECTED]",len(selected),"city records")
    if a.boundary_parquet:
        boundary=pd.read_parquet(a.boundary_parquet)
        boundary=boundary.loc[boundary.city_id.astype(str).isin(selected.city_id.astype(str))]
        val=(pd.read_parquet(a.validated_scale_losses) if a.validated_scale_losses.suffix==".parquet"
             else pd.read_csv(a.validated_scale_losses)) if a.validated_scale_losses else None
        qc=evaluate(boundary,val)
        (a.out/"59_scale_decision.json").write_text(json.dumps(qc,indent=2),encoding="utf-8")
        print("[DECISION]",qc)
if __name__=="__main__":main()
