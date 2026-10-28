# -*- coding: utf-8 -*-
"""Stage67 Results 3.4/3.5: observed hazard-weather shifts + exposed population.

NOT attribution of flooding, NOT inferred flood probability. Frozen 10km
grid only; compare complete early/late ERA5 years, use 2020 GHSL pop as
a FIXED exposure baseline, optionally attach normalized CMIP6 ensembles
only after same-model historical and future exports are available.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd

def analyze(annual_root,pop,out,early=(2000,2010),late=(2016,2025),
            min_years=8,threshold_q=.90):
    files=sorted(annual_root.glob("64b_era5_Y*_grid10.parquet"))
    if not files:raise FileNotFoundError("No ERA5 annual GF10 Parquet in "+str(annual_root))
    data=pd.concat([pd.read_parquet(p) for p in files],ignore_index=True)
    if data.duplicated(["grid_id","label"]).any():raise RuntimeError("Repeated grid/year")
    data["year"]=data.label.astype(str).str.extract(r"Y(\d{4})",expand=False).astype(int)
    cols=["era5_p24_max_mm","era5_annual_rain_mm","era5_temp_mean_c"]
    for c in cols:
        if c not in data:raise ValueError("Missing "+c)
    data=data.replace([np.inf,-np.inf],np.nan)
    def period(lo,hi,tag):
        sub=data[data.year.between(lo,hi)]
        grouped=sub.groupby("grid_id")[cols].agg(["mean","count"])
        result=pd.DataFrame({"grid_id":grouped.index})
        for c in cols:
            result[tag+"_"+c]=grouped[(c,"mean")].to_numpy()
            result[tag+"_"+c+"_years"]=grouped[(c,"count")].to_numpy()
            result.loc[result[tag+"_"+c+"_years"]<min_years,tag+"_"+c]=np.nan
        return result
    before=period(*early,"early")
    after=period(*late,"late")
    joined=before.merge(after,on="grid_id",validate="one_to_one")
    p=pd.read_parquet(pop)
    if "population_count" not in p or "grid_id" not in p:
        raise ValueError("2020 GHSL population field required")
    if p.grid_id.duplicated().any():raise ValueError("Duplicate population grid")
    joined=joined.merge(p[["grid_id","population_count"]],on="grid_id",how="left",validate="one_to_one")
    for c in cols:
        joined["delta_"+c]=joined["late_"+c]-joined["early_"+c]
    key="era5_p24_max_mm"
    ref=joined["early_"+key].dropna()
    if len(ref)<100:raise ValueError("Insufficient early-epoch complete grids")
    threshold=float(ref.quantile(threshold_q))
    joined["early_extreme"]=joined["early_"+key]>=threshold
    joined["late_extreme"]=joined["late_"+key]>=threshold
    eligible=joined["early_"+key].notna()&joined["late_"+key].notna()
    joined["hotspot_appeared"]=eligible&~joined.early_extreme&joined.late_extreme
    joined["hotspot_disappeared"]=eligible&joined.early_extreme&~joined.late_extreme
    joined["population_exposed_to_late_extreme_2020"] = np.where(
         eligible&joined.late_extreme,joined.population_count,np.nan)
    out.mkdir(parents=True,exist_ok=True)
    joined.to_parquet(out/"67_hazard_shift_grid10.parquet",index=False)
    qc={"early":early,"late":late,"min_years":min_years,
        "baseline_global_threshold_q":threshold_q,"rain24_threshold_mm":threshold,
        "paired_grid_cells":int(eligible.sum()),
        "appeared_cells":int(joined.hotspot_appeared.sum()),
        "disappeared_cells":int(joined.hotspot_disappeared.sum()),
        "fixed_2020_pop_exposed_late_hazard":float(joined.population_exposed_to_late_extreme_2020.sum()),
        "caution":"Annual max daily ERA5 rainfall; 2020 fixed population; association not flood risk/attribution",
        "status":"DESCRIPTIVE_HAZARD_EXPOSURE"}
    (out/"67_hazard_exposure_qc.json").write_text(json.dumps(qc,indent=2),encoding="utf-8")
    print("[DONE]",qc)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--annual-root",type=Path,required=True)
    p.add_argument("--population-2020",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--early",default="2000,2010")
    p.add_argument("--late",default="2016,2025")
    p.add_argument("--min-years",type=int,default=8)
    a=p.parse_args()
    parse=lambda s:tuple(int(x) for x in s.split(","))
    analyze(a.annual_root,a.population_2020,a.out,parse(a.early),parse(a.late),a.min_years)
if __name__=="__main__":main()
