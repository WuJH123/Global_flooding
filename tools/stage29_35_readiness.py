"""Read-only local prerequisite audit and recommended Stage29–35 order.

Never create GEE tasks or infer that missing file means invalid hydrology.
Use --strict to enforce only core prerequisites before starting Stage29/30.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import duckdb

def checks(base):
    loc={
        "climate_panel":base/"standard_calendar_22_compact"/"22_standard_daily_with_climate.parquet",
        "static_m3":base/"mechanism_project"/"05_static_merged"/"grid_static_context_m3.parquet",
        "ghsl_csv_root":base/"Groundsource_all",
        "city_crosswalk":base/"mechanism_project"/"01_cities"/"m3"/"grid_city_crosswalk_all.csv",
        "city_grid_centers":base/"mechanism_project"/"01_cities"/"m3"/"grid_centers.csv",
        "episodes":base/"episode_control"/"flood_episodes_m3.parquet",
        "city_ghsl_epochs":base/"mechanism_project"/"30_urban_context"/"30_city_ghsl_epochs.parquet",
        "city_features":base/"mechanism_project"/"30_urban_context"/"30b_city_features_2020.parquet",
        "selection":base/"stage33_feature_selection"/"32_feature_selection.json",
        "city_types":base/"stage33_city_typology"/"33_city_environment_types.csv",
        "hydro_optional":base/"mechanism_project"/"31_hydrogeomorphic"/"31_hydrogeomorphic_grid_context.parquet"
    }
    status={k:{"exists":v.exists(),"path":str(v)} for k,v in loc.items()}
    errors=[]
    if status["climate_panel"]["exists"]:
        con=duckdb.connect()
        try:
            columns={x[0] for x in con.execute("DESCRIBE SELECT * FROM read_parquet(?)",[str(loc["climate_panel"])]).fetchall()}
        finally:con.close()
        need={"grid_id","calendar_stratum_id","grid_x","grid_y","year",
              "episode_onset","reported_non_onset_day",
              "imerg_p24_utc_mm_mean","imerg_p24_utc_mm_max",
              "imerg_p3h_max_mm_mean","imerg_p3h_max_mm_max",
              "era5_api7_mm_mean","era5_sm1_prevday_mean"}
        missing=sorted(need-columns)
        if missing:errors.append("Missing climate panel columns: "+",".join(missing))
    else:errors.append("Stage22b climate panel absent")
    if not loc["static_m3"].exists():errors.append("M3 static context absent")
    if not loc["ghsl_csv_root"].is_dir():errors.append("Historical GHSL CSV root absent")
    return status,errors

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--strict",action="store_true")
    p.add_argument("--output",type=Path)
    a=p.parse_args()
    status,errors=checks(a.base)
    for name,row in status.items():
        print("[OK] " if row["exists"] else "[MISSING] ",name,row["path"])
    for error in errors:print("[CORE ISSUE]",error)
    note={"status":status,"core_errors":errors,
          "order":"29/30 -> optional31 -> 30b -> 32 -> 33 -> 34; 35 parallel",
          "scientific_limit":"No city-wide nonreporting controls, calibrated flood probabilities or physical drainage capacity."}
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(note,indent=2,ensure_ascii=False),encoding="utf-8")
    if errors and a.strict:raise SystemExit(2)

if __name__=="__main__":main()
