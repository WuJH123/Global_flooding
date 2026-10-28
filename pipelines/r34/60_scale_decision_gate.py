# -*- coding: utf-8 -*-
"""Stage60: preregistered scale-readiness scorecard, NO inferred model winner.

Join Stage57/58/59 diagnostics. A 5/10 km *flood-response* winner requires
reconstructed same-event case/reference exposure and a common heldout
storm/city split at all three scales. Until then preserve observed M3 20km
response as verified baseline; 5km is environmental support only.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd

def evaluate(location,boundary,source_qc,min_cities=30,min_zones=3,
             min_precision_classes=2,min_source_audited=200):
    raw=pd.read_parquet(location)
    required={"city_id","event_id","scale_km","origin_offset_fraction",
              "precision_class","touched_cells","max_geocode_overlap_fraction"}
    if not required<=set(raw):raise ValueError("Stage59 missing "+str(required-set(raw)))
    orig=raw[raw.origin_offset_fraction.eq(0)].copy()
    shifted=raw[raw.origin_offset_fraction.eq(.5)].copy()
    if orig.duplicated(["city_id","event_id","scale_km"]).any() or shifted.duplicated(
        ["city_id","event_id","scale_km"]).any():
        raise RuntimeError("Duplicate city/event/scale/origin")
    merged=orig.merge(shifted[["city_id","event_id","scale_km","touched_cells"]],on=[
        "city_id","event_id","scale_km"],how="inner",validate="one_to_one",
        suffixes=("","_shifted"))
    if len(merged)!=len(orig):raise RuntimeError("Missing shifted-grid comparison")
    if set(merged.scale_km)!= {5,10,20}:raise RuntimeError("All 5/10/20km scales required")
    if not {"city_id","status","within_city_5km_sd"}<=set(boundary):
        raise ValueError("Stage58 city-boundary audit missing key fields")
    report=[]
    for scale,g in merged.groupby("scale_km"):
        ok=g.touched_cells.notna() & g.touched_cells_shifted.notna()
        stable=g.loc[ok]
        report.append({"scale_km":int(scale),
          "n_geo_support_evaluable":int(ok.sum()),
          "n_event_records":len(g),
          "fraction_single_unshifted":float((stable.touched_cells==1).mean()) if len(stable) else np.nan,
          "fraction_single_both_origins":float(((stable.touched_cells==1)&
              (stable.touched_cells_shifted==1)).mean()) if len(stable) else np.nan,
          "mean_abs_touched_shift":float((stable.touched_cells-stable.touched_cells_shifted).abs().mean()) if len(stable) else np.nan,
          "median_dom_geocode_overlap":float(stable.max_geocode_overlap_fraction.median()) if len(stable) else np.nan})
    table=pd.DataFrame(report).sort_values("scale_km")
    loc_cities=int(merged.city_id.nunique())
    loc_prec=int(merged.precision_class.nunique())
    city_pass=int(boundary.status.eq("PASS_QUADRATURE").sum())
    if not source_qc:raise ValueError("Independent source audit QC required")
    source_ready=(int(source_qc.get("n_valid",0))>=min_source_audited)
    city_ready=(city_pass>=min_cities)
    zones_ready=True # zone count is checked from Stage59 city selection file, not proxy in event table
    req={"source_audit_min":source_ready,"city_boundary_min":city_ready,
         "pilot_cities_min":loc_cities>=min_cities,
         "precision_proxy_classes_min":loc_prec>=min_precision_classes}
    preliminary=all(req.values())
    result={"status":"PILOT_AUDITED" if preliminary else "INSUFFICIENT_EVIDENCE",
      "response_scale_winner":"NOT_DETERMINABLE_WITHOUT_SAME_EVENT_5_10_20KM_CLIMATE_EXPOSURE",
      "operational_main_response_scale_km":20,
      "environment_native_cache_scale_km":5,
      "next_response_candidate_scale_km":10 if preliminary else None,
      "gate_checks":req,"piloted_cities":loc_cities,
      "event_location_proxy_classes":loc_prec,"city_clipped_pass":city_pass,
      "sensitivity":report,
      "caution":"No finer-scale event labels or meteorological observations inferred from 20km positives.",
      "next_gate":"same event geometry, same referent calendar, compatible 5/10/20km native meteorology, common heldout storm/city folds and paired uncertainty"}
    return result,table

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--location",type=Path,required=True)
    p.add_argument("--boundary",type=Path,required=True)
    p.add_argument("--source-qc",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--min-cities",type=int,default=30)
    p.add_argument("--min-zones",type=int,default=3)
    p.add_argument("--min-precision-classes",type=int,default=2)
    p.add_argument("--min-source-audited",type=int,default=200)
    p.add_argument("--pilot-cities",type=Path,help="Stage59 city table used for Köppen zone coverage")
    p.add_argument("--environment-qc",type=Path,required=True,help="Stage61 all-three-scale city coverage")
    a=p.parse_args()
    source=json.loads(a.source_qc.read_text(encoding="utf8"))
    raw,table=evaluate(a.location,pd.read_csv(a.boundary,dtype={"city_id":str}),
                       source,a.min_cities,a.min_zones,a.min_precision_classes,a.min_source_audited)
    if a.pilot_cities:
        selected=pd.read_csv(a.pilot_cities)
        n_zones=int(selected.zone.nunique())
        raw["n_koppen_zones"]=n_zones
        raw["gate_checks"]["zone_coverage_min"]=n_zones>=a.min_zones
        if n_zones<a.min_zones:
            raw["status"]="INSUFFICIENT_EVIDENCE"
            raw["next_response_candidate_scale_km"]=None
    else:
        raw["status"]="INCOMPLETE_ZONE_VALIDATION"
        raw["next_response_candidate_scale_km"]=None
    env=json.loads(a.environment_qc.read_text(encoding="utf8"))
    enough_env=int(env.get("cities_with_all_3_scales",0))>=a.min_cities
    raw["n_cities_environment_all_scales"]=int(env.get("cities_with_all_3_scales",0))
    raw["gate_checks"]["environment_scale_city_coverage"]=bool(enough_env)
    if not enough_env:
        raw["status"]="INSUFFICIENT_EVIDENCE"
        raw["next_response_candidate_scale_km"]=None
    a.out.mkdir(parents=True,exist_ok=True)
    table.to_csv(a.out/"60_multiscale_support_scorecard.csv",index=False)
    (a.out/"60_scale_decision.json").write_text(json.dumps(raw,ensure_ascii=False,indent=2),encoding="utf8")
    print("[DECISION]",json.dumps(raw,ensure_ascii=False))
if __name__=="__main__":main()
