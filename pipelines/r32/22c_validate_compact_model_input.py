"""Read-only Stage22c: audit model eligibility before Stage23.

No GEE calls. No data imputation or parent data changes. The 22b
"eligible_days" is a pre-weather risk flag, NOT the Stage23 sample count.
Stage23 uses exactly P24, P3H, API7, SM1 and excludes ongoing positive days.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import pandas as pd

WEATHER_BASENAMES = [
    "imerg_p30_max_mm", "imerg_p1h_max_mm", "imerg_p3h_max_mm",
    "imerg_p6h_max_mm", "imerg_p12h_max_mm", "imerg_p24_utc_mm",
    "era5_event_p24_mm", "era5_event_runoff24_mm",
    "era5_event_surface_runoff24_mm", "era5_ant_p3d_mm",
    "era5_ant_p7d_mm", "era5_ant_runoff3d_mm", "era5_ant_runoff7d_mm",
    "era5_api3_mm", "era5_api7_mm", "era5_sm1_prevday",
    "era5_sm2_prevday",
]
WEATHER_COLS = [f"{x}_{stat}" for x in WEATHER_BASENAMES for stat in ("mean","max")]
MODEL = {
    "P24":"imerg_p24_utc_mm_mean",
    "P3H":"imerg_p3h_max_mm_max",
    "API7":"era5_api7_mm_mean",
    "SM1":"era5_sm1_prevday_mean",
}
REQUIRED = set(WEATHER_COLS) | {
    "climate_id", "grid_id", "grid_x", "grid_y", "day", "year",
    "calendar_stratum_id", "reported_non_onset_day", "reported_flood_day",
    "episode_onset", "eligible_risk_day", "y_episode_onset",
}


def quoted(p:Path)->str:
    return p.resolve().as_posix().replace("'","''")


def query_one(con, statement):
    return con.execute(statement).fetchdf().iloc[0].to_dict()


def audit(panel: Path, out: Path, expected_rows:int, expected_onsets:int):
    out.mkdir(parents=True,exist_ok=True)
    con=duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("PRAGMA memory_limit='6GB'")
    path=quoted(panel)
    cols={r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()}
    absent=REQUIRED-cols
    if absent:
        raise RuntimeError("22b missing mandatory fields: "+", ".join(sorted(absent)))

    # Use the same exact 4-variable eligibility logic as Stage23.
    validity = " AND ".join([
        f"(TRY_CAST({col} AS DOUBLE) IS NOT NULL AND "
        f"isfinite(TRY_CAST({col} AS DOUBLE)) AND "
        f"TRY_CAST({col} AS DOUBLE) >= 0)"
        for name,col in MODEL.items() if name!="SM1"
    ] + [
        "(TRY_CAST(era5_sm1_prevday_mean AS DOUBLE) BETWEEN 0 AND 1)"
    ])
    all_weather = " AND ".join(
        f"(TRY_CAST({c} AS DOUBLE) IS NOT NULL AND isfinite(TRY_CAST({c} AS DOUBLE)))"
        for c in WEATHER_COLS
    )
    con.execute(f"""
       CREATE TEMP VIEW daily AS SELECT *,
         CAST(reported_non_onset_day=0 AS INTEGER) AS risk_candidate,
         CAST(COALESCE(({validity}), FALSE) AS INTEGER) AS model_valid,
         CAST(COALESCE(({all_weather}), FALSE) AS INTEGER) AS all_weather_valid
       FROM read_parquet('{path}')
    """)
    totals=query_one(con,"""
        SELECT COUNT(*) rows, COUNT(DISTINCT climate_id) unique_ids,
          COUNT(DISTINCT (grid_id, "day")) unique_grid_dates,
          COUNT(DISTINCT calendar_stratum_id) strata,
          COUNT(DISTINCT grid_id) grids,
          MIN(CAST("day" AS DATE)) first_day, MAX(CAST("day" AS DATE)) last_day,
          SUM(CAST(episode_onset AS BIGINT)) onset_days,
          SUM(CAST(reported_non_onset_day AS BIGINT)) continuing_days,
          SUM(risk_candidate) pre_weather_risk_days,
          SUM(CASE WHEN model_valid=0 THEN 1 ELSE 0 END) bad_model_weather_days,
          SUM(CASE WHEN all_weather_valid=0 THEN 1 ELSE 0 END) bad_any_weather_days,
          SUM(CASE WHEN episode_onset=1 AND model_valid=0 THEN 1 ELSE 0 END) bad_weather_onsets,
          SUM(CASE WHEN risk_candidate=1 AND model_valid=0 THEN 1 ELSE 0 END) bad_weather_risk_days,
          SUM(CASE WHEN episode_onset=1 AND reported_non_onset_day=1 THEN 1 ELSE 0 END) onset_continuing_conflict,
          SUM(CASE WHEN episode_onset=1 AND reported_flood_day=0 THEN 1 ELSE 0 END) onset_not_reported,
          SUM(CASE WHEN y_episode_onset<>episode_onset OR eligible_risk_day<>risk_candidate THEN 1 ELSE 0 END) inconsistent_model_flags,
          SUM(CASE WHEN CAST("day" AS DATE) IS NULL OR climate_id IS NULL OR grid_id IS NULL OR calendar_stratum_id IS NULL THEN 1 ELSE 0 END) invalid_keys
        FROM daily
    """)

    # Store a compact stratum table: daily exclusions can eliminate entire
    # strata. Stage23 uses valid_risk_days == risk_days, n>=2, 0<onsets<n.
    strata_path=quoted(out/"22c_strata_eligibility.parquet")
    con.execute(f"""
        COPY (
          WITH strata AS (
            SELECT calendar_stratum_id, MIN(year) AS year,
                   ANY_VALUE(grid_id) AS grid_id,
                   COUNT(*) AS days_total,
                   SUM(CAST(episode_onset AS INTEGER)) AS original_onsets,
                   SUM(risk_candidate) AS risk_days,
                   SUM(CASE WHEN risk_candidate=1 AND episode_onset=1 THEN 1 ELSE 0 END) AS risk_onsets,
                   SUM(CASE WHEN risk_candidate=1 AND model_valid=1 THEN 1 ELSE 0 END) AS weather_complete_risk_days,
                   SUM(CASE WHEN risk_candidate=1 AND model_valid=0 THEN 1 ELSE 0 END) AS missing_model_weather_days
            FROM daily
            GROUP BY calendar_stratum_id
          )
          SELECT *,
            CAST(risk_days>=2 AND risk_onsets>=1 AND risk_onsets<risk_days
                 AND weather_complete_risk_days=risk_days AS BOOLEAN) AS eligible
          FROM strata
        ) TO '{strata_path}' (FORMAT PARQUET,COMPRESSION ZSTD)
    """)
    stratum_source=f"read_parquet('{strata_path}')"
    retention=query_one(con,f"""
        SELECT COUNT(*) n_strata,
          SUM(CAST(eligible AS INTEGER)) eligible_strata,
          SUM(CASE WHEN eligible THEN risk_days ELSE 0 END) eligible_model_days,
          SUM(CASE WHEN eligible THEN risk_onsets ELSE 0 END) eligible_model_onsets,
          SUM(CASE WHEN NOT eligible THEN original_onsets ELSE 0 END) dropped_onsets,
          SUM(CASE WHEN NOT eligible AND missing_model_weather_days>0 THEN 1 ELSE 0 END) strata_dropped_for_weather,
          SUM(CASE WHEN NOT eligible AND risk_days<2 THEN 1 ELSE 0 END) strata_fewer_than_two_risk_days,
          SUM(CASE WHEN NOT eligible AND risk_onsets=risk_days AND risk_days>0 THEN 1 ELSE 0 END) strata_all_onset_days,
          SUM(CASE WHEN NOT eligible AND risk_onsets=0 THEN 1 ELSE 0 END) strata_no_onsets
        FROM {stratum_source}
    """)
    # Snapshot of year coverage and whether dropped onsets are concentrated
    # during the early/late observation window.
    annual=con.execute(f"""
        SELECT year,COUNT(*) n_strata,
          SUM(days_total) days_total, SUM(original_onsets) original_onsets,
          SUM(risk_days) pre_weather_risk_days,
          SUM(missing_model_weather_days) bad_model_weather_risk_days,
          SUM(CAST(eligible AS INTEGER)) eligible_strata,
          SUM(CASE WHEN eligible THEN risk_days ELSE 0 END) eligible_days,
          SUM(CASE WHEN eligible THEN risk_onsets ELSE 0 END) eligible_onsets,
          SUM(CASE WHEN NOT eligible THEN original_onsets ELSE 0 END) lost_onsets
        FROM {stratum_source}
        GROUP BY year ORDER BY year
    """).fetchdf()
    annual.to_csv(out/"22c_retention_by_year.csv",index=False,encoding="utf-8-sig")

    conditions=[]
    for c in WEATHER_COLS:
        v=f"TRY_CAST({c} AS DOUBLE)"
        conditions.append(f"SUM(CASE WHEN {v} IS NULL OR NOT isfinite({v}) THEN 1 ELSE 0 END) AS \"{c}\"")
    stats=query_one(con,"SELECT "+", ".join(conditions)+" FROM daily")
    pd.DataFrame([{"feature":k,"invalid_or_missing_days":int(v)} for k,v in stats.items()]).to_csv(
        out/"22c_weather_missing_by_field.csv",index=False,encoding="utf-8-sig")
    per_year=con.execute("""
        SELECT year,COUNT(*) AS days,
         SUM(CASE WHEN model_valid=0 THEN 1 ELSE 0 END) bad_model_weather_days,
         SUM(CASE WHEN all_weather_valid=0 THEN 1 ELSE 0 END) bad_any_weather_days,
         SUM(episode_onset) onset_days,
         SUM(CASE WHEN episode_onset=1 AND model_valid=0 THEN 1 ELSE 0 END) bad_weather_onsets
        FROM daily GROUP BY year ORDER BY year
    """).fetchdf()
    per_year.to_csv(out/"22c_weather_missing_by_year.csv",index=False,encoding="utf-8-sig")

    flags=[]
    for key,expected in (("rows",expected_rows),("unique_ids",expected_rows),
                         ("unique_grid_dates",expected_rows),("onset_days",expected_onsets)):
        if int(totals[key])!=expected:
            flags.append(f"{key}={totals[key]} differs from expected={expected}")
    for key in ("onset_continuing_conflict","onset_not_reported","inconsistent_model_flags","invalid_keys"):
        if int(totals[key])!=0:
            flags.append(f"{key}={totals[key]} invalid")
    if int(retention["eligible_strata"])<=0 or int(retention["eligible_model_onsets"])<=0:
        flags.append("No complete informative strata")
    # The first-stage sample and analytic sample intentionally differ.
    output={
        "panel":str(panel.resolve()),
        "raw":{key:str(value) if key.endswith("_day") else int(value) if isinstance(value,(int,float)) or hasattr(value,"__int__") else str(value)
               for key,value in totals.items()},
        "model_readiness":{key:int(value) for key,value in retention.items()},
        "model_features":MODEL,
        "status":"FAIL" if flags else "PASS_STRUCTURE",
        "errors":flags,
        "interpretation":"PASS_STRUCTURE only: numerical eligibility, not proof of reporting representativeness, independent storms, or causal validity.",
    }
    (out/"22c_model_readiness.json").write_text(json.dumps(output,indent=2,ensure_ascii=False),encoding="utf-8")
    con.close()
    print("[STAGE22C RAW]", totals, flush=True)
    print("[STAGE23 ELIGIBILITY]",retention, flush=True)
    print("[SAVED]",out,flush=True)
    if flags:
        raise RuntimeError("Stage22c structural/eligibility QC failed: "+"; ".join(flags))
    print("[PASS_STRUCTURE] Safe to try Stage23; still exploratory, not causal",flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,default=Path(r"E:\Global_flooding"))
    p.add_argument("--panel",type=Path,default=None)
    p.add_argument("--out",type=Path,default=None)
    p.add_argument("--expected-rows",type=int,default=2680571)
    p.add_argument("--expected-onsets",type=int,default=650361)
    args=p.parse_args()
    panel=args.panel or args.base/"standard_calendar_22_compact"/"22_standard_daily_with_climate.parquet"
    out=args.out or args.base/"stage32_qc"
    if not panel.is_file():raise FileNotFoundError(panel)
    audit(panel,out,args.expected_rows,args.expected_onsets)


if __name__=="__main__":
    main()
