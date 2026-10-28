# -*- coding: utf-8 -*-
"""Stage65b: align 10km event/referent weather CSV with locked grid10 calendar.

Anti-duplicate climate IDs, coverage + units QC; write nullable, NEVER zero
fill. 48 half-hour records/day required for strict 24h rainfall, ERA wetness
must be physically bounded. Standard aliases to allow model code reuse.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import duckdb

FEATURES=["imerg_p24_utc_mm","imerg_p3h_rolling_mm",
          "imerg_halfhour_count","era5_api7_mm","era5_api7_valid_days",
          "era5_sm1_prevday","era5_temp_day_c"]
def merge(calendar,requests,csv_root,out):
    files=sorted(csv_root.rglob("gf10event_v1_*.csv"))
    if not files:raise FileNotFoundError(f"No completed GF10EVENT production CSV in {csv_root}")
    out.mkdir(parents=True,exist_ok=True)
    con=duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("CREATE VIEW expected AS SELECT * FROM read_parquet(\x27"+str(requests).replace("\x27","\x27\x27")+"\x27)")
    con.execute("CREATE VIEW calendar AS SELECT * FROM read_parquet(\x27"+str(calendar).replace("\x27","\x27\x27")+"\x27)")
    con.from_csv_auto([str(x) for x in files],header=True,union_by_name=True).create_view("downloaded")
    required=set(["climate_id",*FEATURES])
    have=set(con.execute("DESCRIBE downloaded").df().column_name)
    if not required<=have:raise ValueError(f"Missing weather columns {required-have}")
    dups=con.execute("""SELECT COUNT(*)-COUNT(DISTINCT climate_id)
       FROM downloaded""").fetchone()[0]
    if dups:raise RuntimeError(f"Duplicate weather IDs across exports: {dups}")
    unknown=con.execute("""SELECT COUNT(*) FROM downloaded d LEFT JOIN expected e
       USING(climate_id) WHERE e.climate_id IS NULL""").fetchone()[0]
    if unknown:raise RuntimeError(f"Downloaded {unknown} unexpected IDs")
    sql="""CREATE TEMP TABLE stitched AS
      SELECT c.*,e.climate_id,
        TRY_CAST(w.imerg_p24_utc_mm AS DOUBLE) AS imerg_p24_utc_mm_mean,
        TRY_CAST(w.imerg_p3h_rolling_mm AS DOUBLE) AS imerg_p3h_max_mm_max,
        TRY_CAST(w.imerg_halfhour_count AS DOUBLE) AS imerg_halfhour_count,
        TRY_CAST(w.era5_api7_mm AS DOUBLE) AS era5_api7_mm_mean,
        TRY_CAST(w.era5_api7_valid_days AS DOUBLE) AS era5_api7_valid_days,
        TRY_CAST(w.era5_sm1_prevday AS DOUBLE) AS era5_sm1_prevday_mean,
        TRY_CAST(w.era5_temp_day_c AS DOUBLE) AS era5_temp_day_c,
        e.grid_x,e.grid_y,e.longitude,e.latitude,
        year(c.event_date) AS year
      FROM calendar c
      LEFT JOIN expected e ON e.grid_id=c.grid_id
               AND CAST(e.event_date AS DATE)=c.event_date
      LEFT JOIN downloaded w ON w.climate_id=e.climate_id"""
    con.execute(sql)
    counts=con.execute("""SELECT COUNT(*) n_rows,COUNT(DISTINCT stratum_id) n_strata,
      SUM(is_onset) n_onsets,
      COUNT(*) FILTER (WHERE climate_id IS NULL) unmatched_grid_date,
      COUNT(*) FILTER (WHERE imerg_p24_utc_mm_mean IS NULL OR
          imerg_p3h_max_mm_max IS NULL OR era5_api7_mm_mean IS NULL OR
          era5_sm1_prevday_mean IS NULL) incomplete_weather,
      COUNT(*) FILTER (WHERE imerg_halfhour_count<48 OR imerg_halfhour_count IS NULL) incomplete_halfhours,
      COUNT(*) FILTER (WHERE era5_api7_valid_days<7 OR era5_api7_valid_days IS NULL) incomplete_api7,
      COUNT(*) FILTER (WHERE era5_sm1_prevday_mean<0 OR era5_sm1_prevday_mean>1) bad_soil
      FROM stitched""").df().iloc[0].to_dict()
    if counts["unmatched_grid_date"]>0:raise RuntimeError("Calendar cells not in frozen request universe")
    if counts["bad_soil"]>0:raise RuntimeError("Physical soil moisture violation")
    dst=out/"65b_standard_daily_grid10.parquet"
    con.execute("COPY stitched TO \x27"+str(dst).replace("\x27","\x27\x27")+"\x27 (FORMAT PARQUET,COMPRESSION ZSTD)")
    qc={k:int(v) for k,v in counts.items()}
    qc["completed_csv_files"]=len(files)
    qc["complete_model_input"]=(qc["incomplete_weather"]==0 and qc["incomplete_halfhours"]==0 and qc["incomplete_api7"]==0)
    qc["warning"]="Unreported referent dates are not true negative events; storm clustering and label errors not resolved"
    (out/"65b_weather_qc.json").write_text(json.dumps(qc,indent=2),encoding="utf-8")
    con.close();print("[DONE]",qc)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--calendar",type=Path,required=True)
    p.add_argument("--requests",type=Path,required=True)
    p.add_argument("--csv-root",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args();merge(a.calendar,a.requests,a.csv_root,a.out)
if __name__=="__main__":main()
