"""Synthetic full Stage22b smoke test for mixed L0-success, L1-success, L2-retry CSVs."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
MERGE=ROOT/"pipelines"/"r32"/"22b_merge_compact_weather.py"
WEATHER_NAMES=[
 "imerg_p30_max_mm","imerg_p1h_max_mm","imerg_p3h_max_mm",
 "imerg_p6h_max_mm","imerg_p12h_max_mm","imerg_p24_utc_mm",
 "era5_event_p24_mm","era5_event_runoff24_mm","era5_event_surface_runoff24_mm",
 "era5_ant_p3d_mm","era5_ant_p7d_mm","era5_ant_runoff3d_mm","era5_ant_runoff7d_mm",
 "era5_api3_mm","era5_api7_mm","era5_sm1_prevday","era5_sm2_prevday"
]
WEATHER=[f"{name}_{stat}" for name in WEATHER_NAMES for stat in ("mean","max")]


def to_parquet(frame, path):
    con=duckdb.connect()
    con.register("x",frame)
    con.execute("COPY x TO ? (FORMAT PARQUET)",[str(path)])
    con.close()


def export_csv(path,rows):
    cols=["climate_id","grid_id","date_str",*WEATHER]
    with path.open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=cols)
        writer.writeheader()
        for key,date in rows:
            writer.writerow({"climate_id":key,"grid_id":"g1","date_str":date,
                             **{name:1.0 for name in WEATHER}})


def test_22b_mix_with_second_retry_date_coverage(tmp_path):
    base=tmp_path
    calendar=base/"standard_calendar_20b"
    (calendar/"21_compact").mkdir(parents=True)
    (calendar/"21_rescue").mkdir()
    (base/"climate_global").mkdir()
    originals=base/"orig"; originals.mkdir()
    rescued=base/"rescued"; rescued.mkdir()
    retries=base/"retry"; retries.mkdir()
    sources=[
        ("g1_2020-01-01","2020-01-01",1),
        ("g1_2020-02-01","2020-02-01",2),
        ("g1_2020-02-02","2020-02-02",2),
        ("g1_2020-03-01","2020-03-01",3),
        ("g1_2020-03-02","2020-03-02",3),
    ]
    missing=pd.DataFrame([
        {"climate_id":cid,"grid_id":"g1","year":2020,"month":month,
         "request_chunk":0,"date_str":date,"sample_date":date}
        for cid,date,month in sources[1:]
    ])
    to_parquet(missing,calendar/"20_missing_climate_requests_chunked.parquet")
    to_parquet(pd.DataFrame([{"climate_id":sources[0][0],**{w:1.0 for w in WEATHER}}]),
               base/"climate_global"/"climate_features_primary.parquet")
    daily=pd.DataFrame([
        {"climate_id":cid,"grid_id":"g1","day":date,
         "calendar_stratum_id":"s1","episode_onset":int(i==0),
         "reported_non_onset_day":0}
        for i,(cid,date,_) in enumerate(sources)
    ])
    to_parquet(daily,calendar/"20_standard_daily_panel.parquet")
    source_months=[{"year":2020,"month":m,"chunk":0,"requests":2} for m in (2,3)]
    pd.DataFrame([{"block":8,"requests":4,"filename":"std32c_aaaaaaaa_b008",
                   "members_json":json.dumps(source_months)}]).to_csv(
        calendar/"21_compact"/"compact_task_plan.csv",index=False)
    rescue_rows=[
        {"parent_block":8,"recovery_part":0,"requests":2,
         "filename":"std32r_bbbbbbbb_b008_s00",
         "members_json":json.dumps([source_months[0]])},
        {"parent_block":8,"recovery_part":1,"requests":2,
         "filename":"std32r_bbbbbbbb_b008_s01",
         "members_json":json.dumps([source_months[1]])},
    ]
    pd.DataFrame(rescue_rows).to_csv(calendar/"21_rescue"/"rescue_plan.csv",index=False)
    retry_rows=[]
    for i,day in enumerate(("2020-03-01","2020-03-02")):
        retry_rows.append({
           "parent_block":8,"parent_part":1,"retry_part":i,"requests":1,
           "filename":f"std32t_cccccccc_b008_s01_t{i:02d}",
           "members_json":json.dumps([{"year":2020,"month":3,"chunk":0,
                                      "dates":[day],"requests":1}])
        })
    pd.DataFrame(retry_rows).to_csv(calendar/"retry_plan.csv",index=False)
    export_csv(rescued/"std32r_bbbbbbbb_b008_s00.csv",[(sources[1][0],sources[1][1]),(sources[2][0],sources[2][1])])
    for row,index in zip(retry_rows,(3,4)):
        export_csv(retries/(row["filename"]+".csv"),[(sources[index][0],sources[index][1])])
    cmd=[sys.executable,str(MERGE),"--base",str(base),"--csv-root",str(originals),
         "--rescue-plan",str(calendar/"21_rescue"/"rescue_plan.csv"),
         "--rescue-csv-root",str(rescued),"--retry-plan",str(calendar/"retry_plan.csv"),
         "--retry-csv-root",str(retries)]
    proc=subprocess.run(cmd,capture_output=True,text=True)
    assert proc.returncode==0,proc.stdout+"\n"+proc.stderr
    result=base/"standard_calendar_22_compact"/"22_standard_daily_with_climate.parquet"
    con=duckdb.connect()
    n,unique,onsets=con.execute(
        "SELECT COUNT(*),COUNT(DISTINCT climate_id),SUM(y_episode_onset) FROM read_parquet(?)",
        [str(result)]).fetchone()
    con.close()
    assert (n,unique,onsets)==(5,5,1)
